"""Leam-owned connected accounts; OAuth credentials never cross into the PWA."""

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from collections import defaultdict
from typing import Literal
from urllib.parse import urlsplit

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import Field

from .commitments import Input
from .vault import Vault

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
EMAIL_SCOPE = "openid email " + GMAIL_READONLY

Provider = Literal["google", "microsoft"]
PROVIDERS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "profile": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events",
    },
    "microsoft": {
        "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "profile": "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName,displayName",
        "scope": "openid offline_access https://graph.microsoft.com/User.Read https://graph.microsoft.com/Calendars.ReadWrite",
    },
}


class Configuration(Input):
    revision: int = Field(default=0, ge=0)
    clientId: str = Field(min_length=1, max_length=512)
    clientSecret: str | None = Field(default=None, min_length=1, max_length=4096)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Accounts:
    def __init__(self, store, origins, transport=None, clock=None):
        self.store = store
        self.origins = origins
        self.transport = transport
        self.clock = clock or time.time
        self.vault = Vault(store.path.parent)
        self.locks = defaultdict(asyncio.Lock)

    def configuration(self, provider):
        raw = self.store.get("account_config:" + provider)
        return self.vault.open("config:" + provider, raw) if raw else None

    async def save_config(self, provider, body):
        async with self.locks[provider]:
            return self._save_config(provider, body)

    def _save_config(self, provider, body):
        previous = self.configuration(provider)
        if body.revision != (previous or {}).get("revision", 0):
            raise HTTPException(
                409, "Application configuration changed; reload before saving"
            )
        secret = body.clientSecret or (previous or {}).get("clientSecret")
        if not secret:
            raise HTTPException(422, "An OAuth application client secret is required")
        config = {
            "clientId": body.clientId,
            "clientSecret": secret,
            "revision": body.revision + 1,
        }
        sealed = self.vault.seal("config:" + provider, config)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("account_config:" + provider, json.dumps(sealed)),
            )
            db.execute("DELETE FROM oauth_states WHERE provider=?", (provider,))
            if previous and previous["clientId"] != body.clientId:
                db.execute(
                    "UPDATE accounts SET state='reconnect',error='OAuth application changed; reconnect this account' WHERE provider=?",
                    (provider,),
                )
        return {"saved": True}

    def status(self):
        providers = []
        for key, definition in PROVIDERS.items():
            config = self.configuration(key)
            providers.append(
                {
                    "id": key,
                    "clientId": config["clientId"] if config else "",
                    "revision": config.get("revision", 0) if config else 0,
                    "secretConfigured": bool(config),
                    "scopes": definition["scope"].split(),
                    "callbackPath": "/api/accounts/oauth/" + key + "/callback",
                }
            )
        with self.store.connect() as db:
            accounts = [
                dict(r)
                for r in db.execute(
                    "SELECT id,provider,identity,state,error,checked AS checkedAt,created FROM accounts ORDER BY created"
                )
            ]
        for account in accounts:
            account["email"] = self.email_access(account["id"])
        return {"providers": providers, "items": accounts}

    def email_access(self, account_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT provider,body FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Account not found")
        saved = self.vault.open("account:" + account_id, row["body"])
        mail = saved.get("email") or {}
        granted = GMAIL_READONLY in str(mail.get("token", {}).get("scope", "")).split()
        config = self.configuration(row["provider"])
        valid_client = bool(config and config["clientId"] == saved["clientId"])
        return {
            "supported": row["provider"] == "google",
            "granted": granted,
            "state": (mail.get("state", "connected") if valid_client else "reconnect")
            if granted
            else "not_connected",
        }

    async def start_email(self, account_id, origin, session):
        if self.provider_of(account_id) != "google":
            raise HTTPException(
                422, "Read-only email is currently available for Google accounts"
            )
        return await self.start("google", origin, session, email_account=account_id)

    def client(self, provider, config, **kwargs):
        return AsyncOAuth2Client(
            config["clientId"],
            config["clientSecret"],
            token_endpoint_auth_method="client_secret_post",
            scope=kwargs.pop("scope", PROVIDERS[provider]["scope"]),
            code_challenge_method="S256",
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=20,
            **kwargs,
        )

    async def start(self, provider, origin, session, *, email_account=None):
        if origin not in self.origins:
            raise HTTPException(403, "Use this Leam installation to connect accounts")
        config = self.configuration(provider)
        if not config:
            raise HTTPException(409, "Configure this OAuth application first")
        email_identity = None
        if email_account:
            with self.store.connect() as db:
                target = db.execute(
                    "SELECT identity,body FROM accounts WHERE id=? AND provider='google'",
                    (email_account,),
                ).fetchone()
            if not target:
                raise HTTPException(404, "Account not found")
            saved = self.vault.open("account:" + email_account, target["body"])
            if saved["clientId"] != config["clientId"]:
                raise HTTPException(409, "Reconnect the account before enabling email")
            email_identity = target["identity"]
        scope = EMAIL_SCOPE if email_account else PROVIDERS[provider]["scope"]
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        redirect = origin + "/api/accounts/oauth/" + provider + "/callback"
        key = digest(state)
        sealed = self.vault.seal(
            "oauth:" + key,
            {
                "verifier": verifier,
                "redirect": redirect,
                "config": config,
                "scope": scope,
                "emailAccount": email_account,
            },
        )
        with self.store.connect() as db:
            db.execute("DELETE FROM oauth_states WHERE expires<=?", (self.clock(),))
            db.execute(
                "INSERT INTO oauth_states VALUES (?,?,?,?,?)",
                (key, provider, digest(session), self.clock() + 600, sealed),
            )
        async with self.client(
            provider, config, redirect_uri=redirect, scope=scope
        ) as client:
            extra = (
                {"access_type": "offline", "prompt": "select_account consent"}
                if provider == "google"
                else {"prompt": "select_account", "response_mode": "query"}
            )
            if email_account:
                extra.update(include_granted_scopes="true", login_hint=email_identity)
            url, _ = client.create_authorization_url(
                PROVIDERS[provider]["authorize"],
                state=state,
                code_verifier=verifier,
                **extra,
            )
        return {"url": url, "expiresAt": self.clock() + 600}

    async def callback(self, provider, state, code, error):
        async with self.locks[provider]:
            return await self._callback(provider, state, code, error)

    async def _callback(self, provider, state, code, error):
        key = digest(state)
        now = self.clock()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT o.* FROM oauth_states o JOIN sessions s ON s.token_hash=o.session_hash WHERE o.id=? AND o.provider=? AND o.expires>? AND s.expires>?",
                (key, provider, now, now),
            ).fetchone()
            if not row:
                raise HTTPException(
                    400,
                    "Authorization expired or does not match. Sign in and connect again.",
                )
            db.execute("DELETE FROM oauth_states WHERE id=?", (key,))
        data = self.vault.open("oauth:" + key, row["body"])
        if error or not code:
            return "/?view=settings&account=cancelled"
        try:
            async with self.client(
                provider,
                data["config"],
                redirect_uri=data["redirect"],
                scope=data.get("scope", PROVIDERS[provider]["scope"]),
            ) as client:
                token = await client.fetch_token(
                    PROVIDERS[provider]["token"],
                    code=code,
                    code_verifier=data["verifier"],
                    grant_type="authorization_code",
                )
                profile_response = await client.get(PROVIDERS[provider]["profile"])
                profile_response.raise_for_status()
                profile = profile_response.json()
            subject = profile.get("sub") if provider == "google" else profile.get("id")
            identity = (
                profile.get("email")
                if provider == "google"
                else profile.get("mail") or profile.get("userPrincipalName")
            )
            if (
                not isinstance(subject, str)
                or not subject
                or not isinstance(identity, str)
                or not identity
            ):
                raise ValueError("Provider identity missing")
            account_id = str(uuid.uuid5(uuid.NAMESPACE_URL, provider + ":" + subject))
            if data.get("emailAccount") and data["emailAccount"] != account_id:
                raise HTTPException(
                    409, "Choose the same Google account when enabling email"
                )
            if (
                data.get("emailAccount")
                and GMAIL_READONLY not in str(token.get("scope", "")).split()
            ):
                return "/?view=settings&account=email-denied"
            label = "account:" + account_id
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                # A logout or credential change during token exchange invalidates completion too.
                session = db.execute(
                    "SELECT 1 FROM sessions WHERE token_hash=? AND expires>?",
                    (row["session_hash"], self.clock()),
                ).fetchone()
                current = db.execute(
                    "SELECT value FROM settings WHERE key=?",
                    ("account_config:" + provider,),
                ).fetchone()
                current_config = (
                    self.vault.open("config:" + provider, json.loads(current["value"]))
                    if current
                    else None
                )
                if not session or current_config != data["config"]:
                    raise HTTPException(
                        409,
                        "Sign-in or application configuration changed; connect again",
                    )
                previous = db.execute(
                    "SELECT body FROM accounts WHERE id=?", (account_id,)
                ).fetchone()
                old = self.vault.open(label, previous["body"]) if previous else {}
                same_client = old.get("clientId") == data["config"]["clientId"]
                if data.get("emailAccount"):
                    if not previous or not same_client:
                        raise HTTPException(409, "Account changed; connect email again")
                    previous_mail = old.get("email") or {}
                    previous_token = previous_mail.get("token") or {}
                    if (
                        not token.get("refresh_token")
                        and previous_mail.get("state") == "connected"
                        and GMAIL_READONLY
                        in str(previous_token.get("scope", "")).split()
                    ):
                        token["refresh_token"] = previous_token.get("refresh_token")
                    if not token.get("refresh_token"):
                        raise HTTPException(
                            409,
                            "Google did not grant renewable email access; enable email again",
                        )
                    old["email"] = {
                        "token": dict(token),
                        "state": "connected",
                        "grantId": str(uuid.uuid4()),
                    }
                    db.execute(
                        "UPDATE accounts SET body=? WHERE id=?",
                        (self.vault.seal(label, old), account_id),
                    )
                    return "/?view=settings&account=email-connected"
                if not token.get("refresh_token") and same_client:
                    token["refresh_token"] = old["token"].get("refresh_token")
                saved = {"clientId": data["config"]["clientId"], "token": dict(token)}
                if same_client and old.get("email"):
                    saved["email"] = old["email"]
                body = self.vault.seal(label, saved)
                db.execute(
                    """INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    identity=excluded.identity,body=excluded.body,state=excluded.state,error=NULL,checked=excluded.checked""",
                    (
                        account_id,
                        provider,
                        identity,
                        body,
                        "connected",
                        None,
                        now,
                        now,
                        subject,
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            # OAuth exceptions may include codes, client secrets and provider response bodies.
            raise HTTPException(
                502,
                "Provider connection failed. Check application credentials and consent, then connect again.",
            ) from None
        return "/?view=settings&account=connected"

    def provider_of(self, account_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT provider FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Account not found")
        return row["provider"]

    async def authorized(
        self,
        account_id,
        method,
        path,
        *,
        accepted_statuses=frozenset(),
        capability=None,
        **kwargs,
    ):
        # Serialize credential rotation, reconnect, removal and refresh within a provider.
        async with self.locks[self.provider_of(account_id)]:
            with self.store.connect() as db:
                row = db.execute(
                    "SELECT * FROM accounts WHERE id=?", (account_id,)
                ).fetchone()
            if not row:
                raise HTTPException(404, "Account not found")
            provider = row["provider"]
            url = urlsplit(path)
            hosts = (
                {
                    "www.googleapis.com",
                    "openidconnect.googleapis.com",
                    "gmail.googleapis.com",
                }
                if provider == "google"
                else {"graph.microsoft.com"}
            )
            if (
                url.scheme != "https"
                or url.hostname not in hosts
                or url.netloc != url.hostname
                or url.fragment
            ):
                raise HTTPException(
                    400, "Account request must use this provider’s approved HTTPS API"
                )
            config = self.configuration(provider)
            saved = self.vault.open("account:" + account_id, row["body"])
            if (
                not config
                or config["clientId"] != saved["clientId"]
                or (row["state"] == "reconnect" and capability != "email")
            ):
                raise HTTPException(409, "Reconnect this account in Settings")
            mail = saved.get("email") or {}
            if capability == "email":
                if (
                    provider != "google"
                    or method != "GET"
                    or url.hostname != "gmail.googleapis.com"
                    or not url.path.startswith("/gmail/v1/users/me/messages")
                ):
                    raise HTTPException(
                        400, "Email access only supports reading Gmail messages"
                    )
                if (
                    GMAIL_READONLY
                    not in str(mail.get("token", {}).get("scope", "")).split()
                    or mail.get("state") == "reconnect"
                ):
                    raise HTTPException(409, "Enable read-only email in Settings")
            elif url.hostname == "gmail.googleapis.com":
                raise HTTPException(
                    400, "Gmail requires explicit read-only email access"
                )
            token = mail["token"] if capability == "email" else saved["token"]
            try:
                async with self.client(
                    provider,
                    config,
                    token=token,
                    scope=token.get(
                        "scope",
                        EMAIL_SCOPE
                        if capability == "email"
                        else PROVIDERS[provider]["scope"],
                    ),
                ) as client:
                    if token.get("expires_at", 0) <= self.clock() + 60:
                        if not token.get("refresh_token"):
                            raise ValueError("Refresh token unavailable")
                        refreshed = await client.refresh_token(
                            PROVIDERS[provider]["token"],
                            refresh_token=token["refresh_token"],
                        )
                        old_token = token
                        token = dict(refreshed)
                        token.setdefault("refresh_token", old_token["refresh_token"])
                        if "scope" in old_token:
                            token.setdefault("scope", old_token["scope"])
                        if capability == "email":
                            if (
                                GMAIL_READONLY
                                not in str(token.get("scope", "")).split()
                            ):
                                raise ValueError("Email scope no longer granted")
                            mail["token"] = token
                        else:
                            saved["token"] = token
                        with self.store.connect() as db:
                            db.execute(
                                "UPDATE accounts SET body=? WHERE id=?",
                                (
                                    self.vault.seal("account:" + account_id, saved),
                                    account_id,
                                ),
                            )
                    response = await client.request(method, path, **kwargs)
                    if response.status_code == 401:
                        raise ValueError("Account authorization rejected")
                    if (
                        not response.is_success
                        and response.status_code not in accepted_statuses
                    ):
                        raise HTTPException(
                            502, "Account provider HTTP " + str(response.status_code)
                        )
                    if capability != "email":
                        with self.store.connect() as db:
                            db.execute(
                                "UPDATE accounts SET checked=?,error=NULL,state='connected' WHERE id=?",
                                (self.clock(), account_id),
                            )
                    return response
            except HTTPException:
                raise
            except (OAuthError, ValueError):
                with self.store.connect() as db:
                    if capability == "email":
                        mail["state"] = "reconnect"
                        db.execute(
                            "UPDATE accounts SET body=? WHERE id=?",
                            (
                                self.vault.seal("account:" + account_id, saved),
                                account_id,
                            ),
                        )
                    else:
                        db.execute(
                            "UPDATE accounts SET state='reconnect',error='Reconnect to renew account access' WHERE id=?",
                            (account_id,),
                        )
                raise HTTPException(409, "Reconnect to renew account access") from None
            except Exception:
                raise HTTPException(
                    502, "Account provider is unreachable; retry later"
                ) from None

    async def verify(self, account_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT provider FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Account not found")
        await self.authorized(account_id, "GET", PROVIDERS[row["provider"]]["profile"])
        return {"verified": True, "checkedAt": self.clock()}


def router(accounts):
    routes = APIRouter(prefix="/api/accounts")

    @routes.get("")
    async def status():
        return accounts.status()

    @routes.put("/providers/{provider}")
    async def configure(provider: Provider, body: Configuration):
        return await accounts.save_config(provider, body)

    @routes.delete("/providers/{provider}")
    async def forget_application(provider: Provider):
        async with accounts.locks[provider]:
            with accounts.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM oauth_states WHERE provider=?", (provider,))
                db.execute("DELETE FROM accounts WHERE provider=?", (provider,))
                db.execute(
                    "DELETE FROM settings WHERE key=?", ("account_config:" + provider,)
                )
        return {"removed": True, "providerGrantRevoked": False}

    @routes.post("/providers/{provider}/authorize")
    async def authorize(provider: Provider, request: Request):
        return await accounts.start(
            provider,
            request.headers.get("origin"),
            request.cookies.get("leam_session", ""),
        )

    @routes.get("/oauth/{provider}/callback")
    async def callback(
        provider: Provider, state: str = "", code: str = "", error: str = ""
    ):
        location = await accounts.callback(provider, state, code, error)
        response = RedirectResponse(location, status_code=303)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @routes.post("/{key}/email/authorize")
    async def authorize_email(key: str, request: Request):
        return await accounts.start_email(
            key, request.headers.get("origin"), request.cookies.get("leam_session", "")
        )

    @routes.post("/{key}/verify")
    async def verify(key: str):
        return await accounts.verify(key)

    @routes.delete("/{key}")
    async def disconnect(key: str):
        provider = accounts.provider_of(key)
        async with accounts.locks[provider]:
            with accounts.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM oauth_states WHERE provider=?", (provider,))
                db.execute("DELETE FROM accounts WHERE id=?", (key,))
        return {"removed": True, "providerGrantRevoked": False}

    return routes
