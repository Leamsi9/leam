# Assistant Adoption Prompt

Copy and paste this prompt into a coding assistant when you want it to adopt or
refresh this package in another repo.

```text
You are integrating the reusable `{{VENDOR_DIR}}/` package into this repo.

Work in the repo directly and make the changes yourself. Do not stop at advice.

Requirements:

1. Inspect the repo first and respect its existing structure, docs style, and
   test conventions.
2. Vendor or refresh `{{VENDOR_DIR}}/` from the canonical package if needed.
   - If `{{VENDOR_DIR}}/scripts/install.py` is available, prefer using it.
   - Prefer running the installer from an external package checkout or from an
     already-vendored package copy.
   - If you are already inside the target repo, run it there without `--target`
     so it detects the repo root automatically.
   - If this repo is the cross-repo orchestration root, run the installer from
     this repo and keep the linked sibling repos here.
   - If this repo is only being adopted for standalone local work, prefer
     `--skip-workspace-discovery`.
   - Do not leave a nested git clone of `{{VENDOR_DIR}}/` inside the target repo.
3. Create or update `{{CONFIG_FILE}}` for this repo.
   - Primary repo id: `{{REPO_ID}}`
   - Primary branch: `{{MAIN_BRANCH}}`
   - If linked repos are obvious from local context, record them.
   - If this repo is the cross-repo orchestration root, linked repos should be
     included here.
   - If linked repos are ambiguous, do not invent them; leave the config rooted
     to the main repo only and call out the gap.
4. Point live instruction surfaces such as `AGENTS.md`, `CLAUDE.md`, or the
   repo's equivalent assistant entrypoints at
   `{{VENDOR_DIR}}/substantive-work-protocol.md`.
5. Add or refresh the repo-local planning surfaces expected by the package:
   - `docs/plans/README.md`
   - `docs/plans/plans-index.md`
   - `docs/plans/cross-repo/README.md` if this repo is or may become a
     cross-repo orchestration root
   - `docs/live-workstream-status.md`
   - `docs/temp/README.md`
   - `docs/proposals/README.md` only if the repo keeps proposal logs
   - `docs/adr/pending/README.md` if the repo keeps pending ADRs
6. Do not duplicate the canonical protocol docs outside `{{VENDOR_DIR}}/`.
   Repo-local docs should point back to the vendored package instead.
7. Put repo-specific protocol extensions in `{{VENDOR_DIR}}/local/`.
   - Files under `{{VENDOR_DIR}}/local/` are versioned by the consuming repo.
   - Package refreshes should preserve those files.
   - Generic protocol improvements should be upstreamed to the package root.
8. Treat `{{VENDOR_DIR}}/skills/` as package-owned reusable assistant skills.
   - If this repo has a native skill surface, project or copy package-owned
     skills from `{{VENDOR_DIR}}/skills/` instead of editing a separate fork.
   - Keep product-specific or repo-specific skills outside the package.
   - For git/worktree cleanup, prefer
     `python3 {{VENDOR_DIR}}/scripts/repo_state.py --repo . --json` before
     manual pruning or branch deletion.
9. If the change is substantive, follow `{{VENDOR_DIR}}/substantive-work-protocol.md`:
   create the smallest durable plan family that proves the work, keep phases
   gated, and do not claim completion without the checker passing.
   - If the work is local to this repo, place the plan in the default
     `docs/plans/` taxonomy.
   - If completion or acceptance depends on coordinated work across 2+ repos
     and this repo is the orchestration root, place the canonical plan under
     `docs/plans/cross-repo/`.
   - Do not use `cross-repo/` just because another repo is mentioned as
     background context.
   - Put temporary markdown that is not meant to survive in `docs/temp/` first,
     following `{{VENDOR_DIR}}/temp-doc-protocol.md`.
   - Delete temporary ledgers, inventories, and draft manifests at closeout
     after folding any durable evidence into the plan, ADR, history note, or
     commit message.
   - Use a stable feature slug across branches, worktrees, and temp docs when
     the workstream has a clear center, for example
     `feature/<feature-slug>/<slice>-YYYY-MM-DD` and
     `docs/temp/<feature-slug>/<topic>-YYYY-MM-DD.md`.
10. Run the narrowest validation needed for the repo plus the package checks that
   apply:
   - `python3 {{VENDOR_DIR}}/scripts/check_gated_plan.py ...`
   - `python3 {{VENDOR_DIR}}/scripts/repo_state.py --repo . --json`
   - `python3 {{VENDOR_DIR}}/scripts/workstream.py sync-index --confirm`
   - `python3 -m py_compile {{VENDOR_DIR}}/scripts/check_gated_plan.py {{VENDOR_DIR}}/scripts/workstream.py {{VENDOR_DIR}}/scripts/repo_state.py {{VENDOR_DIR}}/scripts/install.py`
11. Summarize:
   - what changed
   - what was verified
   - whether the plan placement is local or `cross-repo`
   - whether this repo is the orchestration root or a standalone sibling repo
   - whether temporary docs were promoted, preserved, or deleted
   - any linked repos or assistant entrypoints that still need human input

Keep the repo-specific overlay thin. If you discover a generic improvement to
`{{VENDOR_DIR}}/`, note that it should be upstreamed rather than kept as a
consumer-only fork.
```
