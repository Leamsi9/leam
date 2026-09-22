"""Durable Today exchange checks. Timeline evidence, never stream text, admits work.

SQLite contains source identities and outcomes, not another copy of chat. Native
opaque cursors bound recovery work; the worker survives browser disconnects and
process restarts. A model is only the semantic step behind recorded checkpoints.
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .commitments import Input
from .maintenance import work_admission
from .obligation_reconciliation import ObligationReconciler, ReconciliationBusy

CONFIG = "today-reconciliation:coverage"
OWNER = "today-reconciliation:owner"
OWNER_RETRY = "today-reconciliation:owner-retry"
MAX_ATTEMPTS = 3
LEASE_SECONDS = 180
WORKER_INTERVAL = 2
SCAN_INTERVAL = 10
SCAN_ERROR = "Conversation checks could not read canonical history. Retry the check."
CHECK_ERROR = "Obligation check failed. No completion is confirmed; retry this check."
OWNER_ERROR = "Companion owner is unavailable or changed; checks are paused."
INCOMPLETE = "Completion has not been observed. The response may have stopped; retry the check after the reply finishes."


def initialize(store):
    with store.connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS today_reconciliation_threads (owner_id TEXT NOT NULL,thread_id TEXT NOT NULL,scan_cursor TEXT,scan_head INTEGER NOT NULL DEFAULT 0,scanned_sequence INTEGER NOT NULL DEFAULT 0,next_scan REAL NOT NULL DEFAULT 0,error TEXT,failures INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner_id,thread_id));
        CREATE TABLE IF NOT EXISTS today_reconciliation_sources (owner_id TEXT NOT NULL,thread_id TEXT NOT NULL,message_id TEXT NOT NULL,sequence INTEGER NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,turn_id TEXT,run_id TEXT,page_cursor TEXT,created REAL NOT NULL,updated TEXT,completed REAL NOT NULL,PRIMARY KEY(owner_id,thread_id,message_id));
        CREATE INDEX IF NOT EXISTS today_reconciliation_sources_run ON today_reconciliation_sources(owner_id,thread_id,run_id,kind);
        CREATE TABLE IF NOT EXISTS today_reconciliation_jobs (id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,run_id TEXT NOT NULL,user_ref TEXT NOT NULL,assistant_ref TEXT,state TEXT NOT NULL,outcome TEXT,result TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,next_retry REAL,lease_token TEXT,lease_until REAL,created REAL NOT NULL,updated REAL NOT NULL,UNIQUE(owner_id,thread_id,turn_id));
        CREATE INDEX IF NOT EXISTS today_reconciliation_jobs_ready ON today_reconciliation_jobs(state,next_retry,created);
        CREATE TABLE IF NOT EXISTS today_reconciliation_retries (request_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,job_id TEXT NOT NULL,revision INTEGER NOT NULL,result TEXT NOT NULL);
        """)


def stamp(value):
    if not isinstance(value, str):
        raise TypeError("Canonical message timestamp missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Canonical timestamp requires timezone")
    return parsed.timestamp()


def owner_key(pair):
    return hashlib.sha256(
        json.dumps(pair, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class Retry(Input):
    revision: int = Field(ge=1)
    requestId: UUID


class ScanRetry(Input):
    threadId: str = Field(min_length=1, max_length=200)
    requestId: UUID


class TodayReconciliation:
    def __init__(
        self,
        store,
        runtime,
        proposals,
        *,
        clock=time.time,
        reconciler_factory=ObligationReconciler,
    ):
        self.store, self.runtime, self.proposals = store, runtime, proposals
        self.clock, self.factory = clock, reconciler_factory
        self.wake = asyncio.Event()
        self.tick_lock = asyncio.Lock()
        initialize(store)
        with store.connect() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                (
                    CONFIG,
                    json.dumps(
                        {
                            "enabledAt": clock(),
                            "scope": "Today user turns submitted or replies finalized since enabledAt",
                        }
                    ),
                ),
            )
        self.coverage = store.get(CONFIG)

    async def owner(self, session=None):
        session = (
            session
            if session is not None
            else await self.runtime.request("GET", "/session")
        )
        pair = [session.get("tenant_id"), session.get("user_id")]
        if any(
            not isinstance(value, str) or not value or len(value) > 256
            for value in pair
        ):
            raise HTTPException(
                503, "Companion owner identity is unavailable; checks are paused"
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (OWNER,)
            ).fetchone()
            if row and json.loads(row[0]) != pair:
                raise HTTPException(
                    403, "Companion owner changed; personal-state checks are paused"
                )
            if not row:
                db.execute(
                    "INSERT INTO settings VALUES (?,?)", (OWNER, json.dumps(pair))
                )
        return owner_key(pair), pair

    async def page(self, thread_id, pair, cursor=None, *, limit=100):
        query = {"limit": limit}
        if cursor:
            query["cursor"] = cursor
        page = await self.runtime.request(
            "GET",
            "/threads/" + quote(thread_id, safe="") + "/timeline?" + urlencode(query),
        )
        thread = page.get("thread") or {}
        scope = thread.get("scope") or {}
        if (
            thread.get("thread_id") != thread_id
            or [scope.get("tenant_id"), scope.get("owner_user_id")] != pair
        ):
            raise HTTPException(403, "Conversation owner could not be verified")
        rows = page.get("messages")
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("Invalid canonical timeline")
        if any(row.get("thread_id") != thread_id for row in rows):
            raise HTTPException(403, "Conversation contains another scope")
        cursor = page.get("next_cursor")
        if cursor is not None and (
            not isinstance(cursor, str) or not cursor or len(cursor) > 4096
        ):
            raise ValueError("Invalid timeline cursor")
        return page

    async def watch(self, thread_id, session=None):
        # The browser cannot select an owner, and non-Today threads stay outside scope.
        if not self.store.get("companion-agenda:" + thread_id):
            return False
        owner, pair = await self.owner(session)
        await self.page(thread_id, pair, limit=1)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO today_reconciliation_threads(owner_id,thread_id) VALUES (?,?) ON CONFLICT DO NOTHING",
                (owner, thread_id),
            )
        self.notify(thread_id)
        return True

    def notify(self, thread_id):
        # Hints coalesce; only durable deadlines admit work. In particular, a
        # stream must never erase scan cadence or a failed scan's retry delay.
        self.wake.set()

    def discover(self, owner):
        # Bindings are the canonical Today membership index. Existing admitted
        # runtime_actions survive crashes; their threads are included by this index.
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO today_reconciliation_threads(owner_id,thread_id) SELECT ?,substr(key,length('companion-agenda:')+1) FROM settings WHERE key GLOB 'companion-agenda:*' ON CONFLICT DO NOTHING",
                (owner,),
            )

    async def scan(self, owner, pair, watched):
        thread_id, cursor = watched["thread_id"], watched["scan_cursor"]
        for _ in range(4):
            page = await self.page(thread_id, pair, cursor)
            messages = page["messages"]
            now = self.clock()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT * FROM today_reconciliation_threads WHERE owner_id=? AND thread_id=?",
                    (owner, thread_id),
                ).fetchone()
                if current["scan_cursor"] != cursor:
                    return  # Another process committed this page.
                head = (
                    current["scan_head"]
                    if cursor
                    else max(
                        (row["sequence"] for row in messages),
                        default=current["scanned_sequence"],
                    )
                )
                for message in messages:
                    if message.get("kind") not in {"user", "assistant"}:
                        continue
                    created = stamp(message.get("created_at"))
                    if (
                        message["kind"] == "user"
                        and message.get("status") == "submitted"
                        and created >= self.coverage["enabledAt"]
                        and (
                            not message.get("turn_id") or not message.get("turn_run_id")
                        )
                    ):
                        raise ValueError(
                            "Submitted exchange lacks canonical turn identity"
                        )
                    mid, seq = message.get("message_id"), message.get("sequence")
                    if not isinstance(mid, str) or type(seq) is not int or seq < 0:
                        raise ValueError("Invalid canonical message identity")
                    db.execute(
                        "INSERT INTO today_reconciliation_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,thread_id,message_id) DO UPDATE SET status=excluded.status,run_id=excluded.run_id,turn_id=excluded.turn_id,page_cursor=excluded.page_cursor,updated=excluded.updated,completed=excluded.completed",
                        (
                            owner,
                            thread_id,
                            mid,
                            seq,
                            message["kind"],
                            message["status"],
                            message.get("turn_id"),
                            message.get("turn_run_id"),
                            cursor,
                            created,
                            message.get("updated_at") or message["created_at"],
                            stamp(message.get("updated_at") or message["created_at"]),
                        ),
                    )
                eligible = db.execute(
                    "SELECT u.* FROM today_reconciliation_sources u WHERE u.owner_id=? AND u.thread_id=? AND u.kind='user' AND u.status='submitted' AND u.turn_id IS NOT NULL AND u.run_id IS NOT NULL AND (u.created>=? OR EXISTS (SELECT 1 FROM today_reconciliation_sources a WHERE a.owner_id=u.owner_id AND a.thread_id=u.thread_id AND a.run_id=u.run_id AND a.kind='assistant' AND a.status='finalized' AND a.completed>=?)) AND NOT EXISTS (SELECT 1 FROM today_reconciliation_jobs j WHERE j.owner_id=u.owner_id AND j.thread_id=u.thread_id AND j.turn_id=u.turn_id) LIMIT 1000",
                    (
                        owner,
                        thread_id,
                        self.coverage["enabledAt"],
                        self.coverage["enabledAt"],
                    ),
                ).fetchall()
                for user in eligible:
                    key = owner_key([owner, thread_id, user["turn_id"]])
                    db.execute(
                        "INSERT INTO today_reconciliation_jobs(id,owner_id,thread_id,turn_id,run_id,user_ref,state,created,updated) VALUES (?,?,?,?,?,?,'waiting_exchange',?,?) ON CONFLICT DO NOTHING",
                        (
                            key,
                            owner,
                            thread_id,
                            user["turn_id"],
                            user["run_id"],
                            user["message_id"],
                            user["created"],
                            now,
                        ),
                    )
                waiting = db.execute(
                    "SELECT * FROM today_reconciliation_jobs WHERE owner_id=? AND thread_id=? AND (state='waiting_exchange' OR (state='failed' AND error=?))",
                    (owner, thread_id, INCOMPLETE),
                ).fetchall()
                for job in waiting:
                    if (
                        job["state"] == "waiting_exchange"
                        and now - job["created"] > 1800
                    ):
                        db.execute(
                            "UPDATE today_reconciliation_jobs SET state='failed',outcome='check_failed',error=?,updated=?,revision=revision+1 WHERE id=?",
                            (INCOMPLETE, now, job["id"]),
                        )
                    assistant = db.execute(
                        "SELECT * FROM today_reconciliation_sources WHERE owner_id=? AND thread_id=? AND run_id=? AND kind='assistant' AND status IN ('finalized','interrupted') ORDER BY (status='finalized') DESC,sequence DESC LIMIT 1",
                        (owner, thread_id, job["run_id"]),
                    ).fetchone()
                    if assistant and assistant["status"] == "finalized":
                        db.execute(
                            "UPDATE today_reconciliation_jobs SET assistant_ref=?,state='queued',outcome=NULL,error=NULL,next_retry=?,updated=?,revision=revision+1 WHERE id=?",
                            (assistant["message_id"], now, now, job["id"]),
                        )
                    elif assistant and job["state"] == "waiting_exchange":
                        db.execute(
                            "UPDATE today_reconciliation_jobs SET state='failed',outcome='check_failed',error=?,updated=?,revision=revision+1 WHERE id=?",
                            (INCOMPLETE, now, job["id"]),
                        )
                floor = current["scanned_sequence"]
                unresolved = db.execute(
                    "SELECT MIN(s.sequence) FROM today_reconciliation_jobs j JOIN today_reconciliation_sources s ON s.owner_id=j.owner_id AND s.thread_id=j.thread_id AND s.message_id=j.user_ref WHERE j.owner_id=? AND j.thread_id=? AND (j.state='waiting_exchange' OR j.error=?)",
                    (owner, thread_id, INCOMPLETE),
                ).fetchone()[0]
                if unresolved is not None:
                    floor = min(floor, unresolved)
                # Finalization may mutate an existing draft without advancing its
                # sequence. Retain a rescan floor for pre-activation drafts even
                # though their old user turn is not yet eligible for a check.
                draft = db.execute(
                    "SELECT MIN(sequence) FROM today_reconciliation_sources WHERE owner_id=? AND thread_id=? AND kind='assistant' AND status='draft'",
                    (owner, thread_id),
                ).fetchone()[0]
                if draft is not None:
                    floor = min(floor, draft)
                orphan = db.execute(
                    "SELECT 1 FROM today_reconciliation_sources a WHERE a.owner_id=? AND a.thread_id=? AND a.kind='assistant' AND a.status='finalized' AND a.completed>=? AND NOT EXISTS (SELECT 1 FROM today_reconciliation_sources u WHERE u.owner_id=a.owner_id AND u.thread_id=a.thread_id AND u.kind='user' AND u.run_id=a.run_id) LIMIT 1",
                    (owner, thread_id, self.coverage["enabledAt"]),
                ).fetchone()
                before_activation = (
                    not orphan
                    and bool(messages)
                    and all(
                        stamp(row.get("updated_at") or row.get("created_at"))
                        < self.coverage["enabledAt"]
                        for row in messages
                        if row.get("kind") in {"user", "assistant"}
                    )
                    and any(
                        row.get("kind") in {"user", "assistant"} for row in messages
                    )
                )
                reached = (
                    not page.get("next_cursor")
                    or before_activation
                    or not orphan
                    and bool(messages)
                    and min(row["sequence"] for row in messages) <= floor
                )
                next_cursor = None if reached else page["next_cursor"]
                if next_cursor == cursor and not reached:
                    raise ValueError("Timeline cursor did not advance")
                db.execute(
                    "UPDATE today_reconciliation_threads SET scan_cursor=?,scan_head=?,scanned_sequence=?,next_scan=?,error=NULL,failures=0 WHERE owner_id=? AND thread_id=?",
                    (
                        next_cursor,
                        head,
                        head if reached else current["scanned_sequence"],
                        now + SCAN_INTERVAL,
                        owner,
                        thread_id,
                    ),
                )
            if reached:
                if orphan and not page.get("next_cursor"):
                    raise ValueError(
                        "Finalized exchange has no canonical user reference"
                    )
                return
            cursor = next_cursor

    def fail_scan(self, owner, thread_id, error=None):
        self.log_deferred("scan", error)
        with self.store.connect() as db:
            db.execute(
                "UPDATE today_reconciliation_threads SET failures=failures+1,error=?,next_scan=? WHERE owner_id=? AND thread_id=?",
                (SCAN_ERROR, self.clock() + 30, owner, thread_id),
            )

    @staticmethod
    def log_deferred(stage, error):
        # Never log response bodies, source text, owner identities or thread IDs.
        logging.getLogger(__name__).warning(
            "Today check %s deferred (status=%s, type=%s)",
            stage,
            getattr(error, "status_code", None),
            type(error).__name__,
        )

    def work_due(self, now):
        with self.store.connect() as db:
            return bool(db.execute(
                """SELECT 1 WHERE
                EXISTS(SELECT 1 FROM settings s WHERE s.key GLOB 'companion-agenda:*'
                  AND NOT EXISTS(SELECT 1 FROM today_reconciliation_threads t
                    WHERE t.thread_id=substr(s.key,length('companion-agenda:')+1)))
                OR EXISTS(SELECT 1 FROM today_reconciliation_threads
                  WHERE next_scan<=? AND failures<?)
                OR EXISTS(SELECT 1 FROM today_reconciliation_jobs WHERE
                  (state='queued' AND next_retry<=?) OR
                  (state='checking' AND lease_until<=?) OR
                  (state='done' AND outcome='proposals_pending' AND updated<=?))""",
                (now, MAX_ATTEMPTS, now, now, now - SCAN_INTERVAL),
            ).fetchone())

    async def source(self, owner, pair, thread_id, message_id):
        with self.store.connect() as db:
            source = db.execute(
                "SELECT * FROM today_reconciliation_sources WHERE owner_id=? AND thread_id=? AND message_id=?",
                (owner, thread_id, message_id),
            ).fetchone()
        if not source:
            raise ValueError("Source reference missing")
        cursor = source["page_cursor"]
        seen = set()
        for _ in range(10):
            page = await self.page(thread_id, pair, cursor)
            for row in page["messages"]:
                if row.get("message_id") == message_id:
                    if (
                        row.get("kind") != source["kind"]
                        or row.get("status") != source["status"]
                        or row.get("turn_run_id") != source["run_id"]
                        or not isinstance(row.get("content"), str)
                    ):
                        raise ValueError("Canonical source changed")
                    return row
            cursor = page.get("next_cursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        raise ValueError("Source not found within bounded lookup; retry after recovery")

    def claim(self, owner):
        now = self.clock()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Expired leases consume their already-recorded attempt, preventing an
            # endless crash/provider loop. No database transaction spans an await.
            db.execute(
                "UPDATE today_reconciliation_jobs SET state=CASE WHEN attempts>=? THEN 'failed' ELSE 'queued' END,outcome=CASE WHEN attempts>=? THEN 'check_failed' ELSE NULL END,error=?,lease_token=NULL,lease_until=NULL,next_retry=?,revision=revision+1 WHERE owner_id=? AND state='checking' AND lease_until<=?",
                (MAX_ATTEMPTS, MAX_ATTEMPTS, CHECK_ERROR, now, owner, now),
            )
            job = db.execute(
                "SELECT * FROM today_reconciliation_jobs WHERE owner_id=? AND state='queued' AND next_retry<=? ORDER BY created,id LIMIT 1",
                (owner, now),
            ).fetchone()
            if not job:
                return None
            token = str(uuid4())
            db.execute(
                "UPDATE today_reconciliation_jobs SET state='checking',attempts=attempts+1,lease_token=?,lease_until=?,revision=revision+1,updated=? WHERE id=?",
                (token, now + LEASE_SECONDS, now, job["id"]),
            )
            return {**dict(job), "attempts": job["attempts"] + 1, "lease_token": token}

    async def process(self, owner, pair, job):
        try:
            async with asyncio.timeout(125):
                user = await self.source(owner, pair, job["thread_id"], job["user_ref"])
                assistant = await self.source(
                    owner, pair, job["thread_id"], job["assistant_ref"]
                )
                await (
                    self.owner()
                )  # Recheck the installation before personal domain reads.
                domain = self.factory(self.store, self.proposals, self.runtime, owner)
                result = await domain.reconcile(
                    {
                        "user_id": owner,
                        "thread_id": job["thread_id"],
                        "turn_id": job["turn_id"],
                        "user_text": user["content"],
                        "assistant_text": assistant["content"],
                        "source_refs": {
                            "userMessageId": job["user_ref"],
                            "assistantMessageId": job["assistant_ref"],
                            "runId": job["run_id"],
                        },
                        "completed_at": assistant.get("updated_at")
                        or assistant["created_at"],
                    }
                )
                if result.get("outcome") not in {
                    "no_action",
                    "proposals_pending",
                    "changes_confirmed_complete",
                    "clarification_needed",
                }:
                    raise ValueError("Invalid reconciliation outcome")
        except ReconciliationBusy:
            with self.store.connect() as db:
                db.execute(
                    "UPDATE today_reconciliation_jobs SET state='queued',attempts=attempts-1,next_retry=?,lease_token=NULL,lease_until=NULL,revision=revision+1 WHERE id=? AND lease_token=?",
                    (self.clock() + 2, job["id"], job["lease_token"]),
                )
            return
        except asyncio.CancelledError:
            raise  # Lease recovery handles process interruption.
        except Exception:  # noqa: BLE001 - persist sanitized failure at the worker boundary
            with self.store.connect() as db:
                exhausted = job["attempts"] >= MAX_ATTEMPTS
                db.execute(
                    "UPDATE today_reconciliation_jobs SET state=?,outcome=?,error=?,next_retry=?,lease_token=NULL,lease_until=NULL,updated=?,revision=revision+1 WHERE id=? AND lease_token=?",
                    (
                        "failed" if exhausted else "queued",
                        "check_failed",
                        CHECK_ERROR,
                        None if exhausted else self.clock() + 5 * 2 ** job["attempts"],
                        self.clock(),
                        job["id"],
                        job["lease_token"],
                    ),
                )
            return
        # Keep classification/provenance metadata, not duplicate source text.
        result["findings"] = [
            {
                key: value
                for key, value in finding.items()
                if key in {"kind", "confidence", "source", "sourceRefs", "proposalId"}
            }
            for finding in result.get("findings", [])
        ]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A separate process may decline after extraction returns and before
            # this write. The deletion callback cannot clear a future write, so
            # fence the derived result against canonical decisions atomically.
            missing = []
            states = []
            decision = getattr(self.proposals, "decision_state", lambda _: None)
            for proposal_id in result.get("proposalIds", []):
                proposal = db.execute(
                    "SELECT state FROM proposals WHERE id=?", (proposal_id,)
                ).fetchone()
                if proposal:
                    states.append(proposal["state"])
                else:
                    missing.append(proposal_id)
                    states.append(decision(proposal_id) or "missing")
            if missing:
                result["findings"] = [
                    finding
                    for finding in result.get("findings", [])
                    if finding.get("proposalId") not in missing
                ]
                result["clarification"] = None
                if all(state in {"declined", "superseded"} for state in states):
                    result["outcome"] = "no_action"
                elif any(state in {"pending", "executing"} for state in states):
                    result["outcome"] = "proposals_pending"
                elif all(
                    state in {"complete", "declined", "superseded"} for state in states
                ):
                    result["outcome"] = "changes_confirmed_complete"
            failed = any(state in {"missing", "conflict", "failed"} for state in states)
            db.execute(
                "UPDATE today_reconciliation_jobs SET state=?,outcome=?,result=?,error=?,next_retry=NULL,lease_token=NULL,lease_until=NULL,updated=?,revision=revision+1 WHERE id=? AND lease_token=?",
                (
                    "failed" if failed else "done",
                    "check_failed" if failed else result["outcome"],
                    json.dumps(result),
                    CHECK_ERROR if failed else None,
                    self.clock(),
                    job["id"],
                    job["lease_token"],
                ),
            )

    def refresh(self, owner):
        # Approval receipts change status without another model call/reproposal.
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM today_reconciliation_jobs WHERE owner_id=? AND state='done' AND outcome='proposals_pending' ORDER BY updated LIMIT 100",
                (owner,),
            ).fetchall()
            for row in rows:
                result = json.loads(row["result"])
                states = []
                for pid in result.get("proposalIds", []):
                    proposal = db.execute(
                        "SELECT state FROM proposals WHERE id=?", (pid,)
                    ).fetchone()
                    decision = getattr(self.proposals, "decision_state", lambda _: None)
                    states.append(
                        proposal["state"] if proposal else decision(pid) or "missing"
                    )
                if (
                    states
                    and "complete" in states
                    and all(
                        state in {"complete", "declined", "superseded"}
                        for state in states
                    )
                ):
                    outcome = "changes_confirmed_complete"
                elif states and all(
                    state in {"declined", "superseded"} for state in states
                ):
                    outcome = "no_action"
                elif states and any(
                    state in {"failed", "conflict", "missing"} for state in states
                ):
                    db.execute(
                        "UPDATE today_reconciliation_jobs SET state='failed',outcome='check_failed',error=?,updated=?,revision=revision+1 WHERE id=?",
                        (
                            "A proposed change is unavailable or needs review. Check Approvals before retrying.",
                            self.clock(),
                            row["id"],
                        ),
                    )
                    continue
                else:
                    # Rotate bounded receipt inspection without changing the
                    # user-facing revision while all proposals remain pending.
                    db.execute(
                        "UPDATE today_reconciliation_jobs SET updated=? WHERE id=?",
                        (self.clock(), row["id"]),
                    )
                    continue
                result["outcome"] = outcome
                db.execute(
                    "UPDATE today_reconciliation_jobs SET outcome=?,result=?,updated=?,revision=revision+1 WHERE id=?",
                    (outcome, json.dumps(result), self.clock(), row["id"]),
                )

    async def tick(self):
        if self.tick_lock.locked():
            return
        async with self.tick_lock:
            now = self.clock()
            owner_retry = self.store.get(OWNER_RETRY) or {}
            if owner_retry.get("nextRetryAt", 0) > now or not self.work_due(now):
                return
            with self.store.connect() as db:
                active = db.execute(
                    "SELECT 1 FROM settings WHERE key GLOB 'companion-agenda:*' LIMIT 1"
                ).fetchone()
            if not active:
                return
            try:
                owner, pair = await self.owner()
                self.discover(owner)
            except Exception as error:  # noqa: BLE001 - persist sanitized failure at the worker boundary
                self.log_deferred("owner", error)
                failures = min(owner_retry.get("failures", 0) + 1, 3)
                retry_at = self.clock() + 30 * 2 ** (failures - 1)
                self.store.set(OWNER_RETRY, {"failures": failures, "nextRetryAt": retry_at})
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE today_reconciliation_threads SET error=?,next_scan=MAX(next_scan,?)",
                        (
                            OWNER_ERROR,
                            retry_at,
                        ),
                    )
                return
            with self.store.connect() as db:
                db.execute("DELETE FROM settings WHERE key=?", (OWNER_RETRY,))
            with self.store.connect() as db:
                watches = db.execute(
                    "SELECT * FROM today_reconciliation_threads WHERE owner_id=? AND next_scan<=? AND failures<? ORDER BY next_scan,thread_id LIMIT 4",
                    (owner, self.clock(), MAX_ATTEMPTS),
                ).fetchall()
            for watch in watches:
                try:
                    await self.scan(owner, pair, watch)
                except Exception as error:  # noqa: BLE001 - persist sanitized failure at the worker boundary
                    self.fail_scan(owner, watch["thread_id"], error)
            self.refresh(owner)
            job = self.claim(owner)
            if job:
                await self.process(owner, pair, job)

    async def run(self):
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            # A wake hint may shorten the idle wait, never the minimum cadence.
            await asyncio.sleep(max(0, next_tick - loop.time()))
            next_tick = loop.time() + WORKER_INTERVAL
            self.wake.clear()
            try:
                with work_admission(self.store):
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - persist sanitized failure at the worker boundary
                # Durable rows remain available and lease recovery is independent
                # of the browser. Never persist exception/provider payloads.
                logging.getLogger(__name__).warning(
                    "Today check worker deferred; durable checkpoints retained"
                )
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except TimeoutError:
                pass

    def forget_proposal_content(self, db, proposal_id):
        # Called within the proposal decline transaction. Original chat stays in
        # its canonical runtime; derived suggestion content is removed here.
        for row in db.execute(
            "SELECT id,result FROM today_reconciliation_jobs WHERE result IS NOT NULL"
        ):
            result = json.loads(row["result"])
            if proposal_id not in result.get("proposalIds", []):
                continue
            result["findings"] = [
                finding
                for finding in result.get("findings", [])
                if finding.get("proposalId") != proposal_id
            ]
            result["clarification"] = None
            db.execute(
                "UPDATE today_reconciliation_jobs SET result=?,revision=revision+1 WHERE id=?",
                (json.dumps(result), row["id"]),
            )

    async def retry_scan(self, body):
        owner, pair = await self.owner()
        if not self.store.get("companion-agenda:" + body.threadId):
            raise HTTPException(404, "Today conversation not found")
        await self.page(body.threadId, pair, limit=1)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM today_reconciliation_retries WHERE request_id=?",
                (str(body.requestId),),
            ).fetchone()
            if prior:
                if (prior["owner_id"], prior["job_id"], prior["revision"]) != (
                    owner,
                    "scan:" + body.threadId,
                    0,
                ):
                    raise HTTPException(409, "Retry identity belongs to another check")
                return json.loads(prior["result"])
            db.execute(
                "UPDATE today_reconciliation_threads SET failures=0,error=NULL,next_scan=0 WHERE owner_id=? AND thread_id=?",
                (owner, body.threadId),
            )
            db.execute("DELETE FROM settings WHERE key=?", (OWNER_RETRY,))
            result = {"queued": True}
            db.execute(
                "INSERT INTO today_reconciliation_retries VALUES (?,?,?,?,?)",
                (
                    str(body.requestId),
                    owner,
                    "scan:" + body.threadId,
                    0,
                    json.dumps(result),
                ),
            )
        self.wake.set()
        return result

    def item(self, row):
        result = json.loads(row["result"]) if row["result"] else {}
        return {
            "id": row["id"],
            "turnId": row["turn_id"],
            "revision": row["revision"],
            "state": row["state"],
            "outcome": row["outcome"],
            "proposalIds": result.get("proposalIds", []),
            "clarification": result.get("clarification"),
            "error": row["error"],
            "attempts": row["attempts"],
            "nextRetryAt": row["next_retry"],
            "createdAt": row["created"],
        }

    async def status(self, thread_id):
        owner, pair = await self.owner()
        if not self.store.get("companion-agenda:" + thread_id):
            raise HTTPException(404, "Today conversation not found")
        await self.page(thread_id, pair, limit=1)
        owner_retry = self.store.get(OWNER_RETRY)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM today_reconciliation_jobs WHERE owner_id=? AND thread_id=? ORDER BY created DESC,id LIMIT 100",
                (owner, thread_id),
            ).fetchall()
            watch = db.execute(
                "SELECT * FROM today_reconciliation_threads WHERE owner_id=? AND thread_id=?",
                (owner, thread_id),
            ).fetchone()
        return {
            "items": [self.item(row) for row in rows],
            "coverage": {
                **self.coverage,
                "error": OWNER_ERROR if owner_retry else watch["error"] if watch else None,
                "retryable": bool(watch and watch["failures"] >= MAX_ATTEMPTS),
                "state": (
                    "failed"
                    if watch and watch["failures"] >= MAX_ATTEMPTS
                    else "checking"
                    if watch and watch["scan_cursor"]
                    else "queued"
                    if owner_retry or not watch or watch["error"] or watch["next_scan"] <= self.clock()
                    else "idle"
                ),
                "nextRetryAt": owner_retry["nextRetryAt"] if owner_retry else (
                    watch["next_scan"]
                    if watch and watch["error"] and watch["failures"] < MAX_ATTEMPTS
                    else None
                ),
            },
        }

    async def retry(self, key, body):
        owner, pair = await self.owner()
        with self.store.connect() as db:
            job = db.execute(
                "SELECT * FROM today_reconciliation_jobs WHERE id=? AND owner_id=?",
                (key, owner),
            ).fetchone()
        if not job:
            raise HTTPException(404, "Check not found")
        await self.page(job["thread_id"], pair, limit=1)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM today_reconciliation_retries WHERE request_id=?",
                (str(body.requestId),),
            ).fetchone()
            if prior:
                if (prior["owner_id"], prior["job_id"], prior["revision"]) != (
                    owner,
                    key,
                    body.revision,
                ):
                    raise HTTPException(409, "Retry identity belongs to another check")
                return json.loads(prior["result"])
            changed = db.execute(
                "UPDATE today_reconciliation_jobs SET state=CASE WHEN assistant_ref IS NULL THEN 'waiting_exchange' ELSE 'queued' END,outcome=NULL,error=NULL,attempts=0,next_retry=?,updated=?,revision=revision+1 WHERE id=? AND owner_id=? AND revision=? AND state='failed'",
                (self.clock(), self.clock(), key, owner, body.revision),
            ).rowcount
            if changed != 1:
                raise HTTPException(409, "Check changed; refresh before retrying")
            db.execute(
                "UPDATE today_reconciliation_threads SET failures=0,error=NULL,next_scan=0 WHERE owner_id=? AND thread_id=?",
                (owner, job["thread_id"]),
            )
            db.execute("DELETE FROM settings WHERE key=?", (OWNER_RETRY,))
            result = self.item(
                db.execute(
                    "SELECT * FROM today_reconciliation_jobs WHERE id=?", (key,)
                ).fetchone()
            )
            db.execute(
                "INSERT INTO today_reconciliation_retries VALUES (?,?,?,?,?)",
                (str(body.requestId), owner, key, body.revision, json.dumps(result)),
            )
        self.wake.set()
        return result


def router(manager):
    routes = APIRouter(prefix="/api/agenda/reconciliation")

    @routes.get("")
    async def status(threadId: str = Query(min_length=1, max_length=200)):
        return await manager.status(threadId)

    @routes.post("/scan/retry")
    async def retry_scan(body: ScanRetry):
        return await manager.retry_scan(body)

    @routes.post("/{key}/retry")
    async def retry(key: str, body: Retry):
        return await manager.retry(key, body)

    return routes
