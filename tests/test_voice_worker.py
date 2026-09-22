"""Installed stock catalog and caller-level selection boundaries."""

import hashlib

import pytest
from test_api import login
from test_local_voice import fixture

from leam_api.voice_catalog import STOCK_VOICES, installed_voices
from leam_api.voice_worker import SpeechEngine


def test_real_app_lists_installed_stock_voices_and_dispatches_selection(tmp_path):
    client, engine, _worker = fixture(tmp_path)
    engine.voices = ("alba", "anna")
    with client:
        login(client)
        status = client.get("/api/voice/status").json()
        assert status["voices"] == ["alba", "anna"]
        response = client.post(
            "/api/voice/speak",
            json={"text": "Synthetic voice selection.", "voice": "anna"},
        )
        assert response.status_code == 200
        assert response.text.endswith('{"done":true}\n')
        assert engine.spoken == [("Synthetic voice selection.", "anna")]


def test_uninstalled_stock_and_arbitrary_voices_never_reach_synthesis(tmp_path):
    client, engine, _worker = fixture(tmp_path)
    engine.voices = ("alba",)
    with client:
        login(client)
        for voice in ("anna", "../../anna", "https://example.invalid/voice", "jean"):
            response = client.post(
                "/api/voice/speak", json={"text": "Synthetic text.", "voice": voice}
            )
            assert response.status_code == 422
        assert not engine.spoken
        assert client.get("/api/voice/status").json()["busy"] is False
        assert (
            client.post(
                "/api/voice/speak", json={"text": "Alba remains usable."}
            ).status_code
            == 200
        )
        assert engine.spoken == [("Alba remains usable.", "alba")]


def test_installed_catalog_checks_content_and_excludes_unlisted_files(
    tmp_path, monkeypatch
):
    for name in ("alba", "anna", "george"):
        raw = ("synthetic " + name).encode()
        (tmp_path / f"{name}.safetensors").write_bytes(raw)
        monkeypatch.setitem(STOCK_VOICES, name, hashlib.sha256(raw).hexdigest())
    (tmp_path / "anna.safetensors").write_bytes(b"modified content")
    result = installed_voices(
        tmp_path, {"voices": ["alba", "alba", "anna", "../george", {}, "mary"]}
    )
    assert result == ("alba",)
    # A legacy installation without an explicit inventory keeps verified Alba.
    assert installed_voices(tmp_path, {}) == ("alba",)
    (tmp_path / "alba.safetensors").unlink()
    (tmp_path / "alba.safetensors").symlink_to(tmp_path / "george.safetensors")
    with pytest.raises(ValueError, match="No verified stock voices"):
        installed_voices(tmp_path, {"voices": ["alba", "anna"]})


def test_voice_switch_loads_selected_installed_state_and_reuses_it():
    class Chunk:
        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self

        def astype(self, _format):
            return self

        def tobytes(self):
            return b"synthetic PCM"

    class TTS:
        def __init__(self):
            self.loads = []
            self.spoken = []

        def get_state_for_audio_prompt(self, path):
            self.loads.append(path.name)
            return path.stem

        def generate_audio_stream(self, state, text):
            self.spoken.append((state, text))
            yield Chunk()

    from pathlib import Path

    engine = SpeechEngine.__new__(SpeechEngine)
    engine.directory = Path("/synthetic-stock-voices")
    engine.voices = ("alba", "anna")
    engine.voice_name = "alba"
    engine.voice = "alba"
    engine.tts = TTS()
    for voice in ("anna", "anna", "alba"):
        assert list(engine.speak("Synthetic text", voice)) == [b"synthetic PCM"]
    assert engine.tts.loads == ["anna.safetensors", "alba.safetensors"]
    assert engine.tts.spoken == [
        (voice, "Synthetic text") for voice in ("anna", "anna", "alba")
    ]
    with pytest.raises(ValueError, match="not installed"):
        list(engine.speak("Synthetic text", "george"))
    assert len(engine.tts.spoken) == 3
