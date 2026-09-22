"""Pinned official English stock embeddings; no arbitrary voice inputs or paths."""

import hashlib
from pathlib import Path

VOICE_REPOSITORY = "kyutai/pocket-tts-without-voice-cloning"
VOICE_REVISION = "e81d79e8194ad4c7ce879c87a4258ef20cbf2487"
VOICE_PREFIX = "languages/english_2026-04/embeddings"
STOCK_VOICES = {
    "alba": "69c32db63ca56843d994f81f343f62e0bf2d73f7e4c9bc73e44bb1110b1d8845",
    "anna": "5ea82f78db006c9fd34e32ddd5aae82674b5b32646097977436458d00af80dfa",
    "azelma": "9f3e69f29075f991fd47774566865ef0e0e637cb5a35992c9919761b5b84b1de",
    "bill_boerst": "75610127d44e0b05b442154f80f89f993df235aecc6cad7070f11000d006c188",
    "caro_davy": "a5961b63a2e7a5cfd7edc383aa9042fb70fd14a9dee6310cdc633881a7f2449a",
    "charles": "299edc20182eeccfbf94e308626f259da4fbf339daa8d5905218f2b1774639b8",
    "eponine": "bda3b76a384ff355fe0350736387765946304ae8ca16e59f60ea3296a1c99cc6",
    "eve": "ea9c2faf862a6c9d2cb61910fdf02842ae56940382cc8c1000fdb1b43269692b",
    "fantine": "51a8a4355d7f912d4959e4b1918314fda85ad47eba0a33a1d78a4a505d3465f5",
    "george": "0c1c6c57c55a98d81254b33728150c7776f40647fe95258d1a6c1a02780b5d02",
    "jane": "37386227ca8ec5bf1b8e516c13d132ce5ff5437a304fe90129a1c62f41d9a008",
    "javert": "0ae88e03ca4e76a0e16cbf321a807428febda9d9e9bc0358c02e7f9c9e2c263b",
    "marius": "04f84efcb77a0547ba582c058db496f7ff4920891d49d37b9950d128422582a8",
    "mary": "a8f2adf260cab966fe0a113d6b549d6efdeaa79de544ae0ff34b5b6a41445a59",
    "michael": "8937f724ac4719b9aa51ea0ba1f18f9de0af7a663ad6263558266c1a53c9722d",
    "paul": "ed7a019168f94dfe77009f1b0de59387abc6fbb0db954d38ce722ecb77da61aa",
    "peter_yearsley": "dd977a6e15591e347c9a23fa7cc09e35a65b462917f5eeb162baff6dc9e3f685",
    "stuart_bell": "5a49da7ca5df05d02587ec4a0981c0d318e045f68e24423c4203ce474d9b33dc",
    "vera": "4bf50ddd957b5d218b264fdcf18efbbc7384d12da3eca98ca19b9e8dd6976acc",
}


def installed_voices(directory: Path, manifest: dict) -> tuple[str, ...]:
    """Advertise only configured stock files whose content matches the pinned catalog."""
    requested = manifest.get("voices", ["alba"])
    if not isinstance(requested, list):
        raise TypeError("Invalid installed voice manifest")
    verified = []
    for name in dict.fromkeys(v for v in requested if isinstance(v, str)):
        if name not in STOCK_VOICES:
            continue
        path = directory / f"{name}.safetensors"
        if path.is_symlink():
            continue
        try:
            with path.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
        except OSError:
            continue
        if digest == STOCK_VOICES[name]:
            verified.append(name)
    if not verified:
        raise ValueError("No verified stock voices installed")
    return tuple(verified)
