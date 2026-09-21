# Test coverage and evidence limits

Backend tests in `tests/` drive authenticated routes and domain operations with isolated stores and controlled adapters. They cover session/origin enforcement, durable delivery and retries, approval receipts, entity revisions, calendar conflicts, OAuth/PKCE, private attachment handling, backup/restore and recovery.

`test_memory_projection.py` and `test_memory_context.py` cover bounded snippets, revision-pinned detail reads, exact user text, receipt-proven legacy projection and malformed/mismatched references. `test_item_chat.py` and `test_item_chat_concurrency.py` cover per-item bindings, creation retries, fresh references and deletion. These use synthetic records.

Shared IDE tests cover the compatibility protocol and fail-closed identity/version boundary. They must use explicitly configured synthetic thread identifiers. They do not authorize reading or operating a developer's personal conversation.

Browser tests under `apps/web/tests/` load real components. Many use controlled route, speech or provider fixtures; these validate UI behavior, isolation and races. Live scenarios require a separately prepared test installation and may invoke models or change records/settings. The default Playwright configuration includes all files, so do not run the whole browser suite against a personal installation. Choose a feature-specific configuration or explicitly selected fixture files after reviewing their setup.

Automated checks are distinct from actual provider and device acceptance. In particular, synthetic speech engines do not establish physical microphone/synthesis support; generated push events do not establish browser-closed delivery; fake calendar/OAuth responses do not establish a real account grant. Fresh-install runtime verification, exact deployed artifact identity and the full product acceptance contract remain separate gates.

Useful starting commands:

```sh
.venv/bin/pytest -q
npm run build --prefix apps/web
```

Consult the relevant subsystem contract and test file before running a live test. Keep captured private evidence outside Git and tie each deployment claim to the artifact actually observed.

`test_mobile_restore.py`, `test_recovery_bindings.py`, `test_recovery_integration.py`, `test_candidate_rollout.py` and `test_restore_automation.py` cover protected descriptor replacement, archive/vault identity, retained generations, automation holds, fresh review before resume, stale previews, unfinished operations, failed-health rollback, cancellation and operation-lock ownership. They use isolated data and controlled service/sender adapters. The matching mobile restore and automation browser fixtures cover review, confirmation, receipt reconciliation and logout races. These checks do not perform an installed restore or establish physical-device delivery; installation adoption and restore/rollback acceptance remain separate operator tasks.

`test_agenda.py`, `test_agenda_email.py`, `test_calendar_visibility.py` and `test_calendar_instant.py` cover daily windows, current per-turn references, frozen retry context, local-only source choices and exact-source hiding. `test_email.py` covers separate mailbox consent, encrypted last-good snapshots and grant removal; controlled provider fixtures are not live-mail acceptance. `test_backup_email_compatibility.py` covers historical archives without the optional mail cache and present-cache vault validation.

`test_ticket_followup.py`, `test_shared_catalog.py` and `test_shared_stream_renewal.py` cover active-turn steering and discovery/stream continuity. Feature-specific browser configurations cover daily planning, mail setup, coding continuity, ticket follow-ups and install controls. The install fixtures verify UI capability branches, not acceptance by every physical browser or platform.
