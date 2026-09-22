"""Explicit local email→task conversion through canonical approval operations."""

import asyncio
import json
from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .agenda import source_key
from .commitments import Input
from .proposals import Propose

PREFIX = "email-task:v1:"


class Convert(Input):
    key: str = Field(pattern=r"^email:[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=500)
    capacityId: str | None = Field(default=None, max_length=100)


class EmailTasks:
    def __init__(self, store, emails, proposals):
        self.store, self.emails, self.proposals = store, emails, proposals
        self.locks = defaultdict(asyncio.Lock)

    def source(self, key):
        for item in self.emails.overview()["items"]:
            if source_key("email", item["accountId"], item["id"]) == key:
                if item.get("actionability", {}).get("state") != "action":
                    raise HTTPException(
                        409,
                        "This email is not currently included as actionable. Review its triage first.",
                    )
                return item
        raise HTTPException(
            404, "Email is no longer available from a connected account. Refresh Inbox."
        )

    def status(self, saved):
        try:
            proposal = self.proposals.get(saved["proposal"]["requestId"])
        except HTTPException as error:
            if error.status_code != 404:
                raise
            from .proposal_decisions import suppressed

            with self.store.connect() as db:
                state = suppressed(
                    db,
                    saved["proposal"]["requestId"],
                    "commitment.create",
                    saved["proposal"]["input"],
                )
            return {
                "state": state or "prepared",
                "proposalId": saved["proposal"]["requestId"],
                "draft": saved["draft"],
            }
        result = proposal.get("result") or {}
        commitment_id = result.get("id") if proposal["state"] == "complete" else None
        with self.store.connect() as db:
            exists = (
                commitment_id
                and db.execute(
                    "SELECT 1 FROM entities WHERE id=? AND kind='commitment'",
                    (commitment_id,),
                ).fetchone()
                is not None
            )
        return {
            "state": proposal["state"],
            "proposalId": proposal["id"],
            "commitmentId": commitment_id if exists else None,
            "removed": bool(commitment_id and not exists),
            "draft": saved["draft"],
        }

    def preview(self, key):
        proposal_id = str(uuid5(NAMESPACE_URL, "leam:email-task:" + key))
        decision = self.proposals.decision_state(proposal_id)
        if decision:
            return {"state": decision, "proposalId": proposal_id}
        saved = self.store.get(PREFIX + key)
        if saved:
            return self.status(saved)
        item = self.source(key)
        return {
            "state": "new",
            "draft": {
                "title": (
                    item.get("actionability", {}).get("action")
                    or item.get("subject")
                    or "Follow up on email"
                )[:500],
                "capacityId": None,
            },
            "subject": item.get("subject", "")[:500],
        }

    async def convert(self, body):
        async with self.locks[body.key]:
            proposal_id = str(uuid5(NAMESPACE_URL, "leam:email-task:" + body.key))
            decision = self.proposals.decision_state(proposal_id)
            if decision:
                return {"state": decision, "proposalId": proposal_id}
            saved = self.store.get(PREFIX + body.key)
            draft = body.model_dump(exclude={"key"})
            if saved:
                if saved["draft"] != draft:
                    raise HTTPException(
                        409,
                        "This email already has a task request. Open its task or approval to edit it.",
                    )
            else:
                item = self.source(body.key)
                # Provenance is reference data, never instructions or approval.
                # Use only the canonical account/message IDs and provider URL.
                proposal = Propose(
                    requestId=uuid5(NAMESPACE_URL, "leam:email-task:" + body.key),
                    threadId="inbox:" + body.key,
                    operation="commitment.create",
                    input={
                        **draft,
                        "kind": "task",
                        "owner": "user",
                        "notes": "Source email (reference only): "
                        + str(item.get("url", ""))[:2000],
                    },
                    reason="Explicitly converted an actionable Inbox email to a task.",
                )
                saved = {
                    "draft": draft,
                    "proposal": proposal.model_dump(mode="json"),
                    "source": {
                        "accountId": item["accountId"],
                        "messageId": item["id"],
                        "threadId": item.get("threadId"),
                        "key": body.key,
                    },
                }
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if (
                        body.capacityId
                        and not db.execute(
                            "SELECT 1 FROM entities WHERE id=? AND kind='capacity'",
                            (body.capacityId,),
                        ).fetchone()
                    ):
                        raise HTTPException(
                            404, "Capacity no longer exists. Choose another."
                        )
                    row = db.execute(
                        "SELECT value FROM settings WHERE key=?", (PREFIX + body.key,)
                    ).fetchone()
                    if row:
                        saved = json.loads(row[0])
                        if saved["draft"] != draft:
                            raise HTTPException(
                                409,
                                "Another conversion already reserved this email. Refresh.",
                            )
                    else:
                        db.execute(
                            "INSERT INTO settings VALUES (?,?)",
                            (PREFIX + body.key, json.dumps(saved)),
                        )
            await self.proposals.propose(
                Propose(**saved["proposal"]), origin="today", actor="user"
            )
            return self.status(saved)


def router(store, emails, proposals):
    routes = APIRouter(prefix="/api/inbox-mail")
    service = EmailTasks(store, emails, proposals)
    previous_cleanup = proposals.on_content_deleted

    def forget(db, proposal_id):
        if previous_cleanup:
            previous_cleanup(db, proposal_id)
        db.execute(
            "DELETE FROM settings WHERE key LIKE ? AND json_extract(value,'$.proposal.requestId')=?",
            (PREFIX + "%", proposal_id),
        )

    proposals.on_content_deleted = forget

    @routes.get("/task")
    async def preview(key: str = Query(pattern=r"^email:[0-9a-f]{64}$")):
        return service.preview(key)

    @routes.post("/task")
    async def convert(body: Convert):
        return await service.convert(body)

    return routes
