"""Actual upload/resource/download callers using synthetic complete JPEG streams."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_attachments import upload

from leam_api.app import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "jpeg-mobile"
FORMATS = ("baseline", "progressive")


def jpeg(name):
    return (FIXTURES / (name + ".jpg")).read_bytes()


def app1(payload):
    return b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload


def compatible_bytes(data, variation):
    if variation == "trailing-nul":
        return data + b"\0" * 32
    if variation == "exif":
        # Little-endian TIFF with a valid empty IFD, inside an opaque APP1 segment.
        metadata = b"Exif\0\0II\x2a\0\x08\0\0\0\0\0\0\0\0\0"
        return data[:2] + app1(metadata) + data[2:]
    if variation == "trailing-metadata":
        return data + b"\n<x:xmpmeta>synthetic shared image</x:xmpmeta>\n"
    if variation == "secondary-jpeg":
        return data + jpeg("baseline")
    return data


@pytest.mark.parametrize("encoding", FORMATS)
@pytest.mark.parametrize(
    "variation",
    ("original", "trailing-nul", "exif", "trailing-metadata", "secondary-jpeg"),
)
def test_mobile_jpeg_upload_preserves_complete_stream_and_resource(
    tmp_path, encoding, variation
):
    data = compatible_bytes(jpeg(encoding), variation)
    app = create_app(
        tmp_path,
        codex=FakeCodex(),
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
    )
    with TestClient(app) as client:
        login(client)
        response = upload(client, data, "image/jpeg", "shared-image.jpg")
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved["mimeType"] == "image/jpeg"
        assert saved["sha256"] == hashlib.sha256(data).hexdigest()
        downloaded = client.get("/api/attachments/" + saved["id"])
        assert downloaded.status_code == 200
        assert downloaded.content == data
        assert downloaded.headers["content-type"] == "image/jpeg"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        resource = client.get("/api/artifacts/attachment-" + saved["id"])
        assert resource.status_code == 200
        resource = resource.json()
        assert resource["kind"] == "jpeg"
        assert resource["mimeType"] == "image/jpeg"
        assert resource["sha256"] == saved["sha256"]
        assert client.get(resource["downloadUrl"]).content == data
        assert client.get(resource["previewUrl"]).content == data


def malformed_bytes(data, variation):
    assert data.endswith(b"\xff\xd9"), "Fixture must have a real terminal EOI"
    if variation == "missing-eoi":
        return data[:-2]
    if variation == "truncated-segment":
        # A claimed 64-byte APP1 segment cannot fit, even though bytes end in EOI.
        return data[:2] + b"\xff\xe1\x00\x40short\xff\xd9"
    # The apparent EOI is metadata, not a marker terminating the image stream.
    return data[:2] + app1(b"synthetic\xff\xd9metadata") + data[2:-2]


@pytest.mark.parametrize("encoding", FORMATS)
@pytest.mark.parametrize(
    "variation", ("missing-eoi", "truncated-segment", "eoi-in-app1")
)
def test_incomplete_jpeg_is_rejected_before_blob_or_resource_write(
    tmp_path, encoding, variation
):
    data = malformed_bytes(jpeg(encoding), variation)
    app = create_app(
        tmp_path,
        codex=FakeCodex(),
        origins={"http://testserver"},
        bootstrap="bootstrap-for-tests",
    )
    with TestClient(app) as client:
        login(client)
        response = upload(client, data, "image/jpeg", "private-name.jpg")
        assert response.status_code == 422
        assert "private-name" not in response.json()["detail"]
        with app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 0
        resources = client.get("/api/artifacts").json()
        assert resources["items"] == []
