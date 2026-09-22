"""Explicit operator-only assignment of today's recorded activity date.

Preview first; apply the exact preview request. No historical transition is invented.
"""

import argparse
import hashlib
import json
import time
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import Field

from .commitments import Input
from .completion_evidence import subtask_map, task_state
from .store import Store

PREFIX = "activity:backfill:"
SOURCE = "user_requested_backfill"
MAX_CARDS = 2000


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Apply(Input):
    requestId: UUID
    day: date
    timezone: str = Field(min_length=1, max_length=100)
    snapshotHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expectedRevisions: dict[str, int] = Field(max_length=MAX_CARDS)


def selection(db):
    result = []
    rows = db.execute("SELECT * FROM entities WHERE kind='commitment' ORDER BY id")
    for row in rows:
        card = json.loads(row["body"])
        records = []
        status = task_state(card)
        if status in {"completed", "in_progress"}:
            records.append(
                {"subtaskId": None, "status": status, "owner": card.get("owner")}
            )
        for key, child in sorted(subtask_map(card).items()):
            if child.get("status") in {"completed", "in_progress"}:
                records.append(
                    {
                        "subtaskId": key,
                        "status": child["status"],
                        "owner": child.get("owner"),
                    }
                )
        if records:
            result.append(
                {"id": row["id"], "revision": row["revision"], "records": records}
            )
            if len(result) > MAX_CARDS:
                raise ValueError("Activity backfill exceeds 2000-card limit")
    return result


class ActivityBackfill:
    def __init__(self, store, clock=None):
        self.store, self.clock = store, clock or time.time

    def preview(self, day, timezone, request_id=None):
        day = date.fromisoformat(str(day))
        stamp = self.clock()
        if datetime.fromtimestamp(stamp, ZoneInfo(timezone)).date() != day:
            raise ValueError("Backfill day must be today in the selected timezone")
        with self.store.connect() as db:
            db.execute("BEGIN")
            targets = selection(db)
        request = Apply(
            requestId=request_id or uuid4(),
            day=day,
            timezone=timezone,
            snapshotHash=digest(targets),
            expectedRevisions={x["id"]: x["revision"] for x in targets},
        )
        return {
            "request": request.model_dump(mode="json"),
            "targets": targets,
            "cardCount": len(targets),
            "recordCount": sum(len(x["records"]) for x in targets),
        }

    def apply(self, request):
        fingerprint = digest(request.model_dump(mode="json"))
        key = PREFIX + str(request.requestId)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if prior:
                saved = json.loads(prior[0])
                if saved["fingerprint"] != fingerprint:
                    raise ValueError("Backfill request ID reused with different input")
                return saved["receipt"]
            stamp = self.clock()
            if (
                datetime.fromtimestamp(stamp, ZoneInfo(request.timezone)).date()
                != request.day
            ):
                raise ValueError("Backfill day must be today in the selected timezone")
            targets = selection(db)
            revisions = {x["id"]: x["revision"] for x in targets}
            if (
                revisions != request.expectedRevisions
                or digest(targets) != request.snapshotHash
            ):
                raise ValueError("Activity records changed; review a fresh preview")
            for target in targets:
                row = db.execute(
                    "SELECT body FROM entities WHERE id=?", (target["id"],)
                ).fetchone()
                card = json.loads(row[0])
                previous = {
                    field: card.get(field)
                    for field in ("statusChangedAt", "completedAt")
                }
                if any(record["subtaskId"] is None for record in target["records"]):
                    card["statusChangedAt"] = stamp
                    if task_state(card) == "completed":
                        card["completedAt"] = stamp
                changed = db.execute(
                    "UPDATE entities SET body=?,revision=revision+1,updated=? WHERE id=? AND revision=?",
                    (json.dumps(card), stamp, target["id"], target["revision"]),
                )
                if changed.rowcount != 1:
                    raise ValueError("Activity record changed during backfill")
                event = {
                    **target,
                    "revision": target["revision"] + 1,
                    "source": SOURCE,
                    "requestId": str(request.requestId),
                    "day": str(request.day),
                    "timezone": request.timezone,
                    "previousTimestamps": previous,
                }
                db.execute(
                    "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                    ("commitment.activity_backfill", json.dumps(event), stamp),
                )
            receipt = {
                "requestId": str(request.requestId),
                "source": SOURCE,
                "day": str(request.day),
                "timezone": request.timezone,
                "recordedAt": stamp,
                "cardCount": len(targets),
                "recordCount": sum(len(x["records"]) for x in targets),
                "revisions": {x["id"]: x["revision"] + 1 for x in targets},
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?)",
                (key, encoded({"fingerprint": fingerprint, "receipt": receipt})),
            )
            return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview")
    preview.add_argument("--day", required=True)
    preview.add_argument("--timezone", default="Europe/London")
    preview.add_argument("--request-id", type=UUID)
    apply = commands.add_parser("apply")
    apply.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    service = ActivityBackfill(Store(args.data_dir))
    result = (
        service.preview(args.day, args.timezone, args.request_id)
        if args.command == "preview"
        else service.apply(Apply.model_validate_json(args.input.read_text()))
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
