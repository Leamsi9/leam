#!/usr/bin/env python3
"""Live workstream audit and status-ledger sync for substantive work."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GENERATED_START = "<!-- BEGIN GENERATED WORKSTREAM STATE -->"
GENERATED_END = "<!-- END GENERATED WORKSTREAM STATE -->"
DEFAULT_STATUS_LEDGER_PATH = Path("docs/live-workstream-status.md")
DEFAULT_CONFIG_PATH = Path("agent-protocols.toml")
HISTORICAL_PREFIXES = ("backup/", "recovery/", "audit/", "hotfix/", "safety/")
HISTORICAL_NAMES = {"staging"}
STATUS_PATTERN = re.compile(r"^- Status:\s*(.+?)\s*$", re.MULTILINE)
PROPOSAL_STATE_PATTERN = re.compile(r"^- Proposal state:\s*(.+?)\s*$", re.MULTILINE)
GENERATED_TIMESTAMP_PATTERN = re.compile(
    r"_Last generated: .*?_\n\n",
    re.MULTILINE,
)


@dataclass(frozen=True)
class RepoConfig:
    repo_id: str
    root: Path
    main_branch: str = "main"
    ignored_dirty_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkstreamSettings:
    root: Path
    ledger_path: Path
    repos: tuple[RepoConfig, ...]
    config_path: Path | None = None


@dataclass(frozen=True)
class ManifestRecord:
    branch: str | None
    manifest_path: Path
    plan_path: Path | None
    completion_ledger: str | None
    status: str | None
    proposal_state: str | None


def default_root() -> Path:
    package_root = Path(__file__).resolve().parent.parent
    if (package_root / DEFAULT_STATUS_LEDGER_PATH).exists():
        return package_root
    return package_root.parent


def normalize_dirty_path(raw_path: str) -> str:
    normalized = Path(raw_path).as_posix()
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def guess_repo_id(path: Path) -> str:
    common_root = resolve_git_common_root(path)
    if common_root is not None:
        return common_root.name
    return path.name


def resolve_git_common_root(path: Path) -> Path | None:
    result = run_command(["git", "rev-parse", "--git-common-dir"], path)
    if result.returncode == 0:
        common_dir = result.stdout.strip()
        if common_dir:
            common_path = Path(common_dir)
            if not common_path.is_absolute():
                common_path = (path / common_path).resolve()
            return common_path.parent
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit live workstream state.")
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root(),
        help="Repo root that owns plan manifests and the configured live status ledger.",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="Additional repo roots to audit. Overrides repo topology from agent-protocols.toml when provided.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="Audit live branch/worktree state.")
    audit_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Emit a clean-git reconciliation inventory with recommended actions.",
    )
    reconcile_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    sync_parser = subparsers.add_parser(
        "sync-index",
        help="Refresh the generated current-state section in the configured live workstream ledger.",
    )
    sync_mode = sync_parser.add_mutually_exclusive_group()
    sync_mode.add_argument(
        "--check",
        action="store_true",
        help="Fail if the generated section is out of sync.",
    )
    sync_mode.add_argument(
        "--confirm",
        action="store_true",
        help="Write the generated section back to the configured live workstream ledger.",
    )

    return parser.parse_args()


def run_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def require_ok(result: subprocess.CompletedProcess[str], description: str) -> str:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{description}: {detail}")
    return result.stdout


def resolve_repo_config_path(root: Path) -> Path | None:
    config_path = root / DEFAULT_CONFIG_PATH
    if config_path.exists():
        return config_path
    return None


def load_config_data(root: Path) -> tuple[Path | None, dict[str, Any]]:
    config_path = resolve_repo_config_path(root)
    if config_path is None:
        return None, {}
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    version = data.get("version", 1)
    if version != 1:
        raise ValueError(f"unsupported agent-protocols config version: {version}")
    return config_path, data


def parse_status_ledger_path(root: Path, data: dict[str, Any]) -> Path:
    raw_path = str(data.get("status_ledger_path", DEFAULT_STATUS_LEDGER_PATH))
    target = Path(raw_path)
    if target.is_absolute():
        return target.resolve()
    return (root / target).resolve()


def repo_ignored_dirty_paths(
    root: Path,
    repo_root: Path,
    ledger_path: Path,
    raw_paths: list[Any] | tuple[Any, ...] | None,
) -> tuple[str, ...]:
    ignored: list[str] = []
    if repo_root == root:
        ignored.append(normalize_dirty_path(ledger_path.relative_to(root).as_posix()))
    for raw_path in raw_paths or []:
        ignored.append(normalize_dirty_path(str(raw_path)))
    deduped: list[str] = []
    for candidate in ignored:
        if candidate not in deduped:
            deduped.append(candidate)
    return tuple(deduped)


def build_repo_config(
    root: Path,
    repo_id: str,
    repo_path: str,
    main_branch: str,
    ledger_path: Path,
    path_base: str = "config_root",
    ignored_dirty_paths: list[Any] | tuple[Any, ...] | None = None,
) -> RepoConfig:
    if path_base == "config_root":
        base_root = root
    elif path_base == "git_common_root":
        common_root = resolve_git_common_root(root)
        if common_root is None:
            raise ValueError("git_common_root path_base requires a git repo root")
        base_root = common_root
    else:
        raise ValueError(f"unsupported path_base '{path_base}'")
    resolved_root = (base_root / Path(repo_path)).resolve()
    return RepoConfig(
        repo_id=repo_id,
        root=resolved_root,
        main_branch=main_branch,
        ignored_dirty_paths=repo_ignored_dirty_paths(
            root=root,
            repo_root=resolved_root,
            ledger_path=ledger_path,
            raw_paths=ignored_dirty_paths,
        ),
    )


def parse_repo_specs(root: Path, raw_specs: list[str], ledger_path: Path) -> list[RepoConfig]:
    if raw_specs:
        repos = []
        for spec in raw_specs:
            if "=" not in spec:
                raise ValueError(f"invalid repo spec '{spec}', expected ID=PATH")
            repo_id, path = spec.split("=", 1)
            repos.append(
                build_repo_config(
                    root=root,
                    repo_id=repo_id.strip(),
                    repo_path=path.strip(),
                    main_branch="main",
                    ledger_path=ledger_path,
                    path_base="config_root",
                )
            )
        return repos

    root = root.resolve()
    return [
        build_repo_config(
            root=root,
            repo_id=guess_repo_id(root),
            repo_path=".",
            main_branch="main",
            ledger_path=ledger_path,
        )
    ]


def parse_repos_from_config(root: Path, data: dict[str, Any], ledger_path: Path) -> list[RepoConfig]:
    raw_repos = data.get("repos")
    if not raw_repos:
        return parse_repo_specs(root, [], ledger_path)
    if not isinstance(raw_repos, list):
        raise ValueError("agent-protocols.toml [[repos]] must be an array of tables")

    repos: list[RepoConfig] = []
    for index, raw_repo in enumerate(raw_repos, start=1):
        if not isinstance(raw_repo, dict):
            raise ValueError(f"repo entry #{index} must be a table")
        repo_path = str(raw_repo.get("path", ".")).strip() or "."
        repo_id = str(raw_repo.get("id", "")).strip() or guess_repo_id((root / repo_path).resolve())
        main_branch = str(raw_repo.get("main_branch", "main")).strip() or "main"
        path_base = str(raw_repo.get("path_base", "config_root")).strip() or "config_root"
        ignored = raw_repo.get("ignored_dirty_paths")
        if ignored is not None and not isinstance(ignored, list):
            raise ValueError(f"repo entry #{index} ignored_dirty_paths must be a list")
        repos.append(
            build_repo_config(
                root=root,
                repo_id=repo_id,
                repo_path=repo_path,
                main_branch=main_branch,
                ledger_path=ledger_path,
                path_base=path_base,
                ignored_dirty_paths=ignored,
            )
        )
    return repos


def load_settings(root: Path, raw_specs: list[str]) -> WorkstreamSettings:
    root = root.resolve()
    config_path, config_data = load_config_data(root)
    ledger_path = parse_status_ledger_path(root, config_data)
    if raw_specs:
        repos = tuple(parse_repo_specs(root, raw_specs, ledger_path))
    else:
        repos = tuple(parse_repos_from_config(root, config_data, ledger_path))
    return WorkstreamSettings(
        root=root,
        ledger_path=ledger_path,
        repos=repos,
        config_path=config_path,
    )


def manifest_plan_path(manifest_path: Path) -> Path | None:
    if manifest_path.name.endswith(".plan.toml"):
        candidate = manifest_path.with_name(manifest_path.name.replace(".plan.toml", ".md"))
        if candidate.exists():
            return candidate
    return None


def parse_plan_value(path: Path | None, pattern: re.Pattern[str]) -> str | None:
    if path is None or not path.exists():
        return None
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        return None
    return match.group(1).strip().lower()


def parse_plan_status(path: Path | None) -> str | None:
    return parse_plan_value(path, STATUS_PATTERN)


def parse_plan_proposal_state(path: Path | None) -> str | None:
    return parse_plan_value(path, PROPOSAL_STATE_PATTERN)


def discover_manifests(root: Path) -> list[ManifestRecord]:
    manifests: list[ManifestRecord] = []
    plans_root = root / "docs" / "plans"
    if not plans_root.exists():
        return manifests
    for manifest_path in sorted(plans_root.rglob("*.plan.toml")):
        with manifest_path.open("rb") as handle:
            data = tomllib.load(handle)
        branch = data.get("branch")
        plan_path = manifest_plan_path(manifest_path)
        manifests.append(
            ManifestRecord(
                branch=str(branch) if branch else None,
                manifest_path=manifest_path.resolve(),
                plan_path=plan_path.resolve() if plan_path else None,
                completion_ledger=str(data.get("completion_ledger")) if data.get("completion_ledger") else None,
                status=parse_plan_status(plan_path),
                proposal_state=parse_plan_proposal_state(plan_path),
            )
        )
    return manifests


def git_worktrees(repo: RepoConfig) -> dict[str, dict[str, Any]]:
    output = require_ok(
        run_command(["git", "worktree", "list", "--porcelain"], repo.root),
        f"git worktree list failed for {repo.repo_id}",
    )
    entries: dict[str, dict[str, Any]] = {}
    for block in output.strip().split("\n\n"):
        if not block.strip():
            continue
        data: dict[str, Any] = {}
        for line in block.splitlines():
            key, value = line.split(" ", 1)
            data[key] = value
        branch_ref = data.get("branch")
        if not branch_ref:
            continue
        branch = str(branch_ref).removeprefix("refs/heads/")
        path = Path(str(data["worktree"])).resolve()
        status_output = require_ok(
            run_command(
                ["git", "-C", str(path), "status", "--short", "--branch", "--untracked-files=all"],
                repo.root,
            ),
            f"git status failed for worktree {path}",
        )
        status_lines = status_output.splitlines()
        dirty_entries = []
        dirty_paths = []
        for line in status_lines[1:]:
            candidate = line[3:] if len(line) > 3 else line
            normalized_candidate = normalize_dirty_path(candidate)
            if normalized_candidate in repo.ignored_dirty_paths:
                continue
            dirty_entries.append(line)
            if " -> " in normalized_candidate:
                normalized_candidate = normalized_candidate.split(" -> ", 1)[1]
            dirty_paths.append(normalized_candidate)
        entries[branch] = {
            "path": str(path),
            "dirty": bool(dirty_entries),
            "dirty_entries": dirty_entries,
            "dirty_paths": sorted(dict.fromkeys(dirty_paths)),
        }
    return entries


def git_branches(repo: RepoConfig) -> list[dict[str, Any]]:
    worktrees = git_worktrees(repo)
    output = require_ok(
        run_command(
            [
                "git",
                "for-each-ref",
                "refs/heads",
                "--format=%(refname:short)\t%(objectname)\t%(upstream:short)",
            ],
            repo.root,
        ),
        f"git for-each-ref failed for {repo.repo_id}",
    )

    branches: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        branch, head, upstream = line.split("\t")
        merged = True
        if branch != repo.main_branch:
            merged_result = run_command(
                ["git", "merge-base", "--is-ancestor", branch, repo.main_branch],
                repo.root,
            )
            merged = merged_result.returncode == 0
        counts = require_ok(
            run_command(
                ["git", "rev-list", "--left-right", "--count", f"{repo.main_branch}...{branch}"],
                repo.root,
            ),
            f"git rev-list failed for {repo.repo_id}:{branch}",
        ).strip()
        behind, ahead = (int(part) for part in counts.split())
        worktree = worktrees.get(branch)
        branches.append(
            {
                "branch": branch,
                "head": head,
                "upstream": upstream or None,
                "merged_into_main": merged,
                "ahead_of_main": ahead,
                "behind_main": behind,
                "worktree": worktree,
            }
        )
    return branches


def git_diff_name_only(repo: RepoConfig, left: str, right: str) -> list[str]:
    output = require_ok(
        run_command(["git", "diff", "--name-only", f"{left}...{right}", "--"], repo.root),
        f"git diff --name-only failed for {repo.repo_id}:{left}...{right}",
    )
    paths = [normalize_dirty_path(line.strip()) for line in output.splitlines() if line.strip()]
    return sorted(dict.fromkeys(paths))


def summarize_paths(paths: list[str]) -> dict[str, bool]:
    if not paths:
        return {
            "doc_only": False,
            "pending_surface_only": False,
        }

    def is_doc_path(path: str) -> bool:
        return path.startswith("docs/") or path.endswith(".md") or path.endswith(".plan.toml")

    pending_prefixes = (
        "docs/adr/pending/",
        "docs/plans/proposals/",
        "docs/proposals/",
    )
    allowed_pending_companions = {
        "docs/plans/plans-index.md",
        "docs/live-workstream-status.md",
        "docs/README.md",
        "docs/adr/README.md",
        "docs/proposals/README.md",
    }
    return {
        "doc_only": all(is_doc_path(path) for path in paths),
        "pending_surface_only": all(
            path.startswith(pending_prefixes) or path in allowed_pending_companions
            for path in paths
        ),
    }


def infer_implementation_state(
    classification: str,
    plan_status: str | None,
    proposal_state: str | None,
    merged_into_main: bool,
) -> str:
    if proposal_state == "pending":
        return "pending"
    if merged_into_main and classification in {"promotable", "merged_stale"}:
        return "completed"
    if classification == "promotable" or plan_status == "promotion_ready":
        return "ready_to_merge"
    if classification == "active":
        return "active"
    if classification == "merged_stale":
        return "completed"
    if classification == "historical":
        return "historical"
    return "needs_reconciliation"


def recommend_reconciliation_action(
    classification: str,
    proposal_state: str | None,
    plan_status: str | None,
    merged_into_main: bool,
    dirty: bool,
    doc_only: bool,
    pending_surface_only: bool,
) -> tuple[str, str]:
    if proposal_state == "pending" or pending_surface_only:
        return (
            "promote_pending_to_main",
            "Pending proposal artifacts belong on main without a live implementation branch.",
        )
    if merged_into_main or classification == "merged_stale":
        if dirty:
            if doc_only:
                return (
                    "promote_docs_to_main_then_delete",
                    "The branch is already merged, and only uncommitted docs remain to port before cleanup.",
                )
            return (
                "review_port_or_discard_then_delete",
                "The branch is already merged, but non-doc local changes still need an explicit port-versus-discard decision.",
            )
        return (
            "delete_merged_stale",
            "The branch tip is already merged into main and no unique dirty state remains.",
        )
    if classification == "promotable" or plan_status == "promotion_ready":
        return (
            "merge_to_main",
            "The workstream is marked promotion_ready and should be merged back into main.",
        )
    if classification == "active":
        return (
            "keep_active",
            "Substantive implementation is underway and should stay on its dedicated branch.",
        )
    if classification == "diverged":
        if doc_only:
            return (
                "review_docs_for_promotion",
                "Docs-only work diverges from main and likely belongs on main as pending or historical artifacts.",
            )
        return (
            "review_port_or_discard",
            "The branch diverges from main and needs an explicit port-versus-discard decision.",
        )
    return (
        "retain_historical",
        "Historical or protected refs should remain preserved unless a dedicated cleanup stream supersedes them.",
    )


def summarize_action_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        action = str(decision["recommended_action"])
        counts[action] = counts.get(action, 0) + 1
    return dict(sorted(counts.items()))


def build_reconciliation(payload: dict[str, Any], repos: list[RepoConfig]) -> dict[str, Any]:
    repo_lookup = {repo.repo_id: repo for repo in repos}
    branch_decisions: list[dict[str, Any]] = []
    branchless_decisions: list[dict[str, Any]] = []

    for repo_payload in payload["repos"]:
        repo = repo_lookup[str(repo_payload["id"])]
        for branch in repo_payload["branches"]:
            if branch["branch"] == repo.main_branch:
                continue
            branch_diff_paths = (
                git_diff_name_only(repo, repo.main_branch, str(branch["branch"]))
                if branch["ahead_of_main"] or branch["behind_main"]
                else []
            )
            worktree_dirty_paths = list(branch.get("dirty_paths", []))
            combined_paths = sorted(dict.fromkeys(branch_diff_paths + worktree_dirty_paths))
            path_summary = summarize_paths(combined_paths)
            recommended_action, rationale = recommend_reconciliation_action(
                classification=str(branch["classification"]),
                proposal_state=branch.get("proposal_state"),
                plan_status=branch.get("plan_status"),
                merged_into_main=bool(branch["merged_into_main"]),
                dirty=bool(branch["dirty"]),
                doc_only=path_summary["doc_only"],
                pending_surface_only=path_summary["pending_surface_only"],
            )
            branch_decisions.append(
                {
                    "repo": repo.repo_id,
                    "branch": branch["branch"],
                    "classification": branch["classification"],
                    "implementation_state": infer_implementation_state(
                        classification=str(branch["classification"]),
                        plan_status=branch.get("plan_status"),
                        proposal_state=branch.get("proposal_state"),
                        merged_into_main=bool(branch["merged_into_main"]),
                    ),
                    "plan_status": branch.get("plan_status"),
                    "proposal_state": branch.get("proposal_state"),
                    "recommended_action": recommended_action,
                    "rationale": rationale,
                    "worktree_path": branch.get("worktree_path"),
                    "dirty": branch["dirty"],
                    "dirty_entries": branch.get("dirty_entries", []),
                    "branch_diff_paths": branch_diff_paths,
                    "worktree_dirty_paths": worktree_dirty_paths,
                    "combined_paths": combined_paths,
                    "doc_only": path_summary["doc_only"],
                    "pending_surface_only": path_summary["pending_surface_only"],
                }
            )

    for item in payload["pending_proposals"]:
        branchless_decisions.append(
            {
                "branch": item["branch"],
                "classification": item["classification"],
                "status": item["status"],
                "proposal_state": item["proposal_state"],
                "recommended_action": "preserve_pending_on_main",
                "rationale": "Pending proposal artifacts already live on main and should remain branchless until implementation begins.",
                "plan_path": item["plan_path"],
            }
        )

    for item in payload["branchless_plan_manifests"]:
        if item["classification"] == "historical":
            action = "keep_historical_artifact"
            rationale = "Historical plan families are already recorded on main and do not need a live branch."
        else:
            action = "review_branchless_manifest"
            rationale = "Branchless non-pending manifests need an explicit check that their mainline state and archive posture are correct."
        branchless_decisions.append(
            {
                "branch": item["branch"],
                "classification": item["classification"],
                "status": item["status"],
                "proposal_state": item["proposal_state"],
                "recommended_action": action,
                "rationale": rationale,
                "plan_path": item["plan_path"],
            }
        )

    return {
        "generated_at": payload["generated_at"],
        "root": payload["root"],
        "summary": {
            "branch_actions": summarize_action_counts(branch_decisions),
            "branchless_actions": summarize_action_counts(branchless_decisions),
        },
        "branch_decisions": sorted(
            branch_decisions,
            key=lambda item: (str(item["repo"]), str(item["branch"])),
        ),
        "branchless_decisions": sorted(
            branchless_decisions,
            key=lambda item: str(item["branch"]),
        ),
    }


def classify_branch(
    branch: str,
    repo: RepoConfig,
    merged_into_main: bool,
    manifest_status: str | None,
    manifest_paths: list[str],
) -> str:
    if branch == repo.main_branch:
        return "main"
    if manifest_status == "promotion_ready":
        return "promotable"
    if manifest_status in {"active", "blocked"}:
        return "active"
    if manifest_status in {"promoted", "archived"}:
        return "historical"
    if branch.startswith(HISTORICAL_PREFIXES) or branch in HISTORICAL_NAMES:
        return "historical"
    if merged_into_main:
        return "merged_stale"
    if manifest_paths:
        return "active"
    return "diverged"


def build_audit(root: Path, repos: list[RepoConfig]) -> dict[str, Any]:
    manifests = discover_manifests(root)
    manifests_by_branch: dict[str, list[ManifestRecord]] = {}
    for manifest in manifests:
        if manifest.branch:
            manifests_by_branch.setdefault(manifest.branch, []).append(manifest)

    repo_payloads: list[dict[str, Any]] = []
    all_branch_names: set[str] = set()

    for repo in repos:
        branch_records = []
        dirty_worktrees = 0
        for branch_info in git_branches(repo):
            branch = str(branch_info["branch"])
            all_branch_names.add(branch)
            manifest_records = manifests_by_branch.get(branch, [])
            manifest_status = next((record.status for record in manifest_records if record.status), None)
            classification = classify_branch(
                branch=branch,
                repo=repo,
                merged_into_main=bool(branch_info["merged_into_main"]),
                manifest_status=manifest_status,
                manifest_paths=[str(record.manifest_path) for record in manifest_records],
            )
            worktree = branch_info["worktree"]
            if worktree and worktree["dirty"]:
                dirty_worktrees += 1
            branch_records.append(
                {
                    "branch": branch,
                    "head": branch_info["head"],
                    "upstream": branch_info["upstream"],
                    "classification": classification,
                    "merged_into_main": branch_info["merged_into_main"],
                    "ahead_of_main": branch_info["ahead_of_main"],
                    "behind_main": branch_info["behind_main"],
                    "worktree_path": worktree["path"] if worktree else None,
                    "dirty": worktree["dirty"] if worktree else False,
                    "dirty_entries": worktree["dirty_entries"] if worktree else [],
                    "dirty_paths": worktree["dirty_paths"] if worktree else [],
                    "manifest_paths": [str(record.manifest_path) for record in manifest_records],
                    "plan_paths": [str(record.plan_path) for record in manifest_records if record.plan_path],
                    "plan_status": manifest_status,
                    "proposal_state": next(
                        (record.proposal_state for record in manifest_records if record.proposal_state),
                        None,
                    ),
                }
            )

        repo_payloads.append(
            {
                "id": repo.repo_id,
                "root": str(repo.root),
                "main_branch": repo.main_branch,
                "branches": sorted(branch_records, key=lambda item: item["branch"]),
                "counts": {
                    "branches": len(branch_records),
                    "non_main_branches": sum(1 for item in branch_records if item["branch"] != repo.main_branch),
                    "attached_worktrees": sum(1 for item in branch_records if item["worktree_path"]),
                    "dirty_worktrees": dirty_worktrees,
                },
            }
        )

    pending_proposals = []
    branchless_plan_manifests = []
    for manifest in manifests:
        if not manifest.branch:
            continue
        if manifest.branch in all_branch_names:
            continue
        status = manifest.status or "unknown"
        proposal_state = manifest.proposal_state or "none"
        record = {
            "branch": manifest.branch,
            "classification": (
                "pending_proposal"
                if proposal_state == "pending"
                else "historical"
                if status in {"promoted", "archived"}
                else "branchless_plan"
            ),
            "status": status,
            "proposal_state": proposal_state,
            "manifest_path": str(manifest.manifest_path),
            "plan_path": str(manifest.plan_path) if manifest.plan_path else None,
        }
        if record["classification"] == "pending_proposal":
            pending_proposals.append(record)
        else:
            branchless_plan_manifests.append(record)

    branchless_plan_manifests = sorted(
        branchless_plan_manifests, key=lambda item: item["branch"]
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "repos": repo_payloads,
        "pending_proposals": sorted(pending_proposals, key=lambda item: item["branch"]),
        "branchless_plan_manifests": branchless_plan_manifests,
        # Keep the legacy key during the rename so older tooling does not break.
        "orphaned_manifests": branchless_plan_manifests,
    }


def short_path(path: str | None) -> str:
    if not path:
        return "-"
    return Path(path).name


def markdown_link(path: str | None) -> str:
    if not path:
        return "-"
    target = Path(path)
    return f"[{target.name}]({target})"


def preferred_plan_path(branch: str, plan_paths: list[str]) -> str | None:
    if not plan_paths:
        return None
    branch_slug = branch.split("/", 1)[-1]
    for path in plan_paths:
        if branch_slug in Path(path).name:
            return path
    return plan_paths[0]


def build_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_generated_section(payload: dict[str, Any]) -> str:
    lines = [
        GENERATED_START,
        "## Generated Status Snapshot",
        "",
        "This section is generated from live git and worktree state by the agent-protocols workstream script.",
        "",
        f"_Last generated: {payload['generated_at']}_",
        "",
    ]

    summary_rows = []
    active_rows = []
    historical_rows = []
    for repo in payload["repos"]:
        counts = repo["counts"]
        summary_rows.append(
            [
                repo["id"],
                repo["main_branch"],
                str(counts["non_main_branches"]),
                str(counts["attached_worktrees"]),
                str(counts["dirty_worktrees"]),
            ]
        )
        for branch in repo["branches"]:
            if branch["branch"] == repo["main_branch"]:
                continue
            countless_classes = {"active", "promotable", "historical", "merged_stale"}
            row = [
                repo["id"],
                branch["branch"],
                branch["classification"],
                short_path(branch["worktree_path"]),
                "dirty" if branch["dirty"] else "clean",
                "-" if branch["classification"] in countless_classes else str(branch["ahead_of_main"]),
                "-" if branch["classification"] in countless_classes else str(branch["behind_main"]),
                markdown_link(preferred_plan_path(branch["branch"], branch["plan_paths"])),
            ]
            if branch["classification"] in {"active", "promotable", "diverged"}:
                active_rows.append(row)
            else:
                historical_rows.append(row)

    lines.append("### Summary")
    lines.extend(build_table(["Repo", "Main", "Non-main branches", "Attached worktrees", "Dirty worktrees"], summary_rows))
    lines.append("")

    lines.append("### Active, Promotable, And Diverged Branches")
    if active_rows:
        lines.extend(
            build_table(
                ["Repo", "Branch", "Class", "Worktree", "State", "Ahead", "Behind", "Plan"],
                active_rows,
            )
        )
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Historical And Merged-Stale Branches")
    if historical_rows:
        lines.extend(
            build_table(
                ["Repo", "Branch", "Class", "Worktree", "State", "Ahead", "Behind", "Plan"],
                historical_rows,
            )
        )
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Pending Proposals Without Live Branches")
    pending_proposals = payload["pending_proposals"]
    if pending_proposals:
        pending_rows = [
            [
                item["branch"],
                item["classification"],
                item["status"],
                item["proposal_state"],
                markdown_link(item["plan_path"] or item["manifest_path"]),
            ]
            for item in pending_proposals
        ]
        lines.extend(
            build_table(
                ["Branch", "Class", "Status", "Proposal State", "Plan"],
                pending_rows,
            )
        )
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Branchless Plan Manifests")
    branchless = payload["branchless_plan_manifests"]
    if branchless:
        branchless_rows = [
            [
                item["branch"],
                item["classification"],
                item["status"],
                item["proposal_state"],
                markdown_link(item["plan_path"] or item["manifest_path"]),
            ]
            for item in branchless
        ]
        lines.extend(
            build_table(
                ["Branch", "Class", "Status", "Proposal State", "Plan"],
                branchless_rows,
            )
        )
    else:
        lines.append("None.")
    lines.append(GENERATED_END)
    return "\n".join(lines)


def sync_index(settings: WorkstreamSettings, payload: dict[str, Any], check: bool, confirm: bool) -> int:
    ledger_path = settings.ledger_path
    content = ledger_path.read_text(encoding="utf-8")
    start = content.find(GENERATED_START)
    end = content.find(GENERATED_END)
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(
            f"{ledger_path} is missing the generated workstream state markers"
        )
    end += len(GENERATED_END)
    rendered = render_generated_section(payload)
    updated = content[:start] + rendered + content[end:]
    if check:
        normalized_content = GENERATED_TIMESTAMP_PATTERN.sub("", content)
        normalized_updated = GENERATED_TIMESTAMP_PATTERN.sub("", updated)
        if normalized_content == normalized_updated:
            return 0
        print(f"{ledger_path} is out of sync with live workstream state", file=sys.stderr)
        return 1
    if confirm:
        ledger_path.write_text(updated, encoding="utf-8")
        print(f"updated {ledger_path}")
        return 0
    print(rendered)
    return 0


def emit_text(payload: dict[str, Any]) -> int:
    print(f"generated_at: {payload['generated_at']}")
    for repo in payload["repos"]:
        counts = repo["counts"]
        print(
            f"{repo['id']}: {counts['non_main_branches']} non-main branches, "
            f"{counts['attached_worktrees']} attached worktrees, "
            f"{counts['dirty_worktrees']} dirty"
        )
        for branch in repo["branches"]:
            if branch["branch"] == repo["main_branch"]:
                continue
            state = "dirty" if branch["dirty"] else "clean"
            worktree = branch["worktree_path"] or "-"
            print(
                f"  - {branch['branch']} [{branch['classification']}] "
                f"ahead={branch['ahead_of_main']} behind={branch['behind_main']} "
                f"{state} {worktree}"
            )
    if payload["pending_proposals"]:
        print("pending proposals:")
        for item in payload["pending_proposals"]:
            print(f"  - {item['branch']} [{item['classification']}] {item['plan_path'] or item['manifest_path']}")
    if payload["branchless_plan_manifests"]:
        print("branchless plan manifests:")
        for item in payload["branchless_plan_manifests"]:
            print(f"  - {item['branch']} [{item['classification']}] {item['plan_path'] or item['manifest_path']}")
    return 0


def emit_reconciliation_text(payload: dict[str, Any]) -> int:
    print(f"generated_at: {payload['generated_at']}")
    print("branch decisions:")
    for item in payload["branch_decisions"]:
        worktree = item["worktree_path"] or "-"
        print(
            f"  - {item['repo']}:{item['branch']} "
            f"[{item['classification']}/{item['implementation_state']}] "
            f"action={item['recommended_action']} dirty={item['dirty']} worktree={worktree}"
        )
        if item["combined_paths"]:
            print(f"    paths: {', '.join(item['combined_paths'])}")
    if payload["branchless_decisions"]:
        print("branchless decisions:")
        for item in payload["branchless_decisions"]:
            print(
                f"  - {item['branch']} [{item['classification']}/{item['status']}] "
                f"action={item['recommended_action']}"
            )
    return 0


def main() -> int:
    args = parse_args()
    settings = load_settings(args.root.resolve(), args.repo)
    payload = build_audit(settings.root, list(settings.repos))
    if args.command == "audit":
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        return emit_text(payload)
    if args.command == "reconcile":
        reconciliation = build_reconciliation(payload, list(settings.repos))
        if args.json:
            print(json.dumps(reconciliation, indent=2))
            return 0
        return emit_reconciliation_text(reconciliation)
    if args.command == "sync-index":
        return sync_index(settings, payload, check=args.check, confirm=args.confirm)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
