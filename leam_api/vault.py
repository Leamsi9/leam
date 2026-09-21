"""Installation-local authenticated encryption for account secrets."""

import base64
import json
import os
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Vault:
    def __init__(self, directory):
        path = directory / "accounts-key"
        if not path.exists():
            fd, temporary = tempfile.mkstemp(prefix=".accounts-key-", dir=directory)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(AESGCM.generate_key(bit_length=256))
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.unlink(temporary)
        self.cipher = AESGCM(path.read_bytes())

    def seal(self, label, value):
        nonce = os.urandom(12)
        encrypted = self.cipher.encrypt(
            nonce, json.dumps(value).encode(), label.encode()
        )
        return base64.urlsafe_b64encode(nonce + encrypted).decode()

    def open(self, label, value):
        raw = base64.urlsafe_b64decode(value)
        return json.loads(self.cipher.decrypt(raw[:12], raw[12:], label.encode()))
