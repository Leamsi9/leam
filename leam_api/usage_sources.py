"""Bounded, read-only native Codex import. Raw source content never enters analytics."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import time
from datetime import datetime
from pathlib import Path

from .usage_codex_trace import SCHEMA_VERSION, fresh_state, init_codex_trace, observe

MAX_CHUNK = 2 * 1024 * 1024
MAX_FILES = 8


class CodexUsageImporter:
    def __init__(self, service):
        self.service = service
        self.home = service.codex_home
        digest_key = service.store.get("usage.codexTraceKey", None)
        if not isinstance(digest_key, str):
            digest_key = secrets.token_hex(32)
            service.store.set("usage.codexTraceKey", digest_key)
        self.key = bytes.fromhex(digest_key)
        with service.store.connect() as db:
            init_codex_trace(db)

    def step(self):
        started = time.monotonic()
        imported = 0
        pending = 0
        bad = 0
        seen = 0
        visited = 0
        index = self.home / "state_5.sqlite"
        if not index.is_file():
            self.service.store.set(
                "usage.source",
                {
                    "status": "unavailable",
                    "details": "Native Codex usage index unavailable. Existing measurements retained.",
                },
            )
            return
        # Installed schema is capability-checked; never open the native database writable.
        with sqlite3.connect(
            index.as_uri() + "?mode=ro", uri=True, timeout=1
        ) as native:
            native.execute("PRAGMA query_only=ON")
            native.row_factory = sqlite3.Row
            columns = {r[1] for r in native.execute("PRAGMA table_info(threads)")}
            if not {"id", "rollout_path"} <= columns:
                raise ValueError("Unsupported native usage index")
            ordering = "updated_at DESC,id" if "updated_at" in columns else "id"
            files = native.execute(
                f"SELECT id,rollout_path FROM threads ORDER BY {ordering} LIMIT 10000"
            ).fetchall()
            tables = {
                r[0]
                for r in native.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            edges = (
                native.execute(
                    "SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges LIMIT 10000"
                ).fetchall()
                if "thread_spawn_edges" in tables
                else []
            )
        with self.service.store.connect() as db:
            for row in files:
                db.execute(
                    "INSERT OR IGNORE INTO usage_threads(id) VALUES (?)", (row["id"],)
                )
            for parent, child in edges:
                if parent != child:
                    db.execute(
                        "UPDATE usage_threads SET parent=? WHERE id=?", (parent, child)
                    )
            self.service.link_descendants(db)
            cursors = {
                r["thread_id"]: dict(r) for r in db.execute("SELECT * FROM usage_files")
            }
            states = {
                r["thread_id"]: json.loads(r["state"])
                for r in db.execute("SELECT * FROM usage_codex_scan")
            }
        # Reserve two recent threads every pass, while historical scans retain a quota.
        hot = files[:2]
        hot_ids = {r["id"] for r in hot}
        history = sorted(
            (r for r in files if r["id"] not in hot_ids),
            key=lambda r: cursors.get(r["id"], {}).get("checked", 0),
        )
        files = [*hot, *history]
        for row in files:
            if seen >= MAX_FILES or time.monotonic() - started > 2:
                break
            visited += 1
            path = Path(row["rollout_path"])
            thread = row["id"]
            try:
                resolved = path.resolve(strict=True)
                if not any(
                    resolved.is_relative_to(self.home / name)
                    for name in ["sessions", "archived_sessions"]
                ):
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or Path(f"/proc/self/fd/{source.fileno()}").resolve()
                        != resolved
                    ):
                        continue
                    identity = f"{info.st_dev}:{info.st_ino}"
                    cursor = cursors.get(thread, {})
                    offset = cursor.get("offset", 0)
                    model = cursor.get("model", "unknown")
                    turn = cursor.get("turn_id")
                    skipping = cursor.get("skipping", 0)
                    state = states.get(thread, fresh_state())
                    source.seek(max(0, offset - 128))
                    anchor = hmac.new(
                        self.key, source.read(min(offset, 128)), hashlib.sha256
                    ).hexdigest()
                    changed = state.get("anchor") is not None and anchor != state.get(
                        "anchor"
                    )
                    source.seek(0)
                    prefix = hmac.new(
                        self.key,
                        source.read(state.get("prefixLength", 0)),
                        hashlib.sha256,
                    ).hexdigest()
                    changed = changed or (
                        state.get("prefix") is not None and prefix != state["prefix"]
                    )
                    if (
                        cursor.get("file_identity") != identity
                        or offset > info.st_size
                        or state.get("version") != SCHEMA_VERSION
                        or thread not in states
                        or changed
                    ):
                        offset = 0
                        model = "unknown"
                        turn = None
                        skipping = 0
                        state = fresh_state()
                    if offset == info.st_size:
                        # Update fairness without reading immutable history again.
                        with self.service.store.connect() as db:
                            db.execute(
                                "INSERT INTO usage_files VALUES (?,?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET checked=excluded.checked",
                                (
                                    thread,
                                    identity,
                                    offset,
                                    model,
                                    turn,
                                    skipping,
                                    time.time(),
                                ),
                            )
                            state["complete"] = not skipping
                            db.execute(
                                "INSERT INTO usage_codex_scan VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state",
                                (thread, json.dumps(state, separators=(",", ":"))),
                            )
                            states[thread] = state
                        continue
                    seen += 1
                    source.seek(offset)
                    chunk_start = offset
                    chunk = source.read(MAX_CHUNK)
                    end = chunk.rfind(b"\n")
                    if end < 0:
                        if len(chunk) == MAX_CHUNK:
                            offset += len(chunk)
                            if not skipping:
                                state["oversized"] += 1
                            skipping = 1
                        # Leave trailing partial line to be completed by the native writer.
                        lines = []
                    else:
                        lines = chunk[:end].split(b"\n")
                        offset += end + 1
                        if skipping:
                            chunk_start += len(lines[0]) + 1
                            lines = lines[1:]
                            skipping = 0
                    with self.service.store.connect() as db:
                        sequence = chunk_start
                        for line in lines:
                            line_start = sequence
                            sequence += len(line) + 1
                            # Parse bounded native structure; persist only allowlisted metadata.
                            if not any(
                                marker in line[:256]
                                for marker in [
                                    b'"token_usage_record"',
                                    b'"turn_context"',
                                    b'"session_meta"',
                                    b'"thread_settings_applied"',
                                    b'"response_item"',
                                    b'"event_msg"',
                                    b'"compacted"',
                                ]
                            ):
                                continue
                            try:
                                record = json.loads(line)
                                if not isinstance(record, dict):
                                    raise ValueError("Invalid native record")
                                payload = record.get("payload", {})
                                kind = record.get("type")
                                if not isinstance(payload, dict):
                                    raise ValueError("Invalid native payload")
                                if (
                                    kind == "event_msg"
                                    and payload.get("type") == "thread_settings_applied"
                                ):
                                    kind = "thread_settings_applied"
                                if kind == "session_meta":
                                    if payload.get("id") != thread:
                                        continue
                                    parent = payload.get("parent_thread_id")
                                    if (
                                        isinstance(parent, str)
                                        and 0 < len(parent) <= 128
                                        and parent != thread
                                    ):
                                        db.execute(
                                            "UPDATE usage_threads SET parent=COALESCE(parent,?) WHERE id=?",
                                            (parent, thread),
                                        )
                                elif kind in (
                                    "turn_context",
                                    "thread_settings_applied",
                                ):
                                    context = payload.get("thread_settings", payload)
                                    if not isinstance(context, dict):
                                        raise ValueError("Invalid native settings")
                                    if payload.get("thread_id", thread) != thread:
                                        continue
                                    candidate = context.get("model")
                                    if (
                                        isinstance(candidate, str)
                                        and 0 < len(candidate) <= 128
                                    ):
                                        model = candidate
                                    candidate = payload.get("turn_id")
                                    if (
                                        isinstance(candidate, str)
                                        and len(candidate) <= 128
                                    ):
                                        turn = candidate
                                elif (
                                    kind == "token_usage_record"
                                    and payload.get("thread_id") == thread
                                ):
                                    stamp = record["timestamp"]
                                    if not isinstance(stamp, str):
                                        raise ValueError("Invalid native timestamp")
                                    at = datetime.fromisoformat(
                                        stamp.replace("Z", "+00:00")
                                    )
                                    if at.tzinfo is None:
                                        raise ValueError("Timestamp has no zone")
                                    imported += self.service.ingest(
                                        db,
                                        response_id=payload.get("response_id"),
                                        thread_id=thread,
                                        turn_id=payload.get("turn_id") or turn,
                                        model=(
                                            model
                                            if payload.get("turn_id") == turn
                                            else "unknown"
                                        ),
                                        timestamp=at.timestamp(),
                                        usage=payload["usage"],
                                    )
                                observe(db, thread, record, line_start, state, self.key)
                            except (ValueError, TypeError, KeyError, OverflowError):
                                bad += 1
                        source.seek(max(0, offset - 128))
                        state["anchor"] = hmac.new(
                            self.key, source.read(min(offset, 128)), hashlib.sha256
                        ).hexdigest()
                        state["prefixLength"] = min(offset, 128)
                        source.seek(0)
                        state["prefix"] = hmac.new(
                            self.key, source.read(state["prefixLength"]), hashlib.sha256
                        ).hexdigest()
                        state["complete"] = offset == info.st_size and not skipping
                        db.execute(
                            "INSERT INTO usage_codex_scan VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state",
                            (thread, json.dumps(state, separators=(",", ":"))),
                        )
                        states[thread] = state
                        db.execute(
                            "INSERT INTO usage_files VALUES (?,?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET file_identity=excluded.file_identity,offset=excluded.offset,model=excluded.model,turn_id=excluded.turn_id,skipping=excluded.skipping,checked=excluded.checked",
                            (
                                thread,
                                identity,
                                offset,
                                model,
                                turn,
                                skipping,
                                time.time(),
                            ),
                        )
                        self.service.link_descendants(db)
                    if offset < info.st_size:
                        pending += 1
            except (OSError, ValueError):
                bad += 1
        completed = sum(
            bool(states.get(row["id"], {}).get("complete")) for row in files
        )
        previous = self.service.store.get("usage.source", {})
        issues = previous.get("issues", 0) + bad
        self.service.store.set(
            "usage.source",
            {
                "status": (
                    "partial"
                    if issues or pending or completed < len(files)
                    else "ready"
                ),
                "lastSuccess": time.time(),
                "issues": issues,
                "lastBatchResponses": imported,
                "indexedThreads": len(files),
                "scannedThreads": len(states),
                "completedThreads": completed,
                "discoveryCapped": len(files) == 10000,
                "lastBatchMilliseconds": round((time.monotonic() - started) * 1000),
                "details": f"Incremental own-thread response accounting. {imported} new responses in last batch; {issues} skipped invalid/unavailable records. Historical coverage and missing/deleted sessions are not guaranteed. No cumulative snapshots added.",
            },
        )
