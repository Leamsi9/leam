import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.codex import CodexClient
from leam_api.maintenance import Maintenance, MaintenanceHeld


def test_barrier_existing_work_drains_and_seal_is_atomic(tmp_path):
    gate = Maintenance(tmp_path)
    with gate.admit():
        gate.hold(str(uuid4()))
        assert not gate.drained()
        with pytest.raises(MaintenanceHeld), gate.admit():
            pytest.fail("new work admitted")
        with pytest.raises(MaintenanceHeld):
            gate.seal(gate.status()["requestId"])
    with gate.admit(existing_companion=True):
        assert not gate.drained()
    key = gate.status()["requestId"]
    gate.seal(key)
    with pytest.raises(MaintenanceHeld), gate.admit(existing_companion=True):
        pytest.fail("late callback admitted")
    gate.release(key)
    assert gate.drained()


def test_actual_api_rejects_mutation_before_native_dispatch(tmp_path):
    gate = Maintenance(tmp_path / "recovery")
    codex = FakeCodex()
    app = create_app(
        tmp_path / "data",
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=codex,
        maintenance=gate,
    )
    with TestClient(app) as client:
        login(client)
        key = str(uuid4())
        gate.hold(key)
        response = client.post(
            "/api/codex/threads/x/turns",
            json={"text": "do work", "requestId": str(uuid4())},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert not codex.calls
        assert client.get("/api/auth/status").status_code == 200
        assert client.get("/api/codex/threads").status_code == 503
        assert client.get("/api/internal/maintenance").status_code == 401


@pytest.mark.asyncio
async def test_native_drain_uses_loaded_metadata_without_starting_owner():
    client = CodexClient(lambda *_: None)
    assert (await client.drain_status())["idle"]
    client.process = SimpleNamespace(returncode=None)
    client.reader = SimpleNamespace(done=lambda: False)
    client.ready = True
    calls = []

    async def request(method, params):
        calls.append((method, params))
        if method == "thread/loaded/list":
            return {"data": ["native-a"], "nextCursor": None}
        return {
            "thread": {
                "id": "native-a",
                "status": {"type": "active", "activeFlags": []},
            }
        }

    client._request = request
    assert not (await client.drain_status())["idle"]
    assert calls == [
        ("thread/loaded/list", {"cursor": None, "limit": 100}),
        ("thread/read", {"threadId": "native-a", "includeTurns": False}),
    ]


class AllowedDrain:
    async def require(self):
        pass

    async def guard_restart(self, service_id):
        pass


def runtime_db(base, state="completed"):
    import json
    import sqlite3

    path = base / "ironclaw/local-dev/reborn-local-dev.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE root_filesystem_entries(kind TEXT,path TEXT,contents TEXT)"
        )
        db.execute(
            "INSERT INTO root_filesystem_entries VALUES (?,?,?)",
            (
                "process_materialized",
                "a/process/b",
                json.dumps({"process_kind": "agent_turn", "status": state}),
            ),
        )


@pytest.mark.asyncio
async def test_real_stop_caller_requires_sealed_idle_proof_and_never_controls_shared_owner(
    tmp_path,
):
    import httpx

    from leam_api.candidate_deployment import CandidateDeployment, Roots
    from leam_api.maintenance import DrainControl
    from leam_api.restore_services import FixedRestoreServices

    roots = Roots(tmp_path)
    roots.recovery.mkdir(mode=0o700)
    deployment = CandidateDeployment(roots)
    runtime_db(roots.base)

    class Control:
        def __init__(self):
            self.calls = []
            self.running = True

        async def _systemctl(self, *args):
            self.calls.append(args)
            if args[0] == "stop":
                self.running = False
            return (
                "MainPID=42\nActiveState=active\n"
                if self.running
                else "MainPID=0\nActiveState=inactive\nControlGroup=\n"
            )

        async def status(self):
            return [
                {"id": k, "reachable": self.running} for k in ("app", "mcp", "runtime")
            ]

    control = Control()
    native_idle = False

    async def handle(request):
        assert request.url.path == "/api/internal/maintenance"
        assert gate.authorized(request.headers["authorization"])
        return httpx.Response(
            200, json={"idle": native_idle, "maintenance": gate.status()}
        )

    drain = DrainControl(deployment, control, transport=httpx.MockTransport(handle))
    gate = drain.gate
    services = FixedRestoreServices(deployment, control, drain=drain)
    with pytest.raises(MaintenanceHeld):
        await services.stop()
    key = str(uuid4())
    with deployment.lock():
        assert not (await drain.prepare(key))["idle"]
        with pytest.raises(MaintenanceHeld):
            await services.stop()
        native_idle = True
        assert (await drain.prepare(key))["maintenance"]["phase"] == "held"
        native_idle = False
        with pytest.raises(MaintenanceHeld):
            await services.stop()
        assert not [x for x in control.calls if x[0] == "stop"]
        native_idle = True
        await services.stop()
    stops = [x for x in control.calls if x[0] == "stop"]
    assert stops == [
        (
            "stop",
            "leam-next-candidate.service",
            "leam-next-mcp.service",
            "leam-next-runtime.service",
        )
    ]


@pytest.mark.asyncio
async def test_classifier_admission_outlives_http_and_releases_on_early_cancel(
    tmp_path,
):
    from test_agenda import application

    app, _, _ = application(tmp_path / "data")
    classifier = app.state.emails.classifier
    gate = Maintenance(tmp_path / "recovery")
    classifier.store.maintenance = gate
    entered, release = asyncio.Event(), asyncio.Event()

    async def run():
        entered.set()
        await release.wait()

    classifier.run = run
    classifier.start()
    assert not gate.drained()  # Acquired before task scheduling.
    await entered.wait()
    gate.hold(str(uuid4()))
    assert not gate.drained()
    release.set()
    await classifier.task
    await asyncio.sleep(0)
    assert gate.drained()
    with pytest.raises(MaintenanceHeld):
        classifier.start()
    gate.release(gate.status()["requestId"])
    classifier.start()
    await (
        classifier.close()
    )  # Cancellation before the coroutine begins still closes lease.
    await asyncio.sleep(0)
    assert gate.drained()
    await app.state.push.close()


@pytest.mark.asyncio
async def test_authenticated_recovery_drains_actual_api_and_keeps_credentials_separate(
    tmp_path,
):
    import httpx

    from leam_api.candidate_deployment import CandidateDeployment, Roots
    from leam_api.maintenance import DrainControl
    from leam_api.recovery import create_recovery_app

    roots = Roots(tmp_path)
    gate = Maintenance(roots.recovery)
    deployment = CandidateDeployment(roots)
    runtime_db(roots.base)

    class Native(FakeCodex):
        idle = False

        async def drain_status(self):
            return {"idle": self.idle}

    native = Native()
    main = create_app(
        roots.data / "original",
        {"http://main"},
        bootstrap="fixture-bootstrap",
        codex=native,
        maintenance=gate,
    )

    class Control:
        async def _systemctl(self, *args):
            return "MainPID=17\nActiveState=active\n"

        async def status(self):
            return [{"id": "app", "reachable": True}]

    control = Control()
    drain = DrainControl(
        deployment,
        control,
        transport=httpx.ASGITransport(app=main, client=("127.0.0.1", 4567)),
    )
    recovery = create_recovery_app(
        roots.recovery,
        {"http://recovery"},
        control=control,
        deployment=deployment,
        drain=drain,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recovery, client=("127.0.0.1", 1111)),
        base_url="http://recovery",
    ) as client:
        assert (await client.get("/api/maintenance")).status_code == 401
        h = {"origin": "http://recovery"}
        assert (
            await client.post(
                "/api/auth/setup",
                json={
                    "bootstrap": (roots.recovery / "bootstrap-token").read_text(),
                    "password": "separate-recovery-password",
                },
                headers=h,
            )
        ).status_code == 200
        key = str(uuid4())
        body = {"requestId": key}
        assert (
            await client.post("/api/maintenance/prepare", json=body)
        ).status_code == 403
        result = await client.post("/api/maintenance/prepare", json=body, headers=h)
        assert result.status_code == 200 and result.json()["idle"] is False
        assert gate.status()["phase"] == "draining"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main, client=("127.0.0.1", 2222)),
            base_url="http://main",
        ) as m:
            tools = {
                "authorization": "Bearer "
                + (roots.data / "original/tools-token").read_text().strip()
            }
            assert (
                await m.get("/api/internal/maintenance", headers=tools)
            ).status_code == 401
            token = {"authorization": "Bearer " + gate.token_path.read_text().strip()}
            assert (
                await m.get(
                    "/api/internal/maintenance",
                    headers={**token, "origin": "http://main"},
                )
            ).status_code == 403
            assert (
                await m.post(
                    "/api/internal/tools",
                    headers=tools,
                    json={"tool": "leam_context", "arguments": {}},
                )
            ).status_code == 200
            native.idle = True
            with deployment.lock():
                assert (
                    await client.post("/api/maintenance/prepare", json=body, headers=h)
                ).status_code == 409
            assert (
                await client.post("/api/maintenance/prepare", json=body, headers=h)
            ).json()["maintenance"]["phase"] == "held"
            assert (
                await m.post(
                    "/api/internal/tools",
                    headers=tools,
                    json={"tool": "leam_context", "arguments": {}},
                )
            ).status_code == 503
        assert (
            await client.post(
                "/api/maintenance/release", json={"requestId": str(uuid4())}, headers=h
            )
        ).status_code == 409
        from leam_api.candidate_deployment import atomic_json

        operations = roots.recovery / "restore-operations"
        operations.mkdir(mode=0o700)
        old = operations / (str(uuid4()) + ".json")
        atomic_json(old, {"state": "needs_review"})
        assert (
            await client.post("/api/maintenance/release", json=body, headers=h)
        ).status_code == 409
        assert gate.status()["held"]
        atomic_json(old, {"state": "rolled_back"})
        assert (
            await client.post("/api/maintenance/release", json=body, headers=h)
        ).json()["maintenance"] == {"held": False}
    assert not native.calls  # Original owner/native submissions never dispatched.
    await main.state.push.close()


@pytest.mark.asyncio
async def test_dead_api_restart_requires_absent_process_group_and_listener(tmp_path):
    from leam_api.candidate_deployment import CandidateDeployment, Roots
    from leam_api.maintenance import DrainControl

    class Control:
        pid = "0"
        reachable = False

        async def _systemctl(self, *args):
            return f"MainPID={self.pid}\nActiveState=inactive\nControlGroup=\n"

        async def status(self):
            return [{"id": "app", "reachable": self.reachable}]

    roots = Roots(tmp_path)
    Maintenance(roots.recovery)
    control = Control()
    drain = DrainControl(CandidateDeployment(roots), control)
    await drain.guard_restart(
        "app"
    )  # No API, token RPC, runtime or descriptor dependency.
    control.reachable = True
    with pytest.raises(MaintenanceHeld):
        await drain.guard_restart("app")
    control.reachable = False
    control.pid = "12"
    with pytest.raises(MaintenanceHeld):
        await drain.guard_restart("app")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduler", "routines", "push"])
async def test_actual_worker_loop_takes_tick_lease_and_skips_held_work(
    tmp_path, monkeypatch, kind
):
    from test_agenda import application

    app, _, _ = application(tmp_path / "data")
    gate = Maintenance(tmp_path / "recovery")
    worker = getattr(app.state, kind)
    worker.store.maintenance = gate
    calls = []

    def tick():
        calls.append(gate.drained())

    async def async_tick():
        tick()

    async def end_iteration(_):
        raise asyncio.CancelledError

    worker.tick = async_tick if kind == "push" else tick
    monkeypatch.setattr(asyncio, "sleep", end_iteration)
    with pytest.raises(asyncio.CancelledError):
        await worker.run()
    assert calls == [False]
    gate.hold(str(uuid4()))
    with pytest.raises(asyncio.CancelledError):
        await worker.run()
    assert calls == [False] and gate.drained()
    await app.state.push.close()


@pytest.mark.asyncio
async def test_pending_native_approval_requires_explicit_release_without_any_rpc():
    client = CodexClient(lambda *_: None)
    client.process = SimpleNamespace(returncode=None)
    client.reader = SimpleNamespace(done=lambda: False)
    client.ready = True
    client.requests = {
        "approval-1": {"method": "item/commandExecution/requestApproval"}
    }

    async def forbidden(*_):
        pytest.fail("Approval inspection must not approve, dispatch or start")

    client._request = forbidden
    status = await client.drain_status()
    assert status["idle"] is False and "End maintenance" in status["reason"]
    assert "approval-1" in client.requests


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"version": True, "requestId": str(uuid4()), "phase": "held", "createdAt": 1},
        {"version": 1, "requestId": "bad", "phase": "held", "createdAt": 1},
        {"version": 1, "requestId": str(uuid4()), "phase": [], "createdAt": 1},
    ],
)
def test_corrupt_maintenance_state_refuses_admission(tmp_path, value):
    from leam_api.candidate_deployment import atomic_json

    gate = Maintenance(tmp_path)
    atomic_json(gate.path, value)
    with pytest.raises(MaintenanceHeld), gate.admit():
        pytest.fail("corrupt marker admitted work")


@pytest.mark.asyncio
@pytest.mark.parametrize("change_generation", [False, True])
async def test_native_idle_reads_all_loaded_pages_and_fences_generation(
    change_generation,
):
    client = CodexClient(lambda *_: None)
    client.process = SimpleNamespace(returncode=None)
    client.reader = SimpleNamespace(done=lambda: False)
    client.ready = True
    calls = []

    async def request(method, params):
        calls.append((method, params))
        if method == "thread/loaded/list":
            return {
                "data": ["a"] if params["cursor"] is None else ["b"],
                "nextCursor": "page2" if params["cursor"] is None else None,
            }
        if params["threadId"] == "b" and change_generation:
            client.generation += 1
        return {"thread": {"id": params["threadId"], "status": {"type": "idle"}}}

    client._request = request
    result = await client.drain_status()
    assert result["idle"] is (not change_generation)
    assert calls == [
        ("thread/loaded/list", {"cursor": None, "limit": 100}),
        ("thread/read", {"threadId": "a", "includeTurns": False}),
        ("thread/loaded/list", {"cursor": "page2", "limit": 100}),
        ("thread/read", {"threadId": "b", "includeTurns": False}),
    ]
