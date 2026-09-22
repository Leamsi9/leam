"""Encrypted, revision-controlled local drafts. No provider write capability."""

import hashlib
import json
import time
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, HTTPException, Query
from pydantic import ConfigDict, Field, field_validator, model_validator

from .commitments import Input

PREFIX = "email-draft:"
MAX_DRAFTS = 256


class DraftSave(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, strict=True)
    revision: int = Field(ge=0)
    accountId: str = Field(min_length=1, max_length=256)
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    bcc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=100000)
    sourceMessageId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,256}$")

    @field_validator("to", "cc", "bcc")
    @classmethod
    def recipients(cls, values):
        if any(
            not value.strip()
            or len(value) > 500
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            for value in values
        ):
            raise ValueError("Recipients must be bounded single-line addresses")
        return [value.strip() for value in values]

    @field_validator("subject", "accountId")
    @classmethod
    def headers(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Header control characters are not allowed")
        return value


class DraftDelete(Input):
    revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]


class DraftQuery(Input):
    action: Literal["list", "read", "save"] = "list"
    id: UUID | None = None
    draft: DraftSave | None = None
    accountId: str | None = Field(default=None, min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def shape(self):
        if self.action != "list" and self.id is None:
            raise ValueError("Read/save requires a stable draft UUID")
        if (self.action == "save") != (self.draft is not None):
            raise ValueError("Only save requires draft contents")
        return self


def validate_saved(value, key):
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("id") != str(UUID(key))
    ):
        raise ValueError("Invalid local draft identity")
    if type(value.get("revision")) is not int or value["revision"] < 1:
        raise ValueError("Invalid local draft revision")
    payload = DraftSave.model_validate(
        {"revision": value["revision"], **value["payload"]}
    )
    if payload.revision != value["revision"]:
        raise ValueError("Invalid local draft payload revision")
    for name in ("createdAt", "updatedAt"):
        if type(value.get(name)) not in (float, int) or not 0 < value[name] < 1e12:
            raise ValueError("Invalid local draft time")
    receipt = value.get("lastWrite", {})
    if (
        receipt.get("expectedRevision") != value["revision"] - 1
        or not isinstance(receipt.get("fingerprint"), str)
        or len(receipt["fingerprint"]) != 64
    ):
        raise ValueError("Invalid local draft receipt")
    return value


class EmailDrafts:
    def __init__(self, store, vault):
        self.store, self.vault = store, vault

    def _open(self, key, raw):
        try:
            return validate_saved(self.vault.open(PREFIX + key, json.loads(raw)), key)
        except (InvalidTag, ValueError, TypeError, KeyError) as error:
            raise HTTPException(
                503, "Saved local draft is unreadable; restore a verified backup"
            ) from error

    @staticmethod
    def public(saved, summary=False):
        payload = dict(saved["payload"])
        if summary:
            payload.pop("body", None)
            payload.pop("bcc", None)
        return {
            "id": saved["id"],
            "revision": saved["revision"],
            "createdAt": saved["createdAt"],
            "updatedAt": saved["updatedAt"],
            **payload,
            "storage": "leam",
            "sent": False,
            "savedInGmail": False,
            "untrustedContent": True,
        }

    def read(self, key):
        key = str(UUID(str(key)))
        with self.store.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (PREFIX + key,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Local draft not found")
        return self.public(self._open(key, row["value"]))

    def list(self, account_id=None, offset=0, limit=20):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'email-draft:%' LIMIT ?",
                (MAX_DRAFTS + 1,),
            ).fetchall()
        if len(rows) > MAX_DRAFTS:
            raise HTTPException(503, "Local draft store exceeds its supported bound")
        items = [self._open(row["key"][len(PREFIX) :], row["value"]) for row in rows]
        items = [
            item
            for item in items
            if account_id is None or item["payload"]["accountId"] == account_id
        ]
        items.sort(key=lambda item: (item["updatedAt"], item["id"]), reverse=True)
        return {
            "items": [
                self.public(item, True) for item in items[offset : offset + limit]
            ],
            "nextOffset": offset + limit if offset + limit < len(items) else None,
            "total": len(items),
            "storage": "leam",
            "savedInGmail": False,
        }

    def save(self, key, request):
        key = str(UUID(str(key)))
        payload = request.model_dump(exclude={"revision"})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM settings WHERE key=?", ("email-draft-deleted:" + key,)
            ).fetchone():
                raise HTTPException(
                    410, "This local draft was removed; use a new draft ID"
                )
            account = db.execute(
                "SELECT provider FROM accounts WHERE id=?", (request.accountId,)
            ).fetchone()
            if account is None or account["provider"] != "google":
                raise HTTPException(
                    422, "Select a connected Google account for this local draft"
                )
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (PREFIX + key,)
            ).fetchone()
            old = self._open(key, row["value"]) if row else None
            revision = old["revision"] if old else 0
            if (
                old
                and revision == request.revision + 1
                and old["lastWrite"]
                == {"expectedRevision": request.revision, "fingerprint": digest}
            ):
                return self.public(old)
            if revision != request.revision:
                raise HTTPException(409, "Local draft changed; reload before saving")
            if (
                not old
                and db.execute(
                    "SELECT count(*) FROM settings WHERE key LIKE 'email-draft:%'"
                ).fetchone()[0]
                >= MAX_DRAFTS
            ):
                raise HTTPException(
                    409, "Local draft limit reached; remove an unneeded draft first"
                )
            now = time.time()
            saved = {
                "version": 1,
                "id": key,
                "revision": revision + 1,
                "payload": payload,
                "createdAt": old["createdAt"] if old else now,
                "updatedAt": now,
                "lastWrite": {
                    "expectedRevision": request.revision,
                    "fingerprint": digest,
                },
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PREFIX + key, json.dumps(self.vault.seal(PREFIX + key, saved))),
            )
        return self.public(saved)

    def delete(self, key, request):
        key = str(UUID(str(key)))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (PREFIX + key,)
            ).fetchone()
            if row is None:
                return {"removed": True, "storage": "leam", "gmailChanged": False}
            old = self._open(key, row["value"])
            if old["revision"] != request.revision:
                raise HTTPException(409, "Local draft changed; reload before removing")
            db.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                (
                    "email-draft-deleted:" + key,
                    json.dumps({"revision": old["revision"]}),
                ),
            )
            db.execute("DELETE FROM settings WHERE key=?", (PREFIX + key,))
        return {"removed": True, "storage": "leam", "gmailChanged": False}

    def call(self, query):
        if query.action == "save":
            return self.save(query.id, query.draft)
        if query.action == "read":
            return self.read(query.id)
        return self.list(query.accountId, query.offset, query.limit)


def router(drafts):
    routes = APIRouter(prefix="/api/email/drafts")

    @routes.get("")
    async def list_drafts(
        accountId: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=20),
    ):
        return drafts.list(accountId, offset, limit)

    @routes.get("/{key}")
    async def read_draft(key: UUID):
        return drafts.read(key)

    @routes.put("/{key}")
    async def save_draft(key: UUID, body: DraftSave):
        return drafts.save(key, body)

    @routes.delete("/{key}")
    async def delete_draft(key: UUID, body: DraftDelete):
        return drafts.delete(key, body)

    return routes
