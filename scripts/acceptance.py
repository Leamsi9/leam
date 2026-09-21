#!/usr/bin/env python3
"""Behavioral phase checks. Unimplemented gates fail; no placeholder green."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
phase = sys.argv[1] if len(sys.argv) > 1 else ""
if phase not in ["codex", "companion"]:
    sys.exit(f"{phase}: live acceptance is not implemented yet; phase remains pending")

commands = [
    ([str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q"], ROOT),
    (["npm", "run", "build"], ROOT / "apps/web"),
    (
        ["npx", "playwright", "test"]
        + (
            []
            if phase == "codex"
            else [
                "tests/companion-live.spec.ts",
                "tests/companion-interactions.spec.ts",
                "tests/settings.spec.ts",
            ]
        ),
        ROOT / "apps/web",
    ),
]
for command, cwd in commands:
    subprocess.run(command, cwd=cwd, check=True)
if phase == "companion":
    print(
        "Runtime boundary gate passed: real IronClaw configuration/conversation and sourced-memory mobile controls."
    )
    print(
        "Live model response and complete cross-domain continuity remain final acceptance requirements."
    )
    sys.exit(0)
receipt = json.loads((ROOT / ".artifacts/codex-live.json").read_text())
if not receipt.get("readViaTool") or not receipt.get("threadId"):
    sys.exit("Live Codex tool execution evidence missing")
print(
    "Codex substrate gate passed: authenticated UI -> real thread -> real workspace tool -> rendered reply."
)
print(
    "Exact original-thread handoff and whole-product acceptance remain final deployment gates."
)
