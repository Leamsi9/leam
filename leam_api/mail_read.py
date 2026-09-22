"""On-demand Gmail reads. No mailbox mutations, retained bodies or ambient indexing."""

import asyncio
import base64
import binascii
import hashlib
import json
import re
from datetime import UTC, datetime
from email.message import Message
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import Field, model_validator

from .accounts import GMAIL_READONLY
from .commitments import Input
from .email import API, ID

RESPONSE_BYTES = 8 * 1024 * 1024
TEXT_BYTES = 4 * 1024 * 1024
MIME_PARTS = 200
BODY_FETCHES = 10
REQUEST_TIMEOUT = 25
HEADERS = (
    "Subject",
    "From",
    "To",
    "Cc",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
)


class MailSearch(Input):
    accountId: str = Field(min_length=1, max_length=100)
    query: str = Field(default="", max_length=2000)
    pageToken: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=20)
    includeSpamTrash: bool = False


class MailRead(Input):
    accountId: str = Field(min_length=1, max_length=100)
    messageId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,256}$")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12000, ge=1, le=16000)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def continuation(self):
        if self.offset and self.revision is None:
            raise ValueError(
                "Text continuation requires the first page's contentRevision"
            )
        return self


class MailTool(Input):
    action: Literal["accounts", "search", "read"]
    accountId: str | None = Field(default=None, min_length=1, max_length=100)
    query: str = Field(default="", max_length=2000)
    pageToken: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=20)
    includeSpamTrash: bool = False
    messageId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,256}$")
    offset: int = Field(default=0, ge=0)
    textLimit: int = Field(default=12000, ge=1, le=16000)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def selected_identity(self):
        if self.action != "accounts" and self.accountId is None:
            raise ValueError("Select an accountId returned by action=accounts")
        if self.action == "read" and self.messageId is None:
            raise ValueError("Read requires an exact messageId")
        if self.action == "read" and self.offset and self.revision is None:
            raise ValueError("Text continuation requires contentRevision")
        if self.action == "search" and self.offset:
            raise ValueError("Search continues with pageToken, not offset")
        return self


class PlainHTML(HTMLParser):
    """Convert displayable HTML text; never load resources or return markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "template", "noscript"):
            self.hidden.append(tag)
        if not self.hidden and tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3"):
            self.fragments.append("\n")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in ("p", "div", "li", "tr"):
            self.fragments.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.fragments.append(data)

    def text(self):
        return re.sub(r"\n[ \t]*\n+", "\n\n", "".join(self.fragments)).strip()


def headers(payload):
    rows = payload.get("headers", [])
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("Invalid headers")
    values = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("value"), str)
        ):
            raise TypeError("Invalid header")
        values.setdefault(row["name"].lower(), row["value"])
    return values


def metadata(data, account_id, identity, expected_id):
    if (
        data.get("id") != expected_id
        or not isinstance(data.get("threadId"), str)
        or not ID.fullmatch(data["threadId"])
    ):
        raise ValueError("Mismatched message identity")
    values = headers(data.get("payload", {}))
    labels = data.get("labelIds", [])
    if (
        not isinstance(labels, list)
        or len(labels) > 100
        or any(not isinstance(x, str) or len(x) > 128 for x in labels)
    ):
        raise ValueError("Invalid labels")
    selected = {name.lower(): values.get(name.lower(), "")[:2000] for name in HEADERS}
    return {
        "accountId": account_id,
        "id": expected_id,
        "messageId": expected_id,
        "threadId": data["threadId"],
        "subject": selected["subject"] or "(No subject)",
        "from": selected["from"],
        "to": selected["to"],
        "cc": selected["cc"],
        "headers": selected,
        "labels": labels,
        "headersPartial": any(
            len(values.get(name.lower(), "")) > 2000 for name in HEADERS
        ),
        "receivedAt": datetime.fromtimestamp(
            int(data["internalDate"]) / 1000, UTC
        ).isoformat(),
        "url": "https://mail.google.com/mail/?authuser="
        + quote(identity, safe="")
        + "#all/"
        + quote(data["threadId"], safe=""),
    }


class MailReader:
    def __init__(self, emails):
        self.emails, self.accounts, self.store = emails, emails.accounts, emails.store

    def authority(self, account_id, expected=None):
        account = self.emails.account(account_id)
        saved = self.accounts.vault.open("account:" + account_id, account["body"])
        mail = saved.get("email", {})
        config = self.accounts.configuration("google")
        grant = mail.get("grantId")
        if (
            not isinstance(grant, str)
            or not grant
            or mail.get("state", "connected") != "connected"
            or GMAIL_READONLY not in str(mail.get("token", {}).get("scope", "")).split()
            or not config
            or config.get("clientId") != saved.get("clientId")
        ):
            raise HTTPException(409, "Enable or reconnect read-only email in Settings")
        proof = (account_id, saved["clientId"], grant)
        if expected is not None and proof != expected:
            raise HTTPException(
                409, "Mailbox authorization changed; start a fresh read"
            )
        return proof, account["identity"]

    def mailboxes(self, *, offset=0, limit=20):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT id,identity FROM accounts WHERE provider='google' ORDER BY id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        items = [
            {
                "accountId": row["id"],
                "identity": row["identity"],
                **self.accounts.email_access(row["id"]),
            }
            for row in rows[:limit]
        ]
        return {
            "items": items,
            "nextOffset": offset + limit if len(rows) > limit else None,
            "observedAt": self.accounts.clock(),
            "source": "leam:mailboxes",
            "referenceData": True,
        }

    async def fetch(self, account_id, proof, path, *, params):
        self.authority(account_id, proof)
        response = await self.accounts.authorized(
            account_id,
            "GET",
            path,
            capability="email",
            accepted_statuses={404},
            params=params,
            timeout=20,
            response_limit=RESPONSE_BYTES,
        )
        self.authority(account_id, proof)
        if response.status_code == 404:
            raise HTTPException(
                404, "Message or body part is no longer available in this mailbox"
            )
        if len(response.content) > RESPONSE_BYTES:
            raise HTTPException(
                413, "Message exceeds the 8 MiB read limit; open it in Gmail"
            )
        try:
            data = response.json()
        except ValueError:
            raise HTTPException(
                502, "Gmail returned an invalid message response"
            ) from None
        if not isinstance(data, dict):
            raise HTTPException(502, "Gmail returned an invalid message response")
        return data

    async def search(self, body):
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                proof, identity = self.authority(body.accountId)
                args = {
                    "q": body.query,
                    "maxResults": body.limit,
                    "includeSpamTrash": body.includeSpamTrash,
                }
                if body.pageToken:
                    args["pageToken"] = body.pageToken
                data = await self.fetch(body.accountId, proof, API, params=args)
                refs = data.get("messages", [])
                token = data.get("nextPageToken")
                estimate = data.get("resultSizeEstimate")
                if (
                    not isinstance(refs, list)
                    or len(refs) > body.limit
                    or (
                        token is not None
                        and (not isinstance(token, str) or len(token) > 4096)
                    )
                    or (
                        estimate is not None
                        and (type(estimate) is not int or estimate < 0)
                    )
                ):
                    raise ValueError("Invalid search page")
                ids = [row.get("id") for row in refs if isinstance(row, dict)]
                if (
                    len(ids) != len(refs)
                    or any(
                        not isinstance(key, str) or not ID.fullmatch(key) for key in ids
                    )
                    or len(set(ids)) != len(ids)
                ):
                    raise ValueError("Invalid search identities")
                items = []
                for key in ids:
                    message = await self.fetch(
                        body.accountId,
                        proof,
                        API + "/" + key,
                        params={"format": "metadata", "metadataHeaders": list(HEADERS)},
                    )
                    items.append(metadata(message, body.accountId, identity, key))
                self.authority(body.accountId, proof)
                return {
                    "accountId": body.accountId,
                    "query": body.query,
                    "includeSpamTrash": body.includeSpamTrash,
                    "items": items,
                    "nextPageToken": token,
                    "resultSizeEstimate": estimate,
                    "observedAt": self.accounts.clock(),
                    "source": "gmail:messages",
                    "referenceData": True,
                    "untrustedSourceData": True,
                    "bodyIncluded": False,
                }
        except TimeoutError:
            raise HTTPException(
                504, "Mailbox read timed out; no complete page was returned"
            ) from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(
                502, "Gmail returned invalid message metadata"
            ) from None

    async def read(self, body):
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                proof, identity = self.authority(body.accountId)
                data = await self.fetch(
                    body.accountId,
                    proof,
                    API + "/" + body.messageId,
                    params={"format": "full"},
                )
                meta = metadata(data, body.accountId, identity, body.messageId)
                budget = {"parts": 0, "fetches": 0, "bytes": 0, "encodingLoss": False}
                attachments = []

                def scan(part, depth=0):
                    budget["parts"] += 1
                    if (
                        budget["parts"] > MIME_PARTS
                        or depth > 20
                        or not isinstance(part, dict)
                    ):
                        raise HTTPException(
                            413,
                            "Message MIME structure exceeds the read limit; open it in Gmail",
                        )
                    kind = part.get("mimeType", "").lower()
                    filename = part.get("filename", "")
                    content = part.get("body", {})
                    values = headers(part)
                    disposition = values.get("content-disposition", "").lower()
                    if not isinstance(filename, str) or not isinstance(content, dict):
                        raise TypeError("Invalid MIME part")
                    size, attachment_id = (
                        content.get("size", 0),
                        content.get("attachmentId"),
                    )
                    if (
                        type(size) is not int
                        or size < 0
                        or (
                            attachment_id is not None
                            and (
                                not isinstance(attachment_id, str)
                                or not re.fullmatch(
                                    r"[A-Za-z0-9_-]{1,2048}", attachment_id
                                )
                            )
                        )
                    ):
                        raise ValueError("Invalid attachment metadata")
                    if (
                        filename
                        or disposition.startswith("attachment")
                        or (
                            not kind.startswith("multipart/")
                            and kind not in ("text/plain", "text/html")
                        )
                    ):
                        attachments.append(
                            {
                                "partId": str(part.get("partId", ""))[:100],
                                "filename": filename[:512],
                                "filenamePartial": len(filename) > 512,
                                "mimeType": kind[:128],
                                "size": size,
                                "attachmentId": attachment_id,
                                "contentFetched": False,
                            }
                        )
                    children = part.get("parts", [])
                    if not isinstance(children, list):
                        raise TypeError("Invalid MIME children")
                    for child in children:
                        scan(child, depth + 1)

                async def extract(part, depth=0):
                    kind = part.get("mimeType", "").lower()
                    values = headers(part)
                    if part.get("filename") or values.get(
                        "content-disposition", ""
                    ).lower().startswith("attachment"):
                        return ""
                    content = part.get("body", {})
                    children = part.get("parts", [])
                    if kind.startswith("multipart/"):
                        if kind == "multipart/alternative":
                            plain = next(
                                (
                                    p
                                    for p in children
                                    if isinstance(p, dict)
                                    and p.get("mimeType") == "text/plain"
                                    and not p.get("filename")
                                    and not headers(p)
                                    .get("content-disposition", "")
                                    .lower()
                                    .startswith("attachment")
                                ),
                                None,
                            )
                            html = next(
                                (
                                    p
                                    for p in children
                                    if isinstance(p, dict)
                                    and p.get("mimeType") == "text/html"
                                    and not p.get("filename")
                                    and not headers(p)
                                    .get("content-disposition", "")
                                    .lower()
                                    .startswith("attachment")
                                ),
                                None,
                            )
                            chosen = plain or html
                            if chosen is not None:
                                return await extract(chosen, depth + 1)
                        return "\n".join(
                            [await extract(child, depth + 1) for child in children]
                        )
                    if kind not in ("text/plain", "text/html"):
                        return ""
                    encoded = content.get("data")
                    attachment_id = content.get("attachmentId")
                    if encoded is None and attachment_id:
                        if not isinstance(attachment_id, str) or not re.fullmatch(
                            r"[A-Za-z0-9_-]{1,2048}", attachment_id
                        ):
                            raise ValueError("Invalid text body identity")
                        budget["fetches"] += 1
                        if budget["fetches"] > BODY_FETCHES:
                            raise HTTPException(
                                413,
                                "Message has too many external text parts; open it in Gmail",
                            )
                        external = await self.fetch(
                            body.accountId,
                            proof,
                            API
                            + "/"
                            + body.messageId
                            + "/attachments/"
                            + attachment_id,
                            params={},
                        )
                        encoded = external.get("data")
                    if not isinstance(encoded, str):
                        if content.get("size", 0):
                            raise ValueError("Missing text body")
                        return ""
                    try:
                        raw = base64.b64decode(
                            encoded + "=" * (-len(encoded) % 4),
                            altchars=b"-_",
                            validate=True,
                        )
                    except (binascii.Error, ValueError):
                        raise ValueError("Invalid MIME body encoding") from None
                    budget["bytes"] += len(raw)
                    if budget["bytes"] > TEXT_BYTES:
                        raise HTTPException(
                            413,
                            "Message text exceeds the 4 MiB processing limit; open it in Gmail",
                        )
                    declaration = Message()
                    declaration["content-type"] = values.get("content-type", kind)
                    charset = declaration.get_content_charset() or "utf-8"
                    try:
                        text = raw.decode(charset)
                    except (LookupError, UnicodeError):
                        text = raw.decode("utf-8", errors="replace")
                        budget["encodingLoss"] = True
                    if kind == "text/html":
                        parser = PlainHTML()
                        parser.feed(text)
                        parser.close()
                        text = parser.text()
                    return text

                scan(data.get("payload", {}))
                text = await extract(data.get("payload", {}))
                revision = hashlib.sha256(
                    json.dumps(
                        [body.accountId, body.messageId, text, attachments],
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                if body.revision is not None and body.revision != revision:
                    raise HTTPException(
                        409, "Message text changed; restart from offset zero"
                    )
                if body.offset > len(text):
                    raise HTTPException(422, "Text offset is beyond this message")
                self.authority(body.accountId, proof)
                end = min(len(text), body.offset + body.limit)
                return {
                    **meta,
                    "text": text[body.offset : end],
                    "textFormat": "plain",
                    "offset": body.offset,
                    "totalCharacters": len(text),
                    "nextOffset": end if end < len(text) else None,
                    "contentRevision": revision,
                    "partial": body.offset > 0 or end < len(text),
                    "attachments": attachments,
                    "encodingLoss": budget["encodingLoss"],
                    "observedAt": self.accounts.clock(),
                    "source": "gmail:message",
                    "referenceData": True,
                    "untrustedSourceData": True,
                    "remoteResourcesLoaded": False,
                    "providerMutated": False,
                }
        except TimeoutError:
            raise HTTPException(
                504, "Message read timed out; no complete text page was returned"
            ) from None
        except (
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            AttributeError,
            RecursionError,
        ):
            raise HTTPException(502, "Gmail returned invalid message content") from None

    async def call(self, body):
        if body.action == "accounts":
            return self.mailboxes(offset=body.offset, limit=body.limit)
        if body.action == "search":
            return await self.search(
                MailSearch(
                    **body.model_dump(
                        include={
                            "accountId",
                            "query",
                            "pageToken",
                            "limit",
                            "includeSpamTrash",
                        }
                    )
                )
            )
        return await self.read(
            MailRead(
                accountId=body.accountId,
                messageId=body.messageId,
                offset=body.offset,
                limit=body.textLimit,
                revision=body.revision,
            )
        )


def router(reader):
    routes = APIRouter(prefix="/api/email")

    @routes.get("/mailboxes")
    async def mailboxes(
        offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20)
    ):
        return reader.mailboxes(offset=offset, limit=limit)

    @routes.post("/search")
    async def search(body: MailSearch):
        return await reader.search(body)

    @routes.get("/accounts/{account_id}/messages/{message_id}")
    async def read(
        account_id: str = Path(min_length=1, max_length=100),
        message_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,256}$"),
        offset: int = Query(0, ge=0),
        limit: int = Query(12000, ge=1, le=16000),
        revision: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
    ):
        if offset and revision is None:
            raise HTTPException(422, "Text continuation requires contentRevision")
        return await reader.read(
            MailRead(
                accountId=account_id,
                messageId=message_id,
                offset=offset,
                limit=limit,
                revision=revision,
            )
        )

    return routes
