"""Isolated real Leam API for event-input browser callers; no Codex/runtime calls."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn
from argon2 import PasswordHasher

from leam_api.app import create_app


class UnusedCodex:
    async def request(self, method, params, **kwargs):
        return {"data": []}

    async def close(self):
        pass


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="leam-event-browser-") as directory:
        app = create_app(
            Path(directory),
            {"http://127.0.0.1:46553"},
            bootstrap="isolated-event-fixture",
            codex=UnusedCodex(),
        )
        app.state.store.set(
            "password_hash", PasswordHasher().hash("local-event-browser-only")
        )
        uvicorn.run(app, host="127.0.0.1", port=46554, log_level="warning")
