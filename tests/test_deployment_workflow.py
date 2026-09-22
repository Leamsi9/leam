import pytest

from leam_api.backlog import Assessment, Backlog
from leam_api.deployment_workflow import Manifest, finalize
from leam_api.store import Store
from leam_api.updates import QA, Updates

SHA = "a" * 40


def fixture(path):
    store = Store(path)
    Backlog(store).upsert(
        Assessment(feature="example", title="Example", percent=80, currentStep="Built")
    )
    manifest = Manifest(
        sourceCommit=SHA,
        increments=[
            {"feature": "example", "title": "Example", "summary": "Deployed behavior"}
        ],
        acknowledgeMetadata=True,
    )
    receipt = {"sourceCommit": SHA, "deployedAt": "2026-09-22T10:00:00Z"}
    return store, manifest, receipt


def test_gate_requires_review_then_retries_without_duplicate_or_qa_reset(tmp_path):
    store, manifest, receipt = fixture(tmp_path)
    with pytest.raises(ValueError, match="Review changed"):
        finalize(store, manifest, receipt)
    assert store.get("deployment:workflow:" + SHA)["state"] == "review_required"
    assert Backlog(store).list()["items"] == []
    manifest.reviewedEvidence = {
        "example": "Source and live release checked; deployment available, QA pending."
    }
    done = finalize(store, manifest, receipt)
    assert done["state"] == "published_and_reconciled"
    Updates(store).qa(
        done["updateIds"][0],
        QA(deploymentId=SHA, state="passed", details="Actual caller evidence"),
    )
    replay = finalize(store, manifest, receipt)
    assert replay["updateIds"] == done["updateIds"]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM deployment_updates").fetchone()[0] == 1
        assert (
            '"passed"'
            in db.execute("SELECT body FROM deployment_updates").fetchone()[0]
        )


def test_wrong_source_and_unregistered_feature_fail_before_publication(tmp_path):
    store, manifest, receipt = fixture(tmp_path)
    with pytest.raises(ValueError, match="different deployment"):
        finalize(store, manifest, {**receipt, "sourceCommit": "b" * 40})
    manifest.increments[0].feature = "unregistered"
    with pytest.raises(ValueError, match="Register release feature"):
        finalize(store, manifest, receipt)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM deployment_updates").fetchone()[0] == 0


def test_empty_increment_acknowledgement_rejected_before_publication():
    with pytest.raises(ValueError, match="nonblank"):
        Manifest(
            sourceCommit=SHA,
            increments=[
                {"feature": "example", "title": "Example", "summary": "Actual change"}
            ],
            reviewedEvidence={"example": "  "},
        )
