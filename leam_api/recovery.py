"""Independent recovery plane for explicitly allowlisted Leam candidate units."""

import asyncio
import hashlib
import os
import secrets
import stat
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Operator-owned inventory: neither a request nor a model can supply a unit/command.
SERVICES = {
    "app": ("Leam app", "leam-next-candidate.service", 46400),
    "mcp": ("Companion tools", "leam-next-mcp.service", 46420),
    "runtime": ("Companion runtime", "leam-next-runtime.service", 46410),
}
COOKIE = "leam_recovery_session"
ASSETS = Path(__file__).with_name("recovery_assets")


class ServiceControl:
    async def _systemctl(self, *arguments):
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/systemctl",
            "--user",
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise HTTPException(
                504, "Service operation timed out; inspect status before retrying"
            )
        if process.returncode != 0:
            raise HTTPException(
                503, "Candidate service manager could not complete this operation"
            )
        return stdout.decode("utf-8", errors="replace")[:8192]

    async def status(self):
        async def inspect(service_id, service):
            name, unit, port = service
            try:
                output = await self._systemctl(
                    "show", unit, "--property=LoadState,ActiveState,SubState"
                )
                values = dict(
                    line.split("=", 1) for line in output.splitlines() if "=" in line
                )
                loaded = values.get("LoadState", "unknown")
                active = values.get("ActiveState", "unknown")
                sub = values.get("SubState", "unknown")
            except (HTTPException, OSError):
                loaded = active = sub = "unavailable"
            reachable = False
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=2
                )
                writer.close()
                await writer.wait_closed()
                reachable = True
            except (OSError, TimeoutError):
                pass
            return {
                "id": service_id,
                "name": name,
                "unit": unit,
                "loaded": loaded,
                "active": active,
                "sub": sub,
                "reachable": reachable,
            }

        return await asyncio.gather(
            *(inspect(key, value) for key, value in SERVICES.items())
        )

    async def operate(self, service_id, action):
        if service_id not in SERVICES or action not in {"start", "restart"}:
            raise HTTPException(404, "Unknown recovery operation")
        await self._systemctl(action, SERVICES[service_id][1])


def private_read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as file:
        info = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 4096
        ):
            raise RuntimeError(
                "Recovery credential file must be a private owned regular file"
            )
        return file.read()


def private_create(path, content):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=128)


class Setup(Login):
    password: SecretStr = Field(min_length=12, max_length=128)
    bootstrap: SecretStr = Field(min_length=20, max_length=256)


def create_recovery_app(directory: Path, origins: set[str], *, control=None):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RuntimeError("Recovery state directory must be private and owned")
    if not origins or any(
        urlsplit(origin).scheme not in {"http", "https"}
        or urlsplit(origin).path
        or urlsplit(origin).query
        or urlsplit(origin).fragment
        or urlsplit(origin).username
        for origin in origins
    ):
        raise RuntimeError("Recovery requires exact HTTP(S) origins")
    hosts = {urlsplit(origin).netloc.lower() for origin in origins}
    secure = all(origin.startswith("https://") for origin in origins)
    password_path = directory / "password-hash"
    bootstrap_path = directory / "bootstrap-token"
    password_hash = private_read(password_path) if password_path.exists() else None
    if not password_hash:
        if not bootstrap_path.exists():
            private_create(bootstrap_path, secrets.token_urlsafe(32))
        private_read(bootstrap_path)
    sessions = {}
    attempts = deque()
    actions = deque()
    lock = asyncio.Lock()
    hasher = PasswordHasher()
    control = control or ServiceControl()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def signed_in(request):
        token = request.cookies.get(COOKIE, "")
        return bool(
            token
            and len(token) < 256
            and sessions.get(hashlib.sha256(token.encode()).hexdigest(), 0)
            > time.time()
        )

    def rate_limit(queue, count, seconds):
        now = time.monotonic()
        while queue and queue[0] < now - seconds:
            queue.popleft()
        if len(queue) >= count:
            raise HTTPException(429, "Too many attempts; wait before retrying")
        queue.append(now)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        return JSONResponse(
            {
                "detail": [
                    {key: item[key] for key in ("loc", "msg", "type") if key in item}
                    for item in error.errors()
                ]
            },
            status_code=422,
        )

    @app.middleware("http")
    async def security(request, call_next):
        if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse(
                {"detail": "Recovery listener is loopback only"}, status_code=403
            )
        if request.headers.get("host", "").lower() not in hosts:
            return JSONResponse({"detail": "Untrusted Host"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("origin") not in origins:
                return JSONResponse(
                    {"detail": "Untrusted or missing Origin"}, status_code=403
                )
            chunks = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 8192:
                    return JSONResponse(
                        {"detail": "Request too large"}, status_code=413
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        if (
            request.url.path.startswith("/api/")
            and request.url.path
            not in {"/api/auth/status", "/api/auth/setup", "/api/auth/login"}
            and not signed_in(request)
        ):
            return JSONResponse({"detail": "Sign in to recovery"}, status_code=401)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    def sign_in(response):
        now = time.time()
        for key in list(sessions):
            if sessions[key] <= now:
                sessions.pop(key)
        if len(sessions) >= 32:
            sessions.pop(min(sessions, key=sessions.get))
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 12 * 3600
        response.set_cookie(
            COOKIE,
            token,
            max_age=12 * 3600,
            httponly=True,
            secure=secure,
            samesite="strict",
        )

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        return {
            "authenticated": signed_in(request),
            "configured": password_hash is not None,
        }

    @app.post("/api/auth/setup")
    async def setup(body: Setup, response: Response):
        nonlocal password_hash
        rate_limit(attempts, 5, 60)
        async with lock:
            if password_hash is not None:
                raise HTTPException(409, "Recovery is already paired")
            if not secrets.compare_digest(
                body.bootstrap.get_secret_value(), private_read(bootstrap_path).strip()
            ):
                raise HTTPException(401, "Invalid recovery pairing code")
            encoded = await asyncio.to_thread(
                hasher.hash, body.password.get_secret_value()
            )
            private_create(password_path, encoded)
            password_hash = encoded
            bootstrap_path.unlink()
        sign_in(response)
        return {"paired": True}

    @app.post("/api/auth/login")
    async def login(body: Login, response: Response):
        rate_limit(attempts, 5, 60)
        if not password_hash:
            raise HTTPException(409, "Recovery requires pairing")
        try:
            await asyncio.to_thread(
                hasher.verify, password_hash, body.password.get_secret_value()
            )
        except VerificationError:
            raise HTTPException(401, "Invalid recovery password")
        sign_in(response)
        return {"authenticated": True}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE, "")
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        response.delete_cookie(COOKIE, secure=secure, httponly=True, samesite="strict")
        return {"authenticated": False}

    @app.get("/api/services")
    async def service_status():
        return {"services": await control.status(), "checkedAt": time.time()}

    @app.post("/api/services/{service_id}/{action}")
    async def operate(service_id: str, action: str):
        if service_id not in SERVICES or action not in {"start", "restart"}:
            raise HTTPException(404, "Unknown recovery operation")
        rate_limit(actions, 6, 60)
        async with lock:
            try:
                await control.operate(service_id, action)
            except OSError as error:
                raise HTTPException(503, "Service manager unavailable") from error
        return {"services": await control.status(), "checkedAt": time.time()}

    @app.get("/")
    async def index():
        return FileResponse(ASSETS / "index.html")

    @app.get("/recovery.js")
    async def script():
        return FileResponse(ASSETS / "recovery.js", media_type="text/javascript")

    @app.get("/recovery.css")
    async def stylesheet():
        return FileResponse(ASSETS / "recovery.css", media_type="text/css")

    return app


def application():
    return create_recovery_app(
        Path(
            os.environ.get(
                "LEAM_RECOVERY_DIR",
                str(Path.home() / ".local/share/leam-next/recovery"),
            )
        ),
        set(
            os.environ.get("LEAM_RECOVERY_ORIGINS", "http://127.0.0.1:46430").split(",")
        ),
    )
