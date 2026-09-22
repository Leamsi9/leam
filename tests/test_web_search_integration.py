import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from test_tool_permissions import fixture

from leam_api.host_tool_ceiling import RECOMMENDED
from leam_api.ironclaw import IronClaw
from leam_api.tool_permissions import ALLOWED, PROTECTED, Permission, ToolPermissions
from leam_api.web_search import (
    CAPABILITIES,
    integration_status,
    source_urls,
)


def runtime_catalog(*, include_search=True):
    state = fixture()
    if include_search:
        template = deepcopy(state["entries"][1])
        for capability in CAPABILITIES:
            entry = deepcopy(template)
            entry["key"] = "tool." + capability
            entry["value"]["name"] = capability
            entry["value"]["state"] = "ask_each_time"
            state["entries"].append(entry)
    return state


def test_exact_supported_web_tools_are_reviewed_without_generic_http_authority():
    assert CAPABILITIES == frozenset({"web-access.search", "web-access.get_content"})
    assert CAPABILITIES <= ALLOWED
    assert CAPABILITIES <= set(RECOMMENDED)
    assert "builtin.http" not in ALLOWED
    assert "builtin.http.save" in PROTECTED


def test_authorized_web_tool_setting_uses_existing_runtime_permission_boundary():
    state = runtime_catalog()
    writes = []

    async def handler(request):
        if request.method == "POST":
            name = request.url.path.rsplit("/", 1)[-1]
            body = json.loads(request.content)
            writes.append((name, body))
            entry = next(e for e in state["entries"] if e["key"] == "tool." + name)
            entry["value"]["state"] = body["state"]
            entry["value"]["effective_source"] = "override"
        return httpx.Response(200, json=state)

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-runtime-token",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        ToolPermissions(runtime).set(
            "web-access.search",
            Permission(state="always_allow"),
        )
    )
    assert result["state"] == "always_allow"
    assert writes == [("web-access.search", {"state": "always_allow"})]
    asyncio.run(runtime.close())


def test_unavailable_and_error_states_do_not_claim_search_access():
    missing = integration_status({"items": []})
    assert missing["status"] == "unavailable"
    assert missing["missingCapabilities"] == sorted(CAPABILITIES)
    assert missing["behaviorAccepted"] is False

    available = integration_status(
        {
            "items": [
                {"id": capability, "state": "always_allow"}
                for capability in CAPABILITIES
            ]
        }
    )
    assert available["status"] == "available"
    assert available["credentialsRequired"] is False
    assert available["networkTarget"] == "https://mcp.exa.ai"
    disabled = integration_status(
        {
            "items": [
                {"id": capability, "state": "disabled"} for capability in CAPABILITIES
            ]
        }
    )
    assert disabled["status"] == "disabled"
    assert disabled["behaviorAccepted"] is False

    assert integration_status(None) == {
        "status": "unknown",
        "reason": "Runtime tool settings are unavailable",
        "behaviorAccepted": False,
    }


def test_source_url_provenance_is_required_for_search_and_page_results():
    search = {
        "response_id": "response-1",
        "provider_used": "exa_mcp",
        "queries": [
            {
                "query": "Leam",
                "provider_used": "exa_mcp",
                "answer": "",
                "results": [
                    {
                        "index": 0,
                        "title": "Documentation",
                        "url": "https://example.test/docs",
                        "snippet": "Reference",
                    }
                ],
            }
        ],
    }
    page = {
        "provider_used": "exa_mcp",
        "title": "Documentation",
        "url": "https://example.test/docs",
        "content": "Reference",
        "contents": [
            {
                "title": "Documentation",
                "url": "https://example.test/docs",
                "content": "Reference",
            }
        ],
    }
    assert source_urls("web-access.search", search) == ["https://example.test/docs"]
    assert source_urls("web-access.get_content", page) == ["https://example.test/docs"]
    with pytest.raises(ValueError, match="source URL"):
        source_urls(
            "web-access.search",
            {**search, "queries": [{**search["queries"][0], "results": []}]},
        )
    with pytest.raises(ValueError, match="source URL"):
        source_urls(
            "web-access.get_content",
            {**page, "contents": [{**page["contents"][0], "url": "javascript:x"}]},
        )
