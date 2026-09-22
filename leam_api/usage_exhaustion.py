"""User-reported subscription milestones; derived counters never rewrite usage."""

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import Field, field_validator

from .commitments import Input

PROVIDER = "chatgpt"
SUBSCRIPTION_SCOPE = "installation-chatgpt"
USAGE_SOURCE = "codex.response"


class ExhaustionEvent(Input):
    requestId: UUID
    provider: Literal["chatgpt"]
    observedAt: datetime
    timezone: str = Field(min_length=1, max_length=100)
    source: Literal["manual", "user_report"] = "manual"
    sourceRef: str | None = Field(default=None, pattern=r"^[A-Za-z0-9:_-]{1,200}$")
    attributeCodexToChatGPT: Literal[True]

    @field_validator("observedAt", mode="before")
    @classmethod
    def timestamp_format(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Use an ISO timestamp with an explicit offset")  # noqa: TRY004 - Pydantic validators require ValueError for HTTP422.
        return value

    @field_validator("observedAt")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Include a timezone offset for the observed timestamp")
        if not 0 < value.timestamp() < time.time() + 300:
            raise ValueError("Choose an observed time, not a future date")
        return value.astimezone(UTC)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone") from None
        return value


class UsageExhaustions:
    def __init__(self, service):
        self.service, self.store = service, service.store
        with self.store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_exhaustions (
              id TEXT PRIMARY KEY, provider TEXT NOT NULL, subscription_scope TEXT NOT NULL,
              observed_at REAL NOT NULL, timezone TEXT NOT NULL, source TEXT NOT NULL,
              source_ref TEXT, usage_source TEXT NOT NULL, created REAL NOT NULL,
              UNIQUE(provider,subscription_scope,usage_source,observed_at));
            CREATE INDEX IF NOT EXISTS usage_exhaustions_period ON usage_exhaustions(provider,subscription_scope,observed_at);
            CREATE TABLE IF NOT EXISTS usage_exhaustion_requests (
              id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
              event_id TEXT NOT NULL REFERENCES usage_exhaustions(id));
            """)

    @staticmethod
    def event(row):
        return {
            "id": row["id"],
            "provider": row["provider"],
            "subscriptionScope": row["subscription_scope"],
            "observedAt": row["observed_at"],
            "timezone": row["timezone"],
            "source": row["source"],
            "sourceRef": row["source_ref"],
            "usageSource": row["usage_source"],
            "attribution": "user_declared",
            "createdAt": row["created"],
        }

    def record(self, body):
        request_id = str(body.requestId)
        fingerprint = hashlib.sha256(
            json.dumps(
                body.model_dump(mode="json", exclude={"requestId"}), sort_keys=True
            ).encode()
        ).hexdigest()
        observed = body.observedAt.timestamp()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute(
                "SELECT * FROM usage_exhaustion_requests WHERE id=?", (request_id,)
            ).fetchone()
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This event request belongs to a different observation"
                    )
                return self.event(
                    db.execute(
                        "SELECT * FROM usage_exhaustions WHERE id=?",
                        (receipt["event_id"],),
                    ).fetchone()
                )
            existing = db.execute(
                "SELECT * FROM usage_exhaustions WHERE provider=? AND subscription_scope=? AND usage_source=? AND observed_at=?",
                (body.provider, SUBSCRIPTION_SCOPE, USAGE_SOURCE, observed),
            ).fetchone()
            if existing is None:
                db.execute(
                    "INSERT INTO usage_exhaustions VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        body.provider,
                        SUBSCRIPTION_SCOPE,
                        observed,
                        body.timezone,
                        body.source,
                        body.sourceRef,
                        USAGE_SOURCE,
                        time.time(),
                    ),
                )
                existing = db.execute(
                    "SELECT * FROM usage_exhaustions WHERE id=?", (request_id,)
                ).fetchone()
            db.execute(
                "INSERT INTO usage_exhaustion_requests VALUES (?,?,?)",
                (request_id, fingerprint, existing["id"]),
            )
            return self.event(existing)

    def period(self, db, event, end):
        conditions = [
            "r.source=?",
            "r.timestamp>=?",
            "NOT EXISTS(SELECT 1 FROM usage_conflicts c WHERE c.id=r.id)",
        ]
        args = [event["usage_source"], event["observed_at"]]
        if end is not None:
            conditions.append("r.timestamp<?")
            args.append(end)
        # Python's integers preserve exact totals beyond JS/SQLite aggregate ranges.
        row = {
            "requests": 0,
            "input": 0,
            "output": 0,
            "cached": 0,
            "reasoning": 0,
            "cacheKnownRequests": 0,
            "reasoningKnown": 0,
        }
        for record in db.execute(
            "SELECT r.input,r.output,r.cached,r.reasoning FROM usage_records r WHERE "
            + " AND ".join(conditions),
            args,
        ):
            row["requests"] += 1
            row["input"] += record["input"]
            row["output"] += record["output"]
            for quantity, known in (
                ("cached", "cacheKnownRequests"),
                ("reasoning", "reasoningKnown"),
            ):
                if record[quantity] is not None:
                    row[quantity] += record[quantity]
                    row[known] += 1
        return {
            "eventId": event["id"],
            "start": event["observed_at"],
            "end": end,
            "timezone": event["timezone"],
            "usageSource": event["usage_source"],
            "attribution": "user_declared",
            "totals": self.service.totals(row),
        }

    def overview(self, provider=PROVIDER, offset=0, limit=50):
        with self.store.connect() as db:
            db.execute("BEGIN")
            total = db.execute(
                "SELECT count(*) FROM usage_exhaustions WHERE provider=? AND subscription_scope=?",
                (provider, SUBSCRIPTION_SCOPE),
            ).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM usage_exhaustions WHERE provider=? AND subscription_scope=? ORDER BY observed_at DESC,id DESC LIMIT ? OFFSET ?",
                (provider, SUBSCRIPTION_SCOPE, limit, offset),
            ).fetchall()
            latest = db.execute(
                "SELECT * FROM usage_exhaustions WHERE provider=? AND subscription_scope=? ORDER BY observed_at DESC,id DESC LIMIT 1",
                (provider, SUBSCRIPTION_SCOPE),
            ).fetchone()
            periods = []
            for event in rows:
                next_row = db.execute(
                    "SELECT MIN(observed_at) FROM usage_exhaustions WHERE provider=? AND subscription_scope=? AND usage_source=? AND observed_at>?",
                    (
                        provider,
                        SUBSCRIPTION_SCOPE,
                        event["usage_source"],
                        event["observed_at"],
                    ),
                ).fetchone()
                periods.append(self.period(db, event, next_row[0]))
            current = self.period(db, latest, None) if latest else None
        return {
            "provider": provider,
            "subscriptionScope": SUBSCRIPTION_SCOPE,
            "events": [self.event(row) for row in rows],
            "periods": periods,
            "current": current,
            "total": total,
            "nextOffset": offset + limit if offset + limit < total else None,
            "coverage": {
                "automaticDetection": "not_connected",
                "attribution": "user_declared",
                "usageSources": [USAGE_SOURCE],
                "details": "Only collected Codex responses explicitly attributed by you to ChatGPT are counted. Historic per-response subscription identity and Companion runtime usage are unavailable. Milestones are observation/report times, not provider-confirmed exhaustion times. Totals are observed usage, not remaining allowance or a reset forecast.",
            },
            "generatedAt": time.time(),
        }
