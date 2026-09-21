#!/usr/bin/env python3
"""Scaffold repo-local agent-protocols integration files."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LEDGER_PATH = Path("docs/live-workstream-status.md")
LOCAL_OVERLAY_README_PATH = Path("local/README.md")
ASSISTANT_ENTRYPOINTS = [
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path(".cursorrules"),
    Path(".cursor/rules"),
    Path(".github/copilot-instructions.md"),
]
VENDORED_FILES = [
    "README.md",
    "VERSION",
    "SYNCING.md",
    "assistant-integration.md",
    "assistant-adoption-prompt.md",
    "merge-to-main-protocol.md",
    "pull-request-protocol.md",
    "substantive-work-protocol.md",
    "minor-work-protocol.md",
    "delegated-work-protocol.md",
    "proposal-protocol.md",
    "plan-protocol.md",
    "temp-doc-protocol.md",
    "citation-verification-protocol.md",
]
OPTIONAL_VENDORED_FILES = [
    "LICENSE-MIT",
    "LICENSE-APACHE",
]
VENDORED_DIRS = [
    "examples",
    "scripts",
    "skills",
]


@dataclass(frozen=True)
class InstallPlan:
    target: Path
    repo_id: str
    main_branch: str
    linked_repos: tuple[tuple[str, str, str], ...]
    discovered_repos: tuple[tuple[str, str, str], ...] = ()
    target_source: str = "explicit"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scaffold repo-local agent-protocols config and docs."
    )
    parser.add_argument(
        "--target",
        type=Path,
        help=(
            "Target repo root to scaffold. Defaults to the git repo containing "
            "the current working directory, falling back to the cwd."
        ),
    )
    parser.add_argument(
        "--repo-id",
        help="Primary repo id. Defaults to the detected target repo name.",
    )
    parser.add_argument(
        "--main-branch",
        default="main",
        help="Primary integration branch for the root repo.",
    )
    parser.add_argument(
        "--linked-repo",
        action="append",
        default=[],
        metavar="ID=PATH",
        help=(
            "Additional linked repo entries to add to agent-protocols.toml. "
            "Use ID=PATH for config-root-relative repos or "
            "ID@git_common_root=PATH for worktree-aware sibling repos."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Accept the inferred install plan without prompting. "
            "Also includes discovered workspace repos unless disabled."
        ),
    )
    parser.add_argument(
        "--include-discovered-repos",
        action="store_true",
        help=(
            "Include auto-discovered sibling git repos in non-interactive mode. "
            "Interactive mode includes them by default after confirmation."
        ),
    )
    parser.add_argument(
        "--skip-workspace-discovery",
        action="store_true",
        help="Do not scan the surrounding workspace for sibling git repos.",
    )
    parser.add_argument(
        "--print-assistant-snippets",
        action="store_true",
        help="Print short assistant entrypoint snippets after scaffolding.",
    )
    parser.add_argument(
        "--print-adoption-prompt",
        action="store_true",
        help="Print a rendered copy-paste prompt for a code assistant.",
    )
    parser.add_argument(
        "--vendor-dir",
        default="agent-protocols",
        help="Directory name to use when vendoring the package into the target repo.",
    )
    return parser.parse_args()


def parse_linked_repo(spec: str) -> tuple[str, str, str]:
    if "=" not in spec:
        raise ValueError(f"invalid linked repo '{spec}', expected ID=PATH")
    repo_token, repo_path = spec.split("=", 1)
    repo_token = repo_token.strip()
    repo_path = repo_path.strip()
    if not repo_token or not repo_path:
        raise ValueError(f"invalid linked repo '{spec}', expected ID=PATH")
    repo_id = repo_token
    path_base = "config_root"
    if "@" in repo_token:
        repo_id, path_base = repo_token.split("@", 1)
        repo_id = repo_id.strip()
        path_base = path_base.strip()
    if not repo_id:
        raise ValueError(f"invalid linked repo '{spec}', missing repo id")
    if path_base not in {"config_root", "git_common_root"}:
        raise ValueError(
            f"invalid linked repo '{spec}', unsupported path base '{path_base}'"
        )
    return repo_id, repo_path, path_base


def write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def local_overlay_readme() -> str:
    return """# Local Agent Protocols

This directory is reserved for repo-local protocol extensions.

The package installer creates `agent-protocols/local/`, but it does not treat
this directory as package-owned content:

- files in this directory are versioned by this repo
- package refreshes preserve local files here
- generic protocol improvements should be upstreamed to the package root
- repo-specific protocols should stay here

Do not move files from this directory into the upstream package unless the
protocol is intentionally being generalized for all consumers.
"""


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def run_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def existing_probe_path(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists():
        if candidate == candidate.parent:
            return Path.cwd().resolve()
        candidate = candidate.parent
    return candidate


def find_git_repo_root(path: Path) -> Path | None:
    probe = existing_probe_path(path)
    result = run_command(["git", "rev-parse", "--show-toplevel"], probe)
    if result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    if not stdout:
        return None
    return Path(stdout).resolve()


def resolve_git_common_root(path: Path) -> Path | None:
    repo_root = find_git_repo_root(path)
    if repo_root is None:
        return None
    result = run_command(["git", "rev-parse", "--git-common-dir"], repo_root)
    if result.returncode != 0:
        return None
    common_dir = result.stdout.strip()
    if not common_dir:
        return None
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = (repo_root / common_path).resolve()
    return common_path.parent


def guess_repo_id(path: Path) -> str:
    common_root = resolve_git_common_root(path)
    if common_root is not None:
        return common_root.name
    repo_root = find_git_repo_root(path)
    if repo_root is not None:
        return repo_root.name
    return path.name


def resolve_target_path(raw_target: Path | None) -> tuple[Path, str]:
    if raw_target is None:
        cwd = Path.cwd().resolve()
        repo_root = find_git_repo_root(cwd)
        if repo_root is not None:
            return repo_root, "detected from current git repo"
        return cwd, "current working directory"

    target = raw_target.expanduser().resolve()
    repo_root = find_git_repo_root(target)
    if repo_root is not None and (target == repo_root or repo_root in target.parents):
        return repo_root, "normalized to explicit target git repo"
    return target, "explicit target"


def relative_path(from_root: Path, to_root: Path) -> str:
    return Path(os.path.relpath(to_root, from_root)).as_posix()


def dedupe_linked_repos(
    linked_repos: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in linked_repos:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def discover_workspace_repos(
    target: Path,
    vendor_dir: str,
) -> list[tuple[str, str, str]]:
    common_root = resolve_git_common_root(target) or find_git_repo_root(target)
    if common_root is None:
        return []
    workspace_root = common_root.parent
    if not workspace_root.exists():
        return []

    discovered: list[tuple[str, str, str]] = []
    try:
        children = sorted(workspace_root.iterdir(), key=lambda path: path.name)
    except OSError:
        return []

    for child in children:
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        if child.name.startswith("."):
            continue
        if child.resolve() == common_root.resolve():
            continue
        if child.name == vendor_dir:
            continue
        repo_root = find_git_repo_root(child)
        if repo_root is None or repo_root != child.resolve():
            continue
        discovered.append(
            (
                guess_repo_id(repo_root),
                relative_path(common_root, repo_root),
                "git_common_root",
            )
        )
    return dedupe_linked_repos(discovered)


def render_linked_repo(spec: tuple[str, str, str]) -> str:
    repo_id, repo_path, path_base = spec
    token = repo_id if path_base == "config_root" else f"{repo_id}@{path_base}"
    return f"{token}={repo_path}"


def render_linked_repo_list(linked_repos: list[tuple[str, str, str]]) -> str:
    return ", ".join(render_linked_repo(spec) for spec in linked_repos)


def parse_linked_repo_list(raw_specs: str) -> list[tuple[str, str, str]]:
    specs: list[tuple[str, str, str]] = []
    if not raw_specs.strip():
        return specs
    for raw_spec in raw_specs.split(","):
        stripped = raw_spec.strip()
        if not stripped:
            continue
        specs.append(parse_linked_repo(stripped))
    return specs


def prompt_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{prompt} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def prompt_text(prompt: str, *, default: str) -> str:
    answer = input(f"{prompt} [{default}] ").strip()
    return answer or default


def summarize_plan(plan: InstallPlan) -> None:
    print("Detected install plan:\n")
    print(f"- target repo: {plan.target}")
    print(f"- target source: {plan.target_source}")
    print(f"- primary repo id: {plan.repo_id}")
    print(f"- main branch: {plan.main_branch}")
    if plan.linked_repos:
        print("- linked repos:")
        for repo_id, repo_path, path_base in plan.linked_repos:
            print(f"  - {repo_id}: path={repo_path}, path_base={path_base}")
    else:
        print("- linked repos: none")
    if plan.discovered_repos:
        print("- workspace discovery: sibling repos were auto-detected")
    print()


def interactive_mode_enabled(args: argparse.Namespace) -> bool:
    return not args.yes and sys.stdin.isatty() and sys.stdout.isatty()


def build_install_plan(args: argparse.Namespace) -> InstallPlan:
    target, target_source = resolve_target_path(args.target)
    explicit_linked = [parse_linked_repo(spec) for spec in args.linked_repo]
    discovered = (
        []
        if args.skip_workspace_discovery
        else discover_workspace_repos(target=target, vendor_dir=args.vendor_dir)
    )

    if interactive_mode_enabled(args):
        default_plan = InstallPlan(
            target=target,
            repo_id=args.repo_id or guess_repo_id(target),
            main_branch=args.main_branch,
            linked_repos=tuple(dedupe_linked_repos(explicit_linked + discovered)),
            discovered_repos=tuple(discovered),
            target_source=target_source,
        )
        summarize_plan(default_plan)
        if prompt_yes_no("Use this installation plan?", default=True):
            return default_plan

        custom_target = Path(
            prompt_text("Target repo path", default=str(default_plan.target))
        )
        resolved_target, resolved_source = resolve_target_path(custom_target)
        custom_repo_id = prompt_text(
            "Primary repo id",
            default=args.repo_id or guess_repo_id(resolved_target),
        )
        custom_branch = prompt_text("Main branch", default=args.main_branch)
        custom_discovered = (
            []
            if args.skip_workspace_discovery
            else discover_workspace_repos(
                target=resolved_target,
                vendor_dir=args.vendor_dir,
            )
        )
        default_specs = dedupe_linked_repos(explicit_linked + custom_discovered)
        custom_specs = prompt_text(
            "Linked repos (comma-separated ID=PATH or ID@git_common_root=PATH)",
            default=render_linked_repo_list(default_specs),
        )
        return InstallPlan(
            target=resolved_target,
            repo_id=custom_repo_id,
            main_branch=custom_branch,
            linked_repos=tuple(parse_linked_repo_list(custom_specs)),
            discovered_repos=tuple(custom_discovered),
            target_source=resolved_source,
        )

    linked_repos = list(explicit_linked)
    if args.yes or args.include_discovered_repos:
        linked_repos.extend(discovered)
    return InstallPlan(
        target=target,
        repo_id=args.repo_id or guess_repo_id(target),
        main_branch=args.main_branch,
        linked_repos=tuple(dedupe_linked_repos(linked_repos)),
        discovered_repos=tuple(discovered),
        target_source=target_source,
    )


def copy_package(source_root: Path, target_root: Path, vendor_dir: str) -> Path:
    vendor_root = (target_root / vendor_dir).resolve()
    if vendor_root == source_root.resolve():
        if (source_root / ".git").exists():
            raise RuntimeError(
                "detected an agent-protocols git checkout inside the target repo; "
                "clone the package outside the target repo and rerun the installer"
            )
        return vendor_root

    vendor_root.mkdir(parents=True, exist_ok=True)

    for relative in VENDORED_FILES:
        source = source_root / relative
        destination = vendor_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for relative in OPTIONAL_VENDORED_FILES:
        source = source_root / relative
        if not source.exists():
            continue
        destination = vendor_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for relative in VENDORED_DIRS:
        source = source_root / relative
        destination = vendor_root / relative
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    write_if_missing(
        vendor_root / LOCAL_OVERLAY_README_PATH,
        local_overlay_readme(),
    )

    return vendor_root


def build_config(
    repo_id: str,
    main_branch: str,
    linked_repos: list[tuple[str, str, str]],
) -> str:
    lines = [
        'version = 1',
        f'status_ledger_path = "{DEFAULT_LEDGER_PATH.as_posix()}"',
        "",
        "[[repos]]",
        f'id = "{repo_id}"',
        'path = "."',
        f'main_branch = "{main_branch}"',
    ]
    for linked_id, linked_path, linked_path_base in linked_repos:
        lines.extend(
            [
                "",
                "[[repos]]",
                f'id = "{linked_id}"',
                f'path = "{linked_path}"',
            ]
        )
        if linked_path_base != "config_root":
            lines.append(f'path_base = "{linked_path_base}"')
        lines.append(f'main_branch = "{main_branch}"')
    lines.append("")
    return "\n".join(lines)


def plans_readme() -> str:
    return """# Plans

This folder stores durable plan families for this repo.

This `README.md` is only the landing page. The durable inventory lives in
`docs/plans/plans-index.md`.

Start here:

- `docs/plans/plans-index.md`
- `docs/plans/cross-repo/README.md`
- `docs/live-workstream-status.md`
- `agent-protocols/substantive-work-protocol.md`
- `agent-protocols/proposal-protocol.md`
"""


def plans_index() -> str:
    return """# Plans Index

This directory stores durable plan families for this repo.

The canonical reusable planning guidance lives in:

- `agent-protocols/plan-protocol.md`

Live branch and worktree state is tracked separately in:

- `docs/live-workstream-status.md`

## Taxonomy

- `feature/`
- `sync/`
- `hotfix/`
- `proposals/`
- `archive/`

Optional extension:

- `cross-repo/` as an orchestrator-only extension for canonical plan families
  whose completion or acceptance depends on coordinated work across 2+ repos

Temporary working docs belong in:

- `docs/temp/`
"""


def cross_repo_readme() -> str:
    return """# Cross-Repo Plans

This extension is for repos that act as the canonical orchestration root for
coordinated multi-repo work.

Use the default `docs/plans/` taxonomy for local work owned and accepted inside
one repo.

Use `docs/plans/cross-repo/` only when completion or acceptance depends on
coordinated work across 2+ repos.

Another repo being mentioned as context does not make a plan cross-repo.

If this repo is not acting as an orchestration root yet, keep this folder
empty apart from this README.
"""


def temp_docs_readme() -> str:
    return """# Temp Docs

This folder holds temporary working markdown that should be reviewed and then
deleted after the task is complete.

Use it for scratch notes that do not yet belong in durable surfaces such as:

- `docs/plans/`
- `docs/adr/`
- `docs/history/`
- repo-owned proposal logs, when the repo keeps them

Follow:

- `agent-protocols/temp-doc-protocol.md`

Recommended shape:

- `docs/temp/<feature-slug>/<topic>-YYYY-MM-DD.md`
"""


def live_ledger() -> str:
    return """# Live Workstream Status

This ledger is the repo-local mutable current-state surface for active
workstreams, manifest-backed pending proposals, and other manifest-backed plan
families. Compact proposal records without manifests remain valid durable docs
but may not appear in this generated ledger.

## Definitions

- A manifest is a `.plan.toml` phase-gate file paired with a durable plan.
- Manifests are not proposals by default.
- Pending proposals are branchless plan families whose plan records
  `Proposal state: pending`.
- Branchless Plan Manifests are manifest-backed plan families whose recorded
  branch is not currently present locally and that are not classified as
  pending proposals.

Refresh this ledger with:

- `python3 agent-protocols/scripts/workstream.py sync-index --confirm`

<!-- BEGIN GENERATED WORKSTREAM STATE -->
Template placeholder.
<!-- END GENERATED WORKSTREAM STATE -->
"""


def example_plan() -> str:
    return """# Example Workstream

- Date: 2026-03-29
- Status: template
- Scope: example-only plan family for package consumers
- Baseline: example only
- Branch: none (template example)
- Related ADR: none
- Supersedes: none
"""


def example_manifest() -> str:
    return """title = "Example workstream"
root_dir = "../../.."
phase_order = ["setup", "acceptance"]

[phases.setup]
summary = "Ensure the example plan family is present."

[[phases.setup.checks]]
id = "plan-exists"
type = "path_exists"
path = "docs/plans/feature/example-workstream.md"

[[phases.setup.checks]]
id = "manifest-exists"
type = "path_exists"
path = "docs/plans/feature/example-workstream.plan.toml"

[phases.acceptance]
summary = "Validate the example repo skeleton."
depends_on = ["setup"]

[[phases.acceptance.checks]]
id = "plans-readme-links-index"
type = "text_present"
path = "docs/plans/README.md"
text = "docs/plans/plans-index.md"

[[phases.acceptance.checks]]
id = "plans-readme-links-ledger"
type = "text_present"
path = "docs/plans/README.md"
text = "docs/live-workstream-status.md"

[[phases.acceptance.checks]]
id = "cross-repo-readme-exists"
type = "path_exists"
path = "docs/plans/cross-repo/README.md"

[[phases.acceptance.checks]]
id = "scripts-compile"
type = "command"
command = "python3 -m py_compile agent-protocols/scripts/check_gated_plan.py agent-protocols/scripts/workstream.py agent-protocols/scripts/repo_state.py"
"""


def scaffold(
    target: Path,
    repo_id: str,
    main_branch: str,
    linked_repos: list[tuple[str, str, str]],
    vendor_dir: str,
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    seed_example_plan = not (target / "docs/plans").exists()
    copy_package(package_root(), target, vendor_dir)
    write_if_missing(
        target / "agent-protocols.toml",
        build_config(repo_id=repo_id, main_branch=main_branch, linked_repos=linked_repos),
    )

    for relative in [
        "docs/plans/feature",
        "docs/plans/hotfix",
        "docs/plans/sync",
        "docs/plans/archive",
        "docs/plans/cross-repo",
        "docs/temp",
        "docs/plans/proposals",
    ]:
        (target / relative).mkdir(parents=True, exist_ok=True)

    if linked_repos:
        for relative in [
            "docs/plans/cross-repo/feature",
            "docs/plans/cross-repo/hotfix",
            "docs/plans/cross-repo/sync",
            "docs/plans/cross-repo/archive",
            "docs/plans/cross-repo/proposals",
            "docs/plans/cross-repo/proposals/pending",
        ]:
            (target / relative).mkdir(parents=True, exist_ok=True)

    write_if_missing(target / "docs/plans/README.md", plans_readme())
    write_if_missing(target / "docs/plans/plans-index.md", plans_index())
    write_if_missing(target / "docs/plans/cross-repo/README.md", cross_repo_readme())
    write_if_missing(target / "docs/temp/README.md", temp_docs_readme())
    write_if_missing(target / DEFAULT_LEDGER_PATH, live_ledger())
    if seed_example_plan:
        write_if_missing(target / "docs/plans/feature/example-workstream.md", example_plan())
        write_if_missing(
            target / "docs/plans/feature/example-workstream.plan.toml",
            example_manifest(),
        )


def print_assistant_snippets() -> None:
    snippet = (
        "For substantive work, follow `agent-protocols/substantive-work-protocol.md`.\n"
        "Use `docs/temp/` for temporary working docs per `agent-protocols/temp-doc-protocol.md`.\n"
        "Repo topology and linked repos are declared in `agent-protocols.toml`.\n"
    )
    print("AGENTS.md / CLAUDE.md snippet:\n")
    print(snippet)


def assistant_entrypoint_status(target: Path, vendor_dir: str) -> tuple[str, list[Path]]:
    references: list[Path] = []
    protocol_markers = [
        f"{vendor_dir}/substantive-work-protocol.md",
        f"{vendor_dir}/minor-work-protocol.md",
        f"{vendor_dir}/temp-doc-protocol.md",
    ]
    for relative in ASSISTANT_ENTRYPOINTS:
        path = target / relative
        if path.is_dir():
            for child in sorted(path.rglob("*.md")):
                try:
                    content = child.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                if any(marker in content for marker in protocol_markers):
                    references.append(child.relative_to(target))
        elif path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if any(marker in content for marker in protocol_markers):
                references.append(relative)
    status = "ok" if references else "action_needed"
    return status, references


def print_assistant_entrypoint_report(target: Path, vendor_dir: str) -> None:
    status, references = assistant_entrypoint_status(target=target, vendor_dir=vendor_dir)
    if status == "ok":
        rendered = ", ".join(path.as_posix() for path in references)
        print(f"assistant entrypoint wiring: ok ({rendered})")
        return

    print("assistant entrypoint wiring: action needed")
    print(
        "  No common assistant instruction file currently points at "
        f"`{vendor_dir}/substantive-work-protocol.md`."
    )
    print(
        "  Update AGENTS.md, CLAUDE.md, or the repo's equivalent assistant "
        "entrypoint so future agents know to use the protocols."
    )
    print(
        "  Run again with `--print-assistant-snippets` or "
        "`--print-adoption-prompt` for copy-paste guidance."
    )


def render_adoption_prompt(repo_id: str, main_branch: str, vendor_dir: str) -> str:
    template = (package_root() / "assistant-adoption-prompt.md").read_text(
        encoding="utf-8"
    )
    return (
        template.replace("{{REPO_ID}}", repo_id)
        .replace("{{MAIN_BRANCH}}", main_branch)
        .replace("{{VENDOR_DIR}}", vendor_dir)
        .replace("{{CONFIG_FILE}}", "agent-protocols.toml")
    )


def print_adoption_prompt(repo_id: str, main_branch: str, vendor_dir: str) -> None:
    print("Assistant adoption prompt:\n")
    print(render_adoption_prompt(repo_id=repo_id, main_branch=main_branch, vendor_dir=vendor_dir))


def main() -> int:
    args = parse_args()
    try:
        plan = build_install_plan(args)
        scaffold(
            target=plan.target,
            repo_id=plan.repo_id,
            main_branch=plan.main_branch,
            linked_repos=list(plan.linked_repos),
            vendor_dir=args.vendor_dir,
        )
    except EOFError:
        print("install aborted: input stream closed", file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"install aborted: {error}", file=sys.stderr)
        return 2
    print(f"scaffolded agent-protocols integration in {plan.target}")
    print_assistant_entrypoint_report(target=plan.target, vendor_dir=args.vendor_dir)
    if args.print_assistant_snippets:
        print()
        print_assistant_snippets()
    if args.print_adoption_prompt:
        print()
        print_adoption_prompt(
            repo_id=plan.repo_id,
            main_branch=plan.main_branch,
            vendor_dir=args.vendor_dir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
