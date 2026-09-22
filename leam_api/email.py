"""Bounded read-only Gmail snapshots; external email remains untrusted source data."""

import asyncio
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from .email_actionability import SOURCE_LIMIT as OVERVIEW_LIMIT
from .email_actionability import EmailActionability
from .email_review import EmailReview, save_review

MESSAGE_LIMIT = 20
REBUILD_LIMIT = 100
QUERY = "in:inbox newer_than:30d"
API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


def message_record(item, account_id, identity, refill_generation=None):
    key, thread = item.get("id"), item.get("threadId")
    if (
        not isinstance(key, str)
        or not ID.fullmatch(key)
        or not isinstance(thread, str)
        or not ID.fullmatch(thread)
    ):
        raise ValueError("Invalid message identity")
    headers = item.get("payload", {}).get("headers", [])
    if not isinstance(headers, list) or len(headers) > 32:
        raise ValueError("Invalid message headers")
    values = {}
    for header in headers:
        if (
            not isinstance(header, dict)
            or not isinstance(header.get("name"), str)
            or not isinstance(header.get("value"), str)
        ):
            raise TypeError("Invalid message header")
        values.setdefault(header["name"].lower(), header["value"])
    snippet = item.get("snippet") or ""
    labels = item.get("labelIds", [])
    if (
        not isinstance(snippet, str)
        or not isinstance(labels, list)
        or len(labels) > 100
        or any(not isinstance(label, str) or len(label) > 128 for label in labels)
    ):
        raise ValueError("Invalid message metadata")
    received = datetime.fromtimestamp(int(item["internalDate"]) / 1000, UTC).isoformat()
    record = {
        "id": key,
        "threadId": thread,
        "accountId": account_id,
        "subject": values.get("subject", "(No subject)")[:512],
        "from": values.get("from", "")[:512],
        "snippet": snippet[:512],
        "receivedAt": received,
        "unread": "UNREAD" in labels,
        "important": "IMPORTANT" in labels,
        "labels": sorted(labels),
        "listId": values.get("list-id", "")[:256],
        "listUnsubscribe": bool(values.get("list-unsubscribe")),
        "precedence": values.get("precedence", "")[:32],
        "url": "https://mail.google.com/mail/?authuser="
        + quote(identity, safe="")
        + "#all/"
        + quote(thread, safe=""),
    }
    if refill_generation:
        record["refillGeneration"] = refill_generation
    return record


class Emails:
    def __init__(self, store, accounts, runtime=None):
        self.store, self.accounts = store, accounts
        self.locks = defaultdict(asyncio.Lock)
        self.classifier = EmailActionability(self, runtime)

    def account(self, key):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT id,provider,identity,body FROM accounts WHERE id=?", (key,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Account not found")
        if row["provider"] != "google":
            raise HTTPException(
                422, "Read-only email is currently available for Google accounts"
            )
        return dict(row)

    def snapshot(self, key):
        account = self.account(key)
        access = self.accounts.email_access(key)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM email_snapshots WHERE account_id=?", (key,)
            ).fetchone()
        saved = (
            self.accounts.vault.open("email-snapshot:" + key, row["body"])
            if row
            else {}
        )
        synced = row["synced"] if row else None
        error = row["error"] if row else None
        stale = synced is None or self.accounts.clock() - synced > 900
        state = access["state"]
        if state == "connected":
            state = (
                "error"
                if error
                else "never_synced"
                if synced is None
                else "stale"
                if stale
                else "ready"
            )
        account_body = self.accounts.vault.open("account:" + key, account["body"])
        items = saved.get("items", []) if access["granted"] else []
        items = self.classifier.annotate(
            key, items, account_body.get("email", {}).get("grantId")
        )
        return {
            "accountId": key,
            "identity": account["identity"],
            "provider": "google",
            "state": state,
            "granted": access["granted"],
            "syncedAt": synced,
            "error": error,
            "stale": stale,
            "truncated": saved.get("truncated", False),
            "items": items,
            "windowDays": 30,
            "limit": saved.get("limit", MESSAGE_LIMIT),
        }

    def overview(self):
        with self.store.connect() as db:
            keys = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM accounts WHERE provider='google' ORDER BY created"
                )
            ]
        accounts = [self.snapshot(key) for key in keys]
        granted = [account for account in accounts if account["granted"]]
        state = (
            "not_connected"
            if not granted
            else "ready"
            if all(account["state"] == "ready" for account in granted)
            else "attention"
        )
        items = [item for account in granted for item in account["items"]]
        items.sort(key=lambda item: item["receivedAt"], reverse=True)
        return {
            "state": state,
            "accounts": accounts,
            "items": items[:OVERVIEW_LIMIT],
            "truncated": len(items) > OVERVIEW_LIMIT
            or any(account["truncated"] for account in accounts),
            "supportedProvider": "google",
            "classification": self.classifier.status(
                items, [account["accountId"] for account in accounts]
            ),
        }

    async def thread_evidence(self, key, item):
        """Read metadata only. Missing, oversized or malformed threads remain unverified."""
        unknown = {"verified": False, "newerSent": None, "newerIncoming": None}
        try:
            response = await self.accounts.authorized(
                key,
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/threads/"
                + item["threadId"],
                capability="email",
                accepted_statuses={404},
                params={
                    "format": "metadata",
                    "fields": "id,messages(id,labelIds,internalDate)",
                },
            )
            if response.status_code != 200 or len(response.content) > 65536:
                return unknown
            data = response.json()
            messages = data.get("messages")
            if (
                data.get("id") != item["threadId"]
                or not isinstance(messages, list)
                or not 1 <= len(messages) <= 100
            ):
                return unknown
            seen, incoming, sent = set(), [], []
            for row in messages:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not ID.fullmatch(row["id"])
                    or row["id"] in seen
                ):
                    return unknown
                seen.add(row["id"])
                labels = row.get("labelIds", [])
                if not isinstance(labels, list) or any(
                    not isinstance(x, str) for x in labels
                ):
                    return unknown
                stamp = int(row["internalDate"])
                if "DRAFT" not in labels:
                    (sent if "SENT" in labels else incoming).append(stamp)
            if item["id"] not in seen:
                return unknown
            received = int(
                datetime.fromisoformat(item["receivedAt"]).timestamp() * 1000
            )
            return {
                "verified": True,
                "newerSent": max(sent, default=0) >= received,
                "newerIncoming": max(incoming, default=0) > received,
                "latestSentAt": max(sent, default=None),
                "latestIncomingAt": max(incoming, default=None),
            }
        except (
            HTTPException,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
        ):
            return unknown

    async def sync(self, key, *, rebuild=False):
        async with self.locks[key]:
            account = self.account(key)
            access = self.accounts.email_access(key)
            if access["state"] != "connected":
                raise HTTPException(
                    409, "Enable or reconnect read-only email in Settings"
                )
            original = self.accounts.vault.open("account:" + key, account["body"])
            grant = original["email"]["grantId"]
            limit = REBUILD_LIMIT if rebuild else MESSAGE_LIMIT
            with self.store.connect() as db:
                previous_row = db.execute(
                    "SELECT body FROM email_snapshots WHERE account_id=?", (key,)
                ).fetchone()
            previous = (
                self.accounts.vault.open("email-snapshot:" + key, previous_row["body"])
                if previous_row
                else {}
            )
            refill_generation = (
                uuid.uuid4().hex if rebuild else previous.get("refillGeneration")
            )
            try:
                async with asyncio.timeout(120 if rebuild else 30):
                    response = await self.accounts.authorized(
                        key,
                        "GET",
                        API,
                        capability="email",
                        params={
                            "q": QUERY,
                            "maxResults": limit,
                            "fields": "messages(id),nextPageToken",
                        },
                    )
                    body = response.json()
                    listed = body.get("messages", [])
                    if not isinstance(listed, list) or len(listed) > limit:
                        raise ValueError("Invalid bounded inbox response")
                    items, seen = [], set()
                    for entry in listed:
                        message_id = (
                            entry.get("id") if isinstance(entry, dict) else None
                        )
                        if (
                            not isinstance(message_id, str)
                            or not ID.fullmatch(message_id)
                            or message_id in seen
                        ):
                            raise ValueError("Invalid or duplicate inbox ID")
                        seen.add(message_id)
                        response = await self.accounts.authorized(
                            key,
                            "GET",
                            API + "/" + message_id,
                            capability="email",
                            accepted_statuses={404},
                            params={
                                "format": "metadata",
                                "metadataHeaders": [
                                    "From",
                                    "Subject",
                                    "List-Id",
                                    "List-Unsubscribe",
                                    "Precedence",
                                ],
                                "fields": "id,threadId,labelIds,snippet,internalDate,payload(headers)",
                            },
                        )
                        # A message can be removed or leave the inbox during a snapshot read.
                        if response.status_code == 404:
                            continue
                        item = response.json()
                        if item.get("id") != message_id:
                            raise ValueError("Message identity changed")
                        record = message_record(
                            item,
                            key,
                            account["identity"],
                            refill_generation,
                        )
                        if "INBOX" in item.get("labelIds", []):
                            items.append(record)
                    items.sort(
                        key=lambda item: (item["receivedAt"], item["id"]), reverse=True
                    )
                    # One latest retrieved source per thread. Never classify older thread copies as separate obligations.
                    threads = set()
                    unique = []
                    for item in items:
                        if item["threadId"] not in threads:
                            threads.add(item["threadId"])
                            item["thread"] = await self.thread_evidence(key, item)
                            unique.append(item)
                    items = unique
                saved = {
                    "items": items,
                    "truncated": bool(body.get("nextPageToken")),
                    "limit": limit,
                    **(
                        {"refillGeneration": refill_generation}
                        if refill_generation
                        else {}
                    ),
                }
                error = None
            except (
                HTTPException,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OverflowError,
                TimeoutError,
            ) as exc:
                if isinstance(exc, HTTPException) and exc.status_code == 409:
                    error = "Reconnect read-only email in Settings. The previous snapshot is retained."
                else:
                    error = "Email sync failed. Check Gmail API access and retry; the previous snapshot is retained."
                saved = None
            # Account removal or new consent while GETs ran must not resurrect old state.
            async with self.accounts.locks["google"]:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute(
                        "SELECT body FROM accounts WHERE id=?", (key,)
                    ).fetchone()
                    current_body = (
                        self.accounts.vault.open("account:" + key, current["body"])
                        if current
                        else {}
                    )
                    if current_body.get("email", {}).get("grantId") != grant:
                        raise HTTPException(
                            409,
                            "Email connection changed during synchronization; retry",
                        )
                    if saved is not None:
                        db.execute(
                            "INSERT INTO email_snapshots VALUES (?,?,?,NULL) ON CONFLICT(account_id) DO UPDATE SET body=excluded.body,synced=excluded.synced,error=NULL",
                            (
                                key,
                                self.accounts.vault.seal(
                                    "email-snapshot:" + key, saved
                                ),
                                self.accounts.clock(),
                            ),
                        )
                        if rebuild:
                            db.execute(
                                "DELETE FROM settings WHERE key=?",
                                ("email-decisions:" + key,),
                            )
                    else:
                        empty = self.accounts.vault.seal(
                            "email-snapshot:" + key, {"items": [], "truncated": False}
                        )
                        db.execute(
                            "INSERT INTO email_snapshots VALUES (?,?,NULL,?) ON CONFLICT(account_id) DO UPDATE SET error=excluded.error",
                            (key, empty, error),
                        )
            if saved is not None and self.classifier.runtime is not None:
                self.classifier.start()
            return self.snapshot(key)

    async def disconnect(self, key):
        async with self.locks[key], self.accounts.locks["google"]:
            account = self.account(key)
            saved = self.accounts.vault.open("account:" + key, account["body"])
            saved.pop("email", None)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE accounts SET body=? WHERE id=?",
                    (self.accounts.vault.seal("account:" + key, saved), key),
                )
                db.execute("DELETE FROM email_snapshots WHERE account_id=?", (key,))
                db.execute(
                    "DELETE FROM settings WHERE key=?", ("email-decisions:" + key,)
                )
                # Cancel only this mailbox's pending opt-in; calendar and other accounts remain valid.
                for pending in db.execute(
                    "SELECT id,body FROM oauth_states WHERE provider='google'"
                ).fetchall():
                    payload = self.accounts.vault.open(
                        "oauth:" + pending["id"], pending["body"]
                    )
                    if payload.get("emailAccount") == key:
                        db.execute(
                            "DELETE FROM oauth_states WHERE id=?", (pending["id"],)
                        )
        return {"removed": True, "providerGrantRevoked": False}


def router(emails):
    routes = APIRouter(prefix="/api/email")

    @routes.get("")
    async def overview():
        return emails.overview()

    @routes.get("/triage")
    async def triage_status():
        return emails.classifier.overview()

    @routes.put("/triage/review")
    async def review(body: EmailReview):
        return save_review(emails.classifier, body)

    @routes.post("/triage", status_code=202)
    async def triage():
        return emails.classifier.start()

    @routes.post("/accounts/{key}/sync")
    async def sync(key: str):
        return await emails.sync(key)

    @routes.post("/accounts/{key}/rebuild")
    async def rebuild(key: str):
        return await emails.sync(key, rebuild=True)

    @routes.delete("/accounts/{key}")
    async def disconnect(key: str):
        return await emails.disconnect(key)

    return routes
