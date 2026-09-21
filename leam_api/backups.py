"""Explicit product-state backups and no-replace restoration into a fresh directory."""

import argparse
import base64
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
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .local_tls import publish_directory
from .local_tls import validate as validate_tls

MAX_BYTES = 256 * 1024 * 1024
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
    from .backlog import Backlog
    from .routines import Routines
    from .store import Store
    from .updates import Updates

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
        Backlog(store)
        Routines(store)
        Updates(store)
        with store.connect() as db:
            statements = {
                (row[0], row[1]): " ".join(row[2].split())
                for row in db.execute(
                    "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                )
            }
    return required, statements


def private_read(path, limit=MAX_BYTES):
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
                raise ValueError("Unsupported push key")
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
            for key_id, raw in db.execute("SELECT id,body FROM accounts"):
                decrypt("account:" + key_id, raw)
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
    def __init__(self, store):
        self.store = store
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

    def create(self):
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
            files = {"leam.sqlite3": private_read(snapshot), **original_keys}
            if sum(map(len, files.values())) > MAX_BYTES:
                raise ValueError("Backup exceeds the 256 MiB product-state limit")
            manifest = {
                "version": 1,
                "product": "leam",
                "createdAt": time.time(),
                "files": {
                    name: {
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for name, data in files.items()
                },
            }
            output = temp / "backup.zip"
            output.touch(mode=0o600)
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_STORED
            ) as archive:
                archive.writestr("manifest.json", json.dumps(manifest).encode())
                for name, data in files.items():
                    archive.writestr(name, data)
            with output.open("rb") as file:
                os.fsync(file.fileno())
            os.link(output, self.path(key))
            fsync_directory(self.directory)
        return self.describe(key)

    def describe(self, key):
        path = self.path(key)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Backup must remain a private regular file")
        return {
            "id": key,
            "createdAt": info.st_mtime,
            "bytes": info.st_size,
            "downloadUrl": f"/api/backups/{key}/download",
        }

    def list(self):
        items = []
        for path in self.directory.glob("*.zip"):
            try:
                items.append(self.describe(path.stem))
            except (OSError, ValueError):
                continue
        return {
            "items": sorted(items, key=lambda item: item["createdAt"], reverse=True)[
                :100
            ],
            "scope": "Leam SQLite state and installation keys",
            "restoreMode": "new-directory-offline",
        }


def unpack(archive_path, target):
    if archive_path.stat().st_size > MAX_BYTES + 1024 * 1024:
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
            if sum(item.file_size for item in entries) > MAX_BYTES + 65536 or any(
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
            for name in names:
                if name == "manifest.json":
                    continue
                data = archive.read(name)
                metadata = manifest["files"][name]
                if metadata != {
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }:
                    raise ValueError("Backup file digest does not match")
                path = target / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file:
                    file.write(data)
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
        unpack(archive, temporary)
        # Invalidate browser login and incomplete OAuth handshakes, never revive old sessions.
        with sqlite3.connect(temporary / "leam.sqlite3") as db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM oauth_states")
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
    async def download(key: str):
        try:
            manager.describe(key)
        except (OSError, ValueError) as error:
            raise HTTPException(404, "Backup not found") from error
        return FileResponse(
            manager.path(key),
            media_type="application/zip",
            filename=f"leam-backup-{key}.zip",
            headers={"Cache-Control": "no-store"},
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
