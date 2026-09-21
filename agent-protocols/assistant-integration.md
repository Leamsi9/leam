# Assistant Integration

This package only works reliably when the consuming repo exposes it through the
instruction surfaces that local assistants already read.

## Discovery Contract

Keep one reusable package and point assistant-specific entrypoints at it.

Recommended approach:

1. vendor the package as `agent-protocols/`
2. keep repo topology in a repo-local `agent-protocols.toml`
3. point assistant instruction files at
   `agent-protocols/substantive-work-protocol.md`
4. keep repo-specific protocol extensions under `agent-protocols/local/`
5. keep repo-specific landing pages, ledgers, ADRs, and any proposal logs
   outside the vendored package
6. keep temporary working markdown under `docs/temp/` and follow
   `agent-protocols/temp-doc-protocol.md`
7. treat `agent-protocols/skills/` as package-owned reusable assistant skills
   that may be projected into local Codex, Claude, Ironclaw, or repo-specific
   skill surfaces

Do not fork the protocol docs into separate copies for each assistant.
Do not hand-edit generated skill projections as independent source; update the
package-owned skill first, then refresh the projection.

## Minimal Entry Points

At minimum, wire one or more repo instruction files to the package:

- `AGENTS.md`
- `CLAUDE.md`
- any assistant-specific rule or instruction surface your toolchain supports

Those files should stay short and point back to the package instead of
duplicating it.

## Suggested Pointer Text

Use a short pointer such as:

```md
For substantive work, follow `agent-protocols/substantive-work-protocol.md`.
Use `docs/temp/` for temporary working docs per `agent-protocols/temp-doc-protocol.md`.
Repo topology and linked repos are declared in `agent-protocols.toml`.
For git/worktree cleanup, prefer `agent-protocols/scripts/repo_state.py` before manual pruning.
```

## Bootstrap Help

The package installer can vendor the package, create the config file, and
scaffold the docs skeleton for a repo. After scaffolding it also reports
whether common assistant instruction files already point at the vendored
protocols:

- `assistant entrypoint wiring: ok` means future agents should discover the
  package through an existing instruction surface.
- `assistant entrypoint wiring: action needed` means the package was installed,
  but the target repo still needs `AGENTS.md`, `CLAUDE.md`, or an equivalent
  assistant entrypoint to reference the protocols.

```bash
cd /path/to/target-repo
python3 /path/to/external/agent-protocols/scripts/install.py
```

Use an external checkout of `agent-protocols`, not a nested clone inside the
target repo.

Default behavior:

- detect the current repo root when run inside a git repo
- discover sibling repos in the surrounding workspace
- ask for confirmation before writing

For automation:

```bash
cd /path/to/target-repo
python3 /path/to/external/agent-protocols/scripts/install.py --yes
```

To keep the install strictly single-repo:

```bash
cd /path/to/target-repo
python3 /path/to/external/agent-protocols/scripts/install.py --yes --skip-workspace-discovery
```

For linked repos that should resolve relative to the git common root instead of
the config file location:

```bash
cd /path/to/orchestrator-repo
python3 /path/to/external/agent-protocols/scripts/install.py --target . --repo-id my-repo --linked-repo workspace@git_common_root=../workspace
```

To print short assistant pointer snippets after scaffolding:

```bash
python3 /path/to/external/agent-protocols/scripts/install.py --yes --print-assistant-snippets
```

To print a rendered copy-paste adoption prompt for a coding assistant:

```bash
python3 /path/to/external/agent-protocols/scripts/install.py --yes --print-adoption-prompt
```

## Upstreaming Rule

If a change to the vendored package is generic, upstream it to the package repo
instead of keeping it as a permanent consumer-only fork.

Repo-specific protocol extensions belong in `agent-protocols/local/`. The
installer creates that directory when missing and preserves its contents during
package refreshes.

## Multi-Repo Guidance

In multi-repo workspaces, treat one repo as the orchestration root for
cross-repo workstreams and live status.

- That root repo should list sibling repos in `agent-protocols.toml`.
- Run the installer from that root repo when setting up cross-repo authority.
- That root repo should keep coordinated multi-repo plan families under
  `docs/plans/cross-repo/`.
- Temporary cross-repo scratch notes should still start in `docs/temp/` and be
  promoted only if they become durable artifacts.
- Sibling repos that are also worked on independently should vendor their own
  `agent-protocols/` copy too.
- Those sibling local installs should normally use `--skip-workspace-discovery`.
- Local plans should stay in each repo's default `docs/plans/` taxonomy.
- Those sibling local copies do not clash with the orchestration copy unless
  multiple repos start acting as cross-repo authorities at the same time.
- Cross-repo plans should live in one place, not be duplicated across sibling
  repos.
