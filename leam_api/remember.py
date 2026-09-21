"""Previewed, provenance-preserving Remember imports; never reads the source app DB."""

import hashlib
import json
import math
import sqlite3
import time
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from .commitments import Capacity, Commitment, Input


class Import(Input):
    sourceId: str = Field(min_length=1, max_length=100)
    timezone: str = "Europe/London"
    state: dict


class Apply(Import):
    previewDigest: str = Field(max_length=64)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def stable(source, kind, key):
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["leam-remember", source, kind, key]))
    )


class Remember:
    def __init__(self, store):
        self.store = store

    def compile(self, body: Import):
        raw = body.state
        if raw.get("version") != 1:
            raise ValueError("This importer supports Remember state version 1")
        records = []
        capacities = {}
        objectives = {}
        for capacity in raw.get("capacities", []):
            old = capacity["id"]
            if not isinstance(old, str) or not old or old in capacities:
                raise ValueError("Capacity IDs must be unique nonempty strings")
            key = stable(body.sourceId, "capacity", old)
            capacities[old] = key
            mapped = Capacity(
                name=capacity["name"],
                note=capacity.get("note", ""),
                record=capacity.get("record", ""),
            ).model_dump()
            records.append(
                dict(kind="capacity", original=old, id=key, body=mapped, raw=capacity)
            )
        for objective in raw.get("objectives", []):
            old = objective["id"]
            if not isinstance(old, str) or not old or old in objectives:
                raise ValueError("Objective IDs must be unique nonempty strings")
            if objective.get("capacityId") not in capacities:
                raise ValueError(
                    "Every objective must refer to a capacity in this export"
                )
            for field in ["snoozes", "lastReminder"]:
                metadata = objective.get(field, {})
                if not isinstance(metadata, dict):
                    raise ValueError(f"{field} must map dates to timestamps")
                for reminder_day, stamp in metadata.items():
                    if (
                        date.fromisoformat(reminder_day).isoformat() != reminder_day
                        or type(stamp) not in [int, float]
                        or not math.isfinite(stamp)
                        or stamp < 0
                    ):
                        raise ValueError(f"Invalid {field} date or timestamp")
            key = stable(body.sourceId, "commitment", old)
            mapped = Commitment(
                title=objective["name"],
                kind="habit",
                measure=objective["measure"],
                target=objective["target"],
                capacityId=capacities[objective["capacityId"]],
                startDate=objective.get("startDate"),
                endDate=objective.get("endDate"),
                reminderTime=objective.get("time") or None,
                timezone=body.timezone,
                reward=objective.get("reward", ""),
                notes=objective.get("notes", ""),
            ).model_dump(mode="json")
            objectives[old] = (key, mapped)
            records.append(
                dict(
                    kind="commitment", original=old, id=key, body=mapped, raw=objective
                )
            )
        for day, logs in raw.get("dailyLogs", {}).items():
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError("Log dates must use YYYY-MM-DD")
            for old, log in logs.items():
                if old not in objectives:
                    raise ValueError("Daily log references an unknown objective")
                key, obj = objectives[old]
                value = log.get("value", 0)
                done = log.get("done", False)
                before = log.get("preDoneValue")
                if (
                    type(value) not in [int, float]
                    or value < 0
                    or value > 1000000
                    or type(done) is not bool
                ):
                    raise ValueError(
                        "Daily log has an invalid measurement or completion flag"
                    )
                if before is not None and (
                    type(before) not in [int, float] or before < 0 or before > 1000000
                ):
                    raise ValueError("Daily log has an invalid undo measurement")
                mapped = {
                    "date": day,
                    "value": value,
                    "done": done,
                    "preDoneValue": before,
                    "target": obj["target"],
                    "measure": obj["measure"],
                    "revision": 1,
                    "updated": float(log.get("ts", 0)) / 1000,
                }
                records.append(
                    dict(
                        kind="log",
                        original=old + "/" + day,
                        id=key,
                        body=mapped,
                        raw=log,
                    )
                )
        # This validates all preserved unknown fields too: no NaN/Infinity data loss.
        fingerprint = digest(
            {"sourceId": body.sourceId, "timezone": body.timezone, "state": raw}
        )
        return records, fingerprint

    def preview(self, body):
        records, fingerprint = self.compile(body)
        conflicts = []
        new = 0
        with self.store.connect() as db:
            for record in records:
                existing = db.execute(
                    "SELECT fingerprint FROM import_records WHERE source_id=? AND kind=? AND original_id=?",
                    (body.sourceId, record["kind"], record["original"]),
                ).fetchone()
                if existing and existing["fingerprint"] != digest(
                    {"raw": record["raw"], "body": record["body"]}
                ):
                    conflicts.append(
                        {"kind": record["kind"], "sourceId": record["original"]}
                    )
                if not existing:
                    new += 1
        return {
            "digest": fingerprint,
            "counts": {
                "capacities": sum(r["kind"] == "capacity" for r in records),
                "commitments": sum(r["kind"] == "commitment" for r in records),
                "logs": sum(r["kind"] == "log" for r in records),
            },
            "newRecords": new,
            "conflicts": conflicts,
            "timezone": body.timezone,
            "notes": [
                "Original fields and reminder metadata are preserved in the source archive.",
                "Existing imported records are kept; changed source records require conflict resolution.",
            ],
        }

    def apply(self, body: Apply):
        preview = self.preview(body)
        if preview["digest"] != body.previewDigest:
            raise RuntimeError("The data changed after preview. Preview it again.")
        if preview["conflicts"]:
            raise RuntimeError(
                "Some source records changed. Resolve conflicts before importing; existing Leam records were not overwritten."
            )
        key = preview["digest"]
        with self.store.connect() as db:
            previous = db.execute(
                "SELECT result FROM import_batches WHERE id=?", (key,)
            ).fetchone()
        if previous:
            return json.loads(previous["result"])
        records, _ = self.compile(body)
        backup_dir = self.store.path.parent / "backups"
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        backup_name = "before-remember-" + str(uuid.uuid4()) + ".sqlite3"
        backup_path = backup_dir / backup_name
        with self.store.connect() as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        backup_path.chmod(0o600)
        created = 0
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for record in records:
                existing = db.execute(
                    "SELECT fingerprint FROM import_records WHERE source_id=? AND kind=? AND original_id=?",
                    (body.sourceId, record["kind"], record["original"]),
                ).fetchone()
                if existing:
                    if existing["fingerprint"] != digest(
                        {"raw": record["raw"], "body": record["body"]}
                    ):
                        raise RuntimeError(
                            "Source changed during import; nothing was applied"
                        )
                    continue
                if record["kind"] == "log":
                    db.execute(
                        "INSERT INTO daily_logs VALUES (?,?,?,?)",
                        (
                            record["id"],
                            record["body"]["date"],
                            1,
                            json.dumps(record["body"]),
                        ),
                    )
                else:
                    db.execute(
                        "INSERT INTO entities VALUES (?,?,?,?,?)",
                        (
                            record["id"],
                            record["kind"],
                            1,
                            json.dumps(record["body"]),
                            time.time(),
                        ),
                    )
                db.execute(
                    "INSERT INTO import_records VALUES (?,?,?,?,?,?)",
                    (
                        body.sourceId,
                        record["kind"],
                        record["original"],
                        record["id"],
                        digest({"raw": record["raw"], "body": record["body"]}),
                        json.dumps(record["raw"]),
                    ),
                )
                created += 1
            result = {
                "id": key,
                "backup": backup_name,
                "created": created,
                "counts": preview["counts"],
            }
            db.execute(
                "INSERT INTO import_batches VALUES (?,?,?,?,?,?)",
                (
                    key,
                    body.sourceId,
                    key,
                    json.dumps(body.model_dump(exclude={"previewDigest"})),
                    json.dumps(result),
                    time.time(),
                ),
            )
        return result


def router(store):
    routes = APIRouter(prefix="/api/imports")
    service = Remember(store)

    def call(fn, body):
        try:
            return fn(body)
        except (
            ValidationError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as error:
            raise HTTPException(422, "Invalid Remember export: " + str(error))
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except sqlite3.IntegrityError:
            raise HTTPException(
                409, "Import conflicts with existing data. Nothing was applied."
            )

    @routes.post("/remember/preview")
    async def preview(body: Import):
        return call(service.preview, body)

    @routes.post("/remember")
    async def apply(body: Apply):
        return call(service.apply, body)

    @routes.get("")
    async def batches():
        with store.connect() as db:
            return {
                "items": [
                    dict(
                        json.loads(r["result"]),
                        sourceId=r["source_id"],
                        createdAt=r["created"],
                    )
                    for r in db.execute(
                        "SELECT * FROM import_batches ORDER BY created DESC"
                    )
                ]
            }

    @routes.get("/{key}/source")
    async def original(key: str):
        with store.connect() as db:
            row = db.execute(
                "SELECT body FROM import_batches WHERE id=?", (key,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Import not found")
        return json.loads(row["body"])

    return routes
