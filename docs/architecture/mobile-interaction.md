# Mobile interaction contract

Conversation and primary content get the available width and height. Secondary
navigation belongs in More; metadata, technical evidence, routine tool activity
and infrequently used settings start collapsed. Actionable errors, approvals,
questions and delivery uncertainty remain visible. Closed disclosure must not
submit, approve, erase drafts or start a model session.

Mobile navigation has Companion, Today, Coding and More. Desktop exposes all
sections. Native modal dialogs contain focus, have a visible close control,
respond to Escape when no save is in flight, and return focus to their opener.
Chat pages use one bounded flex viewport shared by banners, conversation and
composer; a banner must never push Send/Stop behind bottom navigation. Long
transcripts and proposals scroll within the content area. Message text stays
readable, and code/tables scroll locally without narrowing the page.

VoiceComposer is shared across chat modules. Every new chat should include
editable dictation, conversational voice and read-aloud via the shared adapters.
The module owns durable submission and exact reply correlation; voice must not
infer its answer from the latest message or replay uncertain input. Hiding or
leaving a conversation stops microphone/playback and preserves accepted requests.

Updates distinguish unread highlighting from QA/UAT stage colours. Mark as read
acknowledges the observed ticket revision; a later change becomes unread. Ticket
chat starts collapsed and uses a dedicated Codex session with ticket context
separate from the user's exact message. It does not silently steer the build
session, approve UAT or mutate a ticket merely because the panel opened.

Audit (2026-09-20): original360px navigation clipped its last item; Coding left
195px for transcript; Settings exceeded5000px. Four-target navigation, Settings
disclosure, compact chat headers, grouped activity, composer layout and accessible
forms address the main causes. Browser checks cover360/390/768/1440 and explicit
button hit bounds; physical Android/iOS keyboard, address-bar and speech behavior
remain device acceptance rather than inferred from viewport simulation.

## PWA shell delivery
The production build stamps a worker with the exact public shell and content
integrities. Install precaches HTML, JS, CSS, manifest and icons as one coherent
release; a missing/mismatched asset fails installation and preserves the active
worker. Hashed assets use cache-first. Navigations try the network and fall back
to the matching cached shell, including query-based views. API traffic and private
records never enter Cache Storage. The current and one previous shell are retained.
A waiting worker activates only on explicit Reload Leam; no forced draft-discarding
reload. App installation is offered in collapsed Settings when the browser supplies
an install prompt, otherwise browser-menu instructions remain available. Offline
shell availability does not establish offline authentication or model connectivity.
