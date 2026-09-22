"""Bounded metadata-only resource catalog, canonical parent labels and keyset pages."""

import base64
import binascii
import hashlib
import json
import math


def modified_at(value, links):
    # Legacy link records have linkedAt but no timestamp for past removals.
    return max(
        [
            value["publishedAt"],
            links.get("updatedAt", 0),
            *[link.get("linkedAt", 0) for link in links.get("items", [])],
        ]
    )


def listing(
    artifacts, q="", kind=None, cursor=None, limit=30, *, sort="modified", order="desc", group="all"
):
    from .artifacts import (
        FORMATS,
        IMAGE_KINDS,
        MAX_BYTES,
        MAX_RESOURCES,
        MAX_STORED_BYTES,
        metadata,
    )
    from .resource_links import ResourceLinks
    from .resource_reads import states

    if not isinstance(q, str) or len(q) > 200 or not 1 <= limit <= 100:
        raise ValueError("Invalid resource search or page limit")
    if sort not in {"modified", "parent", "title", "type"} or order not in {"asc", "desc"}:
        raise ValueError("Unknown resource ordering")
    if group not in {"all", "generated", "uploads"}:
        raise ValueError("Unknown resource group")
    if kind == "attachment":
        kinds = []
    elif kind == "image":
        kinds = sorted(IMAGE_KINDS)
    elif kind == "document":
        kinds = [k for k in FORMATS if k not in IMAGE_KINDS | {"html"}]
    elif kind in FORMATS:
        kinds = [kind]
    elif kind in (None, ""):
        kinds = []
    else:
        raise ValueError("Unknown resource type filter")
    query_fingerprint = hashlib.sha256(
        json.dumps([q, kind or ""] if group == "all" else [q, kind or "", group]).encode()
    ).hexdigest()[:32]
    boundary = None
    if cursor:
        try:
            if len(cursor) > 16384:
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(cursor))
            if isinstance(decoded, list) and sort == "modified" and order == "desc" and group == "all":
                boundary = decoded  # pre-ordering cursor compatibility
            elif (
                decoded["v"],
                decoded["sort"],
                decoded["order"],
                decoded["filter"],
            ) == (1, sort, order, query_fingerprint):
                boundary = decoded["key"]
            else:
                raise ValueError()
            expected = 6 if sort == "parent" else 3 if sort == "type" else 2
            if not isinstance(boundary, list) or len(boundary) != expected:
                raise ValueError()
            if sort == "modified":
                if (
                    type(boundary[0]) not in (int, float)
                    or not math.isfinite(boundary[0])
                    or not 0 <= boundary[0] < 1e12
                ):
                    raise ValueError()
            elif sort == "parent" and type(boundary[0]) is not bool:
                raise ValueError()
            if any(
                not isinstance(v, str) or len(v) > 1024
                for v in boundary[(1 if sort in {"modified", "parent"} else 0) :]
            ):
                raise ValueError()
            boundary = tuple(boundary)
        except (ValueError, TypeError, KeyError, IndexError, binascii.Error) as error:
            raise ValueError(
                "Invalid resource page cursor for this ordering"
            ) from error
    where, args = ["s.key >= 'artifact:'", "s.key < 'artifact;'"], []
    if kind == "attachment":
        where.append("json_extract(s.value,'$.storage')='attachment'")
    if group == "uploads":
        where.append("json_extract(s.value,'$.storage')='attachment'")
    elif group == "generated":
        where.append("coalesce(json_extract(s.value,'$.storage'),'')!='attachment'")
    if q:
        where.append(
            "(instr(lower(json_extract(s.value,'$.title')),lower(?))>0 OR instr(lower(coalesce(json_extract(s.value,'$.filename'),'')),lower(?))>0)"
        )
        args.extend([q, q])
    if kinds:
        where.append(
            "json_extract(s.value,'$.kind') IN (" + ",".join("?" for _ in kinds) + ")"
        )
        args.extend(kinds)
    with artifacts.store.connect() as db:
        db.execute("BEGIN")
        used = db.execute(
            "SELECT coalesce(sum(json_extract(value,'$.bytes')),0) FROM settings WHERE key >= 'artifact:' AND key < 'artifact;'"
        ).fetchone()[0]
        rows = db.execute(
            "SELECT json_remove(s.value,'$.content','$.contentBase64') metadata,l.value links FROM settings s LEFT JOIN settings l ON l.key='resource-links:' || substr(s.key,10) WHERE "
            + " AND ".join(where)
            + " LIMIT ?",
            args + [MAX_RESOURCES + 1000 + 1],
        ).fetchall()
        if len(rows) > MAX_RESOURCES + 1000:
            raise ValueError("Resource catalog exceeds supported inventory")
        reads, targets, items = states(db), {}, []
        for row in rows:
            item = metadata(json.loads(row["metadata"]))
            links = json.loads(row["links"]) if row["links"] else {}
            item.update(
                modifiedAt=modified_at(item, links),
                unread=item["id"] not in reads,
                readAt=reads.get(item["id"]),
            )
            if sort == "parent":
                parents = []
                for link in links.get("items", []):
                    identity = (link["targetType"], link["targetId"])
                    if identity not in targets:
                        targets[identity] = ResourceLinks.target(db, *identity)
                    target = targets[identity]
                    if target["available"]:
                        parents.append(
                            {
                                key: target[key]
                                for key in ("targetType", "targetId", "title")
                            }
                        )
                item["sortParent"] = (
                    min(
                        parents,
                        key=lambda p: (
                            p["title"].casefold(),
                            p["targetType"],
                            p["targetId"],
                        ),
                    )
                    if parents
                    else None
                )
            items.append(item)

    def key(item):
        if sort == "modified":
            return (item["modifiedAt"], item["id"])
        if sort == "title":
            return (item["title"].casefold(), item["id"])
        if sort == "type":
            return ("website" if item["kind"] == "html" else item["kind"], item["title"].casefold(), item["id"])
        parent = item["sortParent"]
        return (
            parent is None,
            (parent["title"].casefold() if parent else ""),
            (parent["targetType"] if parent else ""),
            (parent["targetId"] if parent else ""),
            item["title"].casefold(),
            item["id"],
        )

    def follows(item):
        candidate = key(item)
        if boundary is None:
            return True
        if sort == "parent" and candidate[0] != boundary[0]:
            return candidate[0] > boundary[0]  # orphans last in either direction
        return candidate > boundary if order == "asc" else candidate < boundary

    total = len(items)
    items.sort(key=key, reverse=order == "desc")
    if sort == "parent":
        items.sort(key=lambda item: item["sortParent"] is None)
    page = [item for item in items if follows(item)][: limit + 1]
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "sort": sort,
                    "order": order,
                    "filter": query_fingerprint,
                    "key": key(page[limit - 1]),
                }
            ).encode()
        ).decode()
        if len(page) > limit
        else None
    )
    return {
        "items": page[:limit],
        "nextCursor": next_cursor,
        "total": total,
        "sort": sort,
        "order": order,
        "storage": {
            "bytes": used,
            "maxBytes": MAX_STORED_BYTES + 128 * 1024 * 1024,
            "generatedMaxBytes": MAX_STORED_BYTES,
            "attachmentsMaxBytes": 128 * 1024 * 1024,
            "maxFileBytes": MAX_BYTES,
        },
    }
