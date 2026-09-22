"""Selected-day accomplishments from dated logs and actual completion receipts."""

import json
import time


def task_state(card):
    return (
        card.get("stage", "todo")
        if card.get("status", "active") == "active"
        else card["status"]
    )


def stamp_changes(before, after):
    """Server-owned epoch seconds. Never infer historical dates from updated."""
    now = time.time()
    changed = not before or task_state(before) != task_state(after)
    after["statusChangedAt"] = now if changed else before.get("statusChangedAt")
    after["completedAt"] = (
        (now if before.get("status") != "completed" else before.get("completedAt"))
        if after.get("status") == "completed"
        else None
    )


def record_transition(db, key, revision, before, after, stamp):
    if before == after or after != "completed":
        return
    db.execute(
        "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
        (
            "commitment.completed",
            json.dumps({"id": key, "revision": revision}),
            stamp,
        ),
    )


def subtask_map(card):
    result = {}

    def visit(rows):
        for row in rows:
            result[row["id"]] = row
            visit(row.get("children", []))

    visit(card.get("subtasks", []))
    return result


def record_changes(db, key, revision, before, after):
    stamp = after.get("statusChangedAt") or time.time()
    record_transition(
        db, key, revision, before.get("status"), after.get("status"), stamp
    )
    if before and task_state(before) != task_state(after):
        db.execute(
            "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
            (
                "commitment.status_changed",
                json.dumps(
                    {
                        "id": key,
                        "revision": revision,
                        "from": task_state(before),
                        "to": task_state(after),
                    }
                ),
                stamp,
            ),
        )
    events = []
    if (
        before.get("stage", "todo") == "todo"
        and after.get("stage") == "in_progress"
        and after.get("status") == "active"
    ):
        events.append({"action": "started"})
    old = subtask_map(before)
    for sub_id, node in subtask_map(after).items():
        prior = old.get(sub_id)
        if (
            node.get("status") == "in_progress"
            and (prior or {}).get("status", "todo") == "todo"
        ):
            events.append({"action": "subtask_started", "subtaskId": sub_id})
        if prior and prior.get("status") != node.get("status"):
            db.execute(
                "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
                (
                    "commitment.subtask_status_changed",
                    json.dumps(
                        {
                            "id": key,
                            "revision": revision,
                            "subtaskId": sub_id,
                            "owner": node.get("owner"),
                            "from": prior.get("status"),
                            "to": node.get("status"),
                        }
                    ),
                    time.time(),
                ),
            )
        if (
            node.get("status") == "completed"
            and old.get(sub_id, {}).get("status") != "completed"
        ):
            events.append({"action": "subtask_completed", "subtaskId": sub_id})
    for event in events:
        db.execute(
            "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
            (
                "commitment.activity",
                json.dumps({"id": key, "revision": revision, **event}),
                time.time(),
            ),
        )


def accomplishments(store, selection):
    start, end = selection.window()
    lower, upper = start.timestamp(), end.timestamp()
    day = str(selection.date)
    with store.connect() as db:
        db.execute("BEGIN")
        cards = {
            row["id"]: dict(json.loads(row["body"]), id=row["id"])
            for row in db.execute(
                "SELECT id,body FROM entities WHERE kind='commitment'"
            )
        }
        done = {}
        for row in db.execute(
            "SELECT commitment_id,body FROM daily_logs WHERE day=?", (day,)
        ):
            card = cards.get(row["commitment_id"])
            if card and json.loads(row["body"]).get("done") is True:
                done[card["id"]] = {**card, "completionEvidence": "daily_log"}
        completed_at = {}
        for row in db.execute(
            "SELECT payload,created FROM events WHERE topic='commitment.completed'"
        ):
            key = json.loads(row["payload"]).get("id")
            completed_at[key] = max(completed_at.get(key, 0), row["created"])
        # Historical approvals contain the reviewed prior state plus the exact
        # committed result. A title edit on an already-done card is not evidence.
        for row in db.execute(
            "SELECT review,result,updated FROM proposals WHERE operation='commitment.edit' AND state='complete' AND result IS NOT NULL"
        ):
            before = json.loads(row["review"]).get("before") or {}
            result = json.loads(row["result"])
            if (
                before.get("status") in {"active", "paused"}
                and result.get("status") == "completed"
            ):
                key = result.get("id")
                completed_at[key] = max(completed_at.get(key, 0), row["updated"])
        activity = {}
        for row in db.execute(
            "SELECT payload,created FROM events WHERE topic='commitment.activity' AND created>=? AND created<? ORDER BY id",
            (lower, upper),
        ):
            event = json.loads(row["payload"])
            card = cards.get(event.get("id"))
            if not card:
                continue
            owner = card.get("owner")
            action = event.get("action")
            sub_id = event.get("subtaskId")
            title = card["title"]
            if action == "started":
                if card.get("status") != "active" or card.get("stage") != "in_progress":
                    continue
            elif action in {"subtask_completed", "subtask_started"}:
                node = subtask_map(card).get(sub_id)
                expected_status = (
                    "completed" if action == "subtask_completed" else "in_progress"
                )
                if not node or node.get("status") != expected_status:
                    continue
                title = node["title"]
                owner = node.get("owner")
            else:
                continue
            key = (card["id"], action, sub_id)
            activity[key] = {
                "id": ":".join(str(v) for v in key),
                "commitmentId": card["id"],
                "title": title,
                "parentTitle": card["title"],
                "owner": owner,
                "action": action,
                "recordedAt": row["created"],
            }
        # Explicit user-assigned dates are not historical state transitions.
        for row in db.execute(
            "SELECT payload,created FROM events WHERE topic='commitment.activity_backfill' AND created>=? AND created<? ORDER BY id",
            (lower, upper),
        ):
            event = json.loads(row["payload"])
            card = cards.get(event.get("id"))
            if not card or event.get("source") != "user_requested_backfill":
                continue
            children = subtask_map(card)
            for record in event.get("records", []):
                sub_id = record.get("subtaskId")
                node = children.get(sub_id) if sub_id else card
                if not node:
                    continue
                status = node.get("status") if sub_id else task_state(card)
                if status != record["status"]:
                    continue
                if not sub_id and status == "completed":
                    if card.get("completedAt") == row["created"]:
                        done[card["id"]] = {
                            **card,
                            "completionEvidence": "user_requested_backfill",
                        }
                    continue
                action = (
                    (
                        "subtask_completed"
                        if status == "completed"
                        else "subtask_started"
                    )
                    if sub_id
                    else "started"
                )
                key = (card["id"], action, sub_id)
                if key in activity and activity[key]["recordedAt"] > row["created"]:
                    continue
                activity[key] = {
                    "id": ":".join(str(v) for v in key),
                    "commitmentId": card["id"],
                    "title": node["title"],
                    "parentTitle": card["title"],
                    "owner": node.get("owner"),
                    "action": action,
                    "recordedAt": row["created"],
                    "source": "user_requested_backfill",
                }
        logged_ids = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT commitment_id FROM daily_logs WHERE json_extract(body,'$.done')=1"
            )
        }
        undated = 0
        for key, card in cards.items():
            if card.get("status") != "completed":
                continue
            stamp = card.get("completedAt") or completed_at.get(key)
            if stamp is not None and lower <= stamp < upper:
                done.setdefault(
                    key, {**card, "completionEvidence": "status_transition"}
                )
            elif stamp is None and key not in done and key not in logged_ids:
                # Never assign legacy completed records to entity.updated: later
                # metadata edits change that field without completing anything.
                undated += 1
    return {
        "date": day,
        "items": [
            {
                field: card.get(field)
                for field in ("id", "title", "kind", "owner", "completionEvidence")
            }
            for card in sorted(
                done.values(), key=lambda card: (card["title"].casefold(), card["id"])
            )
        ],
        "undatedCompleted": undated,
        "activity": list(activity.values()),
    }
