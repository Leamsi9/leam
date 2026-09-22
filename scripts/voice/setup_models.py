"""Explicit operator-only downloads. No audio capture, voice cloning, or service start."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from leam_api.voice_catalog import (
    STOCK_VOICES,
    VOICE_PREFIX,
    VOICE_REPOSITORY,
    VOICE_REVISION,
)

os.umask(0o077)
os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_XET="1")
parser = argparse.ArgumentParser()
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument(
    "--stock-voices-only",
    action="store_true",
    help="Add verified stock embeddings to an existing installation",
)
args = parser.parse_args()
directory = args.directory.expanduser().resolve()
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
import pocket_tts
import yaml
from huggingface_hub import hf_hub_download
from moonshine_voice import ModelArch
from moonshine_voice.download import find_model_info, get_model_for_language

if not args.stock_voices_only:
    moonshine, arch = get_model_for_language(
        "en",
        ModelArch.SMALL_STREAMING,
        cache_root=directory / "moonshine",
        on_progress=lambda *args: None,
    )
    print("English Moonshine model ready", flush=True)
else:
    manifest = json.loads((directory / "models.json").read_text())
revision = "d29db7978e464fb90cb3359ee0c69a273b9142cc"
voice_revision = VOICE_REVISION
repo = VOICE_REPOSITORY
files = (
    [
        ("languages/english_2026-04/model.safetensors", revision, "pocket.safetensors"),
        ("languages/english_2026-04/tokenizer.model", revision, "tokenizer.model"),
    ]
    if not args.stock_voices_only
    else []
)
files.extend(
    (f"{VOICE_PREFIX}/{name}.safetensors", voice_revision, f"{name}.safetensors")
    for name in STOCK_VOICES
)
artifacts = (
    [
        row
        for row in manifest.get("pocketArtifacts", [])
        if row.get("file") in ("pocket.safetensors", "tokenizer.model")
    ]
    if args.stock_voices_only
    else []
)
for remote, version, name in files:
    # Anonymous official ungated variant; cannot accept account-specific gated terms.
    cached = hf_hub_download(
        repo_id=repo,
        filename=remote,
        revision=version,
        token=False,
        cache_dir=directory / "downloads",
    )
    with Path(cached).open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    voice = Path(name).stem
    if voice in STOCK_VOICES and digest != STOCK_VOICES[voice]:
        raise ValueError("Stock embedding checksum did not match pinned catalog")
    target = directory / name
    staged = directory / (name + ".tmp")
    shutil.copyfile(cached, staged)
    staged.replace(target)
    artifacts.append(
        {
            "file": name,
            "repo": repo,
            "revision": version,
            "path": remote,
            "sha256": digest,
        }
    )
    print(name + " ready", flush=True)
if not args.stock_voices_only:
    config = yaml.safe_load(
        (Path(pocket_tts.__file__).parent / "config/english_2026-04.yaml").read_text()
    )
    config["weights_path"] = str(directory / "pocket.safetensors")
    config["weights_path_without_voice_cloning"] = None
    config["flow_lm"]["lookup_table"]["tokenizer_path"] = str(
        directory / "tokenizer.model"
    )
    (directory / "pocket.yaml").write_text(yaml.safe_dump(config))
    manifest = {
        "moonshine": str(Path(moonshine).relative_to(directory)),
        "moonshineInfo": find_model_info("en", ModelArch.SMALL_STREAMING),
        "language": "en",
        "licenses": {
            "moonshineEnglish": "MIT",
            "pocketWeights": "CC-BY-4.0",
            "pocketCode": "MIT",
        },
    }
manifest["pocketArtifacts"] = artifacts
manifest["voices"] = list(STOCK_VOICES)
manifest.setdefault("licenses", {}).update(
    {
        "stockVoices": "Kyutai supplied stock embeddings: VCTK and Alba CC-BY-4.0; Voice-Zero and donations CC0; https://huggingface.co/kyutai/tts-voices",
        "alba": "CC-BY-4.0; Alba MacKenna / Kyutai stock voice",
    }
)
staged_manifest = directory / "models.json.tmp"
staged_manifest.write_text(json.dumps(manifest, indent=2))
staged_manifest.replace(directory / "models.json")
path = directory / "worker-token"
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(secrets.token_urlsafe(48))
print("Private local models prepared. Worker not started.")
