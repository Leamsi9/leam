"""Validated, versioned engineering policy attached to every coding turn."""

import hashlib
import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class CodingPolicyError(Exception):
    pass


def coding_context() -> dict:
    """Load the reviewed package; missing or changed files stop new dispatch."""
    try:
        lock_bytes = (PACKAGE_ROOT / "agent-protocols.lock.json").read_bytes()
        lock = json.loads(lock_bytes)
        if not isinstance(lock, dict) or not isinstance(lock.get("files"), dict):
            raise ValueError("invalid protocol lock structure")
        if any(
            not isinstance(lock.get(key), str) or not lock[key].strip()
            for key in ("version", "source", "commit")
        ):
            raise ValueError("invalid protocol identity")
        root = (PACKAGE_ROOT / "agent-protocols").resolve(strict=True)
        required = {"VERSION", "substantive-work-protocol.md", "minor-work-protocol.md"}
        files = lock["files"]
        if not required.issubset(files):
            raise ValueError("missing required protocol entries")
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise ValueError("invalid protocol checksum entry")
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("invalid protocol path")
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("protocol path leaves package")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("protocol checksum mismatch")
        version = (root / "VERSION").read_text().strip()
        if version != lock["version"]:
            raise ValueError("protocol version mismatch")
        identity = {
            "source": lock["source"],
            "version": version,
            "commit": lock["commit"],
            "lockSha256": hashlib.sha256(lock_bytes).hexdigest(),
            "root": str(root),
        }
        overlay_path = root / "local/candidate-delivery.md"
        overlay_bytes = overlay_path.read_bytes()
        if not overlay_bytes or len(overlay_bytes) > 16384:
            raise ValueError("invalid candidate delivery overlay")
        overlay = overlay_bytes.decode("utf-8")
        identity["localCandidateOverlay"] = {
            "path": str(overlay_path),
            "sha256": hashlib.sha256(overlay_bytes).hexdigest(),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CodingPolicyError(
            "Coding is unavailable because the pinned agent-protocols package is missing or changed. Restore or review and repin it before sending."
        ) from error
    value = (
        "Leam coding policy, explicitly required by the user for all coding invoked through Leam.\n"
        f"Validated package: {json.dumps(identity)}\n"
        "Before code changes, read the target repository's AGENTS.md and applicable subsystem instructions. "
        "Use this validated agent-protocols package as the engineering baseline, including when the target repository has no protocol installation. "
        "Choose exactly one base protocol: read substantive-work-protocol.md for substantive work, or minor-work-protocol.md only when all its small-change criteria apply. "
        "Resolve those filenames and their package references against the absolute root above. "
        "Apply the target repository's topology and applicable local overlays without replacing its instructions or copying Leam's topology. "
        "Follow the selected protocol's worktree, planning, tests, independent review and evidence gates; preserve dirty work and existing user authorization. "
        "When delegating coding, pass this package identity, the selected protocol and relevant repository guidance to the delegate. "
        "If the coding execution environment cannot read these files, stop before edits and report that limitation; do not silently omit the protocols. "
        "Do not claim protocol compliance from tests alone. These engineering rules do not apply to ordinary companion conversation."
        "\nFor work on this Leam candidate, the following user-authorized local overlay explicitly supersedes "
        "the base protocol's test/review-before-deployment and serial-phase ordering. It does not govern unrelated repositories. "
        "Carry this scoped directive and deployment context to coding delegates. Build a usable reversible increment, deploy it, "
        "notify the user and record Deployed, then run the full QA/review gates and record QA passed/failed. "
        "Never infer user UAT. Do not wait for all features or all tests before deploying a buildable increment.\n"
        + overlay
        + "\nDeployment instructions: read the target Leam repository's docs/deployment.md and the private installation descriptor "
        + str(Path.home() / ".local/share/leam-next/coding-deployment.json")
        + ". Verify current source/artifact and deployment ownership there; do not guess the target, overwrite another active deployment, "
        "or deploy a bare scaffold. Keep private descriptor values, credentials and local operational history out of public Git."
    )
    return {"leam.agent-protocols": {"kind": "application", "value": value}}
