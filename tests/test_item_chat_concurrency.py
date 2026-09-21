import asyncio, json, uuid
from concurrent.futures import ThreadPoolExecutor
import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from leam_api.app import create_app
from leam_api.ironclaw import IronClaw


def test_concurrent_binding_and_deleted_thread_never_recreated(tmp_path):
    calls = []
    both = asyncio.Event()
    deleted = False

    async def handler(request):
        nonlocal deleted
        if request.url.path.endswith("/threads") and request.method == "POST":
            body = json.loads(request.content)
            calls.append(body)
            if len(calls) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 3)
            return httpx.Response(
                200,
                json={
                    "thread": {
                        "thread_id": str(
                            uuid.uuid5(uuid.NAMESPACE_URL, body["client_action_id"])
                        )
                    }
                },
            )
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(200, json={"deleted": True})
        if deleted and "/timeline" in request.url.path:
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(handler),
    )
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        runtime=runtime,
    )
    with TestClient(app) as client:
        login(client)
        headers = {"Origin": "http://testserver"}
        item = client.post(
            "/api/commitments", headers=headers, json={"title": "Concurrent item"}
        ).json()
        endpoint = f"/api/commitments/{item['id']}/chat"
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(lambda _: client.post(endpoint, headers=headers), range(2))
            )
        assert [r.status_code for r in results] == [200, 200]
        assert results[0].json()["threadId"] == results[1].json()["threadId"]
        assert len(calls) == 2 and calls[0] == calls[1]
        thread = results[0].json()["threadId"]
        assert (
            client.request(
                "DELETE",
                f"/api/companion/threads/{thread}",
                headers=headers,
                json={"confirmed": True},
            ).status_code
            == 200
        )
        assert client.post(endpoint, headers=headers).json()["threadId"] == thread
        assert len(calls) == 2
        assert (
            client.get(f"/api/companion/threads/{thread}").status_code == 502
        )  # Existing runtime-error mapping; no recreation.
