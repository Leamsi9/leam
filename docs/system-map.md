# System map

| Area | Owner and entry points |
|---|---|
| Browser shell and coding | `apps/web/src/main.tsx`, `leam_api/app.py`, `leam_api/codex.py` |
| Companion and item chats | `apps/web/src/companion.tsx`, `apps/web/src/item-chat.tsx`, `leam_api/companion.py`, `leam_api/item_chat.py` |
| Bounded model context | `leam_api/memory_projection.py`, `leam_api/companion_context.py`; canonical records remain complete |
| Commitments and capacities | `apps/web/src/today.tsx`, `leam_api/commitments.py`; revisions, progress and reviewed removal |
| Proposals and handoffs | `leam_api/proposals.py`, `leam_api/coding_handoff.py`, shared UI review components |
| Accounts and calendars | `leam_api/accounts.py`, `vault.py`, `calendar.py`, `calendar_actions.py`, `calendar_changes.py` |
| Reminders, push and routines | `leam_api/reminders.py`, `push.py`, `routines.py`, `routine_events.py` |
| Attachments | `leam_api/attachments.py`, `apps/web/src/attachments.tsx`; private bytes and receipt-bound references |
| Voice | `apps/web/src/voice/`; shared dictation, correlated playback and device-local preferences |
| State and recovery | `leam_api/store.py`, `backups.py`, `recovery.py`; SQLite state, private backups and separate recovery process |
| Runtime adapter | `leam_api/ironclaw.py`; pinned loopback IronClaw execution |
| Optional shared IDE session | `leam_api/shared_coding.py`, `shared_session*.py`, `shared_controls.py`, `shared_decisions.py` |
| Engineering policy | `leam_api/coding_policy.py`, pinned `agent-protocols/` and lock |
| Browser API | `contracts/leam.openapi.json` |

Leam owns personal state and domain operations. Specialized providers remain authoritative for their external records. IronClaw executes Companion runs; Codex owns coding sessions. Publishing this source does not publish any installation's records, keys or transcripts. The retained Remember import backend has no mounted Settings UI in the current MVP.
