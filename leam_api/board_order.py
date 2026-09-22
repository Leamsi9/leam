"""Canonical card order metadata; never a copy of cards or a lifecycle operation."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ORDERED_CARDS = 1000


class MoveCard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    requestId: UUID
    capacityId: str | None = Field(default=None, min_length=1, max_length=100)
    revision: int = Field(ge=0, strict=True)
    membershipToken: str = Field(pattern=r"^[a-f0-9]{64}$")
    cardId: str = Field(min_length=1, max_length=100)
    beforeId: str | None = Field(default=None, min_length=1, max_length=100)
    lane: Literal["todo", "in_progress", "blocked", "completed", "paused"] | None = None

    @model_validator(mode="after")
    def distinct(self):
        if self.beforeId == self.cardId:
            raise ValueError("Choose another card as the destination")
        return self


def scope(capacity_id):
    return capacity_id or "unassigned"


def lane(card):
    return (
        card["status"]
        if card["status"] in ("completed", "paused")
        else card.get("stage", "todo")
    )


def projection(db, capacity_id, cards):
    members = sorted(
        (card for card in cards if card.get("capacityId") == capacity_id),
        key=lambda c: (c["title"].casefold(), c["id"]),
    )
    ids = [card["id"] for card in members]
    row = db.execute(
        "SELECT value FROM settings WHERE key=?",
        ("board-order:v1:" + scope(capacity_id),),
    ).fetchone()
    saved = json.loads(row[0]) if row else {"revision": 0, "ids": []}
    membership = [
        [card["id"], card["revision"]]
        for card in sorted(members, key=lambda c: c["id"])
    ]
    token = hashlib.sha256(
        json.dumps(membership, separators=(",", ":")).encode()
    ).hexdigest()
    selected = set(ids)
    ordered = [key for key in saved["ids"] if key in selected]
    existing = set(ordered)
    ordered.extend(key for key in ids if key not in existing)
    return {
        "capacityId": capacity_id,
        "revision": saved["revision"],
        "membershipToken": token,
        "ids": ordered,
        "canReorder": len(ids) <= MAX_ORDERED_CARDS,
    }


def read_orders(db, cards):
    groups = {}
    for card in cards:
        groups.setdefault(card.get("capacityId"), []).append(card)
    return {scope(key): projection(db, key, members) for key, members in groups.items()}


def move(store, body):
    request = body.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(request, sort_keys=True).encode()
    ).hexdigest()
    receipt_key = "board-order:" + str(body.requestId)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        receipt = db.execute(
            "SELECT * FROM requests WHERE id=?", (receipt_key,)
        ).fetchone()
        if receipt:
            if receipt["fingerprint"] != fingerprint:
                raise ValueError("Move ID reused with different contents")
            return json.loads(receipt["result"])
        if (
            body.capacityId
            and not db.execute(
                "SELECT 1 FROM entities WHERE kind='capacity' AND id=?",
                (body.capacityId,),
            ).fetchone()
        ):
            raise KeyError("Capacity no longer exists")
        cards = [
            dict(json.loads(row["body"]), id=row["id"], revision=row["revision"])
            for row in db.execute("SELECT * FROM entities WHERE kind='commitment'")
        ]
        current = projection(db, body.capacityId, cards)
        if not current["canReorder"]:
            raise ValueError("This capacity exceeds the 1000-card ordering limit")
        if (
            current["revision"] != body.revision
            or current["membershipToken"] != body.membershipToken
        ):
            raise ValueError("Cards or order changed. Refresh before moving again.")
        members = {
            card["id"]: card
            for card in cards
            if card.get("capacityId") == body.capacityId
        }
        if body.cardId not in members or (
            body.beforeId is not None and body.beforeId not in members
        ):
            raise ValueError("Source and destination must belong to this capacity")
        if body.lane is not None and (
            lane(members[body.cardId]) != body.lane
            or (body.beforeId is not None and lane(members[body.beforeId]) != body.lane)
        ):
            raise ValueError("Reordering cannot move a card to another status column")
        ordered = [key for key in current["ids"] if key != body.cardId]
        if body.beforeId is not None:
            index = ordered.index(body.beforeId)
        elif body.lane is not None:
            peers = [
                i for i, key in enumerate(ordered) if lane(members[key]) == body.lane
            ]
            index = peers[-1] + 1 if peers else current["ids"].index(body.cardId)
        else:
            index = len(ordered)
        ordered.insert(index, body.cardId)
        result = {**current, "revision": current["revision"] + 1, "ids": ordered}
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (
                "board-order:v1:" + scope(body.capacityId),
                json.dumps({"revision": result["revision"], "ids": ordered}),
            ),
        )
        db.execute(
            "INSERT INTO requests VALUES (?,?,?,?)",
            (receipt_key, fingerprint, "complete", json.dumps(result)),
        )
        return result
