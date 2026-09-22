"""Operator-assessed unfinished work, separate from deployment/acceptance receipts."""

import argparse
import hashlib
import json
import sqlite3
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator, model_validator

from .commitments import Input
from .store import Store

STALE_AFTER = 20 * 60
CONTENT_DEFAULTS = {"rationale": "", "scope": ""}


FeatureId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")]
DELIVERY_DEFAULTS = {
    "paused": False,
    "deliveryState": "queued",
    "rank": None,
    "priority": "normal",
    "nextAction": "",
    "owner": None,
    "worker": None,
    "dependencies": [],
    "subtasks": [],
}
STAGE_ORDER = {
    name: index
    for index, name in enumerate(
        ("handover", "ready", "in_progress", "queued", "blocked")
    )
}
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


class Subtask(Input):
    id: FeatureId
    title: str = Field(min_length=1, max_length=200)
    state: Literal["todo", "in_progress", "done", "blocked"] = "todo"
    blocker: str | None = Field(default=None, min_length=1, max_length=1000)


class Assessment(Input):
    feature: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(default="", max_length=2000)
    scope: str = Field(default="", max_length=4000)
    currentStep: str = Field(min_length=1, max_length=2000)
    percent: int = Field(ge=0, le=99, strict=True)
    blockers: list[str] = Field(default_factory=list, max_length=20)
    assessedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    paused: bool = Field(default=False, strict=True)
    deliveryState: Literal["queued", "ready", "in_progress", "handover", "blocked"] = (
        "queued"
    )
    rank: int | None = Field(default=None, ge=0, le=1000000, strict=True)
    priority: Literal["high", "normal", "low"] = "normal"
    nextAction: str = Field(default="", max_length=1000)
    owner: OpaqueId | None = None
    worker: OpaqueId | None = None
    dependencies: list[FeatureId] = Field(default_factory=list, max_length=40)
    subtasks: list[Subtask] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_delivery_ids(self):
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Duplicate dependencies")
        if self.feature in self.dependencies:
            raise ValueError("A ticket cannot depend on itself")
        if len({item.id for item in self.subtasks}) != len(self.subtasks):
            raise ValueError("Duplicate subtask IDs")
        return self

    @field_validator("blockers")
    @classmethod
    def bounded_blockers(cls, value):
        if any(not note.strip() or len(note) > 1000 for note in value):
            raise ValueError("Each blocker must contain 1–1000 characters")
        return [note.strip() for note in value]

    @field_validator("assessedAt")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Assessment timestamp must include a timezone")
        if value.timestamp() > time.time() + 60:
            raise ValueError("Cannot record a future assessment")
        return value.astimezone(UTC)


class ReviewedAssessment(Assessment):
    evidence: str = Field(min_length=1, max_length=1500)


class Removal(Input):
    feature: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    reason: str = Field(min_length=1, max_length=1000)


class Reconciliation(Input):
    requestId: UUID
    trigger: Literal["status", "deployment", "completion", "heartbeat", "scope_change"]
    expectedRevisions: dict[str, int] = Field(max_length=500)
    assessments: list[ReviewedAssessment] = Field(max_length=500)
    removals: list[Removal] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def complete_review(self):
        assessed = [item.feature for item in self.assessments]
        removed = [item.feature for item in self.removals]
        if len(set(assessed)) != len(assessed) or len(set(removed)) != len(removed):
            raise ValueError("Duplicate review decisions")
        if set(assessed) & set(removed):
            raise ValueError("Cannot assess and remove the same ticket")
        unchanged = getattr(self, "unchanged", {})
        if set(unchanged) & (set(assessed) | set(removed)) or set(unchanged) - set(self.expectedRevisions):
            raise ValueError("Unchanged evidence must uniquely cover existing tickets")
        if set(self.expectedRevisions) - set(assessed) - set(removed) - set(unchanged):
            raise ValueError(
                "Every existing ticket requires a reviewed assessment or removal"
            )
        if set(removed) - set(self.expectedRevisions):
            raise ValueError("Removal must name an existing reviewed ticket")
        if any(type(x) is not int or x < 1 for x in self.expectedRevisions.values()):
            raise ValueError("Expected revisions must be positive integers")
        return self


class UserOrder(Input):
    requestId: UUID
    revision: int = Field(ge=0, strict=True)
    expectedRevisions: dict[FeatureId, Annotated[int, Field(ge=1, strict=True)]] = (
        Field(max_length=500)
    )
    lane: Literal["all", "queued", "ready", "in_progress", "handover", "blocked"]
    features: list[FeatureId] = Field(min_length=1, max_length=500)

    @field_validator("features")
    @classmethod
    def unique_features(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate ordered feature IDs")
        return value


class Backlog:
    def __init__(self, store, clock=None, *, initialize=True):
        self.store = store
        self.clock = clock or time.time
        if not initialize:
            # Evidence CLI reads an existing installation; even dry-run must not
            # bootstrap schema or rewrite legacy user ordering on construction.
            try:
                with store.connect() as db:
                    db.execute("SELECT key,value FROM settings LIMIT 0")
                    db.execute("SELECT feature,revision,assessed,body FROM backlog_assessments LIMIT 0")
            except sqlite3.Error as error:
                raise ValueError("Initialize the Leam backlog before inspecting its evidence") from error
            return
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS backlog_assessments (feature TEXT PRIMARY KEY, revision INTEGER NOT NULL, assessed REAL NOT NULL, body TEXT NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            self.stabilize_priority(db)

    def stabilize_priority(self, db):
        ordering = self.ordering(db)
        deployed = self.deployed(db)
        items = [self.item(row) for row in db.execute("SELECT * FROM backlog_assessments") if row["feature"] not in deployed]
        self.assign_lanes(items)
        features = [item["feature"] for item in self.ranked(items, ordering)]
        if ordering.get("features") != features:
            ordering["features"] = features
            ordering["lanes"] = {}
            ordering["revision"] += 1
            ordering["updatedAt"] = self.clock()
            db.execute("INSERT INTO settings VALUES ('backlog:user-order',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(ordering),))

    def item(self, row):
        return {
            **CONTENT_DEFAULTS,
            **deepcopy(DELIVERY_DEFAULTS),
            **json.loads(row["body"]),
            "revision": row["revision"],
            "stale": self.clock() - row["assessed"] > STALE_AFTER,
        }

    @staticmethod
    def assessment_body(assessment, old, db=None):
        # Old operators must not silently erase newer delivery decisions.
        body = assessment.model_dump(mode="json", exclude={"evidence"})
        previous = json.loads(old["body"]) if old else {}
        for field in {*DELIVERY_DEFAULTS, *CONTENT_DEFAULTS}:
            if field not in assessment.model_fields_set and field in previous:
                body[field] = previous[field]
        if db is not None:
            from .backlog_edit import preserve_user_fields
            return preserve_user_fields(db, assessment.feature, body)
        return Assessment.model_validate(body).model_dump(mode="json")

    @staticmethod
    def next_revision(db, feature, old):
        # Retain a high-water mark across removal/re-addition (inventory ABA).
        key = "backlog:revision:" + feature
        saved = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        revision = max(old["revision"] if old else 0, int(saved[0]) if saved else 0) + 1
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(revision)),
        )
        return revision

    def upsert(self, assessment):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM backlog_assessments WHERE feature=?",
                (assessment.feature,),
            ).fetchone()
            from .backlog_edit import guard_deleted
            guard_deleted(db, assessment.feature)
            assessed = assessment.assessedAt.timestamp()
            facts = self.assessment_body(assessment, old, db)
            body = json.dumps(facts)
            if old and assessed < old["assessed"]:
                raise ValueError("An older assessment cannot replace a newer heartbeat")
            if old and assessed == old["assessed"]:
                if facts != {**CONTENT_DEFAULTS, **DELIVERY_DEFAULTS, **json.loads(old["body"])}:
                    raise ValueError(
                        "Same assessment timestamp contains different facts"
                    )
                return self.item(old)
            revision = self.next_revision(db, assessment.feature, old)
            if (
                not old
                and db.execute("SELECT count(*) FROM backlog_assessments").fetchone()[0]
                >= 500
            ):
                raise ValueError("Backlog has reached the 500-item limit")
            db.execute(
                "INSERT INTO backlog_assessments VALUES (?,?,?,?) ON CONFLICT(feature) DO UPDATE SET revision=excluded.revision,assessed=excluded.assessed,body=excluded.body",
                (assessment.feature, revision, assessed, body),
            )
            self.stabilize_priority(db)
            return self.item(
                db.execute(
                    "SELECT * FROM backlog_assessments WHERE feature=?",
                    (assessment.feature,),
                ).fetchone()
            )

    @staticmethod
    def deployed(db):
        if not db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='deployment_updates'"
        ).fetchone():
            return set()
        return {
            row[0]
            for row in db.execute("SELECT DISTINCT feature FROM deployment_updates")
        }

    def reconcile(self, review):
        raw = review.model_dump_json()
        # New fingerprints retain omission vs explicit clearing for compatibility
        # writers. Pre-upgrade receipts used the old, fully defaulted schema.
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "request": json.loads(raw),
                    "deliveryFields": [
                        sorted(set(item.model_fields_set) & DELIVERY_DEFAULTS.keys())
                        for item in review.assessments
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        version2_raw = review.model_dump_json(exclude={"assessments": {"__all__": set(CONTENT_DEFAULTS)}})
        version2_fingerprint = hashlib.sha256(json.dumps({
            "request": json.loads(version2_raw),
            "deliveryFields": [sorted(set(item.model_fields_set) & DELIVERY_DEFAULTS.keys()) for item in review.assessments],
        }, sort_keys=True).encode()).hexdigest()
        legacy_raw = review.model_dump_json(
            exclude={"assessments": {"__all__": set(DELIVERY_DEFAULTS) | set(CONTENT_DEFAULTS)}}
        )
        legacy_digest = hashlib.sha256(legacy_raw.encode()).hexdigest()
        legacy_compatible = all(
            item.model_dump(mode="json", include=set(DELIVERY_DEFAULTS))
            == DELIVERY_DEFAULTS
            for item in review.assessments
        )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt_key = "backlog:review:" + str(review.requestId)
            previous = db.execute(
                "SELECT value FROM settings WHERE key=?", (receipt_key,)
            ).fetchone()
            # Preserve the latest pre-upgrade receipt before another review replaces it.
            latest = db.execute(
                "SELECT value FROM settings WHERE key='backlog:last-review'"
            ).fetchone()
            if latest:
                prior = json.loads(latest["value"])
                db.execute(
                    "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                    ("backlog:review:" + prior["requestId"], latest["value"]),
                )
                if prior["requestId"] == str(review.requestId) and previous is None:
                    previous = latest
            receipt = json.loads(previous["value"]) if previous else None
            if receipt and receipt["requestId"] == str(review.requestId):
                expected_digest = (
                    request_fingerprint
                    if receipt.get("schemaVersion") in {3, 4}
                    else version2_fingerprint if receipt.get("schemaVersion") == 2
                    else legacy_digest
                )
                if receipt.get("schemaVersion") not in {3, 4} and any(
                    set(item.model_fields_set) & CONTENT_DEFAULTS.keys() for item in review.assessments
                ):
                    raise ValueError("Review ID reused with new editable fields")
                if receipt["fingerprint"] != expected_digest or (
                    receipt.get("schemaVersion") not in {2, 3, 4} and not legacy_compatible
                ):
                    raise ValueError("Review ID reused with different decisions")
                return receipt
            evidence_mode = hasattr(review, "snapshotId")
            if evidence_mode:
                from .backlog_evidence import validate_review
                validate_review(self, db, review)
            deployed = self.deployed(db)
            rows = {
                row["feature"]: row
                for row in db.execute("SELECT * FROM backlog_assessments")
            }
            visible = {
                key: row["revision"] for key, row in rows.items() if key not in deployed
            }
            if visible != review.expectedRevisions:
                raise ValueError(
                    "Backlog changed; reload the complete inventory before reviewing"
                )
            if any(item.feature in deployed for item in review.assessments):
                raise ValueError(
                    "Deployed increments belong in Updates; review only their remaining scope"
                )
            new_count = (
                len(rows)
                - len(review.removals)
                + sum(item.feature not in rows for item in review.assessments)
            )
            if new_count > 500:
                raise ValueError("Backlog has reached the 500-item limit")
            from .backlog_edit import guard_deleted
            for item in review.assessments:
                guard_deleted(db, item.feature)
                old = rows.get(item.feature)
                if old and item.assessedAt.timestamp() <= old["assessed"]:
                    raise ValueError(
                        "A new full review requires a newer factual assessment"
                    )
            revisions = dict(getattr(review, "unchanged", {}))
            applied = {}
            for item in review.removals:
                self.next_revision(db, item.feature, rows[item.feature])
                db.execute(
                    "DELETE FROM backlog_assessments WHERE feature=?", (item.feature,)
                )
            for item in review.assessments:
                facts = self.assessment_body(item, rows.get(item.feature), db)
                revision = self.next_revision(db, item.feature, rows.get(item.feature))
                db.execute(
                    "INSERT INTO backlog_assessments VALUES (?,?,?,?) ON CONFLICT(feature) DO UPDATE SET revision=excluded.revision,assessed=excluded.assessed,body=excluded.body",
                    (
                        item.feature,
                        revision,
                        item.assessedAt.timestamp(),
                        json.dumps(facts),
                    ),
                )
                revisions[item.feature] = revision
                applied[item.feature] = facts
            self.stabilize_priority(db)
            receipt = {
                "requestId": str(review.requestId),
                "fingerprint": request_fingerprint,
                "schemaVersion": 3,
                "assessments": applied,
                "trigger": review.trigger,
                "reviewedAt": self.clock(),
                "reviewedCount": len(review.assessments),
                "addedCount": sum(
                    item.feature not in rows for item in review.assessments
                ),
                "removed": [item.model_dump() for item in review.removals],
                "revisions": revisions,
                "evidence": {
                    item.feature: item.evidence for item in review.assessments
                },
            }
            if evidence_mode:
                from .backlog_evidence import record_checked
                record_checked(self, db, review, receipt)
            db.execute(
                "INSERT INTO settings VALUES ('backlog:last-review',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(receipt),),
            )
            db.execute(
                "INSERT INTO settings VALUES (?,?)", (receipt_key, json.dumps(receipt))
            )
            return receipt

    @staticmethod
    def assign_lanes(items):
        visible_features = {item["feature"] for item in items}
        for item in items:
            item["deliveryLane"] = (
                "blocked"
                if item["blockers"]
                or visible_features.intersection(item["dependencies"])
                or any(
                    task["state"] == "blocked" or task.get("blocker")
                    for task in item["subtasks"]
                )
                else item["deliveryState"]
            )

    @staticmethod
    def ordering(db):
        row = db.execute(
            "SELECT value FROM settings WHERE key='backlog:user-order'"
        ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {"revision": 0, "lanes": {}, "updatedAt": None}
        )

    @staticmethod
    def ranked(items, ordering):
        """One global build order. Old per-lane preferences seed it once."""
        features = ordering.get("features")
        if features is not None:
            positions = {key: n for n, key in enumerate(features)}
            ordered = sorted(items, key=lambda item: (
                positions.get(item["feature"], 1000001),
                item["rank"] if item["rank"] is not None else 1000001,
                item["feature"],
            ))
        else:
            positions = {
                lane: {key: n for n, key in enumerate(keys)}
                for lane, keys in ordering["lanes"].items()
            }
            ordered = sorted(items, key=lambda item: (
                STAGE_ORDER[item["deliveryLane"]],
                positions.get(item["deliveryLane"], {}).get(item["feature"], 501),
                item["rank"] if item["rank"] is not None else 1000001,
                PRIORITY_ORDER[item["priority"]], item["title"].casefold(), item["feature"],
            ))
        return [{**item, "rank": n + 1} for n, item in enumerate(ordered)]

    def reorder(self, request):
        fingerprint = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        key = "backlog:user-order-receipt:" + str(request.requestId)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if previous:
                receipt = json.loads(previous[0])
                if receipt["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This order request ID has different contents"
                    )
                return receipt
            ordering = self.ordering(db)
            deployed = self.deployed(db)
            items = [
                self.item(row)
                for row in db.execute("SELECT * FROM backlog_assessments")
                if row["feature"] not in deployed
            ]
            self.assign_lanes(items)
            revisions = {item["feature"]: item["revision"] for item in items}
            members = {
                item["feature"]
                for item in items
                if request.lane == "all" or item["deliveryLane"] == request.lane
            }
            if (
                ordering["revision"] != request.revision
                or revisions != request.expectedRevisions
            ):
                raise HTTPException(
                    409, "Backlog changed. Refresh and choose the order again"
                )
            if set(request.features) != members:
                raise HTTPException(
                    409, "Lane membership changed. Refresh and choose the order again"
                )
            # Replace lane members in their existing global slots; a list reorder
            # can change priority across stages without changing workflow state.
            ordered = self.ranked(items, ordering)
            chosen = iter(request.features)
            ordering["features"] = [
                next(chosen) if item["feature"] in members else item["feature"]
                for item in ordered
            ]
            ordering["lanes"] = {}
            ordering["revision"] += 1
            ordering["updatedAt"] = self.clock()
            receipt = {
                "requestId": str(request.requestId),
                "fingerprint": fingerprint,
                "revision": ordering["revision"],
                "updatedAt": ordering["updatedAt"],
            }
            db.execute(
                "INSERT INTO settings VALUES ('backlog:user-order',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(ordering),),
            )
            # Permanent compact receipts prevent old retries from undoing a later order.
            db.execute("INSERT INTO settings VALUES (?,?)", (key, json.dumps(receipt)))
            return receipt

    def list(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            deployed = self.deployed(db)
            items = [
                self.item(row)
                for row in db.execute(
                    "SELECT * FROM backlog_assessments ORDER BY assessed DESC,feature"
                )
                if row["feature"] not in deployed
            ]
            row = db.execute(
                "SELECT value FROM settings WHERE key='backlog:last-review'"
            ).fetchone()
            receipt = json.loads(row["value"]) if row else None
            ordering = self.ordering(db)
            evidence_current = None
            if receipt and receipt.get("reviewMode") == "evidence_delta":
                from .backlog_evidence import current_check
                evidence_current = current_check(self, db, receipt)
        self.assign_lanes(items)
        items = self.ranked(items, ordering)
        revisions = {item["feature"]: item["revision"] for item in items}
        current = bool(
            receipt
            and receipt["revisions"] == revisions
            and self.clock() - receipt["reviewedAt"] <= STALE_AFTER
            and evidence_current is not False
        )
        return {
            "items": items,
            "ordering": {
                "revision": ordering["revision"],
                "updatedAt": ordering["updatedAt"],
                "positions": {},
                "mode": "canonical_rank",
                "features": [item["feature"] for item in items],
            },
            "checkedAt": self.clock(),
            "staleAfterSeconds": STALE_AFTER,
            "estimateBasis": "deployment",
            "heartbeatSeconds": 900,
            "review": {
                "reviewedAt": receipt["reviewedAt"] if receipt else None,
                "current": current,
                "reviewedCount": receipt["reviewedCount"] if receipt else 0,
                "mode": receipt.get("reviewMode", "full") if receipt else None,
                "factsCheckedAt": receipt.get("factsCheckedAt") if receipt else None,
                "unchangedCount": len(receipt.get("unchangedFeatures", [])) if receipt else 0,
            },
        }

    def status(self):
        from .backlog_refill import Staffing
        from .backlog_workload import workload

        result = self.list()
        return {
            "checkedAt": result["checkedAt"],
            "tickets": len(result["items"]),
            "workload": workload(self, result),
            "staffing": Staffing(self).status(),
            "staleTickets": sum(item["stale"] for item in result["items"]),
            "reviewDue": not result["review"]["current"],
            "review": result["review"],
        }


def router(backlog):
    routes = APIRouter(prefix="/api/backlog")

    @routes.get("")
    async def listing():
        return backlog.list()

    @routes.post("/order")
    async def order(request: UserOrder):
        return backlog.reorder(request)

    from .backlog_pause import Pause, apply_pause

    @routes.post("/pause")
    async def pause_queue(request: Pause):
        return apply_pause(backlog, request)

    from .backlog_edit import Delete, Edit, Restore, deleted, mutate

    @routes.get("/deleted")
    async def deleted_tickets():
        return deleted(backlog)

    @routes.post("/{feature}/edit")
    async def edit_ticket(feature: FeatureId, request: Edit):
        return mutate(backlog, feature, "edit", request)

    @routes.post("/{feature}/delete")
    async def delete_ticket(feature: FeatureId, request: Delete):
        return mutate(backlog, feature, "delete", request)

    @routes.post("/{feature}/restore")
    async def restore_ticket(feature: FeatureId, request: Restore):
        return mutate(backlog, feature, "restore", request)

    return routes


def main():
    parser = argparse.ArgumentParser(
        description="Record factual Leam backlog progress; percentages never increase automatically"
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    upsert = commands.add_parser("upsert")
    upsert.add_argument("--input", type=Path, required=True)
    commands.add_parser("list")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--input", type=Path, required=True)
    for operation in ("start", "handoff", "blocked", "failed", "pause"):
        command = commands.add_parser(operation)
        command.add_argument("--feature", required=True)
        command.add_argument("--worker", required=True)
        command.add_argument("--owner", default="main")
        command.add_argument("--expected-revision", type=int, required=True)
        command.add_argument("--request-id", required=True)
        command.add_argument("--note", required=True)
    acknowledge = commands.add_parser("acknowledge-delete")
    acknowledge.add_argument("--feature", required=True)
    acknowledge.add_argument("--revision", type=int, required=True)
    commands.add_parser("staffing-snapshot")
    staffing = commands.add_parser("staffing-check")
    staffing.add_argument("--input", type=Path, required=True)
    invalidate = commands.add_parser("staffing-invalidate")
    invalidate.add_argument("--reason", required=True)
    status = commands.add_parser("status")
    status.add_argument("--require-current", action="store_true")
    args = parser.parse_args()
    if not (args.data_dir / "leam.sqlite3").is_file():
        parser.error("Choose an existing Leam data directory")
    try:
        backlog = Backlog(Store(args.data_dir))
        if args.command in {"start", "handoff", "blocked", "failed", "pause"}:
            from .backlog_assignment import Assignment, transition

            result = transition(backlog, Assignment(
                requestId=args.request_id, feature=args.feature, worker=args.worker,
                owner=args.owner, expectedRevision=args.expected_revision,
                operation=args.command, note=args.note,
            ))
        elif args.command == "upsert":
            with args.input.open() as file:
                raw = file.read(32769)
            if len(raw) > 32768:
                raise ValueError("Assessment file exceeds 32 KiB")
            result = backlog.upsert(Assessment.model_validate_json(raw))
        elif args.command == "reconcile":
            with args.input.open() as file:
                raw = file.read(2 * 1024 * 1024 + 1)
            if len(raw.encode()) > 2 * 1024 * 1024:
                raise ValueError("Review file exceeds 2 MiB")
            result = backlog.reconcile(Reconciliation.model_validate_json(raw))
        elif args.command == "acknowledge-delete":
            from .backlog_edit import acknowledge_delete
            result = acknowledge_delete(backlog, args.feature, args.revision)
        elif args.command.startswith("staffing-"):
            from .backlog_refill import Observation, Staffing

            staffing = Staffing(backlog)
            if args.command == "staffing-snapshot":
                result = staffing.snapshot()
            elif args.command == "staffing-invalidate":
                result = staffing.invalidate(args.reason)
            else:
                with args.input.open("rb") as file:
                    raw = file.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Staffing observation exceeds 64 KiB")
                result = staffing.check(Observation.model_validate_json(raw))
        elif args.command == "status":
            result = backlog.status()
        else:
            result = backlog.list()
    except (OSError, ValueError) as error:
        parser.exit(1, f"Backlog assessment rejected: {error}\n")
    print(json.dumps(result))
    if args.command == "staffing-check" and not result["passed"]:
        parser.exit(2, "Staffing check failed; resolve worker state or dispatch eligible work and recheck\n")
    if args.command == "status" and args.require_current and (result["reviewDue"] or result["staffing"]["checkDue"]):
        parser.exit(
            2, "Current status requires full backlog reconciliation and a fresh passing staffing check\n"
        )


if __name__ == "__main__":
    main()
