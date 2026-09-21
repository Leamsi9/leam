"""Narrow companion tools: sourced reads and policy-controlled domain proposals."""

import hmac
import json
import os
import secrets
import tempfile
import time
from datetime import date as CalendarDate
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, ValidationError

from . import memory_policy, memory_projection
from .commitments import Input
from .context_reader import ContextKind, ContextReader
from .proposals import INPUTS, Operation, Propose
from .system_inspection import SystemQuery


class Page(Input):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class ContextQuery(Page):
    query: str = Field(default="", max_length=500)
    kind: ContextKind = "all"
    id: str | None = Field(default=None, min_length=1, max_length=100)
    revision: int | None = Field(default=None, ge=1)
    cursor: int = Field(default=0, ge=0)


class TodayQuery(Page):
    date: CalendarDate | None = None


class CalendarQuery(Page):
    id: str = Field(min_length=1, max_length=100)


class CalendarLinksQuery(Page):
    calendarId: str | None = Field(default=None, min_length=1, max_length=100)


class CalendarInspectQuery(Input):
    creationId: UUID


class ProposalQuery(Page):
    threadId: str = Field(min_length=1, max_length=200)


class SchemaQuery(Input):
    operation: Operation


class ToolCall(Input):
    tool: Literal[
        "leam_context",
        "leam_system",
        "leam_today",
        "leam_calendar_events",
        "leam_calendar_links",
        "leam_calendar_inspect",
        "leam_propose",
        "leam_proposals",
        "leam_operation_schema",
    ]
    arguments: dict = Field(default_factory=dict)


def service_token(directory):
    """Publish once without exposing a partial key or replacing another process's key."""
    path = directory / "tools-token"
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix=".tools-token-", dir=directory)
        try:
            with os.fdopen(fd, "w") as file:
                file.write(secrets.token_urlsafe(48))
                file.flush()
                os.fsync(file.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.unlink(temporary)
    token = path.read_text().strip()
    if len(token) < 40 or path.stat().st_mode & 0o077:
        raise ValueError("Tool credential must be a private installation token")
    return token


class DomainTools:
    def __init__(self, proposals, directory, inspector=None):
        self.inspector = inspector
        self.proposals = proposals
        self.store = proposals.store
        self.calendars = proposals.calendar_actions.calendars
        self.token = service_token(directory)
        self.context_reader = ContextReader(self.store, self.calendars)

    def authorized(self, header):
        return hmac.compare_digest(header.encode(), ("Bearer " + self.token).encode())

    def context(self, body):
        return self.context_reader.query(body)

    async def call(self, body):
        schema = {
            "leam_context": ContextQuery,
            "leam_system": SystemQuery,
            "leam_today": TodayQuery,
            "leam_calendar_events": CalendarQuery,
            "leam_calendar_links": CalendarLinksQuery,
            "leam_calendar_inspect": CalendarInspectQuery,
            "leam_propose": Propose,
            "leam_proposals": ProposalQuery,
            "leam_operation_schema": SchemaQuery,
        }[body.tool]
        try:
            arguments = schema(**body.arguments)
        except ValidationError as error:
            raise HTTPException(
                422,
                [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                    for e in error.errors()
                ],
            ) from None
        if body.tool == "leam_system":
            if self.inspector is None:
                raise HTTPException(503, "System inspection is unavailable")
            return await self.inspector.inspect(arguments.section)
        if body.tool == "leam_context":
            if arguments.id is not None:
                return memory_projection.detail(self.store, arguments)
            if arguments.cursor or arguments.revision is not None:
                raise HTTPException(422, "Detail cursor/revision requires id")
            return memory_projection.page(
                self.context(arguments), arguments.query, arguments.offset
            )
        if body.tool == "leam_today":
            data = self.proposals.commitments.today(arguments.date)
            items = data["items"]
            return {
                **data,
                "items": items[arguments.offset : arguments.offset + arguments.limit],
                "nextOffset": (
                    arguments.offset + arguments.limit
                    if len(items) > arguments.offset + arguments.limit
                    else None
                ),
                "source": "leam:today",
                "observedAt": time.time(),
            }
        if body.tool == "leam_calendar_events":
            data = self.calendars.snapshot(arguments.id)
            events = data["events"]
            return {
                **data,
                "events": events[arguments.offset : arguments.offset + arguments.limit],
                "nextOffset": (
                    arguments.offset + arguments.limit
                    if len(events) > arguments.offset + arguments.limit
                    else None
                ),
                "source": "leam:calendar/" + arguments.id,
            }
        if body.tool == "leam_calendar_inspect":
            data = await self.proposals.calendar_changes.inspect(
                str(arguments.creationId)
            )
            return {
                **data,
                "source": "leam:calendar-action/" + str(arguments.creationId),
            }
        if body.tool == "leam_calendar_links":
            data = self.proposals.calendar_actions.list(
                arguments.calendarId, arguments.offset
            )
            more = (
                len(data["items"]) > arguments.limit or data["nextOffset"] is not None
            )
            return {
                "items": data["items"][: arguments.limit],
                "nextOffset": arguments.offset + arguments.limit if more else None,
                "source": "leam:calendar-actions",
                "liveProviderState": False,
            }
        if body.tool == "leam_propose":
            return await self.proposals.propose(arguments)
        if body.tool == "leam_operation_schema":
            with self.store.connect() as db:
                approval = memory_policy.decision(db, arguments.operation)
            return {
                "operation": arguments.operation,
                "inputSchema": INPUTS[arguments.operation].model_json_schema(),
                "approval": "Automatic under user memory settings"
                if approval["mode"] == "automatic"
                else "Explicit browser approval required",
                "approvalPolicy": approval,
            }
        data = self.proposals.list(arguments.threadId, arguments.offset)
        more = len(data["items"]) > arguments.limit or data["nextOffset"] is not None
        return {
            "items": data["items"][: arguments.limit],
            "nextOffset": arguments.offset + arguments.limit if more else None,
        }


def router(domain):
    routes = APIRouter()

    @routes.post("/api/internal/tools")
    async def call(body: ToolCall):
        result = await domain.call(body)
        if len(json.dumps(result).encode()) > 512 * 1024:
            raise HTTPException(413, "Result too large; request a smaller page")
        return result

    @routes.get("/api/companion/context")
    async def context(
        query: str = Query("", max_length=500),
        kind: ContextKind = "all",
        limit: int = Query(20, ge=1, le=50),
        offset: int = Query(0, ge=0),
    ):
        result = domain.context(
            ContextQuery(query=query, kind=kind, limit=limit, offset=offset)
        )
        if len(json.dumps(result).encode()) > 512 * 1024:
            raise HTTPException(413, "Result too large; request a smaller page")
        return result

    @routes.get("/api/companion/overview")
    async def overview():
        result = domain.context(ContextQuery(limit=1))
        return {
            key: result[key]
            for key in ("observedAt", "modules", "project", "truncated")
        }

    return routes
