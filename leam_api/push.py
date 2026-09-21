"""Encrypted Web Push with durable delivery receipts, distinct from device display."""

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException
from py_vapid import Vapid
from pydantic import Field, field_validator
from pywebpush import WebPusher

from .restore_automation import state as automation_state, MESSAGE as AUTOMATION_PAUSED
from .commitments import Input


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid push key")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_endpoint(value):
    url = urlsplit(value)
    host = url.hostname or ""
    supported = host in {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "updates-push.services.mozaws.net",
    } or host.endswith(".push.apple.com")
    if (
        url.scheme != "https"
        or url.netloc != host
        or not supported
        or url.fragment
        or not url.path
        or any(c.isspace() for c in value)
    ):
        raise ValueError(
            "Use an HTTPS subscription from a supported Chrome, Firefox or Safari push service"
        )
    return value


class Keys(Input):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)

    @field_validator("p256dh")
    @classmethod
    def public_key(cls, value):
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode(value))
        except Exception:
            raise ValueError("Invalid push public key") from None
        return value

    @field_validator("auth")
    @classmethod
    def auth_key(cls, value):
        if len(decode(value)) != 16:
            raise ValueError("Push auth key must contain 16 bytes")
        return value


class Subscription(Input):
    endpoint: str = Field(max_length=4096)
    keys: Keys
    expirationTime: float | None = None

    @field_validator("endpoint")
    @classmethod
    def endpoint_allowed(cls, value):
        return validate_endpoint(value)


class Device(Input):
    name: str = Field(min_length=1, max_length=80)
    subscription: Subscription


class Config(Input):
    contact: str = Field(max_length=254)

    @field_validator("contact")
    @classmethod
    def contact_uri(cls, value):
        if not re.fullmatch(
            r"mailto:[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            value,
        ):
            raise ValueError(
                "Enter a mailto: contact address for push service operators"
            )
        return value


class Push:
    def __init__(self, store, clock=None, transport=None):
        self.store = store
        self.clock = clock or time.time
        self.lock = asyncio.Lock()
        self.last_check = None
        self.error = None
        path = store.path.parent / "push-vapid.pem"
        if not path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            raw = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            fd, temporary = tempfile.mkstemp(prefix=".push-vapid-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    # Publish a complete key without replacing a concurrent winner.
                    os.link(temporary, path)
                except FileExistsError:
                    pass
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.unlink(temporary)
        self.vapid = Vapid.from_file(str(path))
        self.public_key = encode(
            self.vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )
        self.http = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False, timeout=15
        )

    async def close(self):
        await self.http.aclose()

    async def run(self):
        while True:
            try:
                await self.tick()
                self.error = None
            except Exception as error:
                self.error = type(error).__name__
            await asyncio.sleep(15)

    def status(self):
        with self.store.connect() as db:
            automation = automation_state(db)
            devices = [
                dict(r)
                for r in db.execute(
                    "SELECT id,name,state,created,updated FROM push_devices ORDER BY created"
                )
            ]
            deliveries = [
                dict(r)
                for r in db.execute(
                    "SELECT id,device_id AS deviceId,kind,state,attempts,error,created,updated,confirmed AS confirmedAt FROM push_deliveries ORDER BY created DESC LIMIT 50"
                )
            ]
        return {
            "automationHeld": automation["held"],
            "automationInvalid": automation["invalid"],
            "publicKey": self.public_key,
            "contact": self.store.get("push_contact", ""),
            "devices": devices,
            "deliveries": deliveries,
            "lastCheck": self.last_check,
            "healthy": self.error is None
            and self.last_check is not None
            and self.clock() - self.last_check < 60,
            "error": self.error,
        }

    def subscribe(self, body):
        if not self.store.get("push_contact"):
            raise HTTPException(409, "Set your push contact first")
        sub = body.subscription.model_dump(exclude_none=True)
        key = hashlib.sha256(sub["endpoint"].encode()).hexdigest()
        now = self.clock()
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO push_devices VALUES (?,?,?,?,?,?) ON CONFLICT(id)
                DO UPDATE SET name=excluded.name,body=excluded.body,state='active',updated=excluded.updated""",
                (key, body.name, json.dumps(sub), "active", now, now),
            )
        return {"id": key, "name": body.name, "state": "active"}

    def queue_test(self, key):
        now = self.clock()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if automation_state(db)["held"]:
                raise HTTPException(409, AUTOMATION_PAUSED)
            device = db.execute(
                "SELECT id FROM push_devices WHERE id=? AND state='active'", (key,)
            ).fetchone()
            if not device:
                raise HTTPException(404, "Active device not found")
            recent = db.execute(
                "SELECT id FROM push_deliveries WHERE device_id=? AND kind='test' AND created>?",
                (key, now - 30),
            ).fetchone()
            if recent:
                raise HTTPException(429, "Wait 30 seconds between test notifications")
            key_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO push_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key_id,
                    key,
                    "test",
                    None,
                    None,
                    "pending",
                    0,
                    now,
                    now + 3600,
                    None,
                    now,
                    now,
                    None,
                ),
            )
        return {"id": key_id, "state": "pending"}

    async def tick(self):
        async with self.lock:
            now = self.clock()
            contact = self.store.get("push_contact")
            if not contact:
                self.last_check = now
                return
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if automation_state(db)["held"]:
                    self.last_check = now
                    return
                # Each ready revision may be delivered once per device. Snoozes create a new revision.
                for row in db.execute(
                    "SELECT n.id,n.revision,d.id AS device_id FROM reminder_jobs n CROSS JOIN push_devices d WHERE n.state='ready' AND d.state='active'"
                ).fetchall():
                    key = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"push:{row['id']}:{row['revision']}:{row['device_id']}",
                        )
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO push_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            key,
                            row["device_id"],
                            "reminder",
                            row["id"],
                            row["revision"],
                            "pending",
                            0,
                            now,
                            now + 3600,
                            None,
                            now,
                            now,
                            None,
                        ),
                    )
                db.execute(
                    """UPDATE push_deliveries SET state='cancelled',updated=? WHERE state='pending' AND
                    (expires<=? OR NOT EXISTS(SELECT 1 FROM push_devices d WHERE d.id=device_id AND d.state='active') OR
                    (kind='reminder' AND NOT EXISTS(SELECT 1 FROM reminder_jobs n WHERE n.id=reminder_id AND n.revision=reminder_revision AND n.state='ready')))""",
                    (now, now),
                )
                rows = db.execute(
                    """SELECT p.*,d.body FROM push_deliveries p JOIN push_devices d ON p.device_id=d.id
                    WHERE p.state='pending' AND p.next_try<=? ORDER BY p.created LIMIT 10""",
                    (now,),
                ).fetchall()
                # Lease before network calls so restart can recover uncertain deliveries.
                for row in rows:
                    db.execute(
                        "UPDATE push_deliveries SET next_try=?,attempts=attempts+1 WHERE id=?",
                        (now + 180, row["id"]),
                    )
            for row in rows:
                with self.store.connect() as db:
                    if automation_state(db)["held"]:
                        self.last_check = now
                        return
                    still_pending = db.execute(
                        "SELECT p.id FROM push_deliveries p JOIN push_devices d ON d.id=p.device_id WHERE p.id=? AND p.state='pending' AND d.state='active'",
                        (row["id"],),
                    ).fetchone()
                    reminder = (
                        db.execute(
                            "SELECT id FROM reminder_jobs WHERE id=? AND revision=? AND state='ready'",
                            (row["reminder_id"], row["reminder_revision"]),
                        ).fetchone()
                        if row["kind"] == "reminder"
                        else True
                    )
                if not still_pending or not reminder:
                    with self.store.connect() as db:
                        db.execute(
                            "UPDATE push_deliveries SET state='cancelled',updated=? WHERE id=?",
                            (self.clock(), row["id"]),
                        )
                    continue
                state, error, retry, expired = "pending", None, 60, False
                try:
                    sub = json.loads(row["body"])
                    endpoint = validate_endpoint(sub["endpoint"])
                    payload = {
                        "title": "Leam",
                        "body": (
                            "This is your Leam test notification."
                            if row["kind"] == "test"
                            else "You have a reminder. Open Leam to review it."
                        ),
                        "tag": "leam-" + (row["reminder_id"] or row["id"]),
                        "url": "/?view=today",
                    }
                    data = WebPusher(sub).encode(json.dumps(payload).encode())["body"]
                    url = urlsplit(endpoint)
                    headers = self.vapid.sign(
                        {
                            "sub": contact,
                            "aud": url.scheme + "://" + url.netloc,
                            "exp": int(time.time() + 3600),
                        }
                    )
                    headers.update(
                        {
                            "Content-Encoding": "aes128gcm",
                            "Content-Type": "application/octet-stream",
                            "TTL": "3600",
                            "Urgency": "normal",
                            "Topic": hashlib.sha256(row["id"].encode()).hexdigest()[
                                :32
                            ],
                        }
                    )
                    response = await self.http.post(
                        endpoint, content=data, headers=headers
                    )
                    if 200 <= response.status_code < 300:
                        state = "accepted"
                    elif response.status_code in (404, 410):
                        state, error, expired = (
                            "failed",
                            "Subscription expired; enable this device again",
                            True,
                        )
                    elif (
                        response.status_code in (408, 429)
                        or response.status_code >= 500
                    ):
                        error = "Push service HTTP " + str(response.status_code)
                        raw = response.headers.get("retry-after", "")
                        try:
                            retry = max(
                                30,
                                min(
                                    3600,
                                    (
                                        float(raw)
                                        if raw.isdigit()
                                        else parsedate_to_datetime(raw).timestamp()
                                        - self.clock()
                                    ),
                                ),
                            )
                        except (ValueError, TypeError, OverflowError):
                            retry = min(1800, 30 * 2 ** min(row["attempts"], 6))
                    else:
                        state, error = (
                            "failed",
                            "Push service HTTP " + str(response.status_code),
                        )
                except Exception as exc:
                    # Never retain a subscription endpoint, auth header or remote response body in errors.
                    error = type(exc).__name__
                    retry = min(1800, 30 * 2 ** min(row["attempts"], 6))
                now = self.clock()
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE push_deliveries SET state=?,error=?,next_try=?,updated=? WHERE id=? AND state='pending'",
                        (state, error, now + retry, now, row["id"]),
                    )
                    if expired:
                        db.execute(
                            "UPDATE push_devices SET state='expired',updated=? WHERE id=? AND body=?",
                            (now, row["device_id"], row["body"]),
                        )
            self.last_check = self.clock()


def router(push):
    routes = APIRouter(prefix="/api/push")

    @routes.get("/status")
    async def status():
        return push.status()

    @routes.put("/config")
    async def config(body: Config):
        push.store.set("push_contact", body.contact)
        return {"saved": True}

    @routes.post("/devices")
    async def subscribe(body: Device):
        return push.subscribe(body)

    @routes.delete("/devices/{key}")
    async def remove(key: str):
        with push.store.connect() as db:
            db.execute("DELETE FROM push_devices WHERE id=?", (key,))
        return {"removed": True}

    @routes.post("/devices/{key}/test")
    async def test(key: str):
        return push.queue_test(key)

    @routes.post("/deliveries/{key}/confirm")
    async def confirm(key: str):
        with push.store.connect() as db:
            r = db.execute(
                "UPDATE push_deliveries SET confirmed=? WHERE id=? AND state='accepted' AND kind='test'",
                (push.clock(), key),
            )
            if r.rowcount != 1:
                raise HTTPException(409, "No accepted test notification to confirm")
        return {"confirmed": True}

    return routes
