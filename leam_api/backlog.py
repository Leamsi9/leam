"""Operator-assessed unfinished work, separate from deployment/acceptance receipts."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from pydantic import Field, field_validator

from .commitments import Input
from .store import Store

STALE_AFTER = 20 * 60


class Assessment(Input):
    feature: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    currentStep: str = Field(min_length=1, max_length=2000)
    percent: int = Field(ge=0, le=99, strict=True)
    blockers: list[str] = Field(default_factory=list, max_length=20)
    assessedAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
        return value.astimezone(timezone.utc)


class Backlog:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS backlog_assessments (feature TEXT PRIMARY KEY, revision INTEGER NOT NULL, assessed REAL NOT NULL, body TEXT NOT NULL)"
            )

    def item(self, row):
        return {
            **json.loads(row["body"]),
            "revision": row["revision"],
            "stale": self.clock() - row["assessed"] > STALE_AFTER,
        }

    def upsert(self, assessment):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM backlog_assessments WHERE feature=?",
                (assessment.feature,),
            ).fetchone()
            assessed = assessment.assessedAt.timestamp()
            body = assessment.model_dump_json()
            if old and assessed < old["assessed"]:
                raise ValueError("An older assessment cannot replace a newer heartbeat")
            if old and assessed == old["assessed"]:
                if body != old["body"]:
                    raise ValueError(
                        "Same assessment timestamp contains different facts"
                    )
                return self.item(old)
            revision = old["revision"] + 1 if old else 1
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
            return self.item(
                db.execute(
                    "SELECT * FROM backlog_assessments WHERE feature=?",
                    (assessment.feature,),
                ).fetchone()
            )

    def list(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            has_updates = db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='deployment_updates'"
            ).fetchone()
            deployed = (
                {
                    row[0]
                    for row in db.execute(
                        "SELECT DISTINCT feature FROM deployment_updates"
                    )
                }
                if has_updates
                else set()
            )
            items = [
                self.item(row)
                for row in db.execute(
                    "SELECT * FROM backlog_assessments ORDER BY assessed DESC,feature"
                )
                if row["feature"] not in deployed
            ]
        return {
            "items": sorted(
                items,
                key=lambda item: (
                    -item["percent"],
                    item["title"].casefold(),
                    item["feature"],
                ),
            ),
            "checkedAt": self.clock(),
            "staleAfterSeconds": STALE_AFTER,
            "estimateBasis": "deployment",
            "heartbeatSeconds": 900,
        }


def router(backlog):
    routes = APIRouter(prefix="/api/backlog")

    @routes.get("")
    async def listing():
        return backlog.list()

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
    args = parser.parse_args()
    if not (args.data_dir / "leam.sqlite3").is_file():
        parser.error("Choose an existing Leam data directory")
    try:
        backlog = Backlog(Store(args.data_dir))
        if args.command == "upsert":
            with args.input.open() as file:
                raw = file.read(32769)
            if len(raw) > 32768:
                raise ValueError("Assessment file exceeds 32 KiB")
            result = backlog.upsert(Assessment.model_validate_json(raw))
        else:
            result = backlog.list()
    except (OSError, ValueError) as error:
        parser.exit(1, f"Backlog assessment rejected: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
