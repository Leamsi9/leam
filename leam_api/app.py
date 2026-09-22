import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .accounts import Accounts
from .accounts import router as accounts_router
from .agenda import Agenda
from .agenda import router as agenda_router
from .agenda_priorities import router as agenda_priorities_router
from .artifacts import PREVIEW_POLICY, Artifacts
from .artifacts import router as artifacts_router
from .attachments import AttachmentStore
from .attachments import router as attachments_router
from .background_jobs import BackgroundJobs
from .background_jobs import router as background_jobs_router
from .backlog import Backlog
from .backlog import router as backlog_router
from .backups import Backups
from .backups import router as backups_router
from .calendar import Calendars
from .calendar import router as calendar_router
from .calendar_actions import CalendarActions
from .calendar_actions import router as calendar_actions_router
from .calendar_changes import CalendarChanges
from .calendar_changes import router as calendar_changes_router
from .codex import CodexClient, CodexError, CodexGenerationError
from .coding_catalog import include_handoffs, purpose_metadata, visible_threads
from .coding_handoff import CodingHandoffs
from .coding_handoff import router as coding_handoff_router
from .coding_models import CodingModels
from .coding_models import router as coding_models_router
from .coding_policy import CodingPolicyError, coding_context
from .commitments import router as commitments_router
from .companion import router as companion_router
from .conversation_titles import DeleteCodingConversation, RenameConversation
from .document_tools import router as document_tools_router
from .domain_tools import DomainTools
from .domain_tools import router as domain_tools_router
from .email import Emails
from .email import router as email_router
from .email_drafts import EmailDrafts
from .email_drafts import router as email_drafts_router
from .inbox import Inbox
from .inbox import router as inbox_router
from .inbox_events import InboxEvents
from .inbox_mail import router as inbox_mail_router
from .email_tasks import router as email_tasks_router
from .inbox_removal import router as inbox_removal_router
from .ironclaw import IronClaw, RuntimeError
from .wellbeing import router as wellbeing_router
from .item_chat import ItemChats
from .item_chat import router as item_chat_router
from .local_voice import LocalVoice
from .local_voice import router as local_voice_router
from .mail_read import MailReader
from .mail_read import router as mail_read_router
from .main_coding import MainCoding
from .main_coding import router as main_coding_router
from .maintenance import MaintenanceHeld, installed_maintenance
from .permission_profiles import PermissionProfiles
from .permission_profiles import router as permission_profiles_router
from .procedures import router as procedures_router
from .proposals import Proposals
from .proposals import router as proposals_router
from .push import Push
from .push import router as push_router
from .remember import router as remember_router
from .reminders import Scheduler
from .reminders import router as reminders_router
from .resource_links import ResourceLinks
from .resource_links import router as resource_links_router
from .restore_automation import RestoreAutomation
from .restore_automation import router as restore_automation_router
from .routine_events import EventInputs
from .routine_events import router as event_inputs_router
from .routines import Routines
from .routines import router as routines_router
from .shared_coding import SharedCoding
from .shared_controls import router as shared_controls_router
from .shared_decisions import router as shared_decisions_router
from .store import Store
from .system_inspection import SystemInspector
from .thread_models import router as thread_models_router
from .ticket_auto_handoff import TOOL as TICKET_HANDOFF_TOOL
from .ticket_auto_handoff import TicketAutoHandoff
from .ticket_chat import TicketChats
from .ticket_chat import router as ticket_chat_router
from .today_reconciliation import TodayReconciliation
from .today_reconciliation import router as reconciliation_router
from .tool_permissions import ToolPermissions
from .tool_permissions import router as tool_permissions_router
from .tool_scope import RuntimeScopeGuard, configured_credential
from .updates import Updates
from .updates import router as updates_router


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Setup(StrictModel):
    bootstrap: str
    password: str = Field(min_length=14, max_length=512)


class Login(StrictModel):
    password: str = Field(max_length=512)


class Connect(StrictModel):
    handoffConfirmed: bool = False


class Turn(StrictModel):
    attachmentIds: list[str] = Field(default_factory=list, max_length=10)
    generation: str | None = Field(default=None, max_length=128)
    text: str = Field(min_length=0, max_length=100000)
    requestId: str = Field(min_length=8, max_length=100)
    expectedTurnId: str | None = Field(default=None, min_length=1, max_length=128)


class NewThread(StrictModel):
    cwd: str = Field(min_length=1, max_length=4096)


class Reconcile(StrictModel):
    attachmentIds: list[str] = Field(default_factory=list, max_length=10)
    text: str = Field(min_length=0, max_length=100000)
    expectedTurnId: str | None = Field(default=None, min_length=1, max_length=128)


def create_app(
    directory: Path,
    origins: set[str],
    bootstrap=None,
    codex=None,
    runtime=None,
    scheduler_clock=None,
    push_transport=None,
    account_transport=None,
    shared_coding=None,
    maintenance=None,
    local_voice=None,
    usage_home=None,
    runtime_scope_credential=None,
):
    store = Store(directory)
    from .usage import UsageService
    from .usage import router as usage_router

    usage = UsageService(store, usage_home)
    maintenance = maintenance or installed_maintenance(directory)
    store.maintenance = maintenance
    voice = local_voice or LocalVoice()
    inbox_events = InboxEvents(store)
    scheduler = Scheduler(store, scheduler_clock)
    routines = Routines(store, scheduler_clock)
    push = Push(store, scheduler_clock, push_transport)
    accounts = Accounts(store, origins, account_transport, scheduler_clock)
    hasher = PasswordHasher()
    bootstrap_path = directory / "bootstrap-token"
    if bootstrap is None and store.get("password_hash") is None:
        if not bootstrap_path.exists():
            fd = os.open(bootstrap_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                file.write(secrets.token_urlsafe(32))
        bootstrap = bootstrap_path.read_text().strip()
    bridge = codex or CodexClient(store.event)
    runtime = runtime or IronClaw(
        os.environ.get("LEAM_RUNTIME_URL", "http://127.0.0.1:46410"),
        Path(
            os.environ.get(
                "LEAM_RUNTIME_TOKEN_FILE",
                str(Path.home() / ".local/share/leam-next/ironclaw/webui-token"),
            )
        ),
    )
    usage.runtime = runtime
    background_jobs = BackgroundJobs(store, runtime, accounts.vault)
    shared = shared_coding or SharedCoding(store)
    inspector = SystemInspector(
        store,
        runtime,
        scheduler=scheduler,
        routines=routines,
        codex=bridge,
        shared=shared,
    )
    coding_models = CodingModels(store, bridge)
    bindings = {}
    locks = defaultdict(asyncio.Lock)
    attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        inbox_worker = asyncio.create_task(inbox_events.run())
        reconciliation_worker = asyncio.create_task(reconciliation.run())
        usage_worker = asyncio.create_task(usage.run())
        worker = asyncio.create_task(scheduler.run())
        push_worker = asyncio.create_task(push.run())
        routine_worker = asyncio.create_task(routines.run())
        background_worker = asyncio.create_task(background_jobs.run())
        ticket_handoff_worker = asyncio.create_task(ticket_auto_handoff.run())
        try:
            yield
        finally:
            inbox_worker.cancel()
            reconciliation_worker.cancel()
            usage_worker.cancel()
            worker.cancel()
            push_worker.cancel()
            routine_worker.cancel()
            background_worker.cancel()
            ticket_handoff_worker.cancel()
            await asyncio.gather(
                inbox_worker,
                reconciliation_worker,
                usage_worker,
                worker,
                push_worker,
                routine_worker,
                background_worker,
                ticket_handoff_worker,
                return_exceptions=True,
            )
            await emails.classifier.close()
            results = await asyncio.gather(
                bridge.close(),
                runtime.close(),
                push.close(),
                shared.close(),
                voice.close(),
                return_exceptions=True,
            )
            for name, result in zip(
                ("Codex", "IronClaw", "Push", "Shared Codex", "Local voice"), results
            ):
                if isinstance(result, BaseException):
                    logging.getLogger(__name__).error(
                        "%s cleanup failed: %s", name, type(result).__name__
                    )

    app = FastAPI(
        title="Leam", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.include_router(procedures_router(store))
    app.include_router(artifacts_router(Artifacts(store)))
    app.include_router(resource_links_router(ResourceLinks(store)))
    app.include_router(inbox_router(Inbox(store)))
    app.state.store = store
    app.state.usage = usage
    app.include_router(usage_router(usage))
    attachments = AttachmentStore(store)
    app.include_router(attachments_router(attachments))
    app.state.updates = Updates(store)
    app.include_router(shared_controls_router(shared))
    app.include_router(shared_decisions_router(shared))
    app.include_router(updates_router(app.state.updates))
    ticket_chats = TicketChats(store, bridge, bindings, shared)
    app.include_router(ticket_chat_router(ticket_chats))
    main_coding = MainCoding(store, bridge, shared, bindings, ticket_chats)
    ticket_auto_handoff = TicketAutoHandoff(store, bridge, main_coding, ticket_chats, accounts.vault)
    app.state.ticket_auto_handoff = ticket_auto_handoff
    if hasattr(bridge, "dynamic_handlers"):
        bridge.dynamic_handlers[TICKET_HANDOFF_TOOL] = ticket_auto_handoff.invoke
    app.state.main_coding = main_coding
    app.include_router(main_coding_router(main_coding))
    app.include_router(backlog_router(Backlog(store)))
    app.state.shared_coding = shared
    app.state.codex = bridge
    permissions = ToolPermissions(runtime)
    app.include_router(
        permission_profiles_router(PermissionProfiles(store, permissions, inspector))
    )
    app.include_router(tool_permissions_router(permissions))
    app.include_router(document_tools_router(runtime))
    email_drafts = EmailDrafts(store, accounts.vault)
    emails = Emails(store, accounts, runtime)
    app.include_router(inbox_mail_router(store, emails))
    app.include_router(inbox_removal_router(store, emails))
    mail_reader = MailReader(emails)
    app.state.mail_reader = mail_reader
    app.include_router(mail_read_router(mail_reader))
    agenda = Agenda(store, runtime, emails=emails)
    app.state.agenda = agenda
    app.include_router(
        companion_router(store, runtime, bridge, inspector, agenda=agenda, jobs=background_jobs)
    )
    app.include_router(commitments_router(store))
    app.include_router(item_chat_router(ItemChats(store, runtime)))
    app.include_router(agenda_router(agenda))
    app.include_router(agenda_priorities_router(agenda))
    app.include_router(remember_router(store))
    app.include_router(reminders_router(scheduler))
    app.state.scheduler = scheduler
    app.state.routines = routines
    app.include_router(wellbeing_router(store, runtime))
    app.include_router(routines_router(routines))
    app.include_router(event_inputs_router(EventInputs(store, scheduler_clock)))
    app.include_router(coding_models_router(coding_models))
    app.include_router(thread_models_router(coding_models, shared, bindings, locks))
    app.state.push = push
    app.include_router(push_router(push))
    app.state.accounts = accounts
    app.include_router(accounts_router(accounts))
    app.state.email_drafts = email_drafts
    app.include_router(email_drafts_router(email_drafts))
    app.state.emails = emails
    app.include_router(email_router(emails))
    calendars = Calendars(store, accounts)
    calendar_actions = CalendarActions(calendars)
    app.include_router(calendar_actions_router(calendar_actions))
    calendar_changes = CalendarChanges(calendar_actions)
    app.include_router(calendar_changes_router(calendar_changes))
    proposals = Proposals(store, calendar_actions, calendar_changes, agenda=agenda)
    reconciliation = TodayReconciliation(store, runtime, proposals)
    proposals.on_content_deleted = reconciliation.forget_proposal_content
    proposals.purge_declined()  # Authorized content removal; deployment backs up first.
    agenda.reconciliation = reconciliation
    app.state.reconciliation = reconciliation
    app.include_router(reconciliation_router(reconciliation))
    handoffs = CodingHandoffs(store, bridge, bindings, shared, proposals)
    app.include_router(coding_handoff_router(handoffs))
    app.state.coding_handoffs = handoffs
    handoffs.main = main_coding
    if codex is None:
        bridge.on_event = handoffs.record_event
    app.state.proposals = proposals
    app.include_router(proposals_router(proposals))
    app.include_router(email_tasks_router(store, emails, proposals))
    scope_guard = (RuntimeScopeGuard(store, runtime, runtime_scope_credential)
                   if runtime_scope_credential is not None else None)
    app.state.background_jobs = background_jobs
    app.include_router(background_jobs_router(background_jobs))
    domain_tools = DomainTools(proposals, directory, inspector, mail_reader=mail_reader,
                               scope_guard=scope_guard, jobs=background_jobs)
    app.include_router(domain_tools_router(domain_tools))
    app.include_router(backups_router(Backups(store)))
    app.include_router(
        restore_automation_router(RestoreAutomation(store, scheduler_clock))
    )
    app.include_router(calendar_router(calendars))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Pydantic's default `input` field can contain passwords and API tokens.
        detail = [
            {k: e[k] for k in ("loc", "msg", "type") if k in e} for e in error.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    def session_valid(token):
        if not token:
            return False
        with store.connect() as db:
            row = db.execute(
                "SELECT expires FROM sessions WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        return bool(row and row["expires"] > time.time())

    app.include_router(local_voice_router(voice, session_valid))

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.path.startswith("/api/"):
            control_probe = request.url.path == "/api/internal/maintenance"
            internal = request.url.path == "/api/internal/tools" or control_probe
            if internal:
                if request.headers.get("origin"):
                    return JSONResponse(
                        {"detail": "Browser origins cannot use the tool credential"},
                        status_code=403,
                    )
                authority = maintenance if control_probe else domain_tools
                if authority is None or not authority.authorized(
                    request.headers.get("authorization", "")
                ):
                    return JSONResponse(
                        {"detail": "Tool authentication required"}, status_code=401
                    )
                if not request.client or request.client.host not in (
                    "127.0.0.1",
                    "::1",
                ):
                    return JSONResponse(
                        {"detail": "Tool ingress is loopback only"}, status_code=403
                    )
            upload = request.method == "POST" and request.url.path == "/api/attachments"
            resource_upload = request.method == "POST" and request.url.path == "/api/artifacts"
            if (upload or resource_upload) and not session_valid(request.cookies.get("leam_session")):
                return JSONResponse({"detail": "Sign in to Leam"}, status_code=401)
            # Resource JSON includes base64 overhead; only this authenticated route
            # receives the larger envelope. The domain enforces decoded file quotas.
            body_limit = (15 if resource_upload else 10 if upload else 1) * 1024 * 1024
            if request.method not in ["GET", "HEAD", "OPTIONS"]:
                if not internal and request.headers.get("origin") not in origins:
                    return JSONResponse(
                        {"detail": "Untrusted or missing Origin"}, status_code=403
                    )
                if (
                    request.headers.get("content-length", "0").isdigit()
                    and int(request.headers.get("content-length", "0")) > body_limit
                ):
                    return JSONResponse(
                        {"detail": "Request too large"}, status_code=413
                    )
                chunks = []
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > body_limit:
                        return JSONResponse(
                            {"detail": "Request too large"}, status_code=413
                        )
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            public = request.url.path in [
                "/api/health",
                "/api/auth/status",
                "/api/auth/setup",
                "/api/auth/login",
                "/api/accounts/oauth/google/callback",
                "/api/accounts/oauth/microsoft/callback",
            ]
            if (
                not internal
                and not public
                and not session_valid(request.cookies.get("leam_session"))
            ):
                return JSONResponse({"detail": "Sign in to Leam"}, status_code=401)
        admission = nullcontext()
        if (
            maintenance is not None
            and request.url.path.startswith("/api/")
            and request.url.path
            not in {"/api/internal/maintenance", "/api/health", "/api/auth/status"}
        ):
            admission = maintenance.admit(
                existing_companion=request.url.path == "/api/internal/tools"
            )
        try:
            with admission:
                response = await call_next(request)
        except MaintenanceHeld as error:
            response = JSONResponse(
                {"detail": str(error), "maintenance": True, "accepted": False},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers["X-Frame-Options"] = "DENY"
        # Only the exact artifact preview route may opt into its isolated frame.
        artifact_preview = (
            request.url.path.startswith("/api/artifacts/")
            and request.url.path.endswith("/preview")
            and response.headers.get("Content-Security-Policy") == PREVIEW_POLICY
        )
        if artifact_preview:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        # Preserve only the known download and isolated artifact policies.
        if (
            response.headers.get("Content-Security-Policy")
            != "default-src 'none'; sandbox"
            and not artifact_preview
        ):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path in ("/", "/index.html", "/sw.js"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/internal/maintenance")
    async def maintenance_probe():
        state = maintenance.status()
        if not state["held"]:
            return {
                "idle": False,
                "reason": "Maintenance has not been requested",
                "maintenance": state,
            }
        if not maintenance.drained():
            return {
                "idle": False,
                "reason": "Accepted product work is still draining",
                "maintenance": state,
            }
        native = await bridge.drain_status()
        return {
            "idle": native["idle"] and maintenance.drained(),
            "native": native,
            "maintenance": maintenance.status(),
        }

    @app.exception_handler(MaintenanceHeld)
    async def maintenance_error(request, error):
        return JSONResponse(
            {"detail": str(error), "maintenance": True},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(RuntimeError)
    async def runtime_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=502)

    @app.exception_handler(CodexError)
    async def codex_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=502)

    def limit_login(request):
        key = request.client.host if request.client else "local"
        queue = attempts[key]
        now = time.monotonic()
        while queue and now - queue[0] > 300:
            queue.popleft()
        if len(queue) >= 10:
            raise HTTPException(
                429, "Too many sign-in attempts. Try again in five minutes."
            )
        queue.append(now)

    def session_response(request: Request):
        token = secrets.token_urlsafe(32)
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES (?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), time.time() + 86400 * 14),
            )
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "leam_session",
            token,
            httponly=True,
            secure=request.headers.get("origin", "").startswith("https://"),
            samesite="strict",
            max_age=86400 * 14,
        )
        return response

    @app.get("/api/health")
    async def health():
        return {
            "status": "operational",
            "version": "0.1.0",
            "scope": "leam-api",
            "productAccepted": False,
        }

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        return {
            "configured": store.get("password_hash") is not None,
            "authenticated": session_valid(request.cookies.get("leam_session")),
        }

    @app.post("/api/auth/setup")
    async def setup(body: Setup, request: Request):
        limit_login(request)
        if store.get("password_hash") is not None:
            raise HTTPException(409, "Leam is already configured")
        if not bootstrap or not hmac.compare_digest(body.bootstrap, bootstrap):
            raise HTTPException(403, "Invalid pairing code")
        password_hash = await asyncio.to_thread(hasher.hash, body.password)
        try:
            with store.connect() as db:
                db.execute(
                    "INSERT INTO settings VALUES (?,?)",
                    ("password_hash", json.dumps(password_hash)),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Leam is already configured")
        bootstrap_path.unlink(missing_ok=True)
        return session_response(request)

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request):
        limit_login(request)
        saved = store.get("password_hash")
        if not saved:
            raise HTTPException(401, "Set up Leam first")
        try:
            await asyncio.to_thread(hasher.verify, saved, body.password)
        except VerificationError:
            raise HTTPException(401, "Incorrect password")
        return session_response(request)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get("leam_session", "")
        with store.connect() as db:
            db.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("leam_session")
        return response

    @app.get("/api/codex/shared-thread")
    async def shared_thread_metadata():
        # The IDE-owned session remains discoverable even while the independent
        # native App Server catalog is slow or unavailable. No IPC starts here.
        thread = shared.listing()
        if thread:
            thread = purpose_metadata(store, {"data": [thread]})["data"][0]
        return {"configured": thread is not None, "thread": thread}

    @app.get("/api/codex/threads")
    async def threads(
        cursor: str | None = Query(default=None, max_length=4096),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        params = {
            "limit": limit,
            "sourceKinds": ["cli", "vscode", "appServer"],
            "sortKey": "recency_at",
            "sortDirection": "desc",
            "archived": False,
        }
        if cursor:
            params["cursor"] = cursor
        result = visible_threads(await bridge.request("thread/list", params))
        filtered = result["leamFilteredCount"]
        result = visible_threads(
            await include_handoffs(store, bridge, result, first_page=not cursor)
        )
        result["leamFilteredCount"] += filtered
        shared_listing = shared.listing() if not cursor else None
        result["data"] = ([shared_listing] if shared_listing else []) + [
            t for t in result.get("data", []) if not shared.owns(t.get("id"))
        ]
        return purpose_metadata(store, result)

    @app.patch("/api/codex/threads/{thread_id}")
    async def rename_coding_thread(thread_id: str, body: RenameConversation):
        if shared.owns(thread_id):
            raise HTTPException(
                409, "Manage the shared build title in its owning Codex client."
            )
        async with locks[thread_id]:
            await bridge.request(
                "thread/name/set", {"threadId": thread_id, "name": body.title}
            )
            return {"id": thread_id, "name": body.title}

    @app.delete("/api/codex/threads/{thread_id}")
    async def delete_coding_thread(thread_id: str, body: DeleteCodingConversation):
        # Native delete stops running descendants. The explicit browser confirmation
        # acknowledges those semantics, but never authorizes deleting the build or
        # orphaning an update ticket's dedicated conversation.
        async with main_coding.lock, locks[thread_id]:
            shared_listing = shared.listing()
            protected = {shared_listing["id"]} if shared_listing else set()
            if main_coding.binding():
                protected.add(main_coding.binding()["threadId"])
            with store.connect() as db:
                rows = db.execute(
                    "SELECT value FROM settings WHERE key GLOB 'ticket-chat:*' LIMIT 101"
                ).fetchall()
            if len(rows) > 100:
                raise HTTPException(
                    409,
                    "Too many linked conversations to verify safely. Manage deletion in Codex.",
                )
            for row in rows:
                try:
                    saved = json.loads(row["value"])
                    if not isinstance(saved, dict):
                        raise TypeError("invalid mapping")
                    linked = saved.get("threadId")
                    if linked is not None and (
                        not isinstance(linked, str) or not linked
                    ):
                        raise ValueError("invalid identity")
                except (ValueError, TypeError) as error:
                    raise HTTPException(
                        409,
                        "A linked conversation cannot be verified. Nothing was deleted.",
                    ) from error
                if linked:
                    protected.add(linked)
            if thread_id in protected:
                raise HTTPException(
                    409,
                    "Main, shared build and update-linked conversations are protected from deletion here.",
                )
            checked = set()
            try:
                for protected_id in protected:
                    ancestor = protected_id
                    visited = set()
                    for _ in range(32):
                        if ancestor == thread_id:
                            raise HTTPException(
                                409,
                                "This conversation contains Main, the shared build or an update-linked conversation and cannot be deleted here.",
                            )
                        if ancestor in visited:
                            raise HTTPException(
                                409,
                                "Conversation ancestry contains a cycle. Nothing was deleted.",
                            )
                        if ancestor in checked:
                            break
                        visited.add(ancestor)
                        checked.add(ancestor)
                        metadata = await bridge.request(
                            "thread/read", {"threadId": ancestor, "includeTurns": False}
                        )
                        current = metadata.get("thread")
                        if (
                            not isinstance(current, dict)
                            or current.get("id") != ancestor
                        ):
                            raise HTTPException(
                                502,
                                "Codex did not return the requested conversation identity. Nothing was deleted.",
                            )
                        ancestor = current.get("parentThreadId")
                        if ancestor is not None and (
                            not isinstance(ancestor, str) or not ancestor
                        ):
                            raise HTTPException(
                                502,
                                "Codex returned invalid conversation ancestry. Nothing was deleted.",
                            )
                        if not ancestor:
                            break
                    else:
                        raise HTTPException(
                            409,
                            "Conversation ancestry is too deep to verify safely. Manage deletion in Codex.",
                        )
            except CodexError as error:
                raise HTTPException(
                    409,
                    "A protected conversation's history could not be verified. No deletion was requested. Refresh the shared session or its Updates ticket before retrying.",
                ) from error
            try:
                await bridge.request("thread/delete", {"threadId": thread_id})
            except CodexError as error:
                if error.rpc_code == -32601:
                    raise HTTPException(
                        501,
                        "The installed Codex does not support permanent conversation deletion. Nothing was deleted.",
                    ) from error
                raise HTTPException(
                    502,
                    "Codex did not confirm deletion. Its outcome may be uncertain; refresh before retrying.",
                ) from error
            bindings.pop(thread_id, None)
            return {"id": thread_id, "deleted": True}

    @app.post("/api/codex/threads")
    async def new_thread(body: NewThread):
        path = Path(body.cwd).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise HTTPException(422, "Choose an existing absolute workspace directory")
        result = await bridge.request(
            "thread/start", await coding_models.start_parameters(path.resolve())
        )
        bindings[result["thread"]["id"]] = getattr(bridge, "generation", 0)
        return result

    @app.post("/api/codex/threads/{thread_id}/submissions/{request_id}/reconcile")
    async def reconcile(thread_id: str, request_id: str, body: Reconcile):
        if shared.owns(thread_id):
            return await shared.reconcile(request_id, body.text, body.attachmentIds)
        try:
            refs = attachments.resolve(body.attachmentIds)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        from .codex_submissions import fingerprint as submission_fingerprint

        fingerprint = submission_fingerprint(
            thread_id, body.text, refs, body.expectedTurnId
        )
        async with locks[thread_id]:
            with store.connect() as db:
                row = db.execute(
                    "SELECT * FROM requests WHERE id=?", (request_id,)
                ).fetchone()
            if row is None:
                return {"state": "notSubmitted"}
            if row["fingerprint"] != fingerprint:
                raise HTTPException(409, "Submission belongs to another message")
            if row["state"] == "complete":
                return {"state": "complete", "result": json.loads(row["result"])}
            cursor = None
            # Bound inspection. Absence is never treated as proof of non-dispatch.
            for _ in range(10):
                params = {
                    "threadId": thread_id,
                    "limit": 100,
                    "sortDirection": "desc",
                    "itemsView": "full",
                }
                if cursor:
                    params["cursor"] = cursor
                page = await bridge.request("thread/turns/list", params)
                for turn in page.get("data", []):
                    if (
                        body.expectedTurnId is not None
                        and turn.get("id") != body.expectedTurnId
                    ):
                        continue
                    if any(
                        item.get("type") == "userMessage"
                        and item.get("clientId") == request_id
                        for item in turn.get("items", [])
                    ):
                        result = {"turn": turn}
                        if body.expectedTurnId is not None:
                            result["operation"] = "steer"
                        store.finish(request_id, result)
                        return {"state": "complete", "result": result}
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            return {
                "state": "pending",
                "detail": "No matching message found. Delivery is still uncertain; do not automatically resend.",
            }

    @app.get("/api/codex/threads/{thread_id}")
    async def thread(thread_id: str):
        if shared.owns(thread_id):
            return await shared.read()
        result = await bridge.request(
            "thread/read", {"threadId": thread_id, "includeTurns": False}
        )
        result["connected"] = (
            bindings.get(thread_id) == getattr(bridge, "generation", 0)
            and thread_id in bindings
        )
        return result

    @app.get("/api/codex/submissions/{request_id}")
    async def submission(request_id: str):
        with store.connect() as db:
            row = db.execute(
                "SELECT state,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
        if row is None:
            return {"state": "notSubmitted"}
        return {
            "state": row["state"],
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    @app.get("/api/codex/threads/{thread_id}/turns")
    async def turns(thread_id: str, cursor: str | None = None):
        if shared.owns(thread_id):
            return attachments.decorate_history(await shared.turns())
        params = {
            "threadId": thread_id,
            "limit": 20,
            "sortDirection": "desc",
            "itemsView": "full",
        }
        if cursor:
            params["cursor"] = cursor
        try:
            return attachments.decorate_history(
                await bridge.request("thread/turns/list", params)
            )
        except CodexError as error:
            # Codex 0.155.1 creates the durable rollout on the first user turn.
            if (
                str(error)
                == f"thread {thread_id} is not materialized yet; thread/turns/list is unavailable before first user message"
            ):
                return {"data": [], "nextCursor": None}
            raise

    @app.get("/api/codex/threads/{thread_id}/goal")
    async def goal(thread_id: str):
        if shared.owns(thread_id):
            return await shared.goal()
        return await bridge.request("thread/goal/get", {"threadId": thread_id})

    @app.post("/api/codex/threads/{thread_id}/connect")
    async def connect(thread_id: str, body: Connect):
        if shared.owns(thread_id):
            return await shared.read()
        if not body.handoffConfirmed:
            raise HTTPException(
                409,
                "Finish the active turn in the original client before taking over here",
            )
        result = await bridge.request(
            "thread/resume", {"threadId": thread_id, "excludeTurns": True}
        )
        bindings[thread_id] = getattr(bridge, "generation", 0)
        return result

    @app.post("/api/codex/threads/{thread_id}/turns")
    async def send_turn(thread_id: str, body: Turn):
        if not body.text.strip() and not body.attachmentIds:
            raise HTTPException(422, "A message or attachment is required")
        if shared.owns(thread_id):
            if body.expectedTurnId is not None:
                raise HTTPException(
                    422, "Shared Coding uses its owner-bound submission path"
                )
            return await shared.send(
                body.text,
                body.requestId,
                body.generation,
                body.attachmentIds,
                coordination_context=main_coding.context(thread_id),
            )
        try:
            refs = attachments.resolve(body.attachmentIds)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        from .codex_submissions import fingerprint as submission_fingerprint

        fingerprint = submission_fingerprint(
            thread_id, body.text, refs, body.expectedTurnId
        )
        async with locks[thread_id]:
            try:
                existing = store.receipt(body.requestId, fingerprint)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            if existing is not None:
                return existing
            if thread_id not in bindings:
                raise HTTPException(
                    409, "Connect this session before sending a message"
                )
            generation = bindings[thread_id]
            try:
                policy_context = await asyncio.to_thread(coding_context)
                policy_context.update(ticket_chats.context(thread_id))
                policy_context.update(main_coding.context(thread_id))
                policy_context.update(handoffs.context(thread_id))
                extra_input, extra_context = await attachments.coding(
                    body.attachmentIds
                )
                policy_context.update(extra_context)
            except CodingPolicyError as error:
                raise HTTPException(503, str(error)) from error
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if hasattr(bridge, "start"):
                await bridge.start()
            if generation != getattr(bridge, "generation", 0):
                bindings.pop(thread_id, None)
                raise HTTPException(
                    409, "Codex restarted. Reconnect this thread before sending."
                )
            try:
                attachments.bind(body.attachmentIds, body.requestId, thread_id=thread_id, surface="coding")
                existing = store.reserve(body.requestId, fingerprint)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            if existing is not None:
                return existing
            policy_context.update(ticket_auto_handoff.admit(thread_id, body, fingerprint))
            params = {
                "threadId": thread_id,
                "clientUserMessageId": body.requestId,
                "additionalContext": policy_context,
                "input": [
                    {"type": "text", "text": body.text, "text_elements": []},
                    *extra_input,
                ],
            }
            if body.expectedTurnId is not None:
                params["expectedTurnId"] = body.expectedTurnId
            try:
                result = await bridge.request(
                    "turn/steer" if body.expectedTurnId is not None else "turn/start",
                    params,
                    expected_generation=generation,
                )
            except CodexGenerationError as error:
                store.release_unsent(body.requestId, fingerprint)
                bindings.pop(thread_id, None)
                raise HTTPException(409, str(error)) from error
            except CodexError as error:
                # JSON-RPC invalid request/method/parameters are definitive
                # admission rejections. Transport/internal errors stay uncertain.
                if body.expectedTurnId is not None and error.rpc_code in {
                    -32600,
                    -32601,
                    -32602,
                }:
                    store.release_unsent(body.requestId, fingerprint)
                    raise HTTPException(
                        409,
                        "Follow-up was not accepted. Refresh the conversation and send your saved draft again.",
                        headers={"X-Leam-Action-Reserved": "no"},
                    ) from error
                raise
            if body.expectedTurnId is not None:
                if result.get("turnId") != body.expectedTurnId:
                    raise CodexError(
                        "No matching steering receipt; reconcile before retrying"
                    )
                result = {
                    "turn": {"id": result["turnId"], "status": "inProgress"},
                    "operation": "steer",
                }
            store.finish(body.requestId, result)
            return result

    @app.post("/api/codex/threads/{thread_id}/interrupt/{turn_id}")
    async def interrupt(thread_id: str, turn_id: str):
        if shared.owns(thread_id):
            raise HTTPException(
                409, "Stop this shared turn in the original Codex window"
            )
        return await bridge.request(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
        )

    @app.get("/api/codex/requests")
    async def pending_requests():
        return {
            "items": [
                dict(
                    {k: v for k, v in packet.items() if not k.startswith("_")},
                    submitted=packet["id"] in getattr(bridge, "responding", set()),
                )
                for packet in getattr(bridge, "requests", {}).values()
                if not packet.get("_internal")
            ]
            + shared.pending_requests()
        }

    @app.post("/api/codex/requests/{request_id}")
    async def answer(request_id: str, request: Request):
        # Only a response to a currently pending server request is forwarded.
        if request_id.startswith("ide:"):
            raise HTTPException(
                409, "Answer this shared request in the original Codex window"
            )
        body = await request.json()
        try:
            await bridge.respond(request_id, body)
        except ValueError as error:
            raise HTTPException(422, str(error))
        return {"sent": True}

    @app.get("/api/events")
    async def events(
        request: Request,
        after: int | None = None,
        thread_id: str | None = Query(None, max_length=256),
    ):
        with store.connect() as db:
            latest = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        try:
            cursor = (
                int(request.headers["last-event-id"])
                if "last-event-id" in request.headers
                else (
                    after
                    if after is not None
                    else store.codex_replay_cursor(thread_id)
                    if thread_id
                    else latest
                )
            )
        except ValueError:
            raise HTTPException(400, "Invalid event cursor")

        async def stream():
            nonlocal cursor
            yield ": connected\n\n"
            while not await request.is_disconnected():
                if not session_valid(request.cookies.get("leam_session")):
                    return
                rows = await store.wait_for_events(cursor)
                if not session_valid(request.cookies.get("leam_session")):
                    return
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(row)}\n\n"
                if not rows:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    async def dispatch_handoff(thread, text, request_id):
        return await send_turn(thread, Turn(text=text, requestId=request_id))

    async def dispatch_main(thread, text, request_id, generation, expected_turn):
        return await send_turn(
            thread,
            Turn(
                text=text,
                requestId=request_id,
                generation=generation,
                expectedTurnId=expected_turn,
            ),
        )

    async def reconcile_main(thread, request_id, text, expected_turn):
        return await reconcile(
            thread, request_id, Reconcile(text=text, expectedTurnId=expected_turn)
        )

    main_coding.reconcile = reconcile_main
    main_coding.dispatch = dispatch_main
    handoffs.dispatch = dispatch_handoff
    web = Path(__file__).resolve().parents[1] / "apps/web/dist"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def application():
    directory = Path(
        os.environ.get("LEAM_DATA_DIR", str(Path.home() / ".local/share/leam-next"))
    )
    origins = set(
        os.environ.get(
            "LEAM_ORIGINS", "http://127.0.0.1:46400,http://localhost:46400"
        ).split(",")
    )
    return create_app(
        directory,
        origins,
        usage_home=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))),
        runtime_scope_credential=configured_credential(directory),
    )
