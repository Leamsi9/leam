"""Opt-in identity proof for the pinned runtime -> MCP -> domain boundary.

The ordinary tool credential never authorizes product-origin metadata. A separate
operator-provisioned runtime credential is required; no keys are created here.
"""

import hashlib
import hmac
import json
import os
import stat
import time
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ConfigDict, Field, field_validator

from .commitments import Input

META_KEY = "io.ironclaw/host-scope"
SUPPORTED_TOOLS = {"leam_propose", "leam_operation_schema"}


class HostScope(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    version: Literal[1]
    tenantId: str = Field(min_length=1, max_length=256)
    userId: str = Field(min_length=1, max_length=256)
    threadId: str = Field(min_length=1, max_length=200)
    invocationId: str = Field(min_length=1, max_length=200)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported host scope version")
        return value

    @field_validator("tenantId", "userId", "threadId", "invocationId")
    @classmethod
    def opaque_id(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid host scope identity")
        return value


class ScopeProof(Input):
    expiresAt: int = Field(strict=True, ge=1)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


def validate_credential(value):
    if not isinstance(value, str) or len(value) < 40 or len(value) > 512:
        raise ValueError("A separate private runtime scope credential is required")
    return value


def installation_credential(directory, *, enabled=False):
    """Derive, never create, a separate bearer from the existing private vault root."""
    if not enabled:
        return None
    fd = os.open(
        directory / "accounts-key", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Runtime scope requires a private installation key")
        master = source.read(33)
        if len(master) != 32:
            raise ValueError("Runtime scope requires the existing installation key")
    return hmac.new(
        master, b"leam-pinned-runtime-scope-bearer-v1", hashlib.sha256
    ).hexdigest()


def configured_credential(directory):
    setting = os.environ.get("LEAM_RUNTIME_SCOPE_ENABLED", "0")
    if setting not in ("0", "1"):
        raise ValueError("LEAM_RUNTIME_SCOPE_ENABLED must be0 or1")
    return installation_credential(directory, enabled=setting == "1")


def signature(credential, tool, arguments, scope, expires):
    payload = json.dumps(
        {
            "purpose": "leam-runtime-scope-forwarding-v1",
            "tool": tool,
            "arguments": arguments,
            "hostScope": scope.model_dump(),
            "expiresAt": expires,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    proof_key = hmac.new(
        credential.encode(),
        b"leam-runtime-scope-forwarding-proof-key-v1",
        hashlib.sha256,
    ).digest()
    return hmac.new(proof_key, payload, hashlib.sha256).hexdigest()


def forwarding_proof(credential, tool, arguments, scope, *, now=None):
    expires = int(time.time() if now is None else now) + 30
    return ScopeProof(
        expiresAt=expires,
        signature=signature(credential, tool, arguments, scope, expires),
    )


class RuntimeScopeGuard:
    """Explicit composition opt-in; request metadata cannot enable this guard."""

    def __init__(self, store, runtime, credential, *, clock=time.time):
        self.store, self.runtime, self.clock = store, runtime, clock
        self.credential = validate_credential(credential)

    async def origin(self, tool, arguments, scope, proof):
        now = int(self.clock())
        if (
            tool not in SUPPORTED_TOOLS
            or proof is None
            or not now <= proof.expiresAt <= now + 30
            or not hmac.compare_digest(
                signature(self.credential, tool, arguments, scope, proof.expiresAt),
                proof.signature,
            )
        ):
            raise HTTPException(403, "Trusted runtime scope could not be verified")
        pair = [scope.tenantId, scope.userId]
        if self.store.get("today-reconciliation:owner") != pair:
            raise HTTPException(
                403, "Trusted runtime owner does not match this installation"
            )
        if tool == "leam_propose" and arguments.get("threadId") != scope.threadId:
            raise HTTPException(
                403, "Proposal conversation differs from its runtime owner"
            )
        binding = self.store.get("companion-agenda:" + scope.threadId)
        if binding is None:
            return None
        try:
            page = await self.runtime.request(
                "GET",
                "/threads/" + quote(scope.threadId, safe="") + "/timeline?limit=1",
            )
        except Exception as error:
            raise HTTPException(
                503, "Conversation ownership verification is unavailable"
            ) from error
        thread = page.get("thread") if isinstance(page, dict) else None
        owner = thread.get("scope") if isinstance(thread, dict) else None
        if (
            not isinstance(owner, dict)
            or thread.get("thread_id") != scope.threadId
            or [owner.get("tenant_id"), owner.get("owner_user_id")] != pair
        ):
            raise HTTPException(403, "Conversation owner could not be verified")
        # A concurrent retirement or owner change must not grant Today authority.
        if (
            self.store.get("today-reconciliation:owner") != pair
            or self.store.get("companion-agenda:" + scope.threadId) != binding
        ):
            raise HTTPException(
                409, "Conversation binding changed; retry after refreshing"
            )
        return "today"
