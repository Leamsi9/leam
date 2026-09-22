"""Reviewed contract for IronClaw's bundled web-access extension."""

import json
import sys
from urllib.parse import urlsplit

PACKAGE_ID = "web-access"
CAPABILITIES = frozenset({"web-access.search", "web-access.get_content"})
NETWORK_TARGET = "https://mcp.exa.ai"


def integration_status(modes):
    """Project catalog presence separately from live external behavior."""
    if modes is None:
        return {
            "status": "unknown",
            "reason": "Runtime tool settings are unavailable",
            "behaviorAccepted": False,
        }
    items = modes.get("items", []) if isinstance(modes, dict) else []
    states = {
        item.get("id"): item.get("state")
        for item in items
        if isinstance(item, dict) and item.get("id") in CAPABILITIES
    }
    missing = sorted(CAPABILITIES - states.keys())
    status = "unavailable" if missing else "available"
    if not missing and any(value == "disabled" for value in states.values()):
        status = "disabled"
    return {
        "status": status,
        "packageId": PACKAGE_ID,
        "capabilities": sorted(CAPABILITIES),
        "permissionStates": states,
        "missingCapabilities": missing,
        "provider": "Exa MCP",
        "credentialsRequired": False,
        "networkTarget": NETWORK_TARGET,
        "sourceUrlFields": {
            "web-access.search": "queries[].results[].url",
            "web-access.get_content": "contents[].url",
        },
        "untrustedContent": True,
        "behaviorAccepted": False,
    }


def source_urls(capability, output):
    """Validate URL provenance in a privately captured runtime tool result."""
    if capability not in CAPABILITIES or not isinstance(output, dict):
        raise ValueError("Unsupported web result")
    try:
        if capability == "web-access.search":
            urls = [
                result["url"]
                for query in output["queries"]
                for result in query["results"]
            ]
        else:
            urls = [item["url"] for item in output["contents"]]
    except (KeyError, TypeError):
        raise ValueError("Web result is missing source URL provenance") from None
    if not urls:
        raise ValueError("Web result is missing source URL provenance")
    for value in urls:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError("Web result has an invalid source URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Web result has an invalid source URL")
    return urls


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m leam_api.web_search <web-access capability>")
    try:
        urls = source_urls(sys.argv[1], json.load(sys.stdin))
    except (ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps({"sourceUrls": urls}, separators=(",", ":")))


if __name__ == "__main__":
    main()
