"""Small operator CLI binding Updates publication and JUnit QA to a live release."""

import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator

from .commitments import Input
from .store import Store
from .updates import QA, Publication, Updates

MAX_JSON = 128 * 1024
MAX_REPORT = 10 * 1024 * 1024


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    with Path(path).open("rb") as file:
        raw = file.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("JSON input exceeds 128 KiB")
    return json.loads(raw)


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return result.timestamp()


def now_iso():
    return datetime.now(UTC).isoformat()


def command(args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def verify_identity(descriptor, expected):
    """Read actual source/process/client, not merely the mutable receipt label."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("Use the full deployed source commit")
    receipt = read_json(descriptor["currentDeploymentReceipt"])
    root = Path(descriptor["releaseRoot"]).resolve(strict=True)
    release = Path(receipt["release"]).resolve(strict=True)
    if receipt["sourceCommit"] != expected or release != root / expected:
        raise ValueError("The selected release is not the current deployed source")
    manifest_path = release / "release.json"
    if manifest_path.is_symlink() or not manifest_path.resolve().is_relative_to(
        release
    ):
        raise ValueError("Release manifest escapes its release")
    manifest = read_json(manifest_path)
    if manifest["sourceCommit"] != expected:
        raise ValueError("Immutable release manifest source mismatch")
    # Verify every archived tracked file against Git's actual object IDs. A
    # directory name and manifest alone cannot establish immutable source identity.
    tree = command(
        ["git", "-C", descriptor["productRepository"], "ls-tree", "-r", expected]
    )
    for entry in tree.splitlines():
        header, name = entry.split("\t", 1)
        mode, kind, object_id = header.split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unsupported source archive entry")
        file = release / name
        if (
            file.is_symlink()
            or not file.is_file()
            or not file.resolve().is_relative_to(release)
        ):
            raise ValueError("Release source file missing or replaced")
        raw = file.read_bytes()
        if (
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            != object_id
        ):
            raise ValueError("Immutable release source content changed: " + name)
    if not tree:
        raise ValueError("Release source archive is empty")
    processes = {}
    for role, pid_field in [("api", "pid"), ("mcp", "mcpPid")]:
        service = descriptor["services"].get(role)
        if not service:
            if role == "api":
                raise ValueError("API service identity is required")
            continue
        pid = int(
            command(
                ["systemctl", "--user", "show", service, "-p", "MainPID", "--value"]
            )
        )
        if (
            pid <= 0
            or pid != receipt.get(pid_field)
            or Path(f"/proc/{pid}/cwd").resolve(strict=True) != release
        ):
            raise ValueError("Current service process does not match deployed release")
        # Linux process start tick protects a long-running receipt from PID reuse.
        start_tick = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        processes[role] = {"pid": pid, "startTick": start_tick}
    url = descriptor["candidateUrl"].rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Candidate URL must be a plain HTTP(S) origin")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(path, maximum):
        with opener.open(url + path, timeout=15) as response:
            if response.geturl() != url + path:
                raise ValueError("Candidate identity request redirected")
            body = response.read(maximum + 1)
        if len(body) > maximum:
            raise ValueError("Candidate identity response exceeds its size limit")
        return body

    index_path = release / "apps/web/dist/index.html"
    if index_path.is_symlink() or not index_path.resolve().is_relative_to(release):
        raise ValueError("Client index escapes its release")
    index = index_path.read_bytes()
    if (
        digest(index) != manifest["clientIndexSha256"]
        or manifest["clientIndexSha256"] != receipt["clientIndexSha256"]
        or fetch("/", MAX_JSON) != index
    ):
        raise ValueError("Live client index does not match immutable release")
    assets = {}
    expected_assets = manifest.get("assetsSha256")
    if not isinstance(expected_assets, dict) or not expected_assets:
        raise ValueError(
            "This release lacks a staging-time asset inventory; stage a new release"
        )
    asset_root = release / "apps/web/dist/assets"
    if asset_root.is_symlink() or not asset_root.resolve().is_relative_to(release):
        raise ValueError("Client asset directory escapes its release")
    for asset in sorted(asset_root.iterdir()):
        if (
            not asset.is_file()
            or asset.is_symlink()
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", asset.name)
        ):
            raise ValueError("Unsupported client asset")
        body = asset.read_bytes()
        if len(body) > MAX_REPORT or fetch("/assets/" + asset.name, MAX_REPORT) != body:
            raise ValueError("Live client asset does not match immutable release")
        assets[asset.name] = digest(body)
    if assets != expected_assets:
        raise ValueError("Client assets differ from their immutable staging inventory")
    if not assets:
        raise ValueError("No immutable client assets found")
    if stamp(receipt["deployedAt"]) > time.time():
        raise ValueError("Deployment receipt is in the future")
    return {
        "sourceCommit": expected,
        "deployedAt": receipt["deployedAt"],
        "clientIndexSha256": digest(index),
        "assets": assets,
        "processes": processes,
    }


class Feature(Input):
    feature: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=10000)


class Request(Input):
    requestId: UUID
    sourceCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class Publish(Request):
    features: list[Feature] = Field(min_length=1, max_length=50)


class Selected(Request):
    features: list[str] = Field(min_length=1, max_length=50)

    @field_validator("features")
    @classmethod
    def ids(cls, value):
        if len(set(value)) != len(value) or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", item) for item in value
        ):
            raise ValueError("Features must be unique canonical IDs")
        return value


class Notify(Selected):
    evidence: str = Field(min_length=1, max_length=500)


class Suite(Input):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestampOffset: str | None = Field(
        default=None, pattern=r"^[+-][0-2][0-9]:[0-5][0-9]$"
    )


class Report(Request):
    runId: UUID
    suites: list[Suite] = Field(min_length=1, max_length=30)


def junit(suite, directory, started):
    path = (directory / suite.path).resolve(strict=True)
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError("QA report must be a file inside its input directory")
    with path.open("rb") as file:
        raw = file.read(MAX_REPORT + 1)
    if len(raw) > MAX_REPORT or digest(raw) != suite.sha256:
        raise ValueError("QA report exceeds limit or its hash does not match")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD/entity declarations are not accepted in QA reports")
    root = ET.fromstring(raw)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Expected JUnit XML from the actual test runner")
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if not cases or len(cases) > 100000:
        raise ValueError("QA report contains zero tests or too many test cases")
    parents = {child: parent for parent in root.iter() for child in parent}
    for case in cases:
        owner = parents.get(case)
        if owner is None or owner.tag != "testsuite" or "timestamp" not in owner.attrib:
            raise ValueError("Every testcase must belong to a timestamped test suite")
    for item in suites:
        if "timestamp" not in item.attrib:
            continue  # A container with no direct cases may omit its timestamp.
        value = item.attrib["timestamp"]
        if datetime.fromisoformat(value).tzinfo is None:
            if not suite.timestampOffset:
                raise ValueError(
                    "Naive JUnit timestamps require the runner's explicit timestampOffset"
                )
            value += suite.timestampOffset
        timestamp = stamp(value)
        if timestamp <= started or timestamp > time.time():
            raise ValueError(
                "JUnit suite timestamps must prove tests started after begin-qa"
            )
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    outcomes = {}
    for case in cases:
        present = [
            tag for tag in ("error", "failure", "skipped") if case.find(tag) is not None
        ]
        if len(present) > 1:
            raise ValueError("A testcase has contradictory result outcomes")
        state = {"error": "errors", "failure": "failed", "skipped": "skipped"}.get(
            present[0] if present else "", "passed"
        )
        outcomes[case] = state
        counts[state] += 1
    for item in [root, *[item for item in suites if item is not root]]:
        descendants = list(item.iter("testcase"))
        expected = {
            "tests": len(descendants),
            "failures": sum(outcomes[case] == "failed" for case in descendants),
            "errors": sum(outcomes[case] == "errors" for case in descendants),
            "skipped": sum(outcomes[case] == "skipped" for case in descendants),
        }
        for field, count in expected.items():
            declared = item.attrib.get(field)
            if declared is not None and (
                not re.fullmatch(r"[0-9]+", declared) or int(declared) != count
            ):
                raise ValueError("JUnit declared counts disagree with its test cases")
    for item in root.iter():
        if (
            item.tag in {"failure", "error", "skipped"}
            and parents.get(item) not in outcomes
        ):
            raise ValueError("Suite-level outcomes require explicit testcase outcomes")
    if counts["passed"] + counts["failed"] + counts["errors"] == 0:
        raise ValueError("Skipped-only QA cannot establish executed behavior")
    return {"name": suite.name, "sha256": suite.sha256, **counts}


class ReleaseReceipts:
    def __init__(self, descriptor, verify=None):
        self.descriptor = descriptor
        self.verify = verify or verify_identity
        data = Path(descriptor["dataDirectory"])
        if not (data / "leam.sqlite3").is_file():
            raise ValueError(
                "Choose an existing installation, never a new data directory"
            )
        self.store = Store(data)
        self.updates = Updates(self.store)
        self.data = data

    @contextmanager
    def lock(self):
        with (self.data / ".release-receipts.lock").open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            yield

    def selected(self, release, features):
        rows = []
        for feature in features:
            publication = self.store.get(f"release:feature:{release}:{feature}")
            if not publication:
                raise ValueError(
                    "Feature has no publication from this tool: " + feature
                )
            item = self.updates.get(publication["updateId"])
            if item["superseded"] or item["deploymentId"] != release:
                raise ValueError("Feature publication is no longer current: " + feature)
            rows.append(item)
        return rows

    def run(self, operation, request, directory=Path(".")):
        payload = {"operation": operation, **request.model_dump(mode="json")}
        fingerprint = digest(json.dumps(payload, sort_keys=True).encode())
        key = "release:request:" + str(request.requestId)
        with self.lock():
            previous = self.store.get(key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("Request ID was reused with different evidence")
                if previous.get("result"):
                    return {
                        **previous["result"],
                        "replayed": True,
                        "liveVerified": False,
                    }
            identity = self.verify(self.descriptor, request.sourceCommit)
            self.store.set(key, {"fingerprint": fingerprint})
            observed = now_iso()
            if operation == "publish":
                if len({item.feature for item in request.features}) != len(
                    request.features
                ):
                    raise ValueError("Duplicate publication feature IDs")
                items = []
                for feature in request.features:
                    item = self.updates.publish(
                        Publication(
                            **feature.model_dump(),
                            deploymentId=request.sourceCommit,
                            deployedAt=identity["deployedAt"],
                        )
                    )
                    self.store.set(
                        f"release:feature:{request.sourceCommit}:{feature.feature}",
                        {"updateId": item["id"], "publishedAt": observed},
                    )
                    items.append(
                        {
                            "feature": feature.feature,
                            "updateId": item["id"],
                            "revision": item["revision"],
                            "qa": item["qa"]["state"],
                        }
                    )
                result = {"features": items, "notification": "not sent by this tool"}
            elif operation == "notify":
                items = self.selected(request.sourceCommit, request.features)
                for item in items:
                    self.store.set(
                        f"release:notified:{request.sourceCommit}:{item['feature']}",
                        {
                            "at": observed,
                            "evidence": request.evidence,
                            "kind": "operator_attestation",
                        },
                    )
                result = {
                    "features": request.features,
                    "notification": "operator-attested; delivery not independently verified",
                }
            elif operation == "begin-qa":
                items = self.selected(request.sourceCommit, request.features)
                for item in items:
                    notification = self.store.get(
                        f"release:notified:{request.sourceCommit}:{item['feature']}"
                    )
                    if not notification or stamp(notification["at"]) <= stamp(
                        identity["deployedAt"]
                    ):
                        raise ValueError(
                            "Notify the user and record acknowledgement before beginning QA"
                        )
                run = {
                    "runId": str(request.requestId),
                    "sourceCommit": request.sourceCommit,
                    "identity": identity,
                    "startedAt": observed,
                    "features": [
                        {
                            "feature": item["feature"],
                            "updateId": item["id"],
                            "revision": item["revision"],
                        }
                        for item in items
                    ],
                }
                self.store.set("release:qa-run:" + str(request.requestId), run)
                result = {
                    "runId": str(request.requestId),
                    "features": request.features,
                    "startedAt": observed,
                }
            elif operation == "qa":
                run = self.store.get("release:qa-run:" + str(request.runId))
                if (
                    not run
                    or run["sourceCommit"] != request.sourceCommit
                    or run["identity"] != identity
                ):
                    raise ValueError(
                        "QA run is missing or the live release/process identity changed"
                    )
                if (
                    len({suite.path for suite in request.suites}) != len(request.suites)
                    or len({suite.name for suite in request.suites})
                    != len(request.suites)
                    or len({suite.sha256 for suite in request.suites})
                    != len(request.suites)
                ):
                    raise ValueError("Duplicate QA suite reports or duplicate content")
                suites = [
                    junit(suite, directory, stamp(run["startedAt"]))
                    for suite in request.suites
                ]
                failed = any(suite["failed"] or suite["errors"] for suite in suites)
                state = "failed" if failed else "passed"
                details = (
                    "Release "
                    + request.sourceCommit
                    + ". QA run "
                    + str(request.runId)
                    + ". Evidence "
                    + fingerprint
                    + ". "
                    + "; ".join(
                        f"{suite['name']}: {suite['passed']} passed, {suite['failed']} failed, {suite['errors']} errors, {suite['skipped']} skipped"
                        for suite in suites
                    )
                )
                items = self.selected(
                    request.sourceCommit, [item["feature"] for item in run["features"]]
                )
                for expected, item in zip(run["features"], items, strict=True):
                    # Recover a crash after the domain commit but before our receipt:
                    # an exact evidence marker in its event journal proves application.
                    with self.store.connect() as db:
                        applied = db.execute(
                            "SELECT sequence FROM deployment_update_events WHERE update_id=? AND actor='operator.qa' AND json_extract(body,'$.qa.details')=? AND json_extract(body,'$.qa.state')=? LIMIT 1",
                            (item["id"], details, state),
                        ).fetchone()
                    if applied:
                        current = self.updates.get(item["id"])
                        if (
                            current["sequence"] != applied["sequence"]
                            or current["revision"] != expected["revision"] + 1
                            or current["qa"]["details"] != details
                            or current["qa"]["state"] != state
                        ):
                            raise ValueError(
                                "Historical QA evidence was superseded by a newer review; inspect current status before proceeding"
                            )
                    else:
                        self.updates.qa(
                            item["id"],
                            QA(
                                deploymentId=request.sourceCommit,
                                expectedRevision=expected["revision"],
                                state=state,
                                details=details,
                            ),
                        )
                result = {
                    "runId": str(request.runId),
                    "features": [item["feature"] for item in items],
                    "qa": state,
                    "suites": suites,
                    "uat": "unchanged",
                }
            else:
                raise ValueError("Unknown receipt operation")
            result = {
                "requestId": str(request.requestId),
                "sourceCommit": request.sourceCommit,
                "operation": operation,
                "observedAt": observed,
                "liveVerified": True,
                "replayed": False,
                **result,
            }
            self.store.set(key, {"fingerprint": fingerprint, "result": result})
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument(
        "operation", choices=["inspect", "publish", "notify", "begin-qa", "qa"]
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        descriptor = read_json(args.descriptor)
        if args.operation == "inspect":
            result = verify_identity(descriptor, args.source_commit or "")
        else:
            if not args.input:
                raise ValueError("This operation requires --input JSON")
            model = {
                "publish": Publish,
                "notify": Notify,
                "begin-qa": Selected,
                "qa": Report,
            }[args.operation]
            request = model.model_validate(read_json(args.input))
            result = ReleaseReceipts(descriptor).run(
                args.operation, request, args.input.resolve().parent
            )
    except (
        ValueError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        ET.ParseError,
    ) as error:
        parser.exit(1, "Release receipt rejected: " + str(error) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
