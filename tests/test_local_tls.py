import asyncio
import socket
import ssl

import httpx2
import pytest
import uvicorn

from leam_api.local_tls import provision
from leam_api.mcp_server import create_mcp_app


def test_tls_identity_is_private_stable_and_rejects_damage(tmp_path):
    identity = provision(tmp_path)
    first = identity.certificate.read_bytes()
    assert provision(tmp_path).certificate.read_bytes() == first
    assert identity.key.stat().st_mode & 0o077 == 0
    assert identity.certificate.parent.stat().st_mode & 0o077 == 0
    identity.key.write_text("invalid")
    with pytest.raises(ValueError, match="TLS identity"):
        provision(tmp_path)
    assert identity.certificate.read_bytes() == first


async def test_real_tls_mcp_requires_certificate_trust_and_tool_auth(tmp_path):
    identity = provision(tmp_path)
    token = "synthetic-local-tool-token-" + "x" * 40
    app = create_mcp_app(
        "http://127.0.0.1:46400",
        token,
        transport=httpx2.MockTransport(
            lambda req: httpx2.Response(200, json={"items": []})
        ),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            ssl_keyfile=str(identity.key),
            ssl_certfile=str(identity.certificate),
            log_level="error",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.02)
        assert server.started
        url = f"https://127.0.0.1:{port}/mcp"
        async with httpx2.AsyncClient(trust_env=False) as client:
            with pytest.raises(httpx2.ConnectError):
                await client.post(url, json={})
        context = ssl.create_default_context(cafile=str(identity.certificate))
        async with httpx2.AsyncClient(
            verify=context, trust_env=False, follow_redirects=False
        ) as client:
            assert (await client.post(url, json={})).status_code == 401
            headers = {
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream",
            }
            response = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["protocolVersion"] == "2025-06-18"
            response = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "leam_context", "arguments": {}},
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["isError"] is False
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()


def test_tls_publication_never_replaces_a_racing_damaged_identity(
    tmp_path, monkeypatch
):
    from leam_api import local_tls

    original = local_tls.publish_directory

    def race(source, destination):
        destination.mkdir(mode=0o700)
        return original(source, destination)

    monkeypatch.setattr(local_tls, "publish_directory", race)
    with pytest.raises(ValueError, match="TLS identity"):
        provision(tmp_path)
    assert list((tmp_path / "mcp-tls").iterdir()) == []
    assert not list(tmp_path.glob(".mcp-tls-*"))


def test_tls_concurrent_provisioners_share_one_complete_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(lambda _: provision(tmp_path), range(16)))
    assert len({identity.certificate.read_bytes() for identity in identities}) == 1
    assert not list(tmp_path.glob(".mcp-tls-*"))
