"""Metadata references to canonical uploads; no second content store."""

import json
import re
import time

PREFIX = "attachment-"
MAX_ATTACHMENTS = 1000
TERMINAL = {"completed", "failed", "interrupted", "cancelled", "canceled"}


def resource_id(key):
    return PREFIX + key


def value(row):
    from .artifacts import FORMATS

    kinds = {mime: kind for kind, (mime, _) in FORMATS.items()}
    kind = kinds.get(row["mime"], "text")
    return {
        "id": resource_id(row["id"]),
        "title": row["filename"],
        "filename": row["filename"],
        "kind": kind,
        "sha256": row["sha256"],
        "bytes": row["size"],
        "publishedAt": row["created"],
        "storage": "attachment",
        "attachmentId": row["id"],
        "source": None,
        "sources": [],
    }


def index_row(db, row):
    item = value(row)
    db.execute(
        "INSERT OR IGNORE INTO settings VALUES (?,?)",
        ("artifact:" + item["id"], json.dumps(item)),
    )


def initialize_catalog(db):
    # Metadata only: never SELECT the BLOB during migration/catalogue indexing.
    rows = db.execute(
        "SELECT id,filename,mime,size,sha256,created,bindings FROM attachments a "
        "WHERE size>0 AND NOT EXISTS(SELECT 1 FROM settings s WHERE "
        "s.key='artifact:attachment-'||a.id OR s.key='resource-deleted:attachment-'||a.id)"
    ).fetchall()
    for row in rows:
        index_row(db, row)
        for request_id in json.loads(row["bindings"]):
            receipt = db.execute(
                "SELECT result FROM runtime_actions WHERE id=?", (request_id,)
            ).fetchone()
            if receipt and receipt[0]:
                accepted(db, request_id, json.loads(receipt[0]), surface="companion")
            else:
                receipt = db.execute(
                    "SELECT result FROM requests WHERE id=? AND state='complete'",
                    (request_id,),
                ).fetchone()
                if receipt and receipt[0]:
                    accepted(db, request_id, json.loads(receipt[0]), surface="coding")


def remember_binding(db, request_id, thread_id, surface):
    if not thread_id or surface not in {"coding", "companion"}:
        return
    saved = json.dumps({"threadId": thread_id, "surface": surface}, sort_keys=True)
    prior = db.execute(
        "SELECT value FROM settings WHERE key=?", ("attachment-binding:" + request_id,)
    ).fetchone()
    if prior and prior[0] != saved:
        raise ValueError("Attachment request belongs to another conversation")
    db.execute(
        "INSERT OR IGNORE INTO settings VALUES (?,?)",
        ("attachment-binding:" + request_id, saved),
    )


def setting(db, key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def acknowledged(result):
    return (
        isinstance(result, dict)
        and (
            result.get("accepted") is True
            or result.get("outcome")
            in {"submitted", "already_submitted", "deferred_busy"}
        )
        and isinstance(result.get("accepted_message_ref"), str)
        and result["accepted_message_ref"].startswith("msg:")
    )


def accepted(db, request_id, result, *, surface):
    if not db.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='attachments'"
    ).fetchone():
        return
    rows = db.execute(
        "SELECT a.id FROM attachments a WHERE a.size>0 AND EXISTS "
        "(SELECT 1 FROM json_each(a.bindings) b WHERE b.value=?)",
        (request_id,),
    ).fetchall()
    if not rows:
        return
    binding = setting(db, "attachment-binding:" + request_id) or {}
    thread_id = binding.get("threadId")
    if surface == "companion":
        ref = result.get("accepted_message_ref")
        if not acknowledged(result):
            return
        turn_id = ref[4:]
        action = db.execute(
            "SELECT json_extract(body,'$.thread_id') FROM runtime_actions WHERE id=?",
            (request_id,),
        ).fetchone()
        thread_id = action[0] if action else thread_id
    else:
        turn_id = (result.get("turn") or {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            return
    source = {"surface": surface, "turnId": turn_id, "requestId": request_id}
    if thread_id:
        source["threadId"] = thread_id
    targets = []
    if thread_id:
        item = (
            setting(db, "companion-item:" + thread_id)
            if surface == "companion"
            else None
        )
        ticket = (
            setting(db, "ticket-thread:" + thread_id) if surface == "coding" else None
        )
        if item and item.get("kind") == "commitment":
            targets.append(("commitment", item["itemId"]))
        if ticket and ticket.get("ticket", {}).get("feature"):
            targets.append(("feature", ticket["ticket"]["feature"]))
    from .resource_links import MAX_LINKS, MAX_PER_RESOURCE, ResourceLinks

    for row in rows:
        key = resource_id(row["id"])
        artifact = setting(db, "artifact:" + key)
        if not artifact or any(
            s.get("requestId") == request_id for s in artifact.get("sources", [])
        ):
            continue
        sources = artifact.setdefault("sources", [])
        if len(sources) < 32:
            sources.append(source)
        else:
            artifact["sourcesTruncated"] = True
            db.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (json.dumps(artifact), "artifact:" + key),
            )
            continue
        artifact["source"] = artifact.get("source") or {
            k: v for k, v in source.items() if k != "requestId"
        }
        db.execute(
            "UPDATE settings SET value=? WHERE key=?",
            (json.dumps(artifact), "artifact:" + key),
        )
        links = ResourceLinks.saved(db, key)
        for target_type, target_id in targets:
            if any(
                (r["targetType"], r["targetId"]) == (target_type, target_id)
                for r in links["items"]
            ):
                continue
            if not ResourceLinks.target(db, target_type, target_id)["available"]:
                continue
            total = db.execute(
                "SELECT coalesce(sum(json_array_length(value,'$.items')),0) FROM settings WHERE key LIKE 'resource-links:%'"
            ).fetchone()[0]
            if len(links["items"]) >= MAX_PER_RESOURCE or total >= MAX_LINKS:
                continue
            links["items"].append(
                {
                    "targetType": target_type,
                    "targetId": target_id,
                    "linkedAt": time.time(),
                }
            )
            links["revision"] += 1
            links["updatedAt"] = time.time()
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("resource-links:" + key, json.dumps(links)),
            )

    if surface == "companion":
        action = db.execute(
            "SELECT body FROM runtime_actions WHERE id=?", (request_id,)
        ).fetchone()
        if action:
            body = json.loads(action[0])
            if body.get("attachments"):
                # Only authoritative acknowledged receipts enter this branch.
                # Unknown outcomes retain the original exact retry payload.
                body["attachments"] = []
                db.execute(
                    "UPDATE runtime_actions SET body=? WHERE id=?",
                    (json.dumps(body), request_id),
                )


def confirm_history(db, turns):
    for turn in turns:
        if turn.get("status") in TERMINAL and isinstance(turn.get("id"), str):
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES (?,?)",
                (
                    "attachment-terminal:" + turn["id"],
                    json.dumps({"state": turn["status"]}),
                ),
            )


def deletion_allowed(db, row):
    """Recorded delivery is required; unresolved bindings always retain their bytes."""
    for request_id in json.loads(row["bindings"]):
        action = db.execute(
            "SELECT result FROM runtime_actions WHERE id=?", (request_id,)
        ).fetchone()
        if action:
            result = json.loads(action[0]) if action[0] else {}
            if acknowledged(result):
                continue
        else:
            receipt = db.execute(
                "SELECT state,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            result = (
                json.loads(receipt["result"]) if receipt and receipt["result"] else {}
            )
            turn = result.get("turn") or {}
            turn_id = turn.get("id")
            if receipt and receipt["state"] == "complete" and isinstance(turn_id, str):
                if turn.get("status") in TERMINAL or setting(
                    db, "attachment-terminal:" + turn_id
                ):
                    continue
                # Canonical native completed events are evidence, not a model report.
                completed = db.execute(
                    "SELECT 1 FROM events WHERE topic='codex' AND json_extract(payload,'$.method')='turn/completed' AND json_extract(payload,'$.params.turn.id')=? LIMIT 1",
                    (turn_id,),
                ).fetchone()
                if completed:
                    continue
        raise ValueError(
            "Attachment delivery or coding completion is unconfirmed. Open its conversation to refresh status before deleting; uncertain messages retain their files."
        )


def clear_acknowledged_payloads(db, row):
    # The immutable fingerprint/accepted receipt stays intact; no retry can dispatch
    # this scrubbed payload because unresolved actions were rejected above.
    import base64
    import hashlib

    for request_id in json.loads(row["bindings"]):
        action = db.execute(
            "SELECT body FROM runtime_actions WHERE id=?", (request_id,)
        ).fetchone()
        if not action:
            continue
        body = json.loads(action[0])
        kept = []
        for part in body.get("attachments", []):
            try:
                same = (
                    hashlib.sha256(
                        base64.b64decode(part.get("data_base64", ""), validate=True)
                    ).hexdigest()
                    == row["sha256"]
                )
            except ValueError:
                same = False
            if not same:
                kept.append(part)
        body["attachments"] = kept
        db.execute(
            "UPDATE runtime_actions SET body=? WHERE id=?",
            (json.dumps(body), request_id),
        )


def remove_cached_file(store, db, row):
    """Remove only a verified private materialization, preserving shared digests."""
    import hashlib
    import os
    import stat

    from .attachments import MAX_BYTES
    from .office_documents import OFFICE_TYPES

    if db.execute(
        "SELECT 1 FROM attachments WHERE id!=? AND sha256=? AND size>0 LIMIT 1",
        (row["id"], row["sha256"]),
    ).fetchone():
        return
    if not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
        raise ValueError("Attachment cache identity is invalid")
    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        **{m: v[0] for m, v in OFFICE_TYPES.items()},
    }.get(row["mime"], ".txt")
    try:
        directory = os.open(
            store.path.parent / "attachments",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except FileNotFoundError:
        return
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError(
                "Attachment cache must be private and owned by this installation"
            )
        name = row["sha256"] + suffix
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Attachment cache entry is not a private regular file")
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (
                info.st_dev,
                info.st_ino,
                row["size"],
            ):
                raise ValueError(
                    "Attachment cache identity changed; no deletion confirmed"
                )
            data = source.read(MAX_BYTES + 1)
            if (
                len(data) != row["size"]
                or hashlib.sha256(data).hexdigest() != row["sha256"]
            ):
                raise ValueError(
                    "Attachment cache integrity check failed; no deletion confirmed"
                )
            latest = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (latest.st_dev, latest.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(
                    "Attachment cache identity changed; no deletion confirmed"
                )
            os.unlink(name, dir_fd=directory)
    finally:
        os.close(directory)


def delete_bytes(store, db, attachment_id, expected_hash):
    row = db.execute(
        "SELECT id,filename,mime,size,sha256,bindings FROM attachments WHERE id=?",
        (attachment_id,),
    ).fetchone()
    if row is None or row["size"] == 0 or row["sha256"] != expected_hash:
        raise ValueError("Attachment version is no longer available")
    deletion_allowed(db, row)
    remove_cached_file(store, db, row)
    clear_acknowledged_payloads(db, row)
    # Metadata and bindings deliberately remain for readable historical messages.
    db.execute("UPDATE attachments SET bytes=?,size=0 WHERE id=?", (b"", attachment_id))
