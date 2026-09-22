"""Reconcile recent accepted handoffs with native session metadata, read-only."""

import asyncio
import json

from .codex import CodexError

RECENT_HANDOFF_LIMIT = 10


async def include_handoffs(store, bridge, result, *, first_page):
    native = {row["id"]: row for row in result.get("data", [])}
    with store.connect() as db:
        # Decorate the bounded native page even when its handoffs are older
        # than the separate discovery window. This adds no native RPC calls.
        metadata = {}
        ids = list(native)[:100]
        if ids:
            matching = db.execute(
                "SELECT value FROM settings WHERE key GLOB 'coding-handoff:*' "
                "AND json_valid(value) AND json_extract(value,'$.state')='accepted' "
                "AND json_extract(value,'$.threadId') IN ("
                + ",".join("?" for _ in ids)
                + ") ORDER BY json_extract(value,'$.updated') DESC LIMIT 100",
                ids,
            ).fetchall()
            for row in matching:
                saved = json.loads(row[0])
                metadata.setdefault(saved["threadId"], saved)
        native = {
            key: decorate(row, metadata[key]) if key in metadata else row
            for key, row in native.items()
        }
        if not first_page:
            return {**result, "data": list(native.values())}
        rows = db.execute(
            "SELECT value FROM settings WHERE key GLOB 'coding-handoff:*' "
            "AND json_valid(value) AND json_extract(value,'$.state')='accepted' "
            "AND json_type(value,'$.threadId')='text' "
            "ORDER BY json_extract(value,'$.updated') DESC LIMIT ?",
            (RECENT_HANDOFF_LIMIT + 1,),
        ).fetchall()
    saved = [json.loads(row[0]) for row in rows[:RECENT_HANDOFF_LIMIT]]
    semaphore = asyncio.Semaphore(4)

    async def resolve(item):
        thread_id = item["threadId"]
        if thread_id in native:
            return None, False
        try:
            async with semaphore:
                async with asyncio.timeout(2):
                    response = await bridge.request(
                        "thread/read", {"threadId": thread_id, "includeTurns": False}
                    )
            thread = response.get("thread")
            if not isinstance(thread, dict) or thread.get("id") != thread_id:
                return None, True
            return decorate(thread, item), False
        except CodexError as error:
            # Native absence is safe to omit. Other failures must stay visible
            # as incomplete discovery, never fabricated available sessions.
            missing = any(
                text in str(error).lower()
                for text in ("not found", "does not exist", "no such thread")
            )
            return None, not missing
        except TimeoutError:
            return None, True

    resolved = await asyncio.gather(*(resolve(item) for item in saved))
    merged = {row["id"]: row for row, _ in resolved if row is not None}
    merged.update(native)
    return {
        **result,
        "data": list(merged.values()),
        "leamHandoffsUnavailable": any(unavailable for _, unavailable in resolved),
        "leamHandoffsTruncated": len(rows) > RECENT_HANDOFF_LIMIT,
    }


def decorate(thread, handoff):
    """Native names remain authoritative; task provenance is separate display data."""
    title = str(handoff.get("title") or "Coding handoff")[:240]
    return {
        **thread,
        "name": thread.get("name") or title,
        "leamHandoffId": handoff.get("id"),
        "leamHandoffTitle": title,
        "leamSourceThreadId": handoff.get("sourceThreadId"),
        "leamOrigin": "reviewed-companion-handoff",
    }


def visible_threads(result):
    """Filter only explicit native internal markers; never infer from text/title."""
    rows = {}
    hidden = 0
    for row in result.get("data", []):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        source = row.get("source")
        internal_source = (
            isinstance(source, dict) and ("subAgent" in source or "subagent" in source)
        ) or (isinstance(source, str) and source.startswith("subAgent"))
        if (
            row.get("ephemeral") is True
            or row.get("threadSource") in ("ambient_suggestions", "subagent")
            or row.get("parentThreadId")
            or internal_source
        ):
            hidden += 1
            continue
        rows[row["id"]] = row
    return {**result, "data": list(rows.values()), "leamFilteredCount": hidden}


def purpose_metadata(store, result):
    """Existing exact-ID Leam receipts confer purpose, never inferred intention."""
    ids = [row["id"] for row in result.get("data", [])]
    linked = set()
    if ids:
        with store.connect() as db:
            rows = db.execute(
                "SELECT value FROM settings WHERE key GLOB 'ticket-chat:*' "
                "AND json_valid(value) AND json_extract(value,'$.threadId') IN ("
                + ",".join("?" for _ in ids)
                + ")",
                ids,
            ).fetchall()
        linked = {json.loads(row[0])["threadId"] for row in rows}
    main = store.get("coding-main") or {}
    decorated = []
    for row in result.get("data", []):
        item = dict(row)
        if item.get("transport") == "ide-owner":
            item.update(
                leamPurpose="shared",
                leamDeleteProtected=True,
                leamDeleteReason="The shared coding owner is protected from deletion.",
            )
        elif item["id"] in linked:
            item.update(
                leamPurpose="update",
                leamDeleteProtected=True,
                leamDeleteReason="This conversation is linked to a build ticket and is protected from deletion here.",
            )
        elif item.get("leamOrigin") == "reviewed-companion-handoff":
            item["leamPurpose"] = "handoff"
        elif item.get("originator") == "leam":
            # Creation client is known, but a human/QA/internal purpose is not.
            item["leamPurpose"] = "leam-origin"
        if item["id"] == main.get("threadId"):
            item.update(
                leamMain=True,
                leamDeleteProtected=True,
                leamDeleteReason="Main coordinates integration and deployment. Select another Main before deleting it.",
            )
        decorated.append(item)
    return {**result, "data": decorated}
