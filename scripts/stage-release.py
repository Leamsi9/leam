#!/usr/bin/env python3
"""Stage an exact Leam checkout + client in durable storage; never switch services."""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-dir", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument(
        "--releases", type=Path, default=Path.home() / ".local/share/leam-next/releases"
    )
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    ).strip():
        parser.error("Stage from a clean committed checkout")
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    client = args.client_dir.resolve(strict=True)
    if client.name != source or not (client / "index.html").is_file():
        parser.error(
            "Client directory must be named for the current source commit and contain index.html"
        )
    environment = args.venv.resolve(strict=True)
    python = environment / "bin/python"
    if not python.is_file():
        parser.error("Choose the prepared durable Python environment")
    args.releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = args.releases / source
    if destination.exists():
        parser.error("An immutable release already exists for this commit")
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=args.releases) as temporary:
        stage = Path(temporary) / "release"
        stage.mkdir(mode=0o700)
        archive = subprocess.check_output(
            ["git", "archive", "--format=tar", source], cwd=root
        )
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(stage, filter="data")
        shutil.copytree(client, stage / "apps/web/dist")
        installed = subprocess.check_output(
            [str(python), "-m", "pip", "freeze"], text=True
        )
        proof = subprocess.check_output(
            [
                str(python),
                "-c",
                "import leam_api.app; from leam_api.coding_policy import coding_context; coding_context(); print(leam_api.app.__file__)",
            ],
            cwd=stage,
            text=True,
        ).strip()
        if Path(proof).resolve() != (stage / "leam_api/app.py").resolve():
            raise ValueError("Application import did not resolve inside staged release")
        assets = stage / "apps/web/dist/assets"
        asset_hashes = {}
        for asset in sorted(assets.iterdir()):
            if not asset.is_file() or asset.is_symlink() or asset.parent.resolve() != assets.resolve():
                raise ValueError("Client assets must be regular files inside the staged asset directory")
            asset_hashes[asset.name] = hashlib.sha256(asset.read_bytes()).hexdigest()
        if not asset_hashes:
            raise ValueError("Client artifact has no assets")
        manifest = {
            "schemaVersion": 1,
            "assetsSha256": asset_hashes,
            "sourceCommit": source,
            "stagedAt": datetime.now(UTC).isoformat(),
            "clientIndexSha256": hashlib.sha256(
                (client / "index.html").read_bytes()
            ).hexdigest(),
            "requirementsSha256": hashlib.sha256(
                (stage / "requirements.lock").read_bytes()
            ).hexdigest(),
            "installedPackagesSha256": hashlib.sha256(installed.encode()).hexdigest(),
            "pythonEnvironment": str(environment),
        }
        (stage / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (stage / "installed-packages.txt").write_text(installed)
        stage.rename(destination)
    print(json.dumps({"release": str(destination), **manifest}))


if __name__ == "__main__":
    main()
