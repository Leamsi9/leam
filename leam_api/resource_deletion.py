"""Explicit immutable-version resource removal with content-free replay barriers."""

import hashlib
import json
import time
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictBool, model_validator

from .commitments import Input

PREFIX = "resource-deleted:"


class DeleteResource(Input):
    requestId: UUID
    expectedSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: StrictBool

    @model_validator(mode="after")
    def explicit(self):
        if not self.confirmed:
            raise ValueError("Confirm removal of this resource")
        return self


def delete(artifacts, key, body):
    from .artifacts import ID

    if not ID.fullmatch(key):
        raise HTTPException(404, "Resource not found")
    fingerprint = hashlib.sha256(
        json.dumps([key, body.model_dump(mode="json")], sort_keys=True).encode()
    ).hexdigest()
    receipt_key = "resource-delete:" + str(body.requestId)
    with artifacts.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT fingerprint,result FROM requests WHERE id=?", (receipt_key,)
        ).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Deletion request ID belongs to another operation"
                )
            return json.loads(prior["result"])
        tombstone = db.execute(
            "SELECT value FROM settings WHERE key=?", (PREFIX + key,)
        ).fetchone()
        if tombstone:
            saved = json.loads(tombstone[0])
            if saved["sha256"] != body.expectedSha256:
                raise HTTPException(
                    409, "Deleted resource version does not match this request"
                )
            result = {"id": key, "state": "deleted", "deletedAt": saved["deletedAt"]}
        else:
            row = db.execute(
                "SELECT json_extract(value,'$.sha256') hash, json_extract(value,'$.storage') storage, json_extract(value,'$.attachmentId') attachment_id FROM settings WHERE key=?",
                ("artifact:" + key,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Resource not found or no longer available")
            if row["hash"] != body.expectedSha256:
                raise HTTPException(
                    409, "Resource version changed; reload before deleting"
                )
            if row["storage"] == "attachment":
                from .attachment_resources import delete_bytes

                try:
                    delete_bytes(
                        artifacts.store, db, row["attachment_id"], body.expectedSha256
                    )
                except (ValueError, OSError) as error:
                    raise HTTPException(
                        409,
                        str(error)
                        if isinstance(error, ValueError)
                        else "Attachment cache could not be removed safely; no deletion confirmed",
                    ) from error
            now = time.time()
            db.execute(
                "INSERT INTO settings VALUES (?,?)",
                (
                    PREFIX + key,
                    json.dumps({"sha256": body.expectedSha256, "deletedAt": now}),
                ),
            )
            db.executemany(
                "DELETE FROM settings WHERE key=?",
                [
                    (prefix + key,)
                    for prefix in ("artifact:", "resource-read:", "resource-links:")
                ],
            )
            result = {"id": key, "state": "deleted", "deletedAt": now}
        db.execute(
            "INSERT INTO requests VALUES (?,?,?,?)",
            (receipt_key, fingerprint, "completed", json.dumps(result)),
        )
        return result
