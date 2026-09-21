"""Narrow owner-request capabilities; native IDs never become browser authority."""

import hashlib
import json
import uuid
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .commitments import Input
from .shared_session import SharedSessionError
from .shared_session_commands import SubmissionUncertain

COMMAND = "item/commandExecution/requestApproval"
FILE = "item/fileChange/requestApproval"
QUESTION = "item/tool/requestUserInput"
METHODS = {
    COMMAND: "command-approval-decision",
    FILE: "file-approval-decision",
    QUESTION: "submit-user-input",
}


def encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def text(value, limit=4096):
    return isinstance(value, str) and 0 < len(value.encode()) <= limit


def file_items(state, turn_id, item_id):
    """Read raw matching items, never the intentionally truncated public projection."""
    history = state.get("turnHistory") or {}
    if not isinstance(history, dict):
        return []
    if history.get("kind") == "canonical":
        canonical = history.get("history")
        if not isinstance(canonical, dict):
            return []
        entities, islands = canonical.get("entitiesByKey"), canonical.get("islands")
        if (
            not isinstance(entities, dict)
            or len(entities) > 4096
            or not isinstance(islands, list)
            or len(islands) > 128
        ):
            return []
        keys = set()
        for island in islands:
            entries = island.get("entries") if isinstance(island, dict) else None
            if not isinstance(entries, list) or len(entries) > 4096:
                return []
            for entry in entries:
                key = entry.get("value") if isinstance(entry, dict) else None
                if not isinstance(key, str):
                    return []
                keys.add(key)
                if len(keys) > 4096:
                    return []
        turns = [entities.get(key) for key in keys]
    else:
        turns = state.get("turns", [])
        if not isinstance(turns, list) or len(turns) > 4096:
            return []
    matches = []
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("turnId") != turn_id:
            continue
        items = turn.get("items")
        if not isinstance(items, list) or len(items) > 4096:
            return []
        matches.extend(
            i
            for i in items
            if isinstance(i, dict)
            and i.get("type") == "fileChange"
            and i.get("id") == item_id
        )
    return matches


def review(packet, snapshot):
    """Refuse incomplete/complex review rather than truncate approved contents."""
    method, p = packet.get("method"), packet.get("params")
    if method not in METHODS or not isinstance(p, dict):
        return None
    if len(encoded(p)) > 32768 or p.get("threadId") != snapshot.owner.thread_id:
        return None
    if not all(text(p.get(k), 256) for k in ("itemId", "turnId")):
        return None
    if p.get("reason") is not None and not isinstance(p["reason"], str):
        return None
    result = json.loads(encoded(p))
    if method == COMMAND:
        if not text(p.get("command"), 16384) or not text(p.get("cwd")):
            return None
        if (
            p.get("kind", "command") != "command"
            or p.get("networkApprovalContext") is not None
            or p.get("additionalPermissions") is not None
        ):
            return None
        allowed = p.get("availableDecisions")
        if allowed is not None and (
            not isinstance(allowed, list)
            or not all(isinstance(x, (str, dict)) for x in allowed)
        ):
            return None
        decisions = [
            x for x in ("accept", "decline") if allowed is None or x in allowed
        ]
        if not decisions:
            return None
        return {"params": result, "decisions": decisions}
    if method == FILE:
        # Pinned schema documents grantRoot as a possible session-wide write grant.
        if p.get("grantRoot") is not None:
            return None
        matches = file_items(snapshot.state, p["turnId"], p["itemId"])
        if len(matches) != 1:
            return None
        changes = matches[0].get("changes")
        if (
            not isinstance(changes, list)
            or not 1 <= len(changes) <= 30
            or len(encoded(changes)) > 32768
        ):
            return None
        for change in changes:
            if (
                not isinstance(change, dict)
                or not text(change.get("path"))
                or not isinstance(change.get("diff"), str)
            ):
                return None
            kind = change.get("kind")
            if not isinstance(kind, dict) or kind.get("type") not in (
                "add",
                "delete",
                "update",
            ):
                return None
        return {
            "params": result,
            "changes": changes,
            "decisions": ["accept", "decline"],
        }
    questions = p.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 6:
        return None
    ids = set()
    for q in questions:
        if not isinstance(q, dict) or set(q) - {
            "id",
            "header",
            "question",
            "options",
            "isOther",
            "isSecret",
        }:
            return None
        if q.get("isSecret") not in (None, False) or q.get("isOther") not in (
            None,
            False,
            True,
        ):
            return None
        if (
            not all(
                text(q.get(k), 4096 if k == "question" else 128)
                for k in ("id", "header", "question")
            )
            or q["id"] in ids
            or q["id"] in ("__proto__", "constructor", "prototype")
        ):
            return None
        ids.add(q["id"])
        options = q.get("options")
        if options is not None:
            if not isinstance(options, list) or len(options) > 12:
                return None
            if any(
                not isinstance(o, dict)
                or set(o) != {"label", "description"}
                or not text(o.get("label"), 256)
                or not isinstance(o.get("description"), str)
                or len(o["description"]) > 4096
                for o in options
            ):
                return None
            if len({o["label"] for o in options}) != len(options):
                return None
    return {"params": result}


class Decision(Input):
    generation: str = Field(min_length=1, max_length=128)
    requestId: uuid.UUID
    response: dict


class SharedDecisions:
    def __init__(self, shared):
        self.shared = shared
        self.generation = None
        self.entries = {}
        self.active = {}
        self.cards = []

    def sync(self):
        s = self.shared
        if not s.snapshot:
            self.cards = []
            return
        if self.generation != s.generation:
            self.entries, self.active = {}, {}
            self.generation = s.generation
        packets = s.snapshot.state.get("requests") or []
        if not isinstance(packets, list):
            packets = []
        old, current, cards = self.active, {}, []
        native_counts = {}
        for packet in packets:
            if isinstance(packet, dict):
                native = encoded([type(packet.get("id")).__name__, packet.get("id")])
                native_counts[native] = native_counts.get(native, 0) + 1
        for packet in packets[:32]:
            if not isinstance(packet, dict):
                continue
            native = packet.get("id")
            method = packet.get("method")
            public = {
                "id": "ide:unsupported:" + digest(packet),
                "method": str(method)[:128],
                "params": {"threadId": s.snapshot.owner.thread_id},
                "unsupported": True,
                "detail": "Review and answer this request in the original Codex window; Leam cannot safely show its complete review.",
            }
            valid_id = (
                type(native) is int and abs(native) <= 9007199254740991
            ) or text(native, 256)
            data = (
                review(packet, s.snapshot)
                if valid_id
                and native_counts[encoded([type(native).__name__, native])] == 1
                else None
            )
            if data is None or len(self.entries) >= 512:
                cards.append(public)
                continue
            identity = digest(
                [
                    asdict(s.snapshot.owner),
                    type(native).__name__,
                    native,
                    method,
                    packet["params"],
                    data,
                ]
            )
            capability = old.get(identity)
            if capability is None:
                capability = "ide:cap:" + str(uuid.uuid4())
                self.entries[capability] = {
                    "identity": identity,
                    "owner": s.snapshot.owner,
                    "packet": json.loads(encoded(packet)),
                    "data": data,
                }
            current[identity] = capability
            entry = self.entries[capability]
            row = self.receipt_row(identity)
            receipt = (
                json.loads(row["result"])
                if row and row["result"]
                else (
                    {
                        "state": "uncertain",
                        "detail": "A response may already have been delivered. It will not be resent; inspect Codex.",
                    }
                    if row
                    else None
                )
            )
            cards.append(
                {
                    "id": capability,
                    "transport": "ide-owner",
                    "generation": s.generation,
                    "method": method,
                    **entry["data"],
                    "submitted": row is not None,
                    "receipt": receipt,
                    "connected": s.connected,
                }
            )
        for capability, entry in self.entries.items():
            entry["present"] = current.get(entry["identity"]) == capability
        for capability, entry in list(self.entries.items())[-32:]:
            if not entry["present"] and self.receipt_row(entry["identity"]):
                cards.append(
                    {
                        "id": capability,
                        "transport": "ide-owner",
                        "generation": s.generation,
                        "method": entry["packet"]["method"],
                        **entry["data"],
                        "submitted": True,
                        "connected": s.connected,
                        "receipt": {
                            "state": "resolved",
                            "detail": "This request is no longer pending in Codex. It may have been answered in another window; this does not confirm tool execution.",
                        },
                    }
                )
        self.active, self.cards = current, cards

    def receipt_row(self, identity):
        with self.shared.store.connect() as db:
            return db.execute(
                "SELECT * FROM requests WHERE id=?", ("shared-decision:" + identity,)
            ).fetchone()

    def listing(self):
        self.sync()
        return self.cards

    def validate(self, entry, response):
        if len(encoded(response)) > 16384:
            raise HTTPException(422, "Response exceeds its size limit")
        if entry["packet"]["method"] != QUESTION:
            if (
                set(response) != {"decision"}
                or response["decision"] not in entry["data"]["decisions"]
            ):
                raise HTTPException(
                    422, "Choose an available allow-once or decline decision"
                )
            return
        questions = entry["data"]["params"]["questions"]
        answers = response.get("answers")
        if (
            set(response) != {"answers"}
            or not isinstance(answers, dict)
            or set(answers) != {q["id"] for q in questions}
        ):
            raise HTTPException(422, "Answer every requested question")
        for q in questions:
            value = answers[q["id"]]
            if (
                not isinstance(value, dict)
                or set(value) != {"answers"}
                or not isinstance(value["answers"], list)
                or len(value["answers"]) != 1
                or not text(value["answers"][0], 4096)
                or not value["answers"][0].strip()
            ):
                raise HTTPException(422, "Each question needs one nonempty answer")
            if (
                q.get("options")
                and not q.get("isOther")
                and value["answers"][0] not in [o["label"] for o in q["options"]]
            ):
                raise HTTPException(422, "Choose one of the offered answers")

    def current(self, capability, generation):
        self.sync()
        entry = self.entries.get(capability)
        if (
            not self.shared.connected
            or generation != self.shared.generation
            or not entry
            or not entry["present"]
            or entry["owner"] != self.shared.snapshot.owner
        ):
            raise SharedSessionError(
                "This request changed or is no longer pending. Refresh before answering."
            )
        return entry

    async def respond(self, capability, body):
        s = self.shared
        async with s.lock:
            await s.ensure()
            try:
                entry = self.current(capability, body.generation)
            except SharedSessionError as error:
                raise HTTPException(409, str(error)) from error
            self.validate(entry, body.response)
            key = "shared-decision:" + entry["identity"]
            fingerprint = digest([entry["identity"], body.response])
            row = self.receipt_row(entry["identity"])
            if row:
                if row["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409,
                        "A response to this request was already recorded. It will not be replaced.",
                    )
                return (
                    json.loads(row["result"])
                    if row["result"]
                    else {
                        "state": "uncertain",
                        "detail": "Response delivery is uncertain; it will not be resent.",
                    }
                )
            try:
                existing = s.store.reserve(key, fingerprint)
            except ValueError as error:
                raise HTTPException(
                    409,
                    "A response is already reserved; inspect Codex before continuing",
                ) from error
            if existing is not None:
                return existing
            try:
                await s.commands.respond_request(
                    entry["owner"],
                    entry["packet"],
                    body.response,
                    lambda: self.current(capability, body.generation),
                )
                receipt = {
                    "state": "submitted",
                    "detail": "Response submitted to Codex. Waiting for the request to leave its pending list; this does not confirm execution.",
                }
            except SubmissionUncertain:
                receipt = {
                    "state": "uncertain",
                    "detail": "Codex may have received your response. It will not be resent; inspect the original window.",
                }
            except SharedSessionError as error:
                s.store.release_unsent(key, fingerprint)
                raise HTTPException(409, str(error)) from error
            s.store.finish(key, receipt)
            return receipt


def router(shared):
    routes = APIRouter(prefix="/api/codex/shared")

    @routes.post("/requests/{capability}")
    async def answer(capability: str, body: Decision):
        return await shared.decisions.respond(capability, body)

    return routes
