"""Bounded generated output publication; no paths, fetching or approval authority."""

from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from .artifacts import ArtifactConflict, ArtifactQuota, Artifacts
from .commitments import Input
from .inbox_events import resource_event


class ResourceQuery(Input):
    id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,95}$")
    query: str = Field(default="", max_length=200)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class ResourceSave(Input):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,95}$")
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["markdown", "text", "html"]
    content: str = Field(min_length=1, max_length=262144)
    filename: str | None = Field(default=None, min_length=1, max_length=200)
    threadId: str | None = Field(default=None, min_length=1, max_length=128)
    turnId: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_content(self):
        if len(self.content.encode("utf-8")) > 262144:
            raise ValueError("Generated resource exceeds 256 KiB")
        return self


class ResourceTools:
    def __init__(self, store):
        self.artifacts = Artifacts(store)

    def read(self, body):
        if body.id is not None:
            if body.query or body.cursor:
                raise HTTPException(
                    422, "Resource detail cannot include a list query or cursor"
                )
            return {
                "item": self.artifacts.metadata(self.artifacts.get(body.id)),
                "contentIncluded": False,
            }
        try:
            page = self.artifacts.list(
                q=body.query, cursor=body.cursor, limit=body.limit
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {**page, "contentIncluded": False}

    def save(self, body):
        # Runtime mediates tool approvals; provenance is descriptive, not authority.
        source = {"surface": "companion"}
        for key in ("threadId", "turnId"):
            value = getattr(body, key)
            if value is not None:
                source[key] = value
        try:
            item = self.artifacts.publish(
                body.id,
                body.title,
                body.kind,
                body.content,
                filename=body.filename,
                source=source,
                record=resource_event,
            )
        except ArtifactConflict as error:
            raise HTTPException(409, str(error)) from None
        except ArtifactQuota as error:
            raise HTTPException(413, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {
            "state": "saved",
            "item": item,
            "url": item["url"],
            "visibility": "private_authenticated",
            "sourceReferencesVerified": False,
        }
