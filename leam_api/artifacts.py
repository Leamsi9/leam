"""Bounded private resources, never a filesystem browser or external URL proxy."""

import argparse
import base64
import binascii
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from .attachments import validate as validate_attachment
from .resource_deletion import DeleteResource
from .resource_deletion import delete as delete_resource
from .resource_reads import ResourceRead, ResourceReads
from .store import Store

MAX_BYTES = 10 * 1024 * 1024
MAX_STORED_BYTES = 128 * 1024 * 1024
MAX_RESOURCES = 1000
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
TEXT_KINDS = {"markdown", "html", "text"}
IMAGE_KINDS = {"png", "jpeg", "webp"}
FORMATS = {
    "markdown": ("text/markdown", "md"),
    "html": ("text/html", "html"),
    "text": ("text/plain", "txt"),
    "png": ("image/png", "png"),
    "jpeg": ("image/jpeg", "jpg"),
    "webp": ("image/webp", "webp"),
    "pdf": ("application/pdf", "pdf"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
}
PREVIEW_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'; sandbox allow-scripts"
)
DOWNLOAD_POLICY = "default-src 'none'; sandbox"


class ArtifactConflict(ValueError):
    pass


class ArtifactQuota(ValueError):
    pass


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: str | None = Field(default=None, max_length=64)
    threadId: str | None = Field(default=None, max_length=128)
    turnId: str | None = Field(default=None, max_length=128)
    toolCallId: str | None = Field(default=None, max_length=128)


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=96)
    title: str = Field(max_length=200)
    kind: str = Field(max_length=16)
    content: str | None = None
    contentBase64: str | None = None
    filename: str | None = Field(default=None, max_length=200)
    source: Source | None = None


def source_metadata(source):
    if source is None:
        return None
    value = Source.model_validate(source).model_dump(exclude_none=True)
    if any(
        not v or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in value.values()
    ):
        raise ValueError("Resource source references must be bounded plain identifiers")
    return value or None


def metadata(value):
    key, kind = value["id"], value["kind"]
    mime, suffix = FORMATS[kind]
    return {
        **{
            k: value[k]
            for k in ("id", "title", "kind", "sha256", "bytes", "publishedAt")
        },
        "filename": value.get("filename") or f"{key}.{suffix}",
        "mimeType": mime,
        "source": value.get("source") or None,
        "sources": value.get("sources", []),
        "sourcesTruncated": value.get("sourcesTruncated", False),
        "category": "attachment"
        if value.get("storage") == "attachment"
        else "generated",
        "tags": ["Attachment"] if value.get("storage") == "attachment" else [],
        "modifiedAt": value["publishedAt"],
        "previewUrl": f"/api/artifacts/{key}/preview"
        if kind == "html" or kind in IMAGE_KINDS
        else None,
        "downloadUrl": f"/api/artifacts/{key}/download",
        "url": "/?artifact=" + key,
    }


def resource_bytes(value):
    if "_attachmentBytes" in value:
        return value["_attachmentBytes"]
    if value["kind"] in TEXT_KINDS:
        return value["content"].encode("utf-8")
    return base64.b64decode(value["contentBase64"], validate=True)


class Artifacts:
    def __init__(self, store):
        self.store = store
        self.reads = ResourceReads(store)
        from .attachments import AttachmentStore

        self.attachments = AttachmentStore(store)

    def metadata(self, value):
        from .resource_catalog import modified_at

        item = metadata(value)
        item["modifiedAt"] = modified_at(
            value, self.store.get("resource-links:" + value["id"], {})
        )
        return self.reads.decorate([item])[0]

    def publish(
        self,
        key,
        title,
        kind,
        content=None,
        *,
        content_base64=None,
        filename=None,
        source=None,
        record=None,
    ):
        if not isinstance(key, str) or not ID.fullmatch(key):
            raise ValueError(
                "Use a lowercase artifact ID with letters, digits and hyphens"
            )
        if key.startswith("attachment-"):
            raise ValueError("Attachment resource IDs are reserved for uploaded files")
        if kind not in FORMATS:
            raise ValueError("Unsupported resource format")
        if (
            not title.strip()
            or len(title) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in title)
        ):
            raise ValueError("Provide a short plain title")
        mime, suffix = FORMATS[kind]
        filename = filename if filename is not None else f"{key}.{suffix}"
        if (
            not filename
            or len(filename) > 200
            or filename in {".", ".."}
            or any(ord(c) < 32 or ord(c) == 127 or c in '/\\"' for c in filename)
        ):
            raise ValueError(
                "Resource filename must be a plain filename without paths or controls"
            )
        source = source_metadata(source)
        if kind in TEXT_KINDS:
            if (
                not isinstance(content, str)
                or content_base64 is not None
                or "\0" in content
            ):
                raise ValueError("Text resources require UTF-8 content only")
            raw = content.encode("utf-8")
            stored = {"content": content}
        else:
            if (
                content is not None
                or not isinstance(content_base64, str)
                or len(content_base64) > 4 * ((MAX_BYTES + 2) // 3)
            ):
                raise ValueError("Binary resources require bounded base64 content only")
            try:
                raw = base64.b64decode(content_base64, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("Invalid resource base64 content") from error
            validate_attachment(raw, mime, filename)
            stored = {"contentBase64": base64.b64encode(raw).decode("ascii")}
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError("Each resource must contain between 1 byte and 10 MiB")
        value = {
            "id": key,
            "title": title,
            "kind": kind,
            **stored,
            "filename": filename,
            "source": source,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "publishedAt": time.time(),
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM settings WHERE key=?", ("resource-deleted:" + key,)
            ).fetchone():
                raise ArtifactConflict(
                    "Resource ID was deleted; publish a new immutable ID"
                )
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", ("artifact:" + key,)
            ).fetchone()
            if row:
                existing = json.loads(row["value"])
                prior = metadata(existing)
                if any(
                    prior[field] != metadata(value)[field]
                    for field in ("sha256", "kind", "title", "filename", "source")
                ):
                    raise ArtifactConflict(
                        "Artifact ID already published; use a new versioned ID"
                    )
                return prior
            used, count = db.execute(
                "SELECT coalesce(sum(json_extract(value,'$.bytes')),0),count(*) FROM settings WHERE key LIKE 'artifact:%' AND coalesce(json_extract(value,'$.storage'),'') != 'attachment'"
            ).fetchone()
            if used + len(raw) > MAX_STORED_BYTES or count >= MAX_RESOURCES:
                raise ArtifactQuota(
                    "Resource storage is full; publication was not saved"
                )
            db.execute(
                "INSERT INTO settings(key,value) VALUES (?,?)",
                ("artifact:" + key, json.dumps(value)),
            )
            if record:
                record(db, metadata(value))
        return metadata(value)

    def list(
        self, q="", kind=None, cursor=None, limit=30, *, sort="modified", order="desc", group="all"
    ):
        from .resource_catalog import listing

        return listing(self, q, kind, cursor, limit, sort=sort, order=order, group=group)

    def get(self, key):
        if not ID.fullmatch(key):
            raise HTTPException(404, "Artifact not found")
        value = self.store.get("artifact:" + key)
        if not value:
            raise HTTPException(404, "Artifact not found or no longer available")
        if value.get("storage") == "attachment":
            try:
                row = self.attachments.rows([value["attachmentId"]])[0]
            except ValueError as error:
                raise HTTPException(404, "Attachment is no longer available") from error
            if row["sha256"] != value["sha256"]:
                raise HTTPException(409, "Attachment resource integrity mismatch")
            value = {**value, "_attachmentBytes": row["bytes"]}
            if value["kind"] in {"text", "markdown"}:
                value["content"] = row["bytes"].decode("utf-8-sig")
        return value


def router(artifacts):
    routes = APIRouter(prefix="/api/artifacts")

    @routes.get("/_status")
    def read_status():
        return artifacts.reads.status()

    @routes.post("/_read")
    def mark_read(body: ResourceRead):
        return artifacts.reads.mark(body)

    @routes.get("")
    def listing(
        q: str = Query(default="", max_length=200),
        kind: str | None = None,
        cursor: str | None = Query(default=None, max_length=16384),
        limit: int = Query(default=30, ge=1, le=100),
        sort: str = "modified",
        order: str = "desc",
        group: str = "all",
    ):
        try:
            return artifacts.list(q, kind, cursor, limit, sort=sort, order=order, group=group)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @routes.post("")
    def publish(body: Publication):
        try:
            return artifacts.publish(
                body.id,
                body.title,
                body.kind,
                body.content,
                content_base64=body.contentBase64,
                filename=body.filename,
                source=body.source.model_dump(exclude_none=True)
                if body.source
                else None,
            )
        except ArtifactConflict as error:
            raise HTTPException(409, str(error)) from error
        except ArtifactQuota as error:
            raise HTTPException(413, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @routes.delete("/{key}")
    def remove(key: str, body: DeleteResource):
        return delete_resource(artifacts, key, body)

    @routes.get("/{key}")
    def read(key: str):
        value = artifacts.get(key)
        return {
            **artifacts.metadata(value),
            **(
                {"content": value["content"]}
                if value["kind"] in {"markdown", "text"}
                else {}
            ),
        }

    @routes.get("/{key}/preview")
    def preview(key: str):
        value = artifacts.get(key)
        kind = value["kind"]
        if kind != "html" and kind not in IMAGE_KINDS:
            raise HTTPException(404, "This resource has no inline preview")
        return Response(
            resource_bytes(value),
            media_type=FORMATS[kind][0],
            headers={
                "Content-Security-Policy": PREVIEW_POLICY
                if kind == "html"
                else DOWNLOAD_POLICY,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @routes.get("/{key}/download")
    def download(key: str):
        value = artifacts.get(key)
        filename = metadata(value)["filename"]
        disposition = (
            f'attachment; filename="{filename}"'
            if filename.isascii() and '"' not in filename
            else "attachment; filename*=UTF-8''" + quote(filename, safe="")
        )
        return Response(
            resource_bytes(value),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": disposition,
                "Content-Security-Policy": DOWNLOAD_POLICY,
                "Cache-Control": "no-store",
            },
        )

    return routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--kind", choices=sorted(FORMATS), required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--filename")
    parser.add_argument("--source-surface")
    parser.add_argument("--source-thread")
    parser.add_argument("--source-turn")
    args = parser.parse_args()
    # Only the operator CLI accepts an explicitly selected local source file.
    with args.file.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        parser.error("Resource exceeds 10 MiB")
    try:
        result = Artifacts(Store(args.data_dir)).publish(
            args.id,
            args.title,
            args.kind,
            raw.decode("utf-8") if args.kind in TEXT_KINDS else None,
            content_base64=base64.b64encode(raw).decode("ascii")
            if args.kind not in TEXT_KINDS
            else None,
            filename=args.filename,
            source={
                k: v
                for k, v in {
                    "surface": args.source_surface,
                    "threadId": args.source_thread,
                    "turnId": args.source_turn,
                }.items()
                if v
            },
        )
    except (ValueError, UnicodeError) as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
