"""Operator-only evidence snapshots and explicit backlog deltas; zero inference."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from pydantic import Field, StrictBool, ValidationError

from .backlog import (
    STALE_AFTER,
    Backlog,
    FeatureId,
    Reconciliation,
    Removal,
    ReviewedAssessment,
)
from .commitments import Input

PREFIX = "backlog:evidence-snapshot:"
ACCEPTED = "backlog:evidence-accepted"
MAX_SNAPSHOTS = 32


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def setting(db, key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(db, key, value):
    db.execute(
        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, encoded(value)),
    )


def prune(db, keep=None):
    pointer = setting(db, ACCEPTED) or {}
    protected = {PREFIX + key for key in (pointer.get("id"), keep) if key}
    rows = [
        row
        for row in db.execute(
            "SELECT key FROM settings WHERE key GLOB 'backlog:evidence-snapshot:*' ORDER BY json_extract(value,'$.capturedAt') DESC,key DESC"
        )
        if row[0] not in protected
    ]
    for row in rows[MAX_SNAPSHOTS - len(protected) :]:
        db.execute("DELETE FROM settings WHERE key=?", (row[0],))


def collect(backlog, db):
    """One caller-owned transaction. Never export assessment prose or test content."""
    deployed = backlog.deployed(db)
    rows = {
        row["feature"]: row for row in db.execute("SELECT * FROM backlog_assessments")
    }
    visible = {key: row for key, row in rows.items() if key not in deployed}
    updates = {}
    if db.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='deployment_updates' AND type='table'"
    ).fetchone():
        for row in db.execute(
            "SELECT feature,id,deployment_id,revision,sequence,body FROM deployment_updates WHERE superseded=0 ORDER BY sequence"
        ):
            body = json.loads(row["body"])
            updates[row["feature"]] = {
                "id": row["id"],
                "deploymentId": row["deployment_id"],
                "revision": row["revision"],
                "sequence": row["sequence"],
                "qa": body.get("qa", {}).get("state"),
                "uat": body.get("uat", {}).get("state"),
            }
    assignments = {}
    for row in db.execute(
        "SELECT json_object('feature',json_extract(value,'$.feature'),'revision',json_extract(value,'$.revision'),'operation',json_extract(value,'$.operation'),'requestId',json_extract(value,'$.requestId'),'worker',json_extract(value,'$.worker'),'owner',json_extract(value,'$.owner')) FROM settings WHERE key GLOB 'backlog:assignment-receipt:*'"
    ):
        item = json.loads(row[0])
        old = assignments.get(item["feature"])
        if old is None or item["revision"] > old["revision"]:
            assignments[item["feature"]] = item
    items = {}
    for feature, row in visible.items():
        body = json.loads(row["body"])
        dependencies = {}
        for key in body.get("dependencies", []):
            other = rows.get(key)
            dependencies[key] = {
                "deployment": updates.get(key),
                "revision": other["revision"] if other else None,
                "assessmentHash": digest(json.loads(other["body"])) if other else None,
            }
        facts = {
            "revision": row["revision"],
            "assessmentAt": row["assessed"],
            "assessmentHash": digest(body),
            "assignment": assignments.get(feature),
            "dependencies": dependencies,
            "deliveryState": body.get("deliveryState", "queued"),
            "percent": body["percent"],
            "worker": body.get("worker"),
            "owner": body.get("owner"),
        }
        items[feature] = {**facts, "hash": digest(facts)}
    tombstones = [
        [row[0], digest(json.loads(row[1]))]
        for row in db.execute(
            "SELECT key,value FROM settings WHERE key GLOB 'backlog:deleted:*' OR key GLOB 'backlog:user-fields:*' ORDER BY key"
        )
    ]
    order = backlog.ordering(db)
    ordered = backlog.ranked([backlog.item(row) for row in visible.values()], order)
    metadata = {
        "orderRevision": order["revision"],
        "features": [item["feature"] for item in ordered],
        "userIntentHash": digest(tombstones),
    }
    return {"features": items, "metadata": metadata, "hash": digest([items, metadata])}


def compare(base, current):
    prior = base["features"] if base else {}
    ids = sorted(set(prior) | set(current["features"]))
    changes = []
    for feature in ids:
        old, new = prior.get(feature), current["features"].get(feature)
        if old is None or new is None or old["hash"] != new["hash"]:
            changes.append(
                {
                    "feature": feature,
                    "kind": "removed_or_deployed"
                    if new is None
                    else "new_or_unreviewed"
                    if old is None
                    else "changed",
                    "before": old,
                    "after": new,
                }
            )
    metadata_changed = base is None or base["metadata"] != current["metadata"]
    return {
        "changes": changes,
        "changedFeatures": [row["feature"] for row in changes],
        "unchangedCount": len(current["features"])
        - sum(row["after"] is not None for row in changes),
        "metadataChanged": metadata_changed,
        "metadata": current["metadata"] if metadata_changed else None,
    }


class DeltaRequest(Input):
    requestId: UUID
    snapshotId: UUID
    trigger: str = "heartbeat"
    assessments: list[ReviewedAssessment] = Field(default_factory=list, max_length=500)
    removals: list[Removal] = Field(default_factory=list, max_length=500)
    acknowledge: dict[FeatureId, str] = Field(default_factory=dict, max_length=1000)
    acknowledgeMetadata: StrictBool = False


class EvidenceReview(Reconciliation):
    snapshotId: UUID
    unchanged: dict[FeatureId, int] = Field(default_factory=dict, max_length=500)
    acknowledge: dict[FeatureId, str] = Field(default_factory=dict, max_length=1000)
    acknowledgeMetadata: StrictBool = False
    deltaFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_review(backlog, db, review):
    saved = setting(db, PREFIX + str(review.snapshotId))
    if saved is None:
        raise ValueError("Evidence snapshot not found")
    pointer = setting(db, ACCEPTED)
    if saved["baselineId"] != (pointer or {}).get("id"):
        raise ValueError("Accepted evidence changed; take a fresh snapshot")
    current = collect(backlog, db)
    if current["hash"] != saved["evidence"]["hash"]:
        raise ValueError(
            "Source evidence changed; take a fresh snapshot before deciding"
        )
    base = setting(db, PREFIX + saved["baselineId"]) if saved["baselineId"] else None
    delta = compare(base["evidence"] if base else None, current)
    changed = set(delta["changedFeatures"])
    explicit = {item.feature for item in review.assessments} | {
        item.feature for item in review.removals
    }
    if changed - explicit - set(review.acknowledge):
        raise ValueError(
            "Every changed evidence item needs an explicit assessment, removal or acknowledgement"
        )
    if set(review.acknowledge) - changed or set(review.acknowledge) & explicit:
        raise ValueError("Acknowledgements must uniquely cover changed evidence")
    if any(
        not reason.strip() or len(reason) > 1000
        for reason in review.acknowledge.values()
    ):
        raise ValueError("Acknowledgement reasons must contain 1–1000 characters")
    if delta["metadataChanged"] and not review.acknowledgeMetadata:
        raise ValueError(
            "Canonical order/user-intent metadata changed; acknowledge it explicitly"
        )
    expected = {key: value["revision"] for key, value in current["features"].items()}
    if review.expectedRevisions != expected or any(
        expected.get(key) != rev for key, rev in review.unchanged.items()
    ):
        raise ValueError("Evidence revision vector changed")
    return delta


def record_checked(backlog, db, review, receipt):
    evidence = collect(backlog, db)
    identity = str(review.requestId)
    # A checked baseline is never an arbitrary newly captured snapshot.
    saved = {
        "id": identity,
        "capturedAt": backlog.clock(),
        "baselineId": str(review.snapshotId),
        "evidence": evidence,
        "accepted": True,
    }
    if len(encoded(saved).encode()) > 2 * 1024 * 1024:
        raise ValueError("Accepted evidence exceeds 2 MiB bound")
    prior = setting(db, PREFIX + identity)
    if prior is not None:
        raise ValueError("Review request ID collides with an existing snapshot")
    put(db, PREFIX + identity, saved)
    put(db, ACCEPTED, {"id": identity, "hash": evidence["hash"]})
    prune(db)
    receipt.update(
        deltaFingerprint=review.deltaFingerprint,
        schemaVersion=4,
        reviewMode="evidence_delta",
        factsCheckedAt=backlog.clock(),
        evidenceHash=evidence["hash"],
        snapshotId=str(review.snapshotId),
        acceptedSnapshotId=identity,
        unchangedFeatures=sorted(review.unchanged),
        acknowledgedFeatures=sorted(review.acknowledge),
        semanticAssessmentCount=len(review.assessments),
        acknowledgements=review.acknowledge,
    )


def current_check(backlog, db, receipt):
    return bool(
        receipt
        and receipt.get("reviewMode") == "evidence_delta"
        and backlog.clock() - receipt["factsCheckedAt"] <= STALE_AFTER
        and collect(backlog, db)["hash"] == receipt["evidenceHash"]
    )


class BacklogEvidence:
    def __init__(self, backlog):
        self.backlog, self.store = backlog, backlog.store

    def snapshot(self, identity):
        identity = str(UUID(str(identity)))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = setting(db, PREFIX + identity)
            if existing:
                return self.summary(existing)
            pointer = setting(db, ACCEPTED)
            value = {
                "id": identity,
                "capturedAt": self.backlog.clock(),
                "baselineId": (pointer or {}).get("id"),
                "evidence": collect(self.backlog, db),
                "accepted": False,
            }
            if len(encoded(value).encode()) > 2 * 1024 * 1024:
                raise ValueError("Evidence snapshot exceeds 2 MiB bound")
            put(db, PREFIX + identity, value)
            prune(db, keep=identity)
            return self.summary(value)

    @staticmethod
    def summary(value):
        return {
            "snapshotId": value["id"],
            "capturedAt": value["capturedAt"],
            "baselineId": value["baselineId"],
            "hash": value["evidence"]["hash"],
            "featureCount": len(value["evidence"]["features"]),
            "accepted": value["accepted"],
        }

    def delta(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            value = setting(db, PREFIX + str(identity))
            if value is None:
                raise ValueError("Evidence snapshot not found")
            prior = (
                setting(db, PREFIX + value["baselineId"])
                if value["baselineId"]
                else None
            )
            current = collect(self.backlog, db)
            result = compare(prior["evidence"] if prior else None, value["evidence"])
            pointer = setting(db, ACCEPTED)
            return {
                **self.summary(value),
                **result,
                "sourceStillCurrent": current["hash"] == value["evidence"]["hash"],
                "baselineStillCurrent": value["baselineId"]
                == (pointer or {}).get("id"),
                "tokenSavings": None,
            }

    @staticmethod
    def receipt(receipt):
        return {
            key: receipt[key]
            for key in (
                "requestId",
                "schemaVersion",
                "factsCheckedAt",
                "reviewedCount",
                "semanticAssessmentCount",
                "unchangedFeatures",
                "acknowledgedFeatures",
                "revisions",
                "evidenceHash",
                "acceptedSnapshotId",
            )
        }

    def prepare(self, request, *, dry_run):
        fingerprint = digest(
            {
                "input": request.model_dump(mode="json"),
                "assessmentFields": [
                    sorted(item.model_fields_set) for item in request.assessments
                ],
            }
        )
        with self.store.connect() as db:
            db.execute("BEGIN")
            prior = setting(db, "backlog:review:" + str(request.requestId))
            if prior:
                if prior.get("deltaFingerprint") != fingerprint:
                    raise ValueError(
                        "Review request ID reused with different decisions"
                    )
                return (
                    {
                        "dryRun": True,
                        "alreadyApplied": True,
                        "mutated": False,
                        "receipt": self.receipt(prior),
                    }
                    if dry_run
                    else self.receipt(prior)
                )
            saved = setting(db, PREFIX + str(request.snapshotId))
            if saved is None:
                raise ValueError("Evidence snapshot not found")
            expected = {
                key: value["revision"]
                for key, value in saved["evidence"]["features"].items()
            }
            explicit = {item.feature for item in request.assessments} | {
                item.feature for item in request.removals
            }
            review = EvidenceReview(
                deltaFingerprint=fingerprint,
                requestId=request.requestId,
                snapshotId=request.snapshotId,
                trigger=request.trigger,
                assessments=request.assessments,
                removals=request.removals,
                acknowledge=request.acknowledge,
                acknowledgeMetadata=request.acknowledgeMetadata,
                expectedRevisions=expected,
                unchanged={
                    key: revision
                    for key, revision in expected.items()
                    if key not in explicit
                },
            )
            delta = validate_review(self.backlog, db, review)
        if dry_run:
            return {
                "dryRun": True,
                "requestId": str(request.requestId),
                "snapshotId": str(request.snapshotId),
                "assessmentCount": len(request.assessments),
                "removalCount": len(request.removals),
                "mechanicallyPreservedCount": len(review.unchanged),
                "changedEvidence": delta,
                "alreadyApplied": False,
                "mutated": False,
            }
        receipt = self.backlog.reconcile(review)
        return self.receipt(receipt)

    def status(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            receipt = setting(db, "backlog:last-review")
            return {
                "current": current_check(self.backlog, db, receipt),
                "factsCheckedAt": (receipt or {}).get("factsCheckedAt"),
                "semanticAssessmentCount": (receipt or {}).get(
                    "semanticAssessmentCount"
                ),
                "scope": "Recorded evidence only; worker liveness and unobserved source changes are not proven",
            }


class ExistingStore:
    """Only the existing SQLite connection interface, without Store bootstrap writes."""

    def __init__(self, directory):
        self.path = directory / "leam.sqlite3"
        if not self.path.is_file():
            raise ValueError("Existing Leam database is required")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot")
    snap.add_argument("--request-id", type=UUID, required=True)
    delta = commands.add_parser("delta")
    delta.add_argument("--snapshot", type=UUID, required=True)
    for name in ("dry-run", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
    commands.add_parser("status").add_argument("--require-current", action="store_true")
    args = parser.parse_args()
    try:
        service = BacklogEvidence(
            Backlog(ExistingStore(args.data_dir.resolve()), initialize=False)
        )
        if args.command == "snapshot":
            result = service.snapshot(args.request_id)
        elif args.command == "delta":
            result = service.delta(args.snapshot)
        elif args.command == "status":
            result = service.status()
        else:
            if args.input.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Delta input exceeds 2 MiB")
            request = DeltaRequest.model_validate_json(args.input.read_text())
            result = service.prepare(request, dry_run=args.command == "dry-run")
        print(encoded(result))
        if args.command == "status" and args.require_current and not result["current"]:
            parser.exit(1, "Recorded evidence needs review\n")
    except ValidationError:
        parser.exit(
            2, "Invalid structured delta input; inspect schema and field bounds\n"
        )
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
