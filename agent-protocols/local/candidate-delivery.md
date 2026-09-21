# Candidate delivery and post-deployment QA

Applies to the isolated Leam MVP candidate, as explicitly requested by the user
on 2026-09-20. This repo-local overlay changes delivery order; it does not change
the vendored agent-protocols package or acceptance requirements.

Independent feature slices may be implemented in parallel in dedicated worktrees.
Deliver the smallest usable increment once its required dependencies build. Build
and deploy it to the candidate before running the full QA/review/phase gates.
Long-running CI must not block candidate CD or user UAT. Keep one integration owner
and a separate QA worker where concurrency allows. Do not wait for every feature
to finish before deploying an unrelated usable slice.

Record source commit, artifact identity, deployment time, rollback artifact, and
QA status for each deployment. Distinguish available / QA pending, QA passed, and
QA failed. A deployed increment is available for UAT, not automatically accepted.
Run targeted caller-level, browser/mobile and regression checks after deployment;
continue required independent review and phase checks before claiming completion.
A reproduced failure revokes acceptance: repair and redeploy promptly, or restore
the previous candidate if the failure prevents useful testing. Recheck the affected
behavior against the new deployment identity.

Preserve existing installations and user data. Candidate deployment authorization
does not authorize destructive migration or replacement of the old installation.
Keep authentication and secret handling intact. This is not permission to knowingly
publish a broken build or ignore a known data-loss/security defect. Archive or
back up mutable state before a migration that requires it.

The user-authorized parallel implementation and post-deployment QA order supersede
the base protocol's serial-phase and verification-before-deployment ordering for
this candidate only. All applicable acceptance gates remain required for final
completion. Ordinary companion conversations do not invoke engineering protocols.
