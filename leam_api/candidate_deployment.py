"""Operator-owned candidate launch pointer. Browser inputs never choose host paths."""

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .backups import fsync_directory, keys, private_read

ENVIRONMENT_KEYS = frozenset(
    {
        "LEAM_ORIGINS",
        "LEAM_CODING_WORKSPACE",
        "LEAM_RUNTIME_URL",
        "LEAM_RUNTIME_TOKEN_FILE",
        "LEAM_CODEX_IDE_INSTALLATION",
        "LEAM_CODEX_IDE_SOCKET",
        "LEAM_SHARED_CODEX_THREAD",
        "LEAM_API_URL",
    }
)
FIELDS = frozenset(
    {
        "version",
        "installationId",
        "revision",
        "generationId",
        "dataDirectory",
        "releaseDirectory",
        "sourceCommit",
        "clientIndexSha256",
        "pythonEnvironment",
        "requirementsSha256",
        "runtime",
        "environment",
        "environmentSha256",
        "mcpIdentity",
    }
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def uuid_value(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid operation identity")
    return value


def sha(value, length=64):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None
    )


@dataclass(frozen=True)
class Roots:
    base: Path

    @property
    def data(self):
        return self.base / "data"

    @property
    def releases(self):
        return self.base / "releases"

    @property
    def venvs(self):
        return self.base / "venvs"

    @property
    def recovery(self):
        return self.base / "recovery"

    @classmethod
    def installed(cls):
        return cls(Path.home() / ".local/share/leam-next")


def private_directory(path):
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or path.resolve() != path
    ):
        raise ValueError("Expected a private owned directory without symlinks")


def within(value, root, *, private=False):
    path = Path(value)
    if (
        not path.is_absolute()
        or path.resolve() != path
        or not path.is_relative_to(root)
        or path == root
        or not path.is_dir()
    ):
        raise ValueError("Candidate path is outside its fixed installation root")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Candidate path is not owned and protected")
    if private:
        private_directory(path)
    return path


def atomic_json(path, value):
    content = json.dumps(value, sort_keys=True).encode()
    if len(content) > 256 * 1024:
        raise ValueError("Recovery record is too large")
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def installation_identity(directory):
    saved = keys(directory)
    if "tools-token" not in saved or "mcp-tls/certificate.pem" not in saved:
        raise ValueError("Restore requires the current pinned MCP identity")
    return {
        "toolsTokenSha256": hashlib.sha256(saved["tools-token"]).hexdigest(),
        "certificateSha256": hashlib.sha256(
            saved["mcp-tls/certificate.pem"]
        ).hexdigest(),
    }


class CandidateDeployment:
    def __init__(self, roots=None):
        self.roots = roots or Roots.installed()
        self.path = self.roots.recovery / "candidate.json"
        self._lock_held = False
        private_directory(self.roots.recovery)

    @contextmanager
    def lock(self):
        fd = os.open(
            self.roots.recovery / "candidate-operation.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        acquired = False
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("Invalid candidate operation lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(
                    "Another candidate deployment or restore is in progress"
                ) from error
            acquired = True
            self._lock_held = True
            yield
        finally:
            if acquired:
                self._lock_held = False
            os.close(fd)

    def validate(self, value):
        if (
            not isinstance(value, dict)
            or set(value) != FIELDS
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ValueError("Unsupported candidate descriptor")
        if type(value["revision"]) is not int or value["revision"] < 1:
            raise ValueError("Invalid candidate revision")
        uuid_value(value["installationId"])
        uuid_value(value["generationId"])
        data = within(value["dataDirectory"], self.roots.data, private=True)
        release = within(value["releaseDirectory"], self.roots.releases)
        python = within(value["pythonEnvironment"], self.roots.venvs)
        if not sha(value["sourceCommit"], 40) or release.name != value["sourceCommit"]:
            raise ValueError("Release identity does not match immutable directory")
        manifest = json.loads((release / "release.json").read_text())
        for key in (
            "sourceCommit",
            "clientIndexSha256",
            "pythonEnvironment",
            "requirementsSha256",
        ):
            if manifest.get(key) != value[key]:
                raise ValueError("Release manifest changed")
        if (
            not sha(value["requirementsSha256"])
            or python.name != value["requirementsSha256"]
            or manifest.get("installedPackagesSha256") != value["requirementsSha256"]
            or not (python / "bin/python").is_file()
        ):
            raise ValueError("Python environment identity is incompatible")
        if (
            hashlib.sha256(
                (release / "apps/web/dist/index.html").read_bytes()
            ).hexdigest()
            != value["clientIndexSha256"]
        ):
            raise ValueError("Public client does not match release identity")
        runtime = value["runtime"]
        if (
            not isinstance(runtime, dict)
            or set(runtime) != {"sourceCommit", "binarySha256", "ceilingSha256"}
            or not sha(runtime["sourceCommit"], 40)
            or not sha(runtime["binarySha256"])
            or not sha(runtime["ceilingSha256"])
        ):
            raise ValueError("Invalid reviewed runtime identity")
        environment = value["environment"]
        if (
            not isinstance(environment, dict)
            or not set(environment) <= ENVIRONMENT_KEYS
            or not all(
                isinstance(v, str) and len(v) <= 4096 and "\0" not in v
                for v in environment.values()
            )
            or value["environmentSha256"] != digest(environment)
        ):
            raise ValueError("Unsupported launch environment")
        shared = environment.get("LEAM_SHARED_CODEX_THREAD", "").strip()
        if shared:
            try:
                UUID(shared)
            except ValueError:
                raise ValueError("Invalid shared Coding launch binding") from None
        if value["mcpIdentity"] != installation_identity(data):
            raise ValueError(
                "Active MCP identity no longer matches its pinned descriptor"
            )
        return value

    def read(self):
        return self.validate(json.loads(private_read(self.path, 65536)))

    def adopt(self, data, release, runtime, environment):
        """Operator-only initialization. Never called by a browser route."""
        with self.lock():
            if self.path.exists() or self.path.is_symlink():
                raise ValueError("Candidate descriptor already exists")
            manifest = json.loads((release / "release.json").read_text())
            value = {
                "version": 1,
                "installationId": str(uuid4()),
                "revision": 1,
                "generationId": str(uuid4()),
                "dataDirectory": str(data),
                "releaseDirectory": str(release),
                **{
                    k: manifest[k]
                    for k in (
                        "sourceCommit",
                        "clientIndexSha256",
                        "pythonEnvironment",
                        "requirementsSha256",
                    )
                },
                "runtime": runtime,
                "environment": environment,
                "environmentSha256": digest(environment),
                "mcpIdentity": installation_identity(data),
            }
            self.validate(value)
            atomic_json(self.path, value)
            return value

    def replace_locked(self, value, expected):
        """Caller must hold the shared lock for the whole deployment/restore."""
        if not self._lock_held:
            raise ValueError("Candidate changes require the shared operation lock")
        current = self.read()
        if digest(current) != digest(expected):
            raise ValueError("Candidate generation changed; review a fresh preview")
        value = {**value, "revision": current["revision"] + 1}
        self.validate(value)
        atomic_json(self.path, value)
        return value

    def prepare_release_locked(self, release, runtime, expected):
        """Normal deployment seam; caller retains lock through stop/switch/start."""
        if not self._lock_held or digest(self.read()) != digest(expected):
            raise ValueError("Candidate changed or deployment lock is missing")
        operations = self.roots.recovery / "restore-operations"
        if operations.exists():
            private_directory(operations)
            for path in operations.glob("*.json"):
                record = json.loads(private_read(path, 256 * 1024))
                if record.get("state") not in {"ready_for_uat", "rolled_back"}:
                    raise ValueError("Resolve the unfinished restore before deploying")
        release = within(str(release), self.roots.releases)
        manifest = json.loads((release / "release.json").read_text())
        value = {
            **expected,
            "releaseDirectory": str(release),
            "runtime": runtime,
            **{
                k: manifest[k]
                for k in (
                    "sourceCommit",
                    "clientIndexSha256",
                    "pythonEnvironment",
                    "requirementsSha256",
                )
            },
        }
        return self.validate(value)

    def launch_plan(self, role):
        if role not in {"app", "mcp"}:
            raise ValueError("Unknown fixed candidate role")
        value = self.read()
        data = Path(value["dataDirectory"])
        environment = {
            **value["environment"],
            "LEAM_DATA_DIR": str(data),
            "PYTHONPATH": value["releaseDirectory"],
        }
        module = (
            "leam_api.app:application"
            if role == "app"
            else "leam_api.mcp_server:application"
        )
        argv = [
            str(Path(value["pythonEnvironment"]) / "bin/python"),
            "-m",
            "uvicorn",
            module,
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "46400" if role == "app" else "46420",
            "--no-access-log",
            "--no-proxy-headers",
        ]
        if role == "app":
            argv += ["--timeout-graceful-shutdown", "10"]
        if role == "mcp":
            argv += [
                "--ssl-keyfile",
                str(data / "mcp-tls/key.pem"),
                "--ssl-certfile",
                str(data / "mcp-tls/certificate.pem"),
            ]
        return {
            "argv": argv,
            "environment": environment,
            "cwd": value["releaseDirectory"],
        }
