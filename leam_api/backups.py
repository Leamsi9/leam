"""Explicit product-state backups and no-replace restoration into a fresh directory."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .local_tls import publish_directory
from .local_tls import validate as validate_tls

MAX_BYTES = 1024 * 1024 * 1024
ARCHIVE_OVERHEAD = 1024 * 1024
CHUNK_BYTES = 1024 * 1024
KEY_FILES = (
    "accounts-key",
    "push-vapid.pem",
    "tools-token",
    "bootstrap-token",
    "mcp-tls/certificate.pem",
    "mcp-tls/key.pem",
)
ALLOWED_FILES = frozenset(("leam.sqlite3", *KEY_FILES))


@lru_cache(maxsize=1)
def expected_schema():
    # Build the schema from its owners, including optional additive modules.
    from .attachments import AttachmentStore
    from .background_jobs import initialize as initialize_background_jobs
    from .backlog import Backlog
    from .routines import Routines
    from .store import Store
    from .today_reconciliation import initialize as initialize_reconciliation
    from .updates import Updates
    from .usage import UsageService

    with tempfile.TemporaryDirectory(prefix="leam-schema-") as temporary:
        store = Store(Path(temporary))
        with store.connect() as db:
            required = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        AttachmentStore(store)
        # Additive usage ledgers may be absent from earlier compatible snapshots.
        required -= {"usage_admissions", "usage_observations", "usage_codex_cursors"}
        # Older archives predate mail caching; present tables still match exact SQL.
        required.discard("email_snapshots")
        Backlog(store)
        Routines(store)
        Updates(store)
        UsageService(store)
        initialize_reconciliation(store)
        initialize_background_jobs(store)
        with store.connect() as db:
            statements = {
                (row[0], row[1]): " ".join(row[2].split())
                for row in db.execute(
                    "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                )
            }
    return required, statements


@contextmanager
def private_file(path, limit):
    # O_NOFOLLOW prevents file symlinks; parents are checked separately for keys.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError("Backup source must be a regular private file") from error
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Backup source must be a regular private file")
        if info.st_size > limit:
            raise ValueError("Backup source exceeds the size limit")
        yield file


def stream_copy(source, destination, limit):
    """Copy/hash bounded chunks and enforce actual size, including source growth."""
    size, digest = 0, hashlib.sha256()
    while chunk := source.read(min(CHUNK_BYTES, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise ValueError("Backup source exceeds the size limit")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def private_copy(source, target, limit):
    with private_file(source, limit) as file:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            metadata = stream_copy(file, output, limit)
            output.flush()
            os.fsync(output.fileno())
            return metadata


def private_read(path, limit=MAX_BYTES):
    with private_file(path, limit) as file:
        data = file.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Backup source exceeds the size limit")
        return data


def keys(directory):
    result = {}
    for name in KEY_FILES:
        path = directory / name
        if path.parent != directory and path.parent.is_symlink():
            raise ValueError("Backup key directory must not be a symlink")
        if path.exists() or path.is_symlink():
            result[name] = private_read(path, 65536)
    if "accounts-key" not in result or len(result["accounts-key"]) != 32:
        raise ValueError("A valid account vault key is required for backup")
    if ("mcp-tls/key.pem" in result) != ("mcp-tls/certificate.pem" in result):
        raise ValueError("Both TLS identity files are required")
    if "mcp-tls/key.pem" in result:
        validate_tls(directory / "mcp-tls")
    if "push-vapid.pem" in result:
        try:
            vapid = serialization.load_pem_private_key(
                result["push-vapid.pem"], password=None
            )
            if not isinstance(vapid, ec.EllipticCurvePrivateKey) or not isinstance(
                vapid.curve, ec.SECP256R1
            ):
                raise TypeError("Unsupported push key")
        except (ValueError, TypeError) as error:
            raise ValueError("Backup push signing key is invalid") from error
    if "tools-token" in result:
        try:
            token = result["tools-token"].decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise ValueError("Backup tool credential is invalid") from error
        if len(token) < 40 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError("Backup tool credential is invalid")
    return result


def validate_database(path):
    try:
        with sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        ) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup database integrity check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Backup database has broken references")
            if db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type IN ('trigger','view') LIMIT 1"
            ).fetchone():
                raise ValueError(
                    "Backup database contains unsupported executable schema"
                )
            required, expected = expected_schema()
            actual = {
                (row[0], row[1]): " ".join(row[2].split())
                for row in db.execute(
                    "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not required <= {
                name for kind, name in actual if kind == "table"
            } or any(
                expected.get(key) != definition for key, definition in actual.items()
            ):
                raise ValueError("Backup database is not a compatible Leam schema")

    except sqlite3.Error as error:
        raise ValueError("Backup database cannot be read") from error


def validate_vault(path, key):
    cipher = AESGCM(key)

    def decrypt(label, value):
        raw = base64.urlsafe_b64decode(value)
        return json.loads(cipher.decrypt(raw[:12], raw[12:], label.encode()))

    try:
        with sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        ) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            for name, raw in db.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'account_config:%'"
            ):
                decrypt("config:" + name.split(":", 1)[1], json.loads(raw))
            for name, raw in db.execute(
                "SELECT key,value FROM settings WHERE substr(key,1,16)='email-decisions:'"
            ):
                from .email_actionability import validate_cache

                validate_cache(decrypt(name, json.loads(raw)))
            for name, raw in db.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'email-draft:%'"
            ):
                from .email_drafts import PREFIX, validate_saved

                validate_saved(decrypt(name, json.loads(raw)), name[len(PREFIX) :])
            for name, raw in db.execute("SELECT key,value FROM settings WHERE key LIKE 'ticket-auto-handoff:%'"):
                envelope = json.loads(raw)
                payload = decrypt(name, envelope["sealed"])
                if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or len(payload["text"]) > 8000:
                    raise ValueError("Backup ticket handoff request is invalid")
            for key_id, raw in db.execute("SELECT id,body FROM accounts"):
                decrypt("account:" + key_id, raw)
            if db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='background_jobs'"
            ).fetchone():
                for identity, raw in db.execute(
                    "SELECT id,sealed FROM background_jobs"
                ):
                    payload = decrypt("background:" + identity, raw)
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(payload.get("task"), str)
                        or not isinstance(payload.get("context"), str)
                        or len(payload["task"].encode()) > 20000
                        or len(payload["context"].encode()) > 4096
                        or payload.get("result") is not None
                        and (
                            not isinstance(payload["result"], str)
                            or len(payload["result"]) > 12000
                        )
                    ):
                        raise ValueError("Backup background job payload is invalid")
            if db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='email_snapshots'"
            ).fetchone():
                from .email import MESSAGE_LIMIT, REBUILD_LIMIT

                for account_id, raw in db.execute(
                    "SELECT account_id,body FROM email_snapshots"
                ):
                    snapshot = decrypt("email-snapshot:" + account_id, raw)
                    # Validate the cache envelope, allowing additive item/derived metadata.
                    if (
                        not isinstance(snapshot, dict)
                        or not isinstance(snapshot.get("items"), list)
                        or type(snapshot.get("limit", MESSAGE_LIMIT)) is not int
                        or snapshot.get("limit", MESSAGE_LIMIT)
                        not in {MESSAGE_LIMIT, REBUILD_LIMIT}
                        or len(snapshot["items"]) > snapshot.get("limit", MESSAGE_LIMIT)
                        or any(not isinstance(item, dict) for item in snapshot["items"])
                        or not isinstance(snapshot.get("truncated"), bool)
                    ):
                        raise ValueError("Invalid email snapshot")
    except (InvalidTag, ValueError, TypeError, sqlite3.Error) as error:
        raise ValueError(
            "Backup vault key does not decrypt saved account state"
        ) from error


def fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Backups:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        self.directory = store.path.parent / "backups"
        if self.directory.is_symlink():
            raise ValueError("Backup directory must not be a symlink")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)

    def path(self, key):
        try:
            if str(uuid.UUID(key)) != key:
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid backup identity") from error
        return self.directory / f"{key}.zip"

    @contextmanager
    def locked(self):
        fd = os.open(
            self.directory / ".policy.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "rb") as lock:
            info = os.fstat(lock.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("Backup lock must remain a private regular file")
            deadline = time.monotonic() + 30
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "Another backup operation is still running; retry"
                        ) from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def day(self, timestamp):
        # No global app timezone exists yet. Honour the host's configured zone,
        # including TZ, rather than borrowing one calendar's or task's timezone.
        return datetime.fromtimestamp(timestamp).astimezone().date()

    def inventory(self):
        items = []
        for path in self.directory.glob("*.zip"):
            try:
                items.append(self.describe(path.stem))
            except (OSError, ValueError):
                continue
        return sorted(
            items, key=lambda item: (item["createdAt"], item["id"]), reverse=True
        )

    def validate_reuse(self, key):
        before = keys(self.store.path.parent)
        with tempfile.TemporaryDirectory(
            prefix=".validate-", dir=self.directory
        ) as temporary:
            with private_file(self.path(key), MAX_BYTES + ARCHIVE_OVERHEAD) as archive:
                unpack(archive, Path(temporary))
            if (
                keys(Path(temporary)) != before
                or keys(self.store.path.parent) != before
            ):
                raise ValueError(
                    "Today's backup has different installation keys; operator recovery required"
                )

    def prune(self, keep):
        # Called only under the policy lock after a validated, durable snapshot.
        # Keep the selected snapshot even if an old archive has a future mtime.
        inventory = self.inventory()
        selected = next(item for item in inventory if item["id"] == keep)
        days = {self.day(selected["createdAt"])}
        retained = {keep}
        for item in inventory:
            day = self.day(item["createdAt"])
            if item["id"] in retained:
                continue
            if len(retained) < 3 and day not in days:
                retained.add(item["id"])
                days.add(day)
            else:
                self.path(item["id"]).unlink()
        fsync_directory(self.directory)

    def create(self, *, prune=True):
        with self.locked():
            now = self.clock()
            today = [
                item
                for item in self.inventory()
                if self.day(item["createdAt"]) == self.day(now)
            ]
            if today:
                result = today[0]
                self.validate_reuse(result["id"])
                result["reused"] = True
            else:
                result = self._create_snapshot(now)
                result["reused"] = False
            if prune:
                self.prune(result["id"])
            return result

    def _create_snapshot(self, now):
        key = str(uuid.uuid4())
        with tempfile.TemporaryDirectory(
            prefix=".snapshot-", dir=self.directory
        ) as temporary:
            temp = Path(temporary)
            original_keys = keys(self.store.path.parent)
            snapshot = temp / "leam.sqlite3"
            snapshot.touch(mode=0o600)
            with (
                sqlite3.connect(
                    self.store.path.resolve().as_uri() + "?mode=ro", uri=True
                ) as source,
                sqlite3.connect(snapshot) as destination,
            ):
                source.backup(destination)
            validate_database(snapshot)
            validate_vault(snapshot, original_keys["accounts-key"])
            if original_keys != keys(self.store.path.parent):
                raise ValueError("Installation keys changed during backup; retry")
            key_bytes = sum(map(len, original_keys.values()))
            if snapshot.stat().st_size + key_bytes > MAX_BYTES:
                raise ValueError("Backup exceeds the 1 GiB product-state limit")
            manifest = {
                "version": 1,
                "product": "leam",
                "createdAt": now,
                "files": {
                    name: {
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for name, data in original_keys.items()
                },
            }
            output = temp / "backup.zip"
            output.touch(mode=0o600)
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_STORED
            ) as archive:
                with (
                    private_file(snapshot, MAX_BYTES - key_bytes) as source,
                    archive.open("leam.sqlite3", "w") as destination,
                ):
                    manifest["files"]["leam.sqlite3"] = stream_copy(
                        source, destination, MAX_BYTES - key_bytes
                    )
                for name, data in original_keys.items():
                    archive.writestr(name, data)
                archive.writestr("manifest.json", json.dumps(manifest).encode())
            with output.open("rb") as file:
                os.fsync(file.fileno())
            os.utime(output, (now, now))
            os.link(output, self.path(key))
            fsync_directory(self.directory)
        return self.describe(key)

    def describe(self, key):
        path = self.path(key)
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Backup must remain a private regular file")
        return {
            "id": key,
            "createdAt": info.st_mtime,
            "bytes": info.st_size,
            "downloadUrl": f"/api/backups/{key}/download",
        }

    def open_download(self, key):
        # Opening while locked pins this inode before retention can unlink it.
        with self.locked():
            self.describe(key)
            fd = os.open(self.path(key), os.O_RDONLY | os.O_NOFOLLOW)
            return os.fdopen(fd, "rb")

    def list(self):
        return {
            "items": self.inventory()[:100],
            "scope": "Leam SQLite state and installation keys",
            "restoreMode": "new-directory-offline",
            "policy": {
                "frequency": "one-per-local-calendar-day",
                "keep": 3,
                "timezone": os.environ.get("TZ") or "host-local",
                "scheduled": False,
            },
        }


def unpack(archive_path, target):
    size = (
        os.fstat(archive_path.fileno()).st_size
        if hasattr(archive_path, "fileno")
        else archive_path.stat().st_size
    )
    if size > MAX_BYTES + ARCHIVE_OVERHEAD:
        raise ValueError("Archive exceeds the size limit")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(names) != len(set(names)) or not set(names) <= ALLOWED_FILES | {
                "manifest.json"
            }:
                raise ValueError("Archive contains unsupported or duplicate paths")
            if (
                "manifest.json" not in names
                or "leam.sqlite3" not in names
                or "accounts-key" not in names
            ):
                raise ValueError("Archive is missing required files")
            if sum(
                item.file_size for item in entries if item.filename != "manifest.json"
            ) > MAX_BYTES or any(
                item.file_size
                > (MAX_BYTES if item.filename == "leam.sqlite3" else 65536)
                for item in entries
            ):
                raise ValueError("Archive expanded size exceeds the limit")
            if any(
                item.is_dir() or stat.S_ISLNK(item.external_attr >> 16)
                for item in entries
            ):
                raise ValueError("Archive contains non-file entries")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                type(manifest.get("version")) is not int
                or manifest.get("version") != 1
                or manifest.get("product") != "leam"
                or set(manifest.get("files", {})) != set(names) - {"manifest.json"}
            ):
                raise ValueError("Unsupported or inconsistent backup manifest")
            remaining = MAX_BYTES
            for name in names:
                if name == "manifest.json":
                    continue
                metadata = manifest["files"][name]
                path = target / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file, archive.open(name) as source:
                    actual = stream_copy(
                        source,
                        file,
                        min(remaining, MAX_BYTES if name == "leam.sqlite3" else 65536),
                    )
                    remaining -= actual["size"]
                    if metadata != actual:
                        raise ValueError("Backup file digest does not match")
                    file.flush()
                    os.fsync(file.fileno())
            restored_keys = keys(target)
            validate_database(target / "leam.sqlite3")
            validate_vault(target / "leam.sqlite3", restored_keys["accounts-key"])
    except (
        zipfile.BadZipFile,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
    ) as error:
        raise ValueError("Invalid Leam backup archive") from error


def restore(archive, destination, current_data):
    """Restore into a new directory; existing processes keep their existing state."""
    archive, destination, current_data = (
        Path(archive),
        Path(destination),
        Path(current_data),
    )
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore destination already exists; choose a new directory")
    if not (current_data / "leam.sqlite3").is_file():
        raise ValueError(
            "Current Leam data directory is required for the safety backup"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".leam-restore-", dir=destination.parent))
    try:
        # Pin the archive before a concurrent daily retention operation unlinks it.
        with archive.open("rb") as source:
            unpack(source, temporary)
        # Invalidate browser login and incomplete OAuth handshakes, never revive old sessions.
        with sqlite3.connect(temporary / "leam.sqlite3") as db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM oauth_states")
            from .restore_automation import KEY, hold_marker, write_setting

            write_setting(db, KEY, hold_marker())
        manager = Backups(SimpleNamespace(path=current_data / "leam.sqlite3"))
        safety = manager.path(manager.create()["id"])
        fsync_directory(temporary)
        if not publish_directory(temporary, destination):
            raise ValueError("Restore destination already exists")
        fsync_directory(destination.parent)
        return {
            "directory": str(destination),
            "safetyBackup": str(safety),
            "activated": False,
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def router(manager):
    routes = APIRouter(prefix="/api/backups")

    @routes.get("")
    async def listing():
        return manager.list()

    @routes.post("")
    def create():
        try:
            return manager.create()
        except (OSError, ValueError, sqlite3.Error) as error:
            raise HTTPException(
                409,
                "Backup could not be created; check private data files and disk space",
            ) from error

    @routes.get("/{key}/download")
    def download(key: str):
        try:
            file = manager.open_download(key)
        except (OSError, ValueError) as error:
            raise HTTPException(404, "Backup not found") from error

        def chunks():
            try:
                while chunk := file.read(CHUNK_BYTES):
                    yield chunk
            finally:
                file.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Cache-Control": "no-store",
                "Content-Length": str(os.fstat(file.fileno()).st_size),
                "Content-Disposition": f'attachment; filename="leam-backup-{key}.zip"',
            },
            background=BackgroundTask(file.close),
        )

    return routes


def main():
    parser = argparse.ArgumentParser(
        description="Restore Leam product state into a new, inactive directory"
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--current-data", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = restore(args.archive, args.destination, args.current_data)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Restore refused: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
