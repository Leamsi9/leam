"""Drive the operator CLI through real temporary Updates storage and HTTP assets."""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from leam_api import release_receipts as receipts
from leam_api.store import Store
from leam_api.updates import QA, Acceptance, Updates


@pytest.fixture
def installation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "source.py").write_text("value = 'immutable'\n")
    subprocess.run(["git", "-C", str(repo), "add", "source.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    release = tmp_path / "releases" / revision
    release.mkdir(parents=True)
    shutil.copyfile(repo / "source.py", release / "source.py")
    client = release / "apps/web/dist"
    (client / "assets").mkdir(parents=True)
    (client / "assets/main.js").write_text("fixture client")
    index = b'<script src="/assets/main.js"></script>'
    (client / "index.html").write_bytes(index)
    manifest = {
        "sourceCommit": revision,
        "clientIndexSha256": hashlib.sha256(index).hexdigest(),
        "assetsSha256": {
            "main.js": hashlib.sha256(
                (client / "assets/main.js").read_bytes()
            ).hexdigest()
        },
    }
    (release / "release.json").write_text(json.dumps(manifest))
    data = tmp_path / "data"
    Store(data)
    journal = tmp_path / "deployment.json"
    deployed = (datetime.now(UTC) - timedelta(seconds=2)).isoformat()
    journal.write_text(
        json.dumps(
            {
                **manifest,
                "release": str(release),
                "deployedAt": deployed,
                "pid": os.getpid(),
                "mcpPid": os.getpid(),
            }
        )
    )

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(client))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    descriptor = {
        "dataDirectory": str(data),
        "releaseRoot": str(release.parent),
        "currentDeploymentReceipt": str(journal),
        "productRepository": str(repo),
        "candidateUrl": f"http://127.0.0.1:{server.server_port}",
        "services": {"api": "fixture-api.service", "mcp": "fixture-mcp.service"},
    }
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(json.dumps(descriptor))
    calls = []
    real_command = receipts.command

    def command(args):
        calls.append(args)
        if args[0] == "systemctl":
            return str(os.getpid())
        return real_command(args)

    monkeypatch.setattr(receipts, "command", command)
    # /proc PID/cwd remains a real kernel observation; only service discovery is mocked.
    monkeypatch.chdir(release)
    yield {
        "descriptor": descriptor,
        "path": descriptor_path,
        "release": release,
        "revision": revision,
        "data": data,
        "root": tmp_path,
        "calls": calls,
    }
    server.shutdown()
    server.server_close()


def invoke(fixture, monkeypatch, capsys, operation, payload):
    path = fixture["root"] / (operation + "-input.json")
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_receipts",
            "--descriptor",
            str(fixture["path"]),
            operation,
            "--input",
            str(path),
        ],
    )
    receipts.main()
    return json.loads(capsys.readouterr().out)


def body(fixture, **fields):
    return {"requestId": str(uuid4()), "sourceCommit": fixture["revision"], **fields}


def start(fixture, monkeypatch, capsys):
    publication = invoke(
        fixture,
        monkeypatch,
        capsys,
        "publish",
        body(
            fixture,
            features=[
                {
                    "feature": "fixture",
                    "title": "Fixture",
                    "summary": "Shipped behavior",
                }
            ],
        ),
    )
    invoke(
        fixture,
        monkeypatch,
        capsys,
        "notify",
        body(
            fixture,
            features=["fixture"],
            evidence="Coordinator notified in current chat",
        ),
    )
    run = invoke(
        fixture, monkeypatch, capsys, "begin-qa", body(fixture, features=["fixture"])
    )
    return publication["features"][0]["updateId"], run


def report(fixture, run, cases='<testcase name="fixture"/>', timestamp=None):
    timestamp = timestamp or datetime.now(UTC).isoformat()
    path = fixture["root"] / "result.xml"
    path.write_text(
        f'<testsuites><testsuite timestamp="{timestamp}">{cases}</testsuite></testsuites>'
    )
    return body(
        fixture,
        runId=run["runId"],
        suites=[
            {
                "name": "Caller suite",
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ],
    )


def test_cli_publish_notify_actual_junit_updates_and_exact_retry_leave_user_uat_unchanged(
    installation, monkeypatch, capsys
):
    fixture = installation
    key, run = start(fixture, monkeypatch, capsys)
    updates = Updates(Store(fixture["data"]))
    assert updates.get(key)["qa"]["state"] == "pending"
    request = report(fixture, run)
    result = invoke(fixture, monkeypatch, capsys, "qa", request)
    assert result["qa"] == "passed" and result["suites"][0]["passed"] == 1
    item = updates.get(key)
    assert item["stage"] == "UAT" and item["uat"]["state"] == "pending"
    updates.uat(
        key,
        Acceptance(
            deploymentId=fixture["revision"], revision=item["revision"], state="passed"
        ),
    )
    replay = invoke(fixture, monkeypatch, capsys, "qa", request)
    assert replay["replayed"] and not replay["liveVerified"]
    assert updates.get(key)["uat"]["state"] == "passed"
    assert [
        "systemctl",
        "--user",
        "show",
        "fixture-api.service",
        "-p",
        "MainPID",
        "--value",
    ] in fixture["calls"]
    assert [
        "systemctl",
        "--user",
        "show",
        "fixture-mcp.service",
        "-p",
        "MainPID",
        "--value",
    ] in fixture["calls"]


@pytest.mark.parametrize("cases", ["", '<testcase name="skip"><skipped/></testcase>'])
def test_cli_rejects_zero_and_skipped_only_without_marking_qa(
    installation, monkeypatch, capsys, cases
):
    key, run = start(installation, monkeypatch, capsys)
    with pytest.raises(SystemExit):
        invoke(
            installation, monkeypatch, capsys, "qa", report(installation, run, cases)
        )
    assert Updates(Store(installation["data"])).get(key)["qa"]["state"] == "pending"


def test_cli_reports_failures_and_rejects_earlier_timestamps_and_missing_reports(
    installation, monkeypatch, capsys
):
    key, run = start(installation, monkeypatch, capsys)
    earlier = (
        datetime.fromisoformat(run["startedAt"]) - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(SystemExit):
        invoke(
            installation,
            monkeypatch,
            capsys,
            "qa",
            report(installation, run, timestamp=earlier),
        )
    missing = report(installation, run)
    missing["suites"][0]["path"] = "missing.xml"
    with pytest.raises(SystemExit):
        invoke(installation, monkeypatch, capsys, "qa", missing)
    result = invoke(
        installation,
        monkeypatch,
        capsys,
        "qa",
        report(
            installation,
            run,
            '<testcase name="failure"><failure>private traceback</failure></testcase>',
        ),
    )
    assert result["qa"] == "failed"
    item = Updates(Store(installation["data"])).get(key)
    assert item["stage"] == "QA" and "private traceback" not in item["qa"]["details"]


def test_cli_requires_notification_and_explicit_feature_mapping(
    installation, monkeypatch, capsys
):
    invoke(
        installation,
        monkeypatch,
        capsys,
        "publish",
        body(
            installation,
            features=[{"feature": "fixture", "title": "Fixture", "summary": "Shipped"}],
        ),
    )
    for features in [["fixture"], ["unpublished"]]:
        with pytest.raises(SystemExit):
            invoke(
                installation,
                monkeypatch,
                capsys,
                "begin-qa",
                body(installation, features=features),
            )
    assert (
        Updates(Store(installation["data"])).list()["items"][0]["qa"]["state"]
        == "pending"
    )


def test_cli_source_or_live_asset_mismatch_rejects_publication(
    installation, monkeypatch, capsys
):
    (installation["release"] / "source.py").write_text("tampered source")
    with pytest.raises(SystemExit):
        invoke(
            installation,
            monkeypatch,
            capsys,
            "publish",
            body(
                installation,
                features=[
                    {"feature": "fixture", "title": "Fixture", "summary": "Shipped"}
                ],
            ),
        )
    assert Updates(Store(installation["data"])).list()["items"] == []


def test_cli_release_mismatch_and_concurrent_qa_cannot_overwrite_newer_evidence(
    installation, monkeypatch, capsys
):
    key, run = start(installation, monkeypatch, capsys)
    request = report(installation, run)
    with pytest.raises(SystemExit):
        invoke(
            installation,
            monkeypatch,
            capsys,
            "qa",
            {**request, "sourceCommit": "f" * 40},
        )
    updates = Updates(Store(installation["data"]))
    updates.qa(
        key,
        QA(
            deploymentId=installation["revision"],
            state="failed",
            details="Independent newer regression",
        ),
    )
    with pytest.raises(SystemExit):
        invoke(installation, monkeypatch, capsys, "qa", request)
    assert updates.get(key)["qa"]["details"] == "Independent newer regression"


def test_cli_crash_after_domain_write_recovers_without_duplicate_qa_revision(
    installation, monkeypatch, capsys
):
    key, run = start(installation, monkeypatch, capsys)
    request = report(installation, run)
    original = Store.set

    def interrupted(store, name, value):
        if name == "release:request:" + request["requestId"] and value.get("result"):
            raise OSError("Simulated receipt write interruption")
        return original(store, name, value)

    monkeypatch.setattr(Store, "set", interrupted)
    with pytest.raises(SystemExit):
        invoke(installation, monkeypatch, capsys, "qa", request)
    updates = Updates(Store(installation["data"]))
    before = updates.get(key)["revision"]
    monkeypatch.setattr(Store, "set", original)
    result = invoke(installation, monkeypatch, capsys, "qa", request)
    assert result["qa"] == "passed" and updates.get(key)["revision"] == before


@pytest.mark.parametrize("kind", ["asset", "symlink", "no-inventory"])
def test_cli_rejects_asset_tampering_missing_staging_inventory_and_path_escape(
    installation, monkeypatch, capsys, kind
):
    fixture = installation
    asset = fixture["release"] / "apps/web/dist/assets/main.js"
    if kind == "asset":
        asset.write_text("mutated deployed asset")
    elif kind == "symlink":
        outside = fixture["root"] / "outside.js"
        outside.write_bytes(asset.read_bytes())
        asset.unlink()
        asset.symlink_to(outside)
    else:
        path = fixture["release"] / "release.json"
        manifest = json.loads(path.read_text())
        manifest.pop("assetsSha256")
        path.write_text(json.dumps(manifest))
    with pytest.raises(SystemExit):
        invoke(
            fixture,
            monkeypatch,
            capsys,
            "publish",
            body(
                fixture,
                features=[
                    {"feature": "fixture", "title": "Fixture", "summary": "Shipped"}
                ],
            ),
        )
    assert Updates(Store(fixture["data"])).list()["items"] == []


def test_actual_staging_script_records_client_asset_inventory(tmp_path):
    import sys

    script = Path(receipts.__file__).resolve().parents[1] / "scripts/stage-release.py"
    repo = tmp_path / "stage-repo"
    package = repo / "leam_api"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "app.py").write_text("# isolated staging import fixture\n")
    (package / "coding_policy.py").write_text("def coding_context(): return {}\n")
    (repo / "requirements.lock").write_text("# fixture\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    client = tmp_path / "client" / revision
    (client / "assets").mkdir(parents=True)
    (client / "index.html").write_text('<script src="/assets/main.js"></script>')
    (client / "assets/main.js").write_text("fixture artifact")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--client-dir",
            str(client),
            "--venv",
            sys.prefix,
            "--releases",
            str(tmp_path / "staged"),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    manifest = json.loads(result.stdout)
    assert manifest["assetsSha256"] == {
        "main.js": hashlib.sha256(b"fixture artifact").hexdigest()
    }
    assert (
        json.loads((Path(manifest["release"]) / "release.json").read_text())[
            "assetsSha256"
        ]
        == manifest["assetsSha256"]
    )


@pytest.mark.parametrize(
    "variant", ["missing-time", "false-tests", "false-failures", "orphan-case"]
)
def test_cli_rejects_uncovered_cases_and_false_declared_counts(
    installation, monkeypatch, capsys, variant
):
    fixture = installation
    key, run = start(fixture, monkeypatch, capsys)
    request = report(fixture, run)
    path = fixture["root"] / "result.xml"
    timestamp = datetime.now(UTC).isoformat()
    if variant == "missing-time":
        xml = f'<testsuites><testsuite timestamp="{timestamp}"><testcase name="new"/></testsuite><testsuite><testcase name="unbound"/></testsuite></testsuites>'
    elif variant == "orphan-case":
        xml = f'<testsuites><testsuite timestamp="{timestamp}"><testcase name="new"/></testsuite><testcase name="orphan"/></testsuites>'
    else:
        attribute = 'tests="2"' if variant == "false-tests" else 'failures="1"'
        xml = f'<testsuites><testsuite timestamp="{timestamp}" {attribute}><testcase name="new"/></testsuite></testsuites>'
    path.write_text(xml)
    request["suites"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SystemExit):
        invoke(fixture, monkeypatch, capsys, "qa", request)
    assert Updates(Store(fixture["data"])).get(key)["qa"]["state"] == "pending"


def test_cli_rejects_duplicate_report_content_under_different_names(
    installation, monkeypatch, capsys
):
    fixture = installation
    key, run = start(fixture, monkeypatch, capsys)
    request = report(fixture, run)
    shutil.copyfile(fixture["root"] / "result.xml", fixture["root"] / "copy.xml")
    request["suites"].append(
        {**request["suites"][0], "name": "Misleading second suite", "path": "copy.xml"}
    )
    with pytest.raises(SystemExit):
        invoke(fixture, monkeypatch, capsys, "qa", request)
    assert Updates(Store(fixture["data"])).get(key)["qa"]["state"] == "pending"


def test_interrupted_receipt_cannot_claim_historical_pass_after_later_failure(
    installation, monkeypatch, capsys
):
    fixture = installation
    key, run = start(fixture, monkeypatch, capsys)
    request = report(fixture, run)
    original = Store.set

    def interrupted(store, name, value):
        if name == "release:request:" + request["requestId"] and value.get("result"):
            raise OSError("Receipt interrupted")
        return original(store, name, value)

    monkeypatch.setattr(Store, "set", interrupted)
    with pytest.raises(SystemExit):
        invoke(fixture, monkeypatch, capsys, "qa", request)
    monkeypatch.setattr(Store, "set", original)
    updates = Updates(Store(fixture["data"]))
    latest = updates.qa(
        key,
        QA(
            deploymentId=fixture["revision"], state="failed", details="Later regression"
        ),
    )
    with pytest.raises(SystemExit):
        invoke(fixture, monkeypatch, capsys, "qa", request)
    assert updates.get(key)["revision"] == latest["revision"]
    assert updates.get(key)["qa"]["details"] == "Later regression"
