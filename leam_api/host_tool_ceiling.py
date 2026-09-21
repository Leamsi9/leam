"""Read only the fixed runtime's public capability ceiling, never its credentials."""

import hashlib
import json
import re
from pathlib import Path

from .tool_permissions import ALLOWED, LOOP_HELPERS, capability_id

ENV_NAME = b"IRONCLAW_REBORN_ALLOWED_CAPABILITY_IDS="
RECOMMENDED = sorted(ALLOWED | LOOP_HELPERS)


def read_environment(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Oversized environment")
        values = [
            item[len(ENV_NAME) :]
            for item in raw.split(b"\0")
            if item.startswith(ENV_NAME)
        ]
        if not values:
            return {"available": True, "configured": False, "ids": None}
        if len(values) != 1 or len(values[0]) > 65536:
            raise ValueError("Invalid ceiling")
        ids = json.loads(values[0])
        if (
            not isinstance(ids, list)
            or len(ids) > 1024
            or not all(capability_id(i) for i in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("Invalid ceiling")
        return {"available": True, "configured": True, "ids": sorted(ids)}
    except (OSError, ValueError, TypeError):
        return {"available": False, "configured": None, "ids": None}


def evidence(root: Path, release):
    data = release.get("data") or {}
    runtime = data.get("runtime") or {}
    live = ((data.get("services") or {}).get("runtime") or {}).get("hostCeiling") or {}
    result = {
        "enforcementStatus": "unknown",
        "configured": live.get("configured"),
        "allowedIds": live.get("ids"),
        "recommendedIds": RECOMMENDED,
        "matchesRecommended": None,
        "behaviorAccepted": False,
        "scope": "Companion only. Codex engineering permissions are unchanged. Settings cannot grant authority beyond a verified host ceiling. Provider aliases are evaluated by their resolved targets.",
        "inspectionException": "ironclaw.loop.capability_info remains available only for staged targets within the allowed surface.",
    }
    try:
        lock = json.loads((root / "docs/architecture/runtime-lock.json").read_text())
        reviewed = (
            type(lock.get("hostCapabilityCeilingVersion")) is int
            and lock["hostCapabilityCeilingVersion"] == 1
        )
        source = lock.get("sourceCommit")
        reviewed = (
            reviewed
            and isinstance(source, str)
            and re.fullmatch(r"[a-f0-9]{40}", source)
        )
        if (
            reviewed
            and runtime.get("runningBinaryMatches") is True
            and live.get("available") is True
        ):
            result["enforcementStatus"] = (
                "configured" if live.get("configured") else "not_configured"
            )
            result["sourceCommit"] = source
            result["binarySha256"] = runtime.get("observedBinarySha256")
            if live.get("configured"):
                result["matchesRecommended"] = live.get("ids") == RECOMMENDED
                result["allowlistSha256"] = hashlib.sha256(
                    json.dumps(live["ids"], separators=(",", ":")).encode()
                ).hexdigest()
    except (OSError, ValueError, TypeError):
        pass
    return result
