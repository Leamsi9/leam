# Security-checked public source snapshot

Scope: explicitly authorized public snapshot and security/privacy workflow | Base: substantive | Override: operator asks for security/privacy before push, no product-test gate. Existing candidate deployment → notify → QA workflow remains separate. Root owns pushing; this worktree cannot publish itself.

Dedicated branch publication/audited-20260922 from existing public main 98d2a4c. Preserve public lineage and hardened ignore rules; copy source from exact final candidate identified in docs/public-source.json, omit private engineering plans/evidence, redact documentation-only personal home paths, and never rewrite functional code by text substitution. Add a minimal offline scanner and least-privilege pinned-checkout CI. Authoritative source map distinguishes public commit from deployed identity.

Gate: scanner across tracked staged snapshot plus reachable public history and full source-diff review before parent pushes. No build/test pass is claimed here and no user installation is mutated. Root records final deployment mapping and publication outcome.
