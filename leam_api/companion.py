"""Leam companion commands and runtime configuration, separate from Codex coding."""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from contextlib import aclosing
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .agenda import Agenda
from .attachments import AttachmentStore, descriptor
from .companion_context import (
    build_model_context,
    legacy_user_offset,
    project_display_history,
)
from .conversation_titles import (
    ConversationTitles,
    DeleteConversation,
    RenameConversation,
)
from .ironclaw import RuntimeError
from .item_chat import item_context
from .wellbeing import wellbeing_context
from .memory_projection import grounding
from .procedures import Choice, Procedures
from .runtime_approvals import router as runtime_approvals_router
from .system_inspection import Section, SystemInspector


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provider(Input):
    id: str = Field(min_length=1, max_length=100)
    adapter: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    baseUrl: str | None = Field(default=None, max_length=2000)
    apiKey: SecretStr | None = None
    activate: bool = False


class Selection(Input):
    providerId: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    reasoningEffort: str | None = Field(default=None, max_length=20)


class Message(Input):
    procedure: Choice | None = None
    text: str = Field(default="", max_length=100000)
    attachmentIds: list[str] = Field(default_factory=list, max_length=10)
    requestId: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def require_content(self):
        if not self.text.strip() and not self.attachmentIds:
            raise ValueError("Write a message or attach a file")
        return self


class StopRun(Input):
    requestId: UUID


class CreateThread(Input):
    requestId: str = Field(min_length=8, max_length=100)


class Memory(Input):
    text: str = Field(min_length=1, max_length=10000)
    source: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="preference", max_length=100)


class MemoryEdit(Memory):
    revision: int = Field(ge=1)


def router(store, runtime, codex, inspector=None, *, agenda=None, jobs=None):
    routes = APIRouter(prefix="/api")
    locks = defaultdict(asyncio.Lock)
    routes.include_router(runtime_approvals_router(store, runtime, locks))
    titles = ConversationTitles(store)
    attachments = AttachmentStore(store)
    agenda = agenda or Agenda(store, runtime)

    inspector = inspector or SystemInspector(store, runtime)

    async def system_snapshot():
        return await inspector.identity()

    @routes.get("/companion/system")
    async def system(section: Section = "model"):
        if section == "model":
            return await system_snapshot()
        return await inspector.inspect(section)

    async def model_catalog():
        models = []
        cursor = None
        for _ in range(10):
            result = await codex.request("model/list", {"limit": 100, "cursor": cursor})
            for model in result.get("data", []):
                if model.get("hidden") or not isinstance(model.get("model"), str):
                    continue
                models.append(model)
            cursor = result.get("nextCursor")
            if not cursor:
                return models
        raise HTTPException(502, "Model catalog exceeded its page limit")

    @routes.get("/settings/models")
    async def models():
        return {
            "models": await model_catalog(),
            "source": "Codex account",
            "default": {
                "providerId": "openai_codex",
                "model": "gpt-5.6-sol",
                "reasoningEffort": "medium",
            },
        }

    @routes.get("/settings/providers")
    async def providers():
        return await runtime.request("GET", "/llm/providers")

    @routes.post("/settings/providers")
    async def configure(body: Provider):
        data = {
            "id": body.id,
            "adapter": body.adapter,
            "default_model": body.model,
            "model": body.model,
            "set_active": body.activate,
        }
        if body.baseUrl:
            data["base_url"] = body.baseUrl
        if body.apiKey and body.apiKey.get_secret_value():
            data["api_key"] = body.apiKey.get_secret_value()
        await runtime.request("POST", "/llm/providers", data)
        return {"saved": True}

    @routes.post("/settings/providers/active")
    async def activate(body: Selection):
        data = {"provider_id": body.providerId, "model": body.model}
        if body.providerId == "openai_codex":
            available = await model_catalog()
            selected = next((m for m in available if m["model"] == body.model), None)
            if selected is None:
                raise HTTPException(
                    422, "This model is not available in your Codex account"
                )
            effort = (
                body.reasoningEffort
                or selected.get("defaultReasoningEffort")
                or "medium"
            )
            supported = {
                item.get("reasoningEffort")
                for item in selected.get("supportedReasoningEfforts", [])
            }
            if effort not in supported:
                raise HTTPException(
                    422, "This reasoning effort is not supported by the selected model"
                )
            data["reasoning_effort"] = effort
        elif body.reasoningEffort is not None:
            raise HTTPException(
                422,
                "Reasoning controls currently require the ChatGPT subscription provider",
            )
        result = await runtime.request("POST", "/llm/active", data)
        active = result.get("active") or {}
        if body.providerId == "openai_codex" and any(
            active.get(key) != value for key, value in data.items()
        ):
            raise HTTPException(
                502,
                "Runtime has not confirmed reasoning support; update the runtime before using this setting",
            )
        return {"saved": True}

    @routes.post("/settings/providers/test")
    async def probe(body: Provider):
        data = {"provider_id": body.id, "adapter": body.adapter, "model": body.model}
        if body.baseUrl:
            data["base_url"] = body.baseUrl
        if body.apiKey:
            data["api_key"] = body.apiKey.get_secret_value()
        response = await runtime.request("POST", "/llm/test-connection", data)
        return {
            "ok": bool(response.get("ok")),
            "message": "Connection succeeded"
            if response.get("ok")
            else "Connection failed. Check the model, endpoint and credentials.",
        }

    @routes.post("/settings/providers/codex-login")
    async def codex_login():
        return await runtime.request("POST", "/llm/codex/login", {})

    @routes.get("/companion/threads")
    async def threads(
        cursor: str | None = Query(default=None, max_length=4096),
        limit: int | None = Query(default=None, ge=1, le=100),
    ):
        query = {}
        if cursor:
            query["cursor"] = cursor
        if limit is not None:
            query["limit"] = limit
        result = await runtime.request(
            "GET", "/threads" + ("?" + urlencode(query) if query else "")
        )
        if jobs is not None:
            workers = jobs.worker_threads()
            result["threads"] = [item for item in result.get("threads", []) if item.get("thread_id") not in workers]
        items = result.get("threads", [])
        repair = [
            item
            for item in items
            if not item.get("title")
            or str(item["title"]).strip().lower().startswith("you are leam")
        ]
        subjects = {}
        ids = [item["thread_id"] for item in repair[:500]]
        if ids:
            # One bounded-result scan, selecting only the earliest immutable send receipt
            # per displayed thread. Never ask another model to summarize private context.
            with store.connect() as db:
                rows = db.execute(
                    "SELECT body,fingerprint FROM runtime_actions WHERE rowid IN ("
                    "SELECT MIN(rowid) FROM runtime_actions WHERE path GLOB '/channels/*/messages' "
                    "AND json_valid(body) AND json_extract(body,'$.thread_id') IN ("
                    + ",".join("?" for _ in ids)
                    + ") GROUP BY json_extract(body,'$.thread_id'))",
                    ids,
                ).fetchall()
            for row in rows:
                saved = json.loads(row["body"])
                content = saved.get("content", "")
                if not isinstance(content, str):
                    continue
                offset = legacy_user_offset(
                    saved, row["fingerprint"], saved["thread_id"]
                )
                if offset is not None:
                    content = content.encode()[offset:].decode()
                subject = " ".join(content.split())
                subjects[saved["thread_id"]] = subject[:80] + (
                    "…" if len(subject) > 80 else ""
                )
        for item in repair:
            item["title"] = subjects.get(item["thread_id"]) or "Conversation"
        titles.overlay(items)
        return result

    @routes.patch("/companion/threads/{thread_id}")
    async def rename_thread(thread_id: str, body: RenameConversation):
        async with locks[thread_id]:
            # Runtime read verifies caller scope without fetching full history.
            await runtime.request(
                "GET", "/threads/" + quote(thread_id, safe="") + "/timeline?limit=1"
            )
            return titles.rename(thread_id, body)

    @routes.delete("/companion/threads/{thread_id}")
    async def delete_thread(thread_id: str, body: DeleteConversation):
        if not body.confirmed:
            raise HTTPException(422, "Confirm deletion before removing a conversation")
        if jobs is not None and any(item["state"] not in {"completed", "failed", "cancelled"} for item in jobs.list(thread_id)["items"]):
            raise HTTPException(409, "Finish or cancel this conversation's background jobs before deleting it")
        async with locks[thread_id]:
            try:
                result = await runtime.request(
                    "DELETE", "/threads/" + quote(thread_id, safe="")
                )
            except RuntimeError as error:
                if error.status_code == 409:
                    raise HTTPException(
                        409,
                        "This conversation is still working. Stop it or wait before deleting.",
                    ) from error
                if error.status_code != 404:
                    raise
                result = {
                    "thread_id": thread_id,
                    "deleted": True,
                    "alreadyDeleted": True,
                }
            if result.get("deleted") is not True:
                raise HTTPException(
                    502,
                    "Runtime did not confirm deletion. Refresh the list before retrying.",
                )
            titles.forget(thread_id)
            agenda.retire_chat(thread_id)
            return result

    @routes.post("/companion/threads")
    async def create(body: CreateThread):
        return await runtime.request(
            "POST", "/threads", {"client_action_id": body.requestId}
        )

    @routes.get("/companion/threads/{thread_id}")
    async def timeline(
        thread_id: str,
        cursor: str | None = Query(default=None, max_length=4096),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        query = {"limit": limit}
        if cursor:
            query["cursor"] = cursor
        data = await runtime.request(
            "GET",
            "/threads/" + quote(thread_id, safe="") + "/timeline?" + urlencode(query),
        )
        # Join only acknowledged message IDs and private upload metadata. Do not
        # load stored upload bytes/base64 delivery payloads for history rendering.
        message_refs = {
            "msg:" + str(message["message_id"])
            for message in data.get("messages", [])
            if message.get("kind") == "user" and message.get("message_id")
        }
        linked = defaultdict(list)
        if message_refs:
            with store.connect() as db:
                placeholders = ",".join("?" for _ in message_refs)
                actions = db.execute(
                    "SELECT id, json_extract(result, '$.accepted_message_ref') AS ref "
                    "FROM runtime_actions WHERE result IS NOT NULL AND "
                    "json_extract(result, '$.accepted_message_ref') IN ("
                    + placeholders
                    + ")",
                    tuple(message_refs),
                ).fetchall()
                for action in actions:
                    rows = db.execute(
                        "SELECT a.id,a.filename,a.mime,a.size,a.sha256 FROM attachments a, "
                        "json_each(a.bindings) b WHERE b.value=?",
                        (action["id"],),
                    ).fetchall()
                    linked[action["ref"][4:]].extend(descriptor(row) for row in rows)
        for message in data.get("messages", []):
            message["leamAttachments"] = linked.get(message.get("message_id"), [])
        project_display_history(store, thread_id, data.get("messages", []))
        return data

    @routes.get("/companion/threads/{thread_id}/events")
    async def events(
        request: Request,
        thread_id: str,
        cursor: str | None = Query(default=None, max_length=4096),
    ):
        resume = request.headers.get("last-event-id") or cursor
        if resume and (
            len(resume) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in resume)
        ):
            raise HTTPException(422, "Invalid event cursor")

        async def frames():
            completed_runs = set()
            token = request.cookies.get("leam_session", "")
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            async with aclosing(
                runtime.events(
                    "/threads/" + quote(thread_id, safe="") + "/events", resume
                )
            ) as stream:
                async for frame in stream:
                    # Match app.session_valid on every frame, including keepalives.
                    with store.connect() as db:
                        session = db.execute(
                            "SELECT expires FROM sessions WHERE token_hash=?",
                            (token_hash,),
                        ).fetchone()
                    if (
                        not session
                        or session["expires"] <= time.time()
                        or await request.is_disconnected()
                    ):
                        return
                    reconciliation = getattr(agenda, "reconciliation", None)
                    if reconciliation is not None:
                        # Projection updates include older runs. Wake only for a new
                        # terminal run, never for every delta or SSE keepalive.
                        try:
                            payload = json.loads(
                                "\n".join(
                                    line[5:].lstrip()
                                    for line in frame.decode().splitlines()
                                    if line.startswith("data:")
                                )
                            )
                            state = payload.get("state") or {}
                            ended = set()
                            if state.get("thread_id") == thread_id:
                                for item in state.get("items", [])[:512]:
                                    status = item.get("run_status") or {}
                                    text = item.get("text") or {}
                                    if status.get("status", "").lower() in {
                                        "completed",
                                        "failed",
                                        "cancelled",
                                        "canceled",
                                        "interrupted",
                                    }:
                                        ended.add(status.get("run_id"))
                                    if text.get("finalized"):
                                        ended.add(text.get("run_id"))
                            ended = {run for run in ended if isinstance(run, str)}
                            if ended - completed_runs:
                                reconciliation.notify(thread_id)
                                completed_runs.update(ended)
                        except (ValueError, TypeError, AttributeError):
                            pass  # Periodic durable scans remain the recovery path.
                    yield frame

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @routes.post("/companion/threads/{thread_id}/runs/{run_id}/cancel")
    async def stop_run(thread_id: str, run_id: UUID, body: StopRun):
        # Runtime atomically checks the exact scoped run, actor and terminal state.
        # No racy "cancel whichever run is active" lookup, and no prompt resend.
        request_id, run = str(body.requestId), str(run_id)
        fingerprint = hashlib.sha256(
            json.dumps(["cancel", thread_id, run]).encode()
        ).hexdigest()
        payload = {
            "client_action_id": request_id,
            "thread_id": thread_id,
            "run_id": run,
            "reason": "user_requested",
        }
        async with locks[thread_id]:
            try:
                action = store.runtime_action(
                    request_id,
                    fingerprint,
                    "/threads/"
                    + quote(thread_id, safe="")
                    + "/runs/"
                    + run
                    + "/cancel",
                    payload,
                )
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            result = action["result"]
            if result is None:
                # Earlier releases persisted a free-text reason rejected before runtime
                # admission. Upgrade that still-unresolved receipt, retaining its UUID.
                if action["body"] != payload:
                    with store.connect() as db:
                        db.execute(
                            "UPDATE runtime_actions SET body=? WHERE id=? AND fingerprint=? AND result IS NULL",
                            (json.dumps(payload), request_id, fingerprint),
                        )
                result = await runtime.request("POST", action["path"], payload)
                if (
                    not isinstance(result, dict)
                    or result.get("run_id") != run
                    or result.get("status")
                    not in {
                        "CancelRequested",
                        "Cancelled",
                        "Completed",
                        "Failed",
                        "RecoveryRequired",
                    }
                    or type(result.get("already_terminal")) is not bool
                    or type(result.get("event_cursor")) is not int
                    or result["event_cursor"] < 0
                ):
                    raise HTTPException(
                        502,
                        "Runtime returned an invalid Stop receipt; refresh the conversation before retrying",
                    )
                result = {
                    key: result[key]
                    for key in ("run_id", "status", "event_cursor", "already_terminal")
                }
                store.finish_runtime_action(request_id, result)
            if result["already_terminal"]:
                raise HTTPException(
                    409,
                    "This response has already ended. No running response was stopped.",
                )
            return result

    @routes.get("/companion/threads/{thread_id}/submissions/{request_id}")
    async def submission_receipt(thread_id: str, request_id: str):
        if len(request_id) > 200:
            raise HTTPException(422, "Invalid request identity")
        with store.connect() as db:
            row = db.execute(
                "SELECT body,result FROM runtime_actions WHERE id=? AND path GLOB '/channels/*/messages'",
                (request_id,),
            ).fetchone()
        if row is None or json.loads(row["body"]).get("thread_id") != thread_id:
            raise HTTPException(404, "Saved message receipt was not found")
        result = json.loads(row["result"]) if row["result"] else None
        # Read-only reconciliation: never submit, replay or expose stored prompt/context.
        allowed = {
            "outcome",
            "thread_id",
            "run_id",
            "active_run_id",
            "accepted_message_ref",
            "status",
            "event_cursor",
        }
        return {
            "requestId": request_id,
            "state": "recorded" if result is not None else "pending",
            "receipt": (
                {key: value for key, value in result.items() if key in allowed}
                if isinstance(result, dict)
                else None
            ),
        }

    @routes.post("/companion/threads/{thread_id}/messages")
    async def send(thread_id: str, body: Message):
        try:
            return await send_message(thread_id, body)
        except (RuntimeError, HTTPException) as error:
            # A rejected preflight has never reached durable dispatch. Once a
            # reservation exists, keep uncertainty even for upstream HTTP errors.
            with store.connect() as db:
                reserved = (
                    db.execute(
                        "SELECT 1 FROM runtime_actions WHERE id=?", (body.requestId,)
                    ).fetchone()
                    is not None
                )
            status = error.status_code if isinstance(error, HTTPException) else 502
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            logging.getLogger(__name__).warning(
                "Companion submission failed: reserved=%s upstream_status=%s",
                reserved,
                getattr(error, "status_code", None),
            )
            headers = dict(getattr(error, "headers", None) or {})
            if not reserved:
                headers["X-Leam-Action-Reserved"] = "no"
            return JSONResponse({"detail": detail}, status_code=status, headers=headers)

    async def send_message(thread_id: str, body: Message):
        try:
            files = attachments.resolve(body.attachmentIds)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        # Preserve pre-attachment request fingerprints for uncertain legacy sends.
        identity = [thread_id, body.text]
        if files:
            identity.append([[item["id"], item["sha256"]] for item in files])
        if body.procedure is not None:
            identity.append({"procedure": body.procedure.model_dump()})
        fingerprint = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        async with locks[thread_id]:
            reconciliation = getattr(agenda, "reconciliation", None)
            if reconciliation is not None:
                await reconciliation.watch(thread_id)
            try:
                previous = store.existing_runtime_action(body.requestId, fingerprint)
            except ValueError as error:
                raise HTTPException(409, str(error))
            if previous:
                if previous["result"] is not None:
                    return previous["result"]
                result = await runtime.request(
                    "POST", previous["path"], previous["body"]
                )
                store.finish_runtime_action(body.requestId, result)
                if reconciliation is not None:
                    reconciliation.notify(thread_id)
                return result
            session, system = await asyncio.gather(
                runtime.request("GET", "/session"), system_snapshot()
            )
            if reconciliation is not None and store.get(
                "companion-agenda:" + thread_id
            ):
                await reconciliation.owner(session)
            with store.connect() as db:
                memories = [
                    {
                        **json.loads(row["body"]),
                        "id": row["id"],
                        "revision": row["revision"],
                        "dataAsOf": row["updated"],
                    }
                    for row in db.execute(
                        "SELECT * FROM entities WHERE kind='memory' ORDER BY updated DESC, id"
                    )
                ]
            commitments = [
                c for c in store.entities("commitment") if c.get("status") == "active"
            ][:30]
            context = {
                "threadId": thread_id,
                "system": system,
                "memoryContext": grounding(memories, body.text),
                "commitments": [
                    {"id": c["id"], "title": c["title"], "status": c["status"]}
                    for c in commitments
                ],
            }
            wellbeing = wellbeing_context(store, thread_id)
            if wellbeing:
                context["wellbeing"] = wellbeing
            linked_item = item_context(store, thread_id)
            if linked_item:
                context["linkedItem"] = linked_item
            daily_agenda = agenda.reference(thread_id)
            if daily_agenda:
                context["dailyAgenda"] = daily_agenda
            if body.procedure is not None:
                context["selectedProcedure"] = Procedures(store).selected(
                    body.procedure, daily_agenda
                )
            # Grounding is explicitly scoped as data. Codex messages never use this envelope.
            model_context = build_model_context(store, thread_id, context)
            path = (
                "/channels/"
                + quote(session["session_channel_extension_id"], safe="")
                + "/messages"
            )
            payload = {
                "thread_id": thread_id,
                "content": body.text,
                "model_context": model_context,
                "client_action_id": body.requestId,
            }
            try:
                if files:
                    # Retain before dispatch, including uncertain outcomes. Bound IDs
                    # cannot disappear while the durable request is being written.
                    attachments.bind(body.attachmentIds, body.requestId, thread_id=thread_id, surface="companion")
                    payload["attachments"] = attachments.inline_parts(
                        body.attachmentIds
                    )
                action = store.runtime_action(
                    body.requestId, fingerprint, path, payload
                )
            except ValueError as error:
                raise HTTPException(409, str(error))
            if action["result"] is not None:
                return action["result"]
            result = await runtime.request("POST", action["path"], action["body"])
            store.finish_runtime_action(body.requestId, result)
            if reconciliation is not None:
                reconciliation.notify(thread_id)
            return result

    @routes.get("/memory")
    async def memory():
        return {"items": store.entities("memory")}

    @routes.post("/memory")
    async def remember(body: Memory):
        return store.create("memory", body.model_dump())

    @routes.patch("/memory/{item_id}")
    async def edit(item_id: str, body: MemoryEdit):
        try:
            return store.update(
                item_id, "memory", body.revision, body.model_dump(exclude={"revision"})
            )
        except KeyError:
            raise HTTPException(404, "Memory not found")
        except ValueError as error:
            raise HTTPException(409, str(error))

    @routes.delete("/memory/{item_id}")
    async def forget(item_id: str, revision: int):
        try:
            store.delete(item_id, "memory", revision)
        except KeyError:
            raise HTTPException(404, "Memory not found")
        except ValueError as error:
            raise HTTPException(409, str(error))
        return {"removed": True}

    return routes
