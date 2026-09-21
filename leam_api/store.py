"""Leam-owned state. External systems remain authoritative for their domains."""

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        self._event_waiters: set[
            tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]
        ] = set()
        self._event_waiters_lock = threading.Lock()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "leam.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS daily_logs (commitment_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE, day TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(commitment_id,day));
            CREATE TABLE IF NOT EXISTS import_batches (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS import_records (source_id TEXT NOT NULL, kind TEXT NOT NULL, original_id TEXT NOT NULL, entity_id TEXT NOT NULL, fingerprint TEXT NOT NULL, raw TEXT NOT NULL, PRIMARY KEY(source_id,kind,original_id));
            CREATE TABLE IF NOT EXISTS reminder_jobs (id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE, day TEXT NOT NULL, due REAL NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, snoozed INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS push_devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS push_deliveries (id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES push_devices(id) ON DELETE CASCADE, kind TEXT NOT NULL, reminder_id TEXT, reminder_revision INTEGER, state TEXT NOT NULL, attempts INTEGER NOT NULL, next_try REAL NOT NULL, expires REAL NOT NULL, error TEXT, created REAL NOT NULL, updated REAL NOT NULL, confirmed REAL);
            CREATE TABLE IF NOT EXISTS oauth_states (id TEXT PRIMARY KEY,provider TEXT NOT NULL,session_hash TEXT NOT NULL,expires REAL NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY,provider TEXT NOT NULL,identity TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,error TEXT,checked REAL,created REAL NOT NULL,subject TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS email_snapshots (account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,body TEXT NOT NULL,synced REAL,error TEXT);
            CREATE TABLE IF NOT EXISTS calendars (id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,external_id TEXT NOT NULL,name TEXT NOT NULL,timezone TEXT NOT NULL,can_write INTEGER NOT NULL,listed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS calendar_scans (account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,synced REAL,error TEXT);
            CREATE TABLE IF NOT EXISTS calendar_snapshots (calendar_id TEXT PRIMARY KEY REFERENCES calendars(id) ON DELETE CASCADE,start TEXT,end TEXT,body TEXT NOT NULL,synced REAL,error TEXT);
            CREATE TABLE IF NOT EXISTS calendar_actions (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,calendar_id TEXT NOT NULL,request TEXT NOT NULL,review TEXT NOT NULL,payload TEXT NOT NULL,result TEXT,error TEXT,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS calendar_changes (id TEXT PRIMARY KEY,creation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,request TEXT NOT NULL,payload TEXT,state TEXT NOT NULL,result TEXT,error TEXT,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,thread_id TEXT NOT NULL,operation TEXT NOT NULL,input TEXT NOT NULL,review TEXT NOT NULL,reason TEXT NOT NULL,state TEXT NOT NULL,result TEXT,error TEXT,created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS runtime_actions (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def event(self, topic, payload):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                (topic, json.dumps(payload), time.time()),
            )
            sequence = cursor.lastrowid
        # Notify only after the transaction commits. Writers can run on another
        # thread; a Future must only be resolved on its owning event loop.
        with self._event_waiters_lock:
            waiters = tuple(self._event_waiters)
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(self._resolve_event_waiter, future)
            except RuntimeError:
                # A closed client loop cannot consume future events.
                with self._event_waiters_lock:
                    self._event_waiters.discard((loop, future))
        return sequence

    @staticmethod
    def _resolve_event_waiter(future):
        if not future.done():
            future.set_result(None)

    async def wait_for_events(self, after, timeout=2):
        """Wake promptly for local commits; SQLite remains the event authority."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        waiter = (loop, future)
        # Register before reading: a commit between an empty query and the await
        # must either appear in the query or resolve this registered waiter.
        with self._event_waiters_lock:
            self._event_waiters.add(waiter)
        try:
            rows = self.events(after)
            if rows:
                return rows
            try:
                await asyncio.wait_for(future, timeout)
            except TimeoutError:
                pass
            # Other processes/Store instances do not share this local notifier.
            return self.events(after)
        finally:
            with self._event_waiters_lock:
                self._event_waiters.discard(waiter)
            future.cancel()

    def events(self, after):
        with self.connect() as db:
            return [
                {
                    "id": r["id"],
                    "topic": r["topic"],
                    "payload": json.loads(r["payload"]),
                }
                for r in db.execute(
                    "SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200", (after,)
                )
            ]

    def codex_replay_cursor(self, thread_id):
        """Bound replay at a canonical start, never claim a snapshot watermark."""
        with self.connect() as db:
            latest = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
            start = db.execute(
                "SELECT id FROM events WHERE id>? AND topic='codex' "
                "AND json_extract(payload,'$.method')='turn/started' "
                "AND json_extract(payload,'$.params.threadId')=? ORDER BY id DESC LIMIT 1",
                (max(0, latest - 4096), thread_id),
            ).fetchone()
        return start[0] - 1 if start else latest

    def receipt(self, request_id, fingerprint):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise ValueError("Request ID already belongs to a different action")
        if row["state"] != "complete":
            raise ValueError(
                "Previous submission outcome is pending or uncertain; inspect thread before retrying"
            )
        return json.loads(row["result"])

    def release_unsent(self, request_id, fingerprint):
        """Only for proven pre-write or explicit invalid-request admission rejection."""
        with self.connect() as db:
            db.execute(
                "DELETE FROM requests WHERE id=? AND fingerprint=? AND state='pending'",
                (request_id, fingerprint),
            )

    def reserve(self, request_id, fingerprint):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO requests VALUES (?,?,?,NULL)",
                (request_id, fingerprint, "pending"),
            )
            inserted = db.execute("SELECT changes()").fetchone()[0] == 1
            row = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if row["fingerprint"] != fingerprint:
                raise ValueError("Request ID already belongs to a different action")
            if inserted:
                return None
            if row["state"] != "complete":
                raise ValueError(
                    "Previous submission outcome is pending or uncertain; inspect thread before retrying"
                )
            return json.loads(row["result"])

    def finish(self, request_id, result):
        with self.connect() as db:
            db.execute(
                "UPDATE requests SET state=?,result=? WHERE id=?",
                ("complete", json.dumps(result), request_id),
            )

    def entities(self, kind):
        with self.connect() as db:
            return [
                dict(json.loads(r["body"]), id=r["id"], revision=r["revision"])
                for r in db.execute(
                    "SELECT * FROM entities WHERE kind=? ORDER BY updated DESC", (kind,)
                )
            ]

    def create(self, kind, body, *, record=None):
        entity_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?)",
                (entity_id, kind, 1, json.dumps(body), time.time()),
            )
            result = dict(body, id=entity_id, revision=1)
            if record:
                record(db, result)
        return result

    def update(self, entity_id, kind, revision, changes, *, record=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind=?", (entity_id, kind)
            ).fetchone()
            if row is None:
                raise KeyError(entity_id)
            if row["revision"] != revision:
                raise ValueError("Changed on another device. Reload before saving.")
            body = dict(json.loads(row["body"]), **changes)
            db.execute(
                "UPDATE entities SET revision=?,body=?,updated=? WHERE id=?",
                (revision + 1, json.dumps(body), time.time(), entity_id),
            )
            result = dict(body, id=entity_id, revision=revision + 1)
            if record:
                record(db, result)
        return result

    def delete(self, entity_id, kind, revision, *, record=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM entities WHERE id=? AND kind=?", (entity_id, kind)
            ).fetchone()
            if row is None:
                raise KeyError(entity_id)
            if row["revision"] != revision:
                raise ValueError("Changed on another device. Reload before removing.")
            db.execute("DELETE FROM entities WHERE id=? AND kind=?", (entity_id, kind))
            if record:
                record(db, {"removed": True})

    def runtime_action(self, action_id, fingerprint, path, body):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO runtime_actions VALUES (?,?,?,?,NULL)",
                (action_id, fingerprint, path, json.dumps(body)),
            )
            row = db.execute(
                "SELECT * FROM runtime_actions WHERE id=?", (action_id,)
            ).fetchone()
            if row["fingerprint"] != fingerprint:
                raise ValueError("Request ID already belongs to a different action")
        return {
            "path": row["path"],
            "body": json.loads(row["body"]),
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def finish_runtime_action(self, action_id, result):
        with self.connect() as db:
            db.execute(
                "UPDATE runtime_actions SET result=? WHERE id=?",
                (json.dumps(result), action_id),
            )

    def existing_runtime_action(self, action_id, fingerprint):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM runtime_actions WHERE id=?", (action_id,)
            ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise ValueError("Request ID already belongs to a different action")
        return {
            "path": row["path"],
            "body": json.loads(row["body"]),
            "result": json.loads(row["result"]) if row["result"] else None,
        }
