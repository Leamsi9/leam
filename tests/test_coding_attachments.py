import json
import struct
import zlib
from pathlib import Path

import pytest
from shared_coding_fixtures import SHARED_THREAD
from test_api import login, make
from test_attachments import ORIGIN, upload
from test_shared_coding_api import api_owner


def png():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 2) * 2))
        + chunk(b"IEND", b"")
    )


def pdf():
    stream = b"BT /F1 12 Tf 50 100 Td (Synthetic PDF marker) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    offset = len(data)
    data += (
        b"xref\n0 6\n0000000000 65535 f \n"
        + b"".join(f"{n:010} 00000 n \n".encode() for n in offsets[1:])
        + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{offset}\n%%EOF".encode()
    )
    return data


def test_actual_coding_request_keeps_text_protocol_and_attachment_identity(tmp_path):
    client, codex = make(tmp_path)
    with client:
        login(client)
        image = upload(client, png(), "image/png", "image.png").json()
        document = upload(client, pdf(), "application/pdf", "document.pdf").json()
        assert (
            client.post(
                "/api/codex/threads/t/connect",
                json={"handoffConfirmed": True},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        body = {
            "text": "  Exact user text\n",
            "requestId": "attachment-request-1",
            "attachmentIds": [image["id"], document["id"]],
        }
        response = client.post("/api/codex/threads/t/turns", json=body, headers=ORIGIN)
        assert response.status_code == 200, response.text
        calls = [p for m, p in codex.calls if m == "turn/start"]
        assert len(calls) == 1
        sent = calls[0]
        assert sent["input"][0]["text"] == body["text"]
        assert (
            sent["input"][1]["type"] == "localImage"
            and Path(sent["input"][1]["path"]).read_bytes() == png()
        )
        assert (
            sent["additionalContext"]["leam.agent-protocols"]["kind"] == "application"
        )
        attached = sent["additionalContext"]["leam.attachments"]
        assert attached["kind"] == "untrusted"
        assert (
            "Synthetic PDF marker"
            in json.loads(attached["value"])["documents"][0]["text"]
        )
        assert (
            client.post("/api/codex/threads/t/turns", json=body, headers=ORIGIN).json()
            == response.json()
        )
        assert len([m for m, _ in codex.calls if m == "turn/start"]) == 1
        assert (
            client.post(
                "/api/codex/threads/t/turns",
                json={**body, "attachmentIds": [image["id"]]},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        assert (
            client.delete("/api/attachments/" + image["id"], headers=ORIGIN).status_code
            == 409
        )


def test_empty_or_unextractable_pdf_never_dispatches(tmp_path):
    client, codex = make(tmp_path)
    with client:
        login(client)
        doc = upload(client, b"%PDF-1.4\n%%EOF", "application/pdf", "scan.pdf").json()
        client.post(
            "/api/codex/threads/t/connect",
            json={"handoffConfirmed": True},
            headers=ORIGIN,
        )
        response = client.post(
            "/api/codex/threads/t/turns",
            json={
                "text": "",
                "requestId": "pdf-request-1",
                "attachmentIds": [doc["id"]],
            },
            headers=ORIGIN,
        )
        assert response.status_code == 422, response.text
        assert not [m for m, _ in codex.calls if m == "turn/start"]


@pytest.mark.parametrize("active", [True, False])
async def test_shared_owner_attachment_api_forwards_start_and_steer_without_policy_override(
    tmp_path, monkeypatch, active
):
    async with api_owner(tmp_path, monkeypatch, active=active) as (
        client,
        _service,
        observed,
        other,
        _,
    ):
        image = (
            await client.post(
                "/api/attachments?filename=fixture.png",
                content=png(),
                headers={"content-type": "image/png"},
            )
        ).json()
        text = (
            await client.post(
                "/api/attachments?filename=note.txt",
                content=b"Synthetic reference",
                headers={"content-type": "text/plain"},
            )
        ).json()
        view = (await client.get(f"/api/codex/threads/{SHARED_THREAD}")).json()
        body = {
            "text": "Exact owner input",
            "requestId": "shared-image-request",
            "generation": view["generation"],
            "attachmentIds": [image["id"], text["id"]],
        }
        response = await client.post(
            f"/api/codex/threads/{SHARED_THREAD}/turns", json=body
        )
        assert response.status_code == 200, response.text
        method = (
            "thread-follower-steer-turn" if active else "thread-follower-start-turn"
        )
        packet = next(m for m in observed if m["method"] == method)["params"]
        sent = packet if active else packet["turnStart"]["request"]
        assert sent["input"][0]["text"] == body["text"]
        assert sent["input"][1]["type"] == "localImage"
        assert Path(sent["input"][1]["path"]).read_bytes() == png()
        assert (
            sent["additionalContext"]["leam.agent-protocols"]["kind"] == "application"
        )
        assert sent["additionalContext"]["leam.attachments"]["kind"] == "untrusted"
        assert (
            "model" not in sent
            and "effort" not in sent
            and "approvalPolicy" not in sent
        )
        assert (
            await client.post(f"/api/codex/threads/{SHARED_THREAD}/turns", json=body)
        ).json() == response.json()
        assert sum(m["method"] == method for m in observed) == 1
        assert not other.calls
