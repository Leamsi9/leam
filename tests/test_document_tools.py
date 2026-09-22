import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw
from leam_api.tool_permissions import ALLOWED, PROTECTED

THREAD = "a001cf3b-6e66-5348-aff3-34524e013c3e"
URL = "/api/companion/threads/" + THREAD + "/files/content"


def client_for(tmp_path, response):
    calls = []

    async def handler(request):
        calls.append(request)
        return response

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="private-fixture-token",
        transport=httpx.MockTransport(handler),
    )
    return TestClient(
        create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
            runtime=runtime,
        )
    ), calls


def test_document_scope_enables_only_reviewed_filesystem_tools():
    assert {
        "builtin." + n
        for n in [
            "read_file",
            "write_file",
            "list_dir",
            "glob",
            "grep",
            "document_edit",
            "html_to_pdf",
        ]
    } <= ALLOWED
    assert not ALLOWED & PROTECTED
    assert "builtin.apply_patch" in PROTECTED
    assert not {"builtin.shell", "builtin.http", "builtin.spawn_subagent"} & ALLOWED


def test_download_requires_auth_and_uses_exact_runtime_thread_path(tmp_path):
    client, calls = client_for(
        tmp_path,
        httpx.Response(
            200, content=b"%PDF-fixture", headers={"Content-Type": "application/pdf"}
        ),
    )
    with client:
        assert (
            client.get(URL, params={"path": "/workspace/report.pdf"}).status_code == 401
        )
        assert calls == []
        login(client)
        r = client.get(URL, params={"path": "/workspace/report.pdf"})
        assert r.status_code == 200
        assert r.content == b"%PDF-fixture"
        assert r.headers["content-disposition"].startswith("attachment;")
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert (
            calls[0].url.path == "/api/webchat/v2/threads/" + THREAD + "/files/content"
        )
        assert dict(calls[0].url.params) == {"path": "/workspace/report.pdf"}
        assert calls[0].headers["authorization"] == "Bearer private-fixture-token"
        assert calls[0].content == b""


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/workspace/../secret",
        "/workspace/a/../../secret",
        "/workspaceevil/a",
        "/workspace/a\\b",
        "/workspace/",
        "/workspace/a\x00b",
    ],
)
def test_invalid_paths_never_dispatch(tmp_path, path):
    client, calls = client_for(tmp_path, httpx.Response(200, content=b"no"))
    with client:
        login(client)
        assert client.get(URL, params={"path": path}).status_code == 422
        assert calls == []


@pytest.mark.parametrize("status", [403, 404, 500])
def test_runtime_failure_does_not_disclose_diagnostics(tmp_path, status):
    client, _calls = client_for(
        tmp_path, httpx.Response(status, content=b"private runtime diagnostic")
    )
    with client:
        login(client)
        r = client.get(URL, params={"path": "/workspace/file.txt"})
        assert r.status_code == (status if status in (403, 404) else 502)
        assert "private runtime diagnostic" not in r.text


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml"])
def test_active_content_always_downloads(tmp_path, mime):
    client, _ = client_for(
        tmp_path,
        httpx.Response(
            200,
            content=b"<script>bad()</script>",
            headers={"Content-Type": mime, "Content-Disposition": "inline"},
        ),
    )
    with client:
        login(client)
        r = client.get(URL, params={"path": "/workspace/report.html"})
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment;")
        assert "sandbox" in r.headers["content-security-policy"]


def test_oversized_runtime_body_rejected_before_download(tmp_path):
    client, _ = client_for(
        tmp_path,
        httpx.Response(
            200, content=b"x", headers={"Content-Length": str(26 * 1024 * 1024)}
        ),
    )
    with client:
        login(client)
        assert (
            client.get(URL, params={"path": "/workspace/large.bin"}).status_code == 502
        )
