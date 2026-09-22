"""Content-free decision suppression. No declined payloads or semantic vectors."""

import hashlib
import hmac
import json
import re
import secrets

SECRET_KEY = "proposals.suppression-secret.v1"
PREFIX = "proposals.suppression:"


def secret(db, *, create=False):
    row = db.execute("SELECT value FROM settings WHERE key=?", (SECRET_KEY,)).fetchone()
    if row:
        return bytes.fromhex(json.loads(row[0]))
    if not create:
        return None
    db.execute(
        "INSERT OR IGNORE INTO settings VALUES (?,?)",
        (SECRET_KEY, json.dumps(secrets.token_hex(32))),
    )
    return bytes.fromhex(
        json.loads(
            db.execute(
                "SELECT value FROM settings WHERE key=?", (SECRET_KEY,)
            ).fetchone()[0]
        )
    )


def key(db, kind, value, *, create=False):
    signing_key = secret(db, create=create)
    if signing_key is None:
        return None
    data = json.dumps([kind, value], sort_keys=True, separators=(",", ":")).encode()
    return PREFIX + hmac.new(signing_key, data, hashlib.sha256).hexdigest()


def identity(operation, data):
    if operation.endswith(".create"):
        title = data.get("title", data.get("name", ""))
        normalized = " ".join(re.findall(r"\w+", str(title).casefold()))
        return (
            [operation, normalized, data.get("capacityId"), data.get("kind", "task")]
            if normalized
            else [operation, data]
        )
    # Revisions change on unrelated edits and do not establish fresh intent.
    return [
        operation,
        {k: v for k, v in data.items() if k not in ("revision", "commitmentRevision")},
    ]


def state(db, proposal_id):
    row = db.execute(
        "SELECT value FROM settings WHERE key=?", (key(db, "id", proposal_id),)
    ).fetchone()
    return json.loads(row[0])["state"] if row else None


def suppressed(db, proposal_id, operation, data):
    prior = state(db, proposal_id)
    if prior:
        return prior
    row = db.execute(
        "SELECT value FROM settings WHERE key=?",
        (key(db, "intent", identity(operation, data)),),
    ).fetchone()
    return json.loads(row[0])["state"] if row else None


def record(db, proposal_id, operation, data, decision):
    for kind, value in (("id", proposal_id), ("intent", identity(operation, data))):
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key(db, kind, value, create=True), json.dumps({"state": decision})),
        )
