"""Mandatory operator publication step for an observed candidate deployment.

No QA or UAT is inferred. A failed bookkeeping step is retryable without redeploying.
"""

import argparse
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from uuid import uuid4

from pydantic import Field, model_validator

from .backlog import Backlog, FeatureId
from .backlog_evidence import BacklogEvidence, DeltaRequest
from .commitments import Input
from .store import Store
from .updates import Publication, Updates


class Increment(Input):
    feature: FeatureId
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)


class Manifest(Input):
    sourceCommit: str = Field(pattern=r"^[a-f0-9]{40}$")
    increments: list[Increment] = Field(min_length=1, max_length=50)
    reviewedEvidence: dict[FeatureId, str] = Field(default_factory=dict, max_length=500)
    acknowledgeMetadata: bool = False

    @model_validator(mode="after")
    def unique(self):
        if any(
            not reason.strip() or len(reason) > 1000
            for reason in self.reviewedEvidence.values()
        ):
            raise ValueError(
                "Every reviewed evidence reason must contain 1–1000 nonblank characters"
            )
        if len({x.feature for x in self.increments}) != len(self.increments):
            raise ValueError("Duplicate release feature")
        return self


def verify_live(receipt):
    release = Path(__file__).resolve().parents[1]
    if release != Path(receipt["release"]).resolve():
        raise ValueError("Run publication from the exact installed release")
    installed = json.loads((release / "release.json").read_text())
    for key in ("sourceCommit", "clientIndexSha256"):
        if installed[key] != receipt[key]:
            raise ValueError("Deployment receipt differs from installed artifact")
    pid = int(
        subprocess.check_output(
            [
                "systemctl",
                "--user",
                "show",
                "leam-next-candidate.service",
                "-p",
                "MainPID",
                "--value",
            ],
            text=True,
            timeout=5,
        )
    )
    if Path(f"/proc/{pid}/cwd").resolve() != release:
        raise ValueError("Installed release is not the running API")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:46400/", timeout=5) as response:
        content = response.read(1048577)
    if hashlib.sha256(content).hexdigest() != receipt["clientIndexSha256"]:
        raise ValueError("Served client does not match deployed artifact")


def finalize(store, manifest, receipt):
    if manifest.sourceCommit != receipt["sourceCommit"]:
        raise ValueError("Release manifest is for a different deployment")
    backlog, updates = Backlog(store), Updates(store)
    with store.connect() as db:
        for change in manifest.increments:
            if not db.execute(
                "SELECT 1 FROM backlog_assessments WHERE feature=?", (change.feature,)
            ).fetchone():
                raise ValueError(
                    "Register release feature before publication: " + change.feature
                )
            tombstone = store.get("backlog:deleted:" + change.feature)
            if tombstone and not tombstone.get("restoredAt"):
                raise ValueError(
                    "Deleted release feature requires explicit review: "
                    + change.feature
                )
    key = "deployment:workflow:" + manifest.sourceCommit
    store.set(key, {"state": "publishing", "sourceCommit": manifest.sourceCommit})
    published = []
    for change in manifest.increments:
        publication = Publication(
            **change.model_dump(),
            deploymentId=manifest.sourceCommit,
            deployedAt=receipt["deployedAt"],
        )
        published.append(updates.publish(publication)["id"])
    evidence = BacklogEvidence(backlog)
    snapshot = evidence.snapshot(uuid4())
    delta = evidence.delta(snapshot["snapshotId"])
    changes = set(delta["changedFeatures"])
    supplied = manifest.reviewedEvidence
    missing = (changes | {change.feature for change in manifest.increments}) - set(
        supplied
    )
    if missing:
        store.set(
            key,
            {
                "state": "review_required",
                "sourceCommit": manifest.sourceCommit,
                "updateIds": published,
                "changedFeatures": sorted(missing),
            },
        )
        raise ValueError(
            "Review changed backlog evidence before workflow completion: "
            + ", ".join(sorted(missing))
        )
    review = evidence.prepare(
        DeltaRequest(
            requestId=uuid4(),
            snapshotId=snapshot["snapshotId"],
            trigger="deployment",
            acknowledge={feature: supplied[feature] for feature in changes},
            acknowledgeMetadata=manifest.acknowledgeMetadata,
        ),
        dry_run=False,
    )
    result = {
        "state": "published_and_reconciled",
        "sourceCommit": manifest.sourceCommit,
        "updateIds": published,
        "review": review,
        "qa": "not_inferred",
        "uat": "user_only",
    }
    store.set(key, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(args.manifest.read_text())
    receipt = json.loads(args.receipt.read_text())
    verify_live(receipt)
    print(
        json.dumps(finalize(Store(Path(receipt["dataDirectory"])), manifest, receipt))
    )


if __name__ == "__main__":
    main()
