"""Deployment receipts: operator QA and authenticated user UAT remain distinct."""

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, field_validator

from .commitments import Input
from .store import Store

State = Literal["pending", "passed", "failed"]


class Publication(Input):
    feature: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=10000)
    deploymentId: str = Field(min_length=1, max_length=256)
    deployedAt: datetime

    @field_validator("deployedAt")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Deployment timestamp must include a timezone")
        if value.timestamp() > time.time() + 60:
            raise ValueError("Cannot publish a future deployment as deployed")
        return value.astimezone(UTC)


class QA(Input):
    expectedRevision: int | None = Field(default=None, ge=1, strict=True)
    deploymentId: str = Field(min_length=1, max_length=256)
    state: State
    details: str = Field(default="", max_length=10000)


class Acceptance(QA):
    revision: int = Field(ge=1)


class Seen(Input):
    sequence: int = Field(ge=0)


class Updates:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS deployment_updates (
                    id TEXT PRIMARY KEY, feature TEXT NOT NULL, deployment_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, sequence INTEGER NOT NULL,
                    superseded INTEGER NOT NULL, body TEXT NOT NULL,
                    UNIQUE(feature,deployment_id)
                );
                CREATE TABLE IF NOT EXISTS deployment_update_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, update_id TEXT NOT NULL,
                    actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deployment_update_seen (
                    id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO deployment_update_seen VALUES (1,0);
            """)

    @staticmethod
    def item(row, seen=0):
        data = json.loads(row["body"])
        acknowledged = max(seen, data.pop("seenSequence", 0))
        superseded = bool(row["superseded"])
        stage = (
            "Fail"
            if data["uat"]["state"] == "failed"
            else "QA"
            if data["qa"]["state"] == "failed"
            else "Complete"
            if data["qa"]["state"] == "passed" and data["uat"]["state"] == "passed"
            else "UAT"
            if data["qa"]["state"] == "passed"
            else "Deployed"
        )
        return {
            **data,
            "stage": stage,
            "awaitingUserInput": data["uat"]["state"] == "failed",
            "id": row["id"],
            "revision": row["revision"],
            "sequence": row["sequence"],
            "unread": not superseded and row["sequence"] > acknowledged,
            "superseded": superseded,
            "completed": not superseded
            and data["qa"]["state"] == "passed"
            and data["uat"]["state"] == "passed",
        }

    @staticmethod
    def rationale(db, feature):
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backlog_assessments'"
        ).fetchone():
            return {}
        row = db.execute(
            "SELECT body FROM backlog_assessments WHERE feature=?", (feature,)
        ).fetchone()
        if not row:
            return {}
        body = json.loads(row["body"])
        return {
            key: body[key]
            for key in ("rationale", "scope")
            if isinstance(body.get(key), str)
        }

    def described_item(self, db, row, seen=0):
        # Backfill the display from the exact feature, without rewriting QA/UAT
        # receipts or manufacturing a reason for legacy tickets without evidence.
        result = {**self.item(row, seen), **self.rationale(db, row["feature"])}
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backlog_assessments'"
        ).fetchone():
            assessment = db.execute(
                "SELECT revision,body FROM backlog_assessments WHERE feature=?",
                (row["feature"],),
            ).fetchone()
            body = json.loads(assessment["body"]) if assessment else {}
            if body.get("worker") and body.get("deliveryState") in {
                "in_progress",
                "blocked",
                "handover",
            }:
                result["activeWork"] = {
                    name: body.get(name)
                    for name in ("deliveryState", "owner", "worker", "currentStep")
                }
                result["activeWork"]["revision"] = assessment["revision"]
        return result

    @staticmethod
    def event(db, key, actor, body):
        return db.execute(
            "INSERT INTO deployment_update_events(update_id,actor,body,created) VALUES (?,?,?,?)",
            (key, actor, json.dumps(body), time.time()),
        ).lastrowid

    def publish(self, publication):
        data = publication.model_dump(mode="json")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM deployment_updates WHERE feature=? AND deployment_id=?",
                (publication.feature, publication.deploymentId),
            ).fetchone()
            if existing:
                saved = json.loads(existing["body"])
                if any(saved[key] != value for key, value in data.items()):
                    raise ValueError(
                        "Deployment receipt already exists with different content"
                    )
                return self.item(existing)
            current = db.execute(
                "SELECT * FROM deployment_updates WHERE feature=? AND superseded=0",
                (publication.feature,),
            ).fetchone()
            if (
                current
                and datetime.fromisoformat(json.loads(current["body"])["deployedAt"])
                >= publication.deployedAt
            ):
                raise ValueError(
                    "A new deployment must be later than the current receipt"
                )
            key = str(uuid.uuid4())
            data.update(self.rationale(db, publication.feature))
            data.update(
                {
                    "qa": {"state": "pending", "details": "", "updatedAt": None},
                    "uat": {"state": "pending", "details": "", "updatedAt": None},
                }
            )
            sequence = self.event(db, key, "operator.publish", data)
            db.execute(
                "UPDATE deployment_updates SET superseded=1 WHERE feature=?",
                (publication.feature,),
            )
            db.execute(
                "INSERT INTO deployment_updates VALUES (?,?,?,?,?,?,?)",
                (
                    key,
                    publication.feature,
                    publication.deploymentId,
                    1,
                    sequence,
                    0,
                    json.dumps(data),
                ),
            )
            return self.item(
                db.execute(
                    "SELECT * FROM deployment_updates WHERE id=?", (key,)
                ).fetchone()
            )

    def change(self, key, review, field):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM deployment_updates WHERE id=?", (key,)
            ).fetchone()
            if not row:
                raise KeyError("Update not found")
            if row["superseded"]:
                raise ValueError(
                    "This deployment was superseded; review the latest update"
                )
            if (
                row["deployment_id"] != review.deploymentId
                or (field == "uat" and row["revision"] != review.revision)
                or (
                    field == "qa"
                    and review.expectedRevision is not None
                    and row["revision"] != review.expectedRevision
                )
            ):
                raise ValueError("Deployment update changed; refresh before reviewing")
            data = json.loads(row["body"])
            if (
                field == "uat"
                and review.state == "passed"
                and data["qa"]["state"] != "passed"
            ):
                raise ValueError("Full QA must pass before user UAT can pass")
            data[field] = {
                "state": review.state,
                "details": review.details,
                "updatedAt": time.time(),
            }
            sequence = self.event(
                db, key, "user.uat" if field == "uat" else "operator.qa", data
            )
            if field == "uat":
                data["seenSequence"] = sequence
            db.execute(
                "UPDATE deployment_updates SET revision=revision+1,sequence=?,body=? WHERE id=?",
                (sequence, json.dumps(data), key),
            )
            return self.item(
                db.execute(
                    "SELECT * FROM deployment_updates WHERE id=?", (key,)
                ).fetchone()
            )

    def qa(self, key, review):
        return self.change(key, review, "qa")

    def uat(self, key, review):
        return self.change(key, review, "uat")

    @staticmethod
    def _status(db):
        sequence = db.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM deployment_update_events"
        ).fetchone()[0]
        seen = db.execute(
            "SELECT sequence FROM deployment_update_seen WHERE id=1"
        ).fetchone()[0]
        unread = db.execute(
            "SELECT count(*) FROM deployment_updates WHERE superseded=0 AND sequence>MAX(?,COALESCE(json_extract(body,'$.seenSequence'),0))",
            (seen,),
        ).fetchone()[0]
        return {"sequence": sequence, "seenSequence": seen, "unreadCount": unread}

    def status(self):
        with self.store.connect() as db:
            return self._status(db)

    def get(self, key):
        with self.store.connect() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM deployment_updates WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError("Update not found")
            return self.described_item(db, row, self._status(db)["seenSequence"])

    def list(self, before=None, limit=50):
        with self.store.connect() as db:
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT * FROM deployment_updates WHERE superseded=0 AND (? IS NULL OR sequence<?) ORDER BY sequence DESC LIMIT ?",
                (before, before, limit + 1),
            ).fetchall()
            status = self._status(db)
            return {
                **status,
                "items": [
                    self.described_item(db, row, status["seenSequence"])
                    for row in rows[:limit]
                ],
                "nextCursor": rows[limit - 1]["sequence"]
                if len(rows) > limit
                else None,
            }

    def seen(self, sequence):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if sequence > self._status(db)["sequence"]:
                raise ValueError("Cannot acknowledge unseen future updates")
            db.execute(
                "UPDATE deployment_update_seen SET sequence=MAX(sequence,?) WHERE id=1",
                (sequence,),
            )
            return self._status(db)

    def seen_item(self, key, sequence):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM deployment_updates WHERE id=?", (key,)
            ).fetchone()
            if not row:
                raise KeyError("Update not found")
            if row["superseded"]:
                raise ValueError("This deployment was superseded")
            if sequence > row["sequence"]:
                raise ValueError("Cannot acknowledge unseen future changes")
            data = json.loads(row["body"])
            data["seenSequence"] = max(data.get("seenSequence", 0), sequence)
            db.execute(
                "UPDATE deployment_updates SET body=? WHERE id=?",
                (json.dumps(data), key),
            )
            return self._status(db)

    def prune_obsolete(self, *, apply=False):
        """Operator-only removal; preserve private events and snapshot before deletion."""
        with self.store.connect() as db:
            candidates = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM deployment_updates WHERE superseded=1 ORDER BY sequence"
                )
            ]
        result = {"candidateIds": candidates, "deleted": 0, "backupId": None}
        if not apply or not candidates:
            return result
        from .backups import Backups

        result["backupId"] = Backups(self.store).create()["id"]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Only delete the selected pre-backup identities. A concurrent publication
            # may obsolete another receipt after the snapshot; preserve that receipt.
            for key in candidates:
                result["deleted"] += db.execute(
                    "DELETE FROM deployment_updates WHERE id=? AND superseded=1",
                    (key,),
                ).rowcount
        return result


def router(updates):
    routes = APIRouter(prefix="/api/updates")

    def call(fn, *args):
        try:
            return fn(*args)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @routes.get("")
    async def listing(
        before: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)
    ):
        return updates.list(before, limit)

    @routes.get("/status")
    async def status():
        return updates.status()

    @routes.get("/{key}")
    async def get(key: uuid.UUID):
        return call(updates.get, str(key))

    @routes.post("/seen")
    async def seen(body: Seen):
        return call(updates.seen, body.sequence)

    @routes.post("/{key}/uat")
    async def uat(key: str, body: Acceptance):
        return call(updates.uat, key, body)

    @routes.post("/{key}/seen")
    async def seen_item(key: str, body: Seen):
        return call(updates.seen_item, key, body.sequence)

    return routes


def read_notes(path):
    with path.open() as file:
        return file.read(10001)


def main():
    parser = argparse.ArgumentParser(
        description="Publish Leam deployment updates or record operator QA; user UAT is browser-only"
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--feature", required=True)
    publish.add_argument("--title", required=True)
    publish.add_argument("--summary-file", type=Path, required=True)
    publish.add_argument("--deployment-id", required=True)
    publish.add_argument("--deployed-at", required=True)
    qa = commands.add_parser("qa")
    qa.add_argument("--id", required=True)
    qa.add_argument("--deployment-id", required=True)
    qa.add_argument("--state", choices=["pending", "passed", "failed"], required=True)
    qa.add_argument("--details-file", type=Path)
    prune = commands.add_parser(
        "prune-obsolete",
        help="Preview obsolete receipts; --apply snapshots then removes them",
    )
    prune.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not (args.data_dir / "leam.sqlite3").is_file():
        parser.error("Choose an existing Leam data directory")
    try:
        updates = Updates(Store(args.data_dir))
        if args.command == "publish":
            result = updates.publish(
                Publication(
                    feature=args.feature,
                    title=args.title,
                    summary=read_notes(args.summary_file),
                    deploymentId=args.deployment_id,
                    deployedAt=args.deployed_at,
                )
            )
        elif args.command == "qa":
            result = updates.qa(
                args.id,
                QA(
                    deploymentId=args.deployment_id,
                    state=args.state,
                    details=read_notes(args.details_file) if args.details_file else "",
                ),
            )
        else:
            result = updates.prune_obsolete(apply=args.apply)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Update rejected: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
