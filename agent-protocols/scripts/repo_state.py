#!/usr/bin/env python3
"""Summarize branch, worktree, and cleanliness state for a git repo."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List local branches, registered worktrees, stale worktree metadata, and orphaned checkout directories."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repo path or worktree to inspect. Defaults to the current directory.",
    )
    parser.add_argument(
        "--main",
        default="main",
        help="Integration branch to compare against. Defaults to main.",
    )
    parser.add_argument(
        "--scan-dir",
        action="append",
        type=Path,
        default=[],
        help="Directory whose immediate children should be scanned for orphaned checkout dirs. May be repeated.",
    )
    parser.add_argument(
        "--no-default-scan",
        action="store_true",
        help="Do not automatically scan the repo parent's .worktrees directory and registered worktree parents.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )
    parser.add_argument(
        "--apply-safe-cleanup",
        action="store_true",
        help=(
            "Apply deterministic safe cleanup: prune stale git worktree metadata "
            "and quarantine redundant checkout directories. Never deletes dirty "
            "or unmerged branch state."
        ),
    )
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        help="Directory used when --apply-safe-cleanup moves redundant checkout dirs.",
    )
    return parser.parse_args()


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def git_stdout(repo: Path, args: list[str]) -> str:
    completed = run_git(repo, args)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SystemExit(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def repo_root(path: Path) -> Path:
    return Path(git_stdout(path, ["rev-parse", "--show-toplevel"])).resolve()


def git_common_dir(repo: Path) -> Path:
    raw = git_stdout(repo, ["rev-parse", "--git-common-dir"])
    common = Path(raw)
    if not common.is_absolute():
        common = repo / common
    return common.resolve()


def parse_worktree_porcelain(output: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in output.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue

        key, _, value = line.partition(" ")
        if key == "branch" and value.startswith("refs/heads/"):
            current["branch"] = value.removeprefix("refs/heads/")
            current["branch_ref"] = value
        elif key == "prunable":
            current["prunable"] = True
            current["prunable_reason"] = value
        elif key in {"worktree", "HEAD", "detached", "bare"}:
            current[key] = value if value else True
        else:
            current[key] = value

    if current:
        entries.append(current)

    return entries


def worktree_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "git_status_ok": False,
            "dirty": None,
            "dirty_entries": [],
            "status_error": "path does not exist",
        }

    completed = run_git(path, ["status", "--short", "--branch", "--untracked-files=all"])
    if completed.returncode != 0:
        return {
            "exists": True,
            "git_status_ok": False,
            "dirty": None,
            "dirty_entries": [],
            "status_error": completed.stderr.strip() or completed.stdout.strip(),
        }

    entries = [
        line
        for line in completed.stdout.splitlines()
        if line and not line.startswith("## ")
    ]
    return {
        "exists": True,
        "git_status_ok": True,
        "dirty": bool(entries),
        "dirty_entries": entries,
        "status_error": None,
    }


def registered_worktrees(repo: Path) -> list[dict[str, Any]]:
    output = git_stdout(repo, ["worktree", "list", "--porcelain"])
    entries = parse_worktree_porcelain(output)
    for entry in entries:
        path = Path(str(entry["worktree"])).resolve()
        gitdir = resolve_gitdir_file(path)
        entry["path"] = str(path)
        entry["gitdir"] = str(gitdir) if gitdir else None
        entry["head"] = entry.pop("HEAD", None)
        entry.setdefault("branch", None)
        entry.setdefault("branch_ref", None)
        entry.setdefault("prunable", False)
        entry.setdefault("prunable_reason", None)
        entry.update(worktree_status(path))
    return entries


def branch_refs(repo: Path) -> list[dict[str, Any]]:
    output = git_stdout(
        repo,
        [
            "for-each-ref",
            "--format=%(refname:short)%00%(objectname:short)%00%(upstream:short)",
            "refs/heads",
        ],
    )
    branches: list[dict[str, Any]] = []
    for line in output.splitlines():
        branch, head, upstream = (line.split("\0") + ["", "", ""])[:3]
        branches.append(
            {
                "branch": branch,
                "head": head,
                "upstream": upstream or None,
            }
        )
    return branches


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{ref}"]).returncode == 0


def merged_into(repo: Path, branch: str, target: str) -> bool | None:
    if not ref_exists(repo, target):
        return None
    return run_git(repo, ["merge-base", "--is-ancestor", branch, target]).returncode == 0


def ahead_behind(repo: Path, branch: str, target: str) -> tuple[int | None, int | None]:
    if not ref_exists(repo, target):
        return (None, None)
    completed = run_git(repo, ["rev-list", "--left-right", "--count", f"{target}...{branch}"])
    if completed.returncode != 0:
        return (None, None)
    behind, ahead = completed.stdout.strip().split()
    return (int(ahead), int(behind))


def classify_branch(branch: dict[str, Any], main_branch: str) -> str:
    if branch["branch"] == main_branch:
        return "integration"
    worktree = branch.get("worktree")
    if worktree and worktree.get("prunable"):
        return "stale_worktree"
    if worktree and worktree.get("exists"):
        return "active_dirty" if worktree.get("dirty") else "active_clean"
    if branch.get("merged_into_main"):
        return "merged_no_worktree"
    return "branch_only"


def branches_with_state(
    repo: Path,
    main_branch: str,
    worktrees: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_branch = {
        str(worktree["branch"]): worktree
        for worktree in worktrees
        if worktree.get("branch")
    }
    branches = []
    for branch in branch_refs(repo):
        name = branch["branch"]
        worktree = by_branch.get(name)
        ahead, behind = (0, 0) if name == main_branch else ahead_behind(repo, name, main_branch)
        branch.update(
            {
                "worktree_path": worktree.get("path") if worktree else None,
                "worktree_prunable": bool(worktree and worktree.get("prunable")),
                "dirty": worktree.get("dirty") if worktree else None,
                "dirty_entries": worktree.get("dirty_entries", []) if worktree else [],
                "merged_into_main": True
                if name == main_branch
                else merged_into(repo, name, main_branch),
                "ahead_of_main": ahead,
                "behind_main": behind,
                "worktree": worktree,
            }
        )
        branch["classification"] = classify_branch(branch, main_branch)
        branches.append(branch)
    return sorted(branches, key=lambda item: item["branch"])


def resolve_gitdir_file(path: Path) -> Path | None:
    git_file = path / ".git"
    try:
        is_file = git_file.is_file()
    except OSError:
        return None
    if not is_file:
        return None
    try:
        first_line = git_file.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    except OSError:
        return None
    if not first_line or not first_line[0].startswith("gitdir:"):
        return None
    raw = first_line[0].split(":", 1)[1].strip()
    gitdir = Path(raw)
    if not gitdir.is_absolute():
        gitdir = path / gitdir
    return gitdir.resolve()


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def scan_dirs_for_orphans(
    repo: Path,
    common_git_dir: Path,
    scan_dirs: list[Path],
    registered_paths: set[Path],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    orphaned: list[dict[str, str]] = []
    unregistered: list[dict[str, str]] = []
    shadowed: list[dict[str, str]] = []
    seen_dirs: set[Path] = set()
    registered_gitdirs: dict[Path, Path] = {}
    for path in registered_paths:
        gitdir = resolve_gitdir_file(path)
        if gitdir is not None:
            registered_gitdirs[gitdir] = path

    for scan_dir in scan_dirs:
        scan_dir = scan_dir.expanduser().resolve()
        if not scan_dir.is_dir() or scan_dir in seen_dirs:
            continue
        seen_dirs.add(scan_dir)

        for child in sorted(path for path in scan_dir.iterdir() if path.is_dir()):
            child = child.resolve()
            if child in registered_paths:
                continue

            gitdir = resolve_gitdir_file(child)
            if gitdir is None:
                continue

            belongs_to_repo = is_inside(gitdir, common_git_dir) or str(gitdir).startswith(
                f"{common_git_dir}{Path('/')}"
            )
            if not belongs_to_repo:
                continue

            record = {"path": str(child), "gitdir": str(gitdir)}
            if gitdir in registered_gitdirs:
                record["registered_worktree"] = str(registered_gitdirs[gitdir])
                shadowed.append(record)
            elif gitdir.exists():
                unregistered.append(record)
            else:
                orphaned.append(record)

    return orphaned, unregistered, shadowed


def branch_leaf(branch: str) -> str:
    return branch.rsplit("/", 1)[-1]


def checkout_suffix(repo: Path, checkout_path: Path) -> str:
    name = checkout_path.name
    prefix = f"{repo.name}-"
    if name.startswith(prefix):
        return name.removeprefix(prefix)
    return name


def enrich_orphaned_checkout_dirs(
    repo: Path,
    orphaned: list[dict[str, str]],
    branches: list[dict[str, Any]],
) -> None:
    by_leaf: dict[str, list[dict[str, Any]]] = {}
    for branch in branches:
        by_leaf.setdefault(branch_leaf(str(branch["branch"])), []).append(branch)

    for item in orphaned:
        suffix = checkout_suffix(repo, Path(item["path"]))
        matches = by_leaf.get(suffix, [])
        item["checkout_suffix"] = suffix
        if len(matches) == 1:
            match = matches[0]
            item["matched_branch"] = str(match["branch"])
            item["matched_branch_classification"] = str(match["classification"])
            item["matched_branch_merged_into_main"] = str(bool(match["merged_into_main"])).lower()
            item["auto_cleanup_eligible"] = str(
                bool(match["merged_into_main"] and not match["worktree_path"])
            ).lower()
        else:
            item["auto_cleanup_eligible"] = "false"


def default_scan_dirs(repo: Path, worktrees: list[dict[str, Any]]) -> list[Path]:
    candidates = [repo.parent / ".worktrees"]
    for worktree in worktrees:
        parent = Path(str(worktree["path"])).parent
        if parent.name == ".worktrees" or parent.parent == repo.parent:
            candidates.append(parent)
    return candidates


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    common_git_dir = git_common_dir(root)
    worktrees = registered_worktrees(root)
    branches = branches_with_state(root, args.main, worktrees)

    scan_dirs = [path for path in args.scan_dir]
    if not args.no_default_scan:
        scan_dirs.extend(default_scan_dirs(root, worktrees))
    registered_paths = {Path(str(worktree["path"])).resolve() for worktree in worktrees}
    orphaned, unregistered, shadowed = scan_dirs_for_orphans(
        root,
        common_git_dir,
        scan_dirs,
        registered_paths,
    )
    enrich_orphaned_checkout_dirs(root, orphaned, branches)

    root_status = worktree_status(root)
    stale = [worktree for worktree in worktrees if worktree.get("prunable") or not worktree.get("exists")]
    dirty_worktrees = [worktree for worktree in worktrees if worktree.get("dirty") is True]
    branches_without_worktrees = [
        branch for branch in branches if branch["branch"] != args.main and not branch["worktree_path"]
    ]

    payload = {
        "repo": str(root),
        "git_common_dir": str(common_git_dir),
        "main_branch": args.main,
        "current_branch": git_stdout(root, ["branch", "--show-current"]),
        "current_clean": root_status["dirty"] is False,
        "summary": {
            "local_branches": len(branches),
            "registered_worktrees": len(worktrees),
            "stale_registered_worktrees": len(stale),
            "dirty_worktrees": len(dirty_worktrees),
            "branches_without_worktrees": len(branches_without_worktrees),
            "orphaned_checkout_dirs": len(orphaned),
            "unregistered_checkout_dirs": len(unregistered),
            "shadowed_checkout_dirs": len(shadowed),
        },
        "worktrees": worktrees,
        "stale_registered_worktrees": stale,
        "branches": branches,
        "orphaned_checkout_dirs": orphaned,
        "unregistered_checkout_dirs": unregistered,
        "shadowed_checkout_dirs": shadowed,
    }
    payload["safe_cleanup_actions"] = safe_cleanup_actions(payload)
    return payload


def safe_cleanup_actions(payload: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if payload["stale_registered_worktrees"]:
        actions.append(
            {
                "action": "git_worktree_prune",
                "reason": "remove stale registered worktree metadata for paths that no longer exist",
                "repo": str(payload["repo"]),
            }
        )

    for item in payload["shadowed_checkout_dirs"]:
        actions.append(
            {
                "action": "quarantine_dir",
                "category": "shadowed_checkout_dir",
                "path": str(item["path"]),
                "reason": "directory is not a registered worktree and points at another registered worktree gitdir",
            }
        )

    for item in payload["orphaned_checkout_dirs"]:
        if item.get("auto_cleanup_eligible") == "true":
            actions.append(
                {
                    "action": "quarantine_dir",
                    "category": "merged_orphaned_checkout_dir",
                    "path": str(item["path"]),
                    "reason": f"orphaned checkout maps to merged branch {item.get('matched_branch')}",
                }
            )

    for branch in payload["branches"]:
        if branch.get("classification") == "merged_no_worktree":
            actions.append(
                {
                    "action": "delete_merged_branch",
                    "branch": str(branch["branch"]),
                    "reason": "branch is merged into main and has no registered worktree",
                }
            )

    return actions


def quarantine_root(args: argparse.Namespace, payload: dict[str, Any]) -> Path:
    if args.quarantine_dir:
        return args.quarantine_dir.expanduser().resolve()
    repo = Path(str(payload["repo"])).resolve()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return (repo.parent / ".worktrees" / ".repo-state-quarantine" / stamp).resolve()


def unique_destination(root: Path, source: Path) -> Path:
    destination = root / source.name
    if not destination.exists():
        return destination
    index = 2
    while True:
        candidate = root / f"{source.name}-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def apply_safe_cleanup(args: argparse.Namespace, payload: dict[str, Any]) -> tuple[list[dict[str, str]], Path | None]:
    actions = list(payload["safe_cleanup_actions"])
    if not actions:
        return [], None

    applied: list[dict[str, str]] = []
    quarantine_dir: Path | None = None
    repo = Path(str(payload["repo"]))

    if any(action["action"] == "git_worktree_prune" for action in actions):
        completed = run_git(repo, ["worktree", "prune", "-v"])
        applied.append(
            {
                "action": "git_worktree_prune",
                "returncode": str(completed.returncode),
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )
        if completed.returncode != 0:
            raise SystemExit(completed.stderr.strip() or "git worktree prune failed")

    for action in actions:
        if action["action"] != "quarantine_dir":
            continue
        source = Path(action["path"]).resolve()
        if not source.exists():
            continue
        if quarantine_dir is None:
            quarantine_dir = quarantine_root(args, payload)
            quarantine_dir.mkdir(parents=True, exist_ok=True)
        destination = unique_destination(quarantine_dir, source)
        shutil.move(str(source), str(destination))
        applied.append(
            {
                **action,
                "destination": str(destination),
            }
        )

    for action in actions:
        if action["action"] != "delete_merged_branch":
            continue
        completed = run_git(repo, ["branch", "-d", str(action["branch"])])
        applied.append(
            {
                **action,
                "returncode": str(completed.returncode),
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )
        if completed.returncode != 0:
            raise SystemExit(completed.stderr.strip() or f"failed to delete {action['branch']}")

    return applied, quarantine_dir


def state_label(worktree: dict[str, Any]) -> str:
    if worktree.get("prunable") or not worktree.get("exists"):
        return "stale"
    if worktree.get("dirty"):
        return "dirty"
    return "clean"


def print_human(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    print(f"Repo: {payload['repo']}")
    print(f"Current: {payload['current_branch']} ({'clean' if payload['current_clean'] else 'dirty'})")
    print(
        "Summary: "
        f"{summary['local_branches']} branches, "
        f"{summary['registered_worktrees']} registered worktrees, "
        f"{summary['stale_registered_worktrees']} stale, "
        f"{summary['dirty_worktrees']} dirty, "
        f"{summary['orphaned_checkout_dirs']} orphan dirs, "
        f"{summary['shadowed_checkout_dirs']} shadow dirs"
    )

    print("\nRegistered Worktrees")
    for worktree in payload["worktrees"]:
        branch = worktree.get("branch") or "detached"
        label = state_label(worktree)
        detail = f" ({worktree['prunable_reason']})" if worktree.get("prunable_reason") else ""
        print(f"  - {branch} [{label}] {worktree['path']}{detail}")

    print("\nBranches")
    for branch in payload["branches"]:
        worktree = branch["worktree_path"] or "-"
        dirty = "dirty" if branch["dirty"] else "clean" if branch["dirty"] is False else "-"
        ahead = "-" if branch["ahead_of_main"] is None else branch["ahead_of_main"]
        behind = "-" if branch["behind_main"] is None else branch["behind_main"]
        print(
            f"  - {branch['branch']} [{branch['classification']}] "
            f"ahead={ahead} behind={behind} {dirty} {worktree}"
        )

    if payload["stale_registered_worktrees"]:
        print("\nStale Registered Worktrees")
        for worktree in payload["stale_registered_worktrees"]:
            reason = worktree.get("prunable_reason") or worktree.get("status_error") or "stale"
            print(f"  - {worktree['path']} ({reason})")
        print("  cleanup: git worktree prune")

    if payload["orphaned_checkout_dirs"]:
        print("\nOrphaned Checkout Dirs")
        for item in payload["orphaned_checkout_dirs"]:
            match = (
                f"; matched {item['matched_branch']}"
                if item.get("matched_branch")
                else ""
            )
            eligibility = (
                "; safe cleanup eligible"
                if item.get("auto_cleanup_eligible") == "true"
                else ""
            )
            print(f"  - {item['path']} (.git -> {item['gitdir']}{match}{eligibility})")
        print("  cleanup: safe mode quarantines only exact merged-branch matches")

    if payload["unregistered_checkout_dirs"]:
        print("\nUnregistered Checkout Dirs")
        for item in payload["unregistered_checkout_dirs"]:
            print(f"  - {item['path']} (.git -> {item['gitdir']})")

    if payload["shadowed_checkout_dirs"]:
        print("\nShadowed Checkout Dirs")
        for item in payload["shadowed_checkout_dirs"]:
            print(
                f"  - {item['path']} (.git -> {item['gitdir']}; registered as {item['registered_worktree']})"
            )
        print("  cleanup: remove; it is not a registered worktree and points at another checkout's gitdir")

    if payload["safe_cleanup_actions"]:
        print("\nSafe Cleanup Actions")
        for action in payload["safe_cleanup_actions"]:
            target_value = action.get("path") or action.get("branch")
            target = f" {target_value}" if target_value else ""
            print(f"  - {action['action']}{target}: {action['reason']}")
        print("  apply: scripts/repo_state.py --apply-safe-cleanup")

    if payload.get("applied_actions"):
        print("\nApplied Actions")
        for action in payload["applied_actions"]:
            destination = f" -> {action['destination']}" if action.get("destination") else ""
            print(f"  - {action['action']}{destination}")


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    if args.apply_safe_cleanup:
        applied_actions, quarantine_dir = apply_safe_cleanup(args, payload)
        payload = build_payload(args)
        payload["applied_actions"] = applied_actions
        payload["quarantine_dir"] = str(quarantine_dir) if quarantine_dir else None
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_human(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
