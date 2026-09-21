"""Deterministic model-only views. Canonical records and browser views stay intact."""

import json
import re

from fastapi import HTTPException

SEARCH_BYTES = 8192
GROUNDING_BYTES = 4096


def size(value):
    # The ASCII representation also bounds JSON serializers which escape Unicode.
    return len(json.dumps(value, ensure_ascii=True).encode("utf-8"))


def model_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def model_size(value):
    # IronClaw retains the complete MCP result, not just the text block. Count
    # that envelope and its second JSON escaping layer as part of admission.
    envelope = {
        "content": [{"type": "text", "text": model_text(value)}],
        "isError": False,
    }
    return max(size(value), size(envelope))


def bounded_text(text, budget):
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if size(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def snippet(text, query):
    query = query[:500]
    terms = [query] + list(dict.fromkeys(re.findall(r"\w{3,}", query)))[:32]
    found = next(
        (
            match
            for term in terms
            if term
            for match in [re.search(re.escape(term), text, re.IGNORECASE)]
            if match
        ),
        None,
    )
    # Center on evidence before clipping; long Unicode prefixes cannot crowd it out.
    start = max(0, found.start() - 12) if found else 0
    value = bounded_text(text[start:], 512)
    return value, start


def compact(item, query):
    if item.get("recordType", "memory") != "memory":
        return item
    text, start = snippet(item.get("text", ""), query)
    result = {
        key: item[key]
        for key in (
            "id",
            "revision",
            "recordType",
            "observedAt",
            "dataAsOf",
            "availability",
            "sourceType",
        )
        if key in item
    }
    result.update(
        text=text,
        source=bounded_text(item.get("source", ""), 256),
        category=bounded_text(item.get("category", ""), 128),
    )
    result.update(
        textOffset=start,
        totalTextCharacters=len(item.get("text", "")),
        partial=(
            text != item.get("text", "")
            or result["source"] != item.get("source", "")
            or result["category"] != item.get("category", "")
        ),
    )
    return result


def page(result, query, offset=0, budget=SEARCH_BYTES, measure=model_size):
    candidates = result["items"]
    output = {
        **result,
        "items": [],
        "partial": bool(result.get("truncated")),
        "byteBudget": budget,
    }
    # The overview duplicates the project row and can itself contain long titles.
    # Model callers read that row from items; browser overview stays unchanged.
    if "project" in output:
        output["project"] = None
    for raw in candidates:
        item = compact(raw, query)
        # Other domains retain existing shape when possible; a too-large record
        # receives an explicit partial pointer so pagination always makes progress.
        if measure({**output, "items": [item]}) > budget:
            item = {
                key: raw[key] for key in ("id", "revision", "recordType") if key in raw
            }
            item.update(
                partial=True,
                detail="Record exceeds model page budget; inspect the corresponding Leam view.",
            )
        trial = {
            **output,
            "items": [*output["items"], item],
            "nextOffset": max(1000, offset + len(output["items"]) + 1),
            "partial": False,
        }
        if measure(trial) > budget:
            break
        output["items"].append(item)
    consumed = len(output["items"])
    output["nextOffset"] = (
        offset + consumed if consumed < len(candidates) else result.get("nextOffset")
    )
    output["partial"] = bool(
        output["partial"]
        or output["nextOffset"] is not None
        or any(i.get("partial") for i in output["items"])
    )
    return output


def grounding(memories, query):
    query = query[:500]
    terms = list(dict.fromkeys(t.casefold() for t in re.findall(r"\w{3,}", query)))[:32]
    ranked = sorted(
        memories, key=lambda item: -sum(t in item["text"].casefold() for t in terms)
    )
    result = page(
        {
            "items": ranked,
            "total": len(ranked),
            "referenceData": True,
            "retrieval": "Use leam_context to search full current records or read exact ID details.",
            "nextOffset": None,
        },
        query,
        budget=GROUNDING_BYTES,
        measure=size,
    )
    # Ranked automatic grounding has no stable public paging order.
    result.pop("nextOffset")
    return result


def detail(store, body):
    if body.kind != "memory":
        raise HTTPException(422, "Exact record detail requires kind=memory")
    with store.connect() as db:
        row = db.execute(
            "SELECT * FROM entities WHERE kind='memory' AND id=?", (body.id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Memory no longer exists")
    if body.revision is not None and body.revision != row["revision"]:
        raise HTTPException(
            409, "Memory changed; restart detail with its current revision"
        )
    if body.cursor and body.revision is None:
        raise HTTPException(422, "Continuation requires the initial revision")
    record = {
        **json.loads(row["body"]),
        "id": row["id"],
        "revision": row["revision"],
        "dataAsOf": row["updated"],
    }
    serialized = json.dumps(record, ensure_ascii=False)
    if body.cursor > len(serialized):
        raise HTTPException(422, "Detail cursor exceeds record length")
    output = {
        "id": row["id"],
        "revision": row["revision"],
        "recordJson": "",
        "cursor": body.cursor,
        "nextCursor": None,
        "totalCharacters": len(serialized),
        "partial": True,
        "referenceData": True,
        "byteBudget": SEARCH_BYTES,
    }
    # Account for the actual outer JSON escaping of the complete fragment.
    remaining = serialized[body.cursor :]
    low, high = 0, len(remaining)
    while low < high:
        middle = (low + high + 1) // 2
        trial = {
            **output,
            "recordJson": remaining[:middle],
            "nextCursor": max(1000, body.cursor + middle),
            "partial": False,
        }
        if model_size(trial) <= SEARCH_BYTES:
            low = middle
        else:
            high = middle - 1
    output["recordJson"] = remaining[:low]
    output["nextCursor"] = body.cursor + low if low < len(remaining) else None
    output["partial"] = body.cursor != 0 or output["nextCursor"] is not None
    return output
