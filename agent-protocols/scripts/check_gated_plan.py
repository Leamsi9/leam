#!/usr/bin/env python3
"""Generic gated-phase checker for substantive work manifests."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    check_id: str
    check_type: str
    passed: bool
    detail: str


@dataclass
class PhaseResult:
    phase: str
    summary: str
    dependencies: list[str]
    dependency_passed: bool
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        return self.dependency_passed and all(check.passed for check in self.checks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check a substantive-work phase manifest."
    )
    parser.add_argument("manifest", type=Path, help="Path to the .plan.toml manifest")
    parser.add_argument(
        "--phase",
        help="Check only this phase and its transitive dependencies",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if "phases" not in data or not isinstance(data["phases"], dict):
        raise ValueError("manifest must contain a [phases] table")
    return data


def resolve_root_dir(manifest_path: Path, data: dict[str, Any]) -> Path:
    root_dir = data.get("root_dir", ".")
    return (manifest_path.parent / root_dir).resolve()


def ordered_phases(data: dict[str, Any]) -> list[str]:
    """Validate the complete graph before any check can execute."""
    phases = data["phases"]
    if not phases:
        raise ValueError("manifest must contain at least one phase")
    order = data.get("phase_order", list(phases))
    if not isinstance(order, list) or any(not isinstance(name, str) for name in order):
        raise ValueError("phase_order must be a list of phase names")
    if len(order) != len(set(order)) or set(order) != set(phases):
        raise ValueError("phase_order must contain every phase exactly once")
    positions = {name: index for index, name in enumerate(order)}
    for name in order:
        phase = phases[name]
        if not isinstance(phase, dict):
            raise ValueError(f"phase '{name}' must be a table")
        checks = phase.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"phase '{name}' must contain at least one check")
        ids = set()
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not check["id"].strip():
                raise ValueError(f"phase '{name}' contains a check without an id")
            if check["id"] in ids:
                raise ValueError(f"phase '{name}' has duplicate check ids")
            ids.add(check["id"])
        dependencies = phase.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            raise ValueError(f"phase '{name}' depends_on must be a list of names")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"phase '{name}' has duplicate dependencies")
        for dependency in dependencies:
            if dependency not in phases:
                raise ValueError(f"phase '{name}' depends on unknown phase '{dependency}'")
            if positions[dependency] >= positions[name]:
                raise ValueError(f"dependency '{dependency}' must precede '{name}'; cycles are invalid")
    return order


def dependency_closure(phases: dict[str, Any], order: list[str], target: str | None) -> list[str]:
    if target is None:
        return order
    if target not in phases:
        raise ValueError(f"unknown phase: {target}")

    visited: set[str] = set()

    def visit(phase_name: str) -> None:
        if phase_name in visited:
            return
        visited.add(phase_name)
        depends_on = phases[phase_name].get("depends_on", [])
        for dependency in depends_on:
            if dependency not in phases:
                raise ValueError(
                    f"phase '{phase_name}' depends on unknown phase '{dependency}'"
                )
            visit(dependency)

    visit(target)
    return [phase for phase in order if phase in visited]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def resolve_check_path(root_dir: Path, raw_value: str) -> Path:
    target = Path(raw_value)
    if target.is_absolute():
        return target
    return (root_dir / target).resolve()


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def parse_git_worktree_porcelain(output: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue

        key, _, value = line.partition(" ")
        current[key] = value

    if current:
        entries.append(current)

    return entries


def resolve_repo_dir(root_dir: Path, raw_check: dict[str, Any]) -> Path:
    repo_value = str(raw_check.get("repo", "."))
    return resolve_check_path(root_dir, repo_value)


def _command_test_output_contract(
    raw_check: dict[str, Any], output: str
) -> tuple[bool, str] | None:
    """Enforce opt-in unittest counts so exit zero cannot hide no evidence."""

    contract_fields = {"min_tests", "max_skipped", "max_expected_failures"}
    if not contract_fields.intersection(raw_check):
        return None

    configured: dict[str, int] = {}
    for field in contract_fields:
        value = raw_check.get(field, 0 if field.startswith("max_") else None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False, f"{field} must be a non-negative integer"
        configured[field] = value
    if "min_tests" not in raw_check or configured["min_tests"] < 1:
        return False, "min_tests must be a positive integer"

    summaries = re.findall(r"\bRan\s+(\d+)\s+tests?\b", output)
    if len(summaries) != 1:
        return False, "command output must contain exactly one unittest summary"
    terminal_results = re.findall(r"^(OK(?: \([^\n]*\))?|FAILED(?: \([^\n]*\))?)\s*$", output, re.MULTILINE)
    if len(terminal_results) != 1 or not terminal_results[0].startswith("OK"):
        return False, "command output must contain exactly one successful unittest result"
    if re.search(r"(?<!expected )\b(?:failures|errors|unexpected successes)=([1-9]\d*)\b", output):
        return False, "command output reports failed unittest results"
    test_count = int(summaries[0])
    skipped_matches = re.findall(r"\bskipped=(\d+)\b", output)
    expected_failure_matches = re.findall(r"\bexpected failures=(\d+)\b", output)
    skipped = sum(int(value) for value in skipped_matches)
    expected_failures = sum(int(value) for value in expected_failure_matches)
    counts = (
        f"tests={test_count}, skipped={skipped}, "
        f"expected_failures={expected_failures}"
    )
    if test_count < configured["min_tests"]:
        return False, f"{counts}; requires at least {configured['min_tests']} tests"
    if skipped > configured["max_skipped"]:
        return False, f"{counts}; allows at most {configured['max_skipped']} skipped"
    if expected_failures > configured["max_expected_failures"]:
        return (
            False,
            f"{counts}; allows at most "
            f"{configured['max_expected_failures']} expected failures",
        )
    return True, counts


def run_check(
    root_dir: Path,
    raw_check: dict[str, Any],
    manifest_data: dict[str, Any],
) -> CheckResult:
    check_id = str(raw_check.get("id", "<unnamed>"))
    check_type = str(raw_check.get("type", "")).strip()
    if not check_type:
        return CheckResult(check_id, check_type, False, "missing check type")

    if check_type in {
        "path_exists",
        "path_absent",
        "text_present",
        "text_absent",
        "regex_present",
        "regex_absent",
        "worktree_absent",
    }:
        path_value = raw_check.get("path")
        if not path_value:
            return CheckResult(check_id, check_type, False, "missing path")
        target = resolve_check_path(root_dir, str(path_value))

    if check_type == "path_exists":
        passed = target.exists()
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "path_absent":
        passed = not target.exists()
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "text_present":
        expected = str(raw_check.get("text", ""))
        content = read_text(target)
        passed = expected in content
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "text_absent":
        expected = str(raw_check.get("text", ""))
        content = read_text(target)
        passed = expected not in content
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "regex_present":
        pattern = str(raw_check.get("pattern", ""))
        content = read_text(target)
        passed = re.search(pattern, content, re.MULTILINE) is not None
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "regex_absent":
        pattern = str(raw_check.get("pattern", ""))
        content = read_text(target)
        passed = re.search(pattern, content, re.MULTILINE) is None
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "worktree_absent":
        passed = not target.exists()
        return CheckResult(check_id, check_type, passed, str(target))

    if check_type == "git_clean":
        repo_dir = resolve_repo_dir(root_dir, raw_check)
        completed = run_git(["status", "--porcelain", "--untracked-files=all"], repo_dir)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"git exited {completed.returncode}"
            return CheckResult(check_id, check_type, False, detail)
        detail = completed.stdout.strip()
        passed = detail == ""
        return CheckResult(check_id, check_type, passed, detail or "clean")

    if check_type == "git_head_ahead":
        repo_dir = resolve_repo_dir(root_dir, raw_check)
        baseline = str(raw_check.get("baseline") or manifest_data.get("baseline") or "").strip()
        ref = str(raw_check.get("ref", "HEAD")).strip()
        if not baseline:
            return CheckResult(check_id, check_type, False, "missing baseline")
        if not ref:
            return CheckResult(check_id, check_type, False, "missing ref")

        baseline_result = run_git(["rev-parse", baseline], repo_dir)
        ref_result = run_git(["rev-parse", ref], repo_dir)
        if baseline_result.returncode != 0 or ref_result.returncode != 0:
            detail = (
                baseline_result.stderr.strip()
                or ref_result.stderr.strip()
                or "failed to resolve baseline or ref"
            )
            return CheckResult(check_id, check_type, False, detail)

        baseline_sha = baseline_result.stdout.strip()
        ref_sha = ref_result.stdout.strip()
        ancestor_result = run_git(
            ["merge-base", "--is-ancestor", baseline_sha, ref_sha],
            repo_dir,
        )
        if ancestor_result.returncode != 0:
            return CheckResult(
                check_id,
                check_type,
                False,
                f"{ref} is not descended from baseline {baseline} ({baseline_sha})",
            )

        count_result = run_git(["rev-list", "--count", f"{baseline_sha}..{ref_sha}"], repo_dir)
        if count_result.returncode != 0:
            detail = count_result.stderr.strip() or f"git exited {count_result.returncode}"
            return CheckResult(check_id, check_type, False, detail)
        ahead_count = int(count_result.stdout.strip() or "0")
        passed = ahead_count > 0
        detail = (
            f"{ref} is {ahead_count} commit(s) ahead of {baseline}"
            if passed
            else f"{ref} is not ahead of {baseline}"
        )
        return CheckResult(check_id, check_type, passed, detail)

    if check_type == "git_merged_into":
        repo_dir = resolve_repo_dir(root_dir, raw_check)
        branch = str(raw_check.get("branch", "")).strip()
        target_branch = str(raw_check.get("into", "")).strip()
        if not branch or not target_branch:
            return CheckResult(
                check_id,
                check_type,
                False,
                "missing branch or into",
            )
        completed = run_git(
            ["merge-base", "--is-ancestor", branch, target_branch],
            repo_dir,
        )
        detail = (
            f"{branch} is merged into {target_branch}"
            if completed.returncode == 0
            else completed.stderr.strip() or f"{branch} is not merged into {target_branch}"
        )
        return CheckResult(check_id, check_type, completed.returncode == 0, detail)

    if check_type == "git_ref_equals":
        repo_dir = resolve_repo_dir(root_dir, raw_check)
        left = str(raw_check.get("left", "")).strip()
        right = str(raw_check.get("right", "")).strip()
        if not left or not right:
            return CheckResult(check_id, check_type, False, "missing left or right")
        left_result = run_git(["rev-parse", left], repo_dir)
        right_result = run_git(["rev-parse", right], repo_dir)
        if left_result.returncode != 0 or right_result.returncode != 0:
            detail = (
                left_result.stderr.strip()
                or right_result.stderr.strip()
                or "failed to resolve refs"
            )
            return CheckResult(check_id, check_type, False, detail)
        left_sha = left_result.stdout.strip()
        right_sha = right_result.stdout.strip()
        passed = left_sha == right_sha
        detail = f"{left_sha} == {right_sha}" if passed else f"{left_sha} != {right_sha}"
        return CheckResult(check_id, check_type, passed, detail)

    if check_type == "git_branch_absent":
        repo_dir = resolve_repo_dir(root_dir, raw_check)
        branch = str(raw_check.get("branch", "")).strip()
        if not branch:
            return CheckResult(check_id, check_type, False, "missing branch")
        completed = run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo_dir)
        passed = completed.returncode != 0
        detail = (
            f"refs/heads/{branch} absent"
            if passed
            else f"refs/heads/{branch} present"
        )
        return CheckResult(check_id, check_type, passed, detail)

    if check_type == "git_branch_worktree":
        repo_dir = resolve_repo_dir(root_dir, raw_check)
        branch = str(raw_check.get("branch") or manifest_data.get("branch") or "").strip()
        if not branch:
            return CheckResult(check_id, check_type, False, "missing branch")
        branch_ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"

        completed = run_git(["worktree", "list", "--porcelain"], repo_dir)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"git exited {completed.returncode}"
            return CheckResult(check_id, check_type, False, detail)

        match = next(
            (
                entry
                for entry in parse_git_worktree_porcelain(completed.stdout)
                if entry.get("branch") == branch_ref and entry.get("worktree")
            ),
            None,
        )
        if match is None:
            return CheckResult(
                check_id,
                check_type,
                False,
                f"{branch_ref} has no registered worktree",
            )

        worktree = Path(match["worktree"]).resolve()
        path_value = raw_check.get("path")
        if path_value:
            expected = resolve_check_path(root_dir, str(path_value)).resolve()
            if worktree != expected:
                return CheckResult(
                    check_id,
                    check_type,
                    False,
                    f"{branch_ref} registered at {worktree}, expected {expected}",
                )

        current_required = raw_check.get("current", False)
        if not isinstance(current_required, bool):
            return CheckResult(
                check_id,
                check_type,
                False,
                "current must be a boolean",
            )
        if current_required:
            current_result = run_git(["rev-parse", "--show-toplevel"], repo_dir)
            if current_result.returncode != 0:
                detail = current_result.stderr.strip() or "failed to resolve current checkout"
                return CheckResult(check_id, check_type, False, detail)
            current_worktree = Path(current_result.stdout.strip()).resolve()
            if current_worktree != worktree:
                return CheckResult(
                    check_id,
                    check_type,
                    False,
                    f"current checkout is {current_worktree}, expected {worktree}",
                )

        contains = str(raw_check.get("contains", "")).strip()
        if contains:
            contained_path = (worktree / contains).resolve()
            if not contained_path.exists():
                return CheckResult(
                    check_id,
                    check_type,
                    False,
                    f"{branch_ref} worktree lacks {contains}",
                )

        return CheckResult(check_id, check_type, True, f"{branch_ref} -> {worktree}")

    if check_type == "command":
        command = str(raw_check.get("command", "")).strip()
        if not command:
            return CheckResult(check_id, check_type, False, "missing command")
        cwd = root_dir
        if raw_check.get("cwd"):
            cwd = (root_dir / str(raw_check["cwd"])).resolve()
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        detail = completed.stdout.strip() or completed.stderr.strip() or f"exit {completed.returncode}"
        if completed.returncode != 0:
            return CheckResult(check_id, check_type, False, detail)
        contract = _command_test_output_contract(raw_check, f"{completed.stdout}\n{completed.stderr}")
        if contract is not None:
            passed, detail = contract
            return CheckResult(check_id, check_type, passed, detail)
        return CheckResult(check_id, check_type, True, detail)

    return CheckResult(
        check_id,
        check_type,
        False,
        f"unsupported check type: {check_type}",
    )


def evaluate_manifest(
    data: dict[str, Any], root_dir: Path, target_phase: str | None
) -> list[PhaseResult]:
    phases = data["phases"]
    order = ordered_phases(data)
    selected = dependency_closure(phases, order, target_phase)

    results_by_phase: dict[str, PhaseResult] = {}
    ordered_results: list[PhaseResult] = []

    for phase_name in selected:
        phase_data = phases[phase_name]
        dependencies = list(phase_data.get("depends_on", []))
        dependency_passed = all(
            results_by_phase[dependency].passed
            for dependency in dependencies
        )
        checks = [
            run_check(root_dir, raw_check, data)
            for raw_check in phase_data.get("checks", [])
        ]
        result = PhaseResult(
            phase=phase_name,
            summary=str(phase_data.get("summary", "")).strip(),
            dependencies=dependencies,
            dependency_passed=dependency_passed,
            checks=checks,
        )
        results_by_phase[phase_name] = result
        ordered_results.append(result)

    return ordered_results


def emit_text(results: list[PhaseResult]) -> int:
    all_passed = True
    for phase in results:
        phase_prefix = "PASS" if phase.passed else "FAIL"
        print(f"{phase_prefix} {phase.phase}: {phase.summary}")
        if phase.dependencies:
            dep_prefix = "deps ok" if phase.dependency_passed else "deps failed"
            print(f"  {dep_prefix}: {', '.join(phase.dependencies)}")
        for check in phase.checks:
            check_prefix = "ok" if check.passed else "fail"
            print(
                f"  [{check_prefix}] {check.check_id} ({check.check_type}) - {check.detail}"
            )
        all_passed = all_passed and phase.passed
    return 0 if all_passed else 1


def emit_json(results: list[PhaseResult]) -> int:
    payload = {
        "passed": all(phase.passed for phase in results),
        "phases": [
            {
                "phase": phase.phase,
                "summary": phase.summary,
                "dependencies": phase.dependencies,
                "dependency_passed": phase.dependency_passed,
                "passed": phase.passed,
                "checks": [
                    {
                        "id": check.check_id,
                        "type": check.check_type,
                        "passed": check.passed,
                        "detail": check.detail,
                    }
                    for check in phase.checks
                ],
            }
            for phase in results
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    try:
        data = load_manifest(manifest_path)
        root_dir = resolve_root_dir(manifest_path, data)
        results = evaluate_manifest(data, root_dir, args.phase)
    except Exception as exc:  # pragma: no cover - CLI guard
        if args.json:
            print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        return emit_json(results)
    return emit_text(results)


if __name__ == "__main__":
    raise SystemExit(main())
