"""Durable item-to-Companion bindings; canonical state remains in domain records."""

import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from .conversation_titles import ConversationTitles

ITEM_CONTEXT_BYTES = 2048


def _entity(db, kind, item_id):
    row = db.execute(
        "SELECT * FROM entities WHERE kind=? AND id=?", (kind, item_id)
    ).fetchone()
    return (
        {
            **json.loads(row["body"]),
            "id": row["id"],
            "revision": row["revision"],
            "dataAsOf": row["updated"],
        }
        if row
        else None
    )


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


def item_context(store, thread_id):
    """Never trust browser-supplied facts or infer a binding from a thread title."""
    binding = store.get("companion-item:" + thread_id)
    if not binding:
        return None
    kind, item_id = binding["kind"], binding["itemId"]
    with store.connect() as db:
        db.execute("BEGIN")
        item = _entity(db, kind, item_id)
        result = {
            "source": f"leam:{kind}/{item_id}",
            "kind": kind,
            "itemId": item_id,
            "observedAt": time.time(),
        }
        if not item:
            return {
                **result,
                "state": "source_unavailable",
                "note": "The linked item was removed. Conversation history is retained; do not infer current values or recreate the item.",
            }
        result.update(state="current", item=item, truncatedFields=[])
        if kind == "capacity":
            rows = db.execute(
                "SELECT id,revision,body FROM entities WHERE kind='commitment' AND json_extract(body,'$.capacityId')=? ORDER BY updated DESC LIMIT 7",
                (item_id,),
            ).fetchall()
            result["relatedCommitments"] = [
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    "title": json.loads(row["body"]).get("title"),
                    "status": json.loads(row["body"]).get("status"),
                }
                for row in rows[:6]
            ]
            result["relatedPartial"] = len(rows) > 6
            result["relatedCount"] = db.execute(
                "SELECT COUNT(*) FROM entities WHERE kind='commitment' AND json_extract(body,'$.capacityId')=?",
                (item_id,),
            ).fetchone()[0]
        else:
            day = (
                datetime.now(ZoneInfo(item.get("timezone", "Europe/London")))
                .date()
                .isoformat()
            )
            row = db.execute(
                "SELECT body,revision FROM daily_logs WHERE commitment_id=? AND day=?",
                (item_id, day),
            ).fetchone()
            result["todayProgress"] = {
                "date": day,
                **(json.loads(row["body"]) if row else {"value": 0, "done": False}),
                "revision": row["revision"] if row else 0,
            }
            if item.get("capacityId"):
                capacity = _entity(db, "capacity", item["capacityId"])
                if capacity:
                    result["capacity"] = {
                        k: capacity[k] for k in ["id", "name", "revision"]
                    }
    # Shrink data fields, never serialized JSON fragments; escaping and metadata
    # count against the same small budget. The full record remains tool-readable.
    for key in ["notes", "note", "record", "reward"]:
        if key in item and len(_encoded(item[key])) > 256:
            while len(_encoded(item[key])) > 256:
                item[key] = item[key][: max(0, len(item[key]) // 2)]
            result["truncatedFields"].append(key)
    while len(_encoded(result)) > ITEM_CONTEXT_BYTES:
        related = result.get("relatedCommitments", [])
        if related:
            related.pop()
            result["relatedPartial"] = True
            continue
        strings = [
            (len(_encoded(v)), k)
            for k, v in item.items()
            if isinstance(v, str)
            and k not in ["id", "kind", "status", "measure", "timezone"]
            and v
        ]
        if not strings:
            # Even extreme metadata cannot force an oversized reference.
            return {
                k: result[k]
                for k in ["source", "kind", "itemId", "observedAt", "state"]
            } | {"revision": item["revision"], "partial": True}
        _, key = max(strings)
        item[key] = item[key][: len(item[key]) // 2]
        if key not in result["truncatedFields"]:
            result["truncatedFields"].append(key)
    return result


class ItemChats:
    def __init__(self, store, runtime):
        self.store, self.runtime = store, runtime

    def inspect(self, kind, item_id, *, reserve=False):
        key = f"item-chat:{kind}:{item_id}"
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE" if reserve else "BEGIN")
            item = _entity(db, kind, item_id)
            if not item:
                raise HTTPException(404, "This item is no longer available")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            saved = json.loads(row[0]) if row else None
            if saved is None and reserve:
                saved = {"actionId": str(uuid.uuid4()), "threadId": None}
                db.execute(
                    "INSERT INTO settings VALUES (?,?)", (key, json.dumps(saved))
                )
        return item, saved

    def get(self, kind, item_id):
        item, saved = self.inspect(kind, item_id)
        return {
            "threadId": saved.get("threadId") if saved else None,
            "title": item.get("title", item.get("name")),
            "kind": kind,
            "itemId": item_id,
        }

    async def ensure(self, kind, item_id):
        item, saved = self.inspect(kind, item_id, reserve=True)
        if saved["threadId"]:
            return self.get(kind, item_id)
        result = await self.runtime.request(
            "POST", "/threads", {"client_action_id": saved["actionId"]}
        )
        thread_id = result.get("thread", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise HTTPException(
                502, "Companion did not return a conversation identifier"
            )
        # Native thread creation is idempotent per authenticated action ID.
        # A concurrent request/restart therefore converges without orphaning a run.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            key = f"item-chat:{kind}:{item_id}"
            latest = json.loads(
                db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[
                    0
                ]
            )
            if latest.get("threadId") and latest["threadId"] != thread_id:
                raise HTTPException(
                    409, "Conversation binding changed; reload before continuing"
                )
            db.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (json.dumps({**latest, "threadId": thread_id}), key),
            )
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                (
                    "companion-item:" + thread_id,
                    json.dumps({"kind": kind, "itemId": item_id}),
                ),
            )
            title_key = ConversationTitles.key(thread_id)
            title = str(item.get("title", item.get("name", "Item")))[:100]
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                (
                    title_key,
                    json.dumps(
                        {"title": f"{kind.capitalize()}: {title}", "revision": 1}
                    ),
                ),
            )
        return self.get(kind, item_id)


def router(chats):
    routes = APIRouter(prefix="/api")

    def handlers(selected_kind):
        async def get_item_chat(item_id: str):
            return chats.get(selected_kind, item_id)

        async def ensure_item_chat(item_id: str):
            return await chats.ensure(selected_kind, item_id)

        return get_item_chat, ensure_item_chat

    for plural, kind in [("commitments", "commitment"), ("capacities", "capacity")]:
        get_handler, ensure_handler = handlers(kind)
        routes.add_api_route(
            f"/{plural}/{{item_id}}/chat", get_handler, methods=["GET"]
        )
        routes.add_api_route(
            f"/{plural}/{{item_id}}/chat", ensure_handler, methods=["POST"]
        )
    return routes
