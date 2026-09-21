#!/usr/bin/env python3
"""Validate merge-to-main protocol installation and local testing policy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


MERGE_PROTOCOL = "merge-to-main-protocol.md"
TESTING_PROTOCOL = "testing-branch-promotion-protocol.md"
LEGACY_LOCAL_MAIN_PROTOCOL = "local-main-merge-protocol.md"

MERGE_REQUIRED_SNIPPETS = (
    "Agents must not push directly to `main`.",
    "GitHub pull request",
    "not an exception to the consumer repo's branch, review, CI, or merge rules",
    "Do not auto-push or auto-merge",
    "full `headRefOid`",
    "Verify the post-merge `main` workflow",
    "Emergency Exception",
)

MERGE_FORBIDDEN_SNIPPETS = (
    "Testing Branch Promotion",
)

TESTING_REQUIRED_SNIPPETS = (
    "Testing Branch Promotion",
    "After the operator approves the in-branch work for manual testing",
    "keep production and `main` unchanged",
    "record the source branch, source commit, target testing branch",
    "preserve the original implementation branch for PR review",
    "After manual testing is complete and the operator approves production",
    "The production PR should include the testing evidence",
)

AGENTS_REQUIRED_SNIPPETS = (
    "agent-protocols/merge-to-main-protocol.md",
    "agent-protocols/local/testing-branch-promotion-protocol.md",
)
LEGACY_LOCAL_DIR = "local-agent-protocols"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check merge-to-main and testing-promotion protocol wiring."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repo root to validate. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--vendor-dir",
        default="agent-protocols",
        help="Vendored package directory, or '.' when checking the package repo.",
    )
    parser.add_argument(
        "--local-dir",
        help="Repo-local protocol directory. Defaults to <vendor-dir>/local.",
    )
    parser.add_argument(
        "--require-testing-promotion",
        action="store_true",
        help="Require the repo-local testing branch promotion protocol.",
    )
    parser.add_argument(
        "--require-agents-reference",
        action="store_true",
        help="Require AGENTS.md to reference both merge and testing protocols.",
    )
    parser.add_argument(
        "--forbid-legacy-local-main",
        action="store_true",
        help="Fail if the old local-main-merge protocol file is still present.",
    )
    parser.add_argument(
        "--forbid-legacy-local-dir",
        action="store_true",
        help="Fail if the old local-agent-protocols directory is still present.",
    )
    return parser.parse_args()


def read_text(path: Path, failures: list[str]) -> str:
    if not path.exists():
        failures.append(f"missing required file: {path}")
        return ""
    if not path.is_file():
        failures.append(f"expected a file but found something else: {path}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        failures.append(f"could not read {path} as UTF-8: {exc}")
        return ""


def require_snippets(path: Path, text: str, snippets: tuple[str, ...], failures: list[str]) -> None:
    for snippet in snippets:
        if snippet not in text:
            failures.append(f"{path} is missing required text: {snippet!r}")


def forbid_snippets(path: Path, text: str, snippets: tuple[str, ...], failures: list[str]) -> None:
    for snippet in snippets:
        if snippet in text:
            failures.append(f"{path} contains forbidden text: {snippet!r}")


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    vendor_root = (repo_root / args.vendor_dir).resolve()
    local_dir = args.local_dir or str(Path(args.vendor_dir) / "local")
    local_root = (repo_root / local_dir).resolve()
    failures: list[str] = []

    merge_path = vendor_root / MERGE_PROTOCOL
    merge_text = read_text(merge_path, failures)
    require_snippets(merge_path, merge_text, MERGE_REQUIRED_SNIPPETS, failures)
    forbid_snippets(merge_path, merge_text, MERGE_FORBIDDEN_SNIPPETS, failures)

    if args.require_testing_promotion:
        testing_path = local_root / TESTING_PROTOCOL
        testing_text = read_text(testing_path, failures)
        require_snippets(testing_path, testing_text, TESTING_REQUIRED_SNIPPETS, failures)

    if args.require_agents_reference:
        agents_path = repo_root / "AGENTS.md"
        agents_text = read_text(agents_path, failures)
        require_snippets(agents_path, agents_text, AGENTS_REQUIRED_SNIPPETS, failures)

    if args.forbid_legacy_local_main:
        legacy_path = vendor_root / LEGACY_LOCAL_MAIN_PROTOCOL
        if legacy_path.exists():
            failures.append(f"legacy protocol should be removed: {legacy_path}")

    if args.forbid_legacy_local_dir:
        legacy_dir = repo_root / LEGACY_LOCAL_DIR
        if legacy_dir.exists():
            failures.append(f"legacy local protocol dir should be removed: {legacy_dir}")

    if failures:
        print("merge-to-main protocol check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("merge-to-main protocol check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
