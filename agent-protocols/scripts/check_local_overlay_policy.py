#!/usr/bin/env python3
"""Validate that repo-local overlay files are not package-owned."""

from __future__ import annotations

import sys
from pathlib import Path

from install import OPTIONAL_VENDORED_FILES, VENDORED_DIRS, VENDORED_FILES


LOCAL_DIR = Path("local")
ALLOWED_PACKAGE_LOCAL_FILES = {
    LOCAL_DIR / "README.md",
}


def main() -> int:
    package_root = Path(__file__).resolve().parent.parent
    failures: list[str] = []

    for relative in [*VENDORED_FILES, *OPTIONAL_VENDORED_FILES, *VENDORED_DIRS]:
        path = Path(relative)
        if path == LOCAL_DIR or LOCAL_DIR in path.parents:
            failures.append(f"{relative!r} must not be package-owned")

    local_root = package_root / LOCAL_DIR
    if local_root.exists():
        for path in sorted(local_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(package_root)
            if relative not in ALLOWED_PACKAGE_LOCAL_FILES:
                failures.append(
                    f"{relative.as_posix()} belongs in a consumer repo, not the package"
                )

    if failures:
        print("local overlay policy check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("local overlay policy check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
