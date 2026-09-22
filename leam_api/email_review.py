"""Explicit local owner judgments; source text and model output confer no authority."""

import json
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from .commitments import Input
from .email_actionability import (
    CACHE_PREFIX,
    VERSION,
    fingerprint,
    identity,
    review_revision,
)


class EmailReview(Input):
    accountId: str = Field(min_length=1, max_length=200)
    messageId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,256}$")
    reviewRevision: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["action", "ignore"]
    kind: Literal["reply", "todo"] | None = None
    action: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def explicit_choice(self):
        if self.decision == "action" and (not self.action or self.kind is None):
            raise ValueError("Confirm an action label and reply or task kind")
        if self.decision == "ignore" and (self.action or self.kind is not None):
            raise ValueError("Excluding mail must not include an action")
        return self


def save_review(classifier, body):
    """One synchronous source/access/revision check and encrypted write transaction."""
    conflict = "Saved mail or its review changed. Refresh before confirming again."
    key = CACHE_PREFIX + body.accountId
    with classifier.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT a.body AS account_body,e.body FROM accounts a JOIN email_snapshots e ON e.account_id=a.id WHERE a.id=?",
            (body.accountId,),
        ).fetchone()
        if not row:
            raise HTTPException(409, conflict)
        account = classifier.vault.open(
            "account:" + body.accountId, row["account_body"]
        )
        grant = account.get("email", {}).get("grantId")
        items = classifier.vault.open(
            "email-snapshot:" + body.accountId, row["body"]
        ).get("items", [])
        item = next(
            (
                item
                for item in items
                if item["id"] == body.messageId and item["accountId"] == body.accountId
            ),
            None,
        )
        if item is None or classifier.current_items(db, item, grant) is None:
            raise HTTPException(409, conflict)
        saved = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        cache = classifier.decode_cache(key, json.loads(saved[0]) if saved else None)
        if key in classifier.cache_errors:
            raise HTTPException(409, "Saved email decisions need repair before review")
        if (
            review_revision(item, grant, cache.get(identity(item)))
            != body.reviewRevision
        ):
            raise HTTPException(409, conflict)
        now = time.time()
        decision = {
            "state": body.decision,
            "kind": body.kind,
            "basis": None,
            "reason": "Explicit user review; not independently verified",
            "action": body.action,
            "evidence": "",
            "classifiedAt": now,
            "pending": False,
            "reviewedBy": "user",
            "reviewedAt": now,
        }
        live = {identity(source) for source in items}
        cache = {key: value for key, value in cache.items() if key in live}
        record = {
            "version": VERSION,
            "inputHash": fingerprint(item),
            "grant": grant,
            "decision": decision,
        }
        cache[identity(item)] = record
        db.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(classifier.vault.seal(key, cache))),
        )
        return {
            "accountId": body.accountId,
            "messageId": body.messageId,
            "reviewRevision": review_revision(item, grant, record),
            "actionability": decision,
        }
