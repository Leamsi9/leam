# Scoped document tools

Companion may use IronClaw's builtin read_file, list_dir, glob, grep, write_file,
document_edit and html_to_pdf. These tools use runtime-scoped mounts. Deployment
must bind the workspace to a private documents directory, retain existing landed
attachments and verify raw host paths and traversal remain denied. A capability
permission is not permission to modify Leam's source, runtime configuration or
credentials. Software engineering continues through the existing Codex handoff.

read_file supports text and runtime-supported PDF/Office extraction. document_edit
supports the pinned runtime's structural DOCX/XLSX/PPTX edits and writes a separate
copy; it is not a general Office editor. html_to_pdf renders the runtime's supported
basic HTML subset, without CSS or browser rendering, to a new PDF. Existing source
files are retained during deployment. Shell, apply_patch and generic HTTP are not
granted by this feature.

GET /api/companion/threads/{thread_id}/files/content?path=/workspace/... downloads
through the pinned runtime's owner/thread authorization and confined project-file
route. The main Leam owner session is mandatory. Paths must be normalized absolute
workspace file paths; traversal, raw host paths and control characters fail before
runtime dispatch. Runtime errors do not disclose response bodies or credentials.
Downloads are bounded to25MiB and30seconds, always application/octet-stream with
attachment disposition, no-store, nosniff and sandbox CSP. There is no browser write
endpoint. Companion supplies a download link only after a successful tool receipt.

Changing the private workspace binding preserves prior landed attachments by copy,
with a private file-hash receipt. Rollback preserves both directories and restores
the previous launcher/permission snapshot. Runtime-produced files are not yet in
Leam's product-only backup archive; preserve the document workspace separately until
that backup integration is delivered. No claim of document-backup coverage is made.

Companion saved and streaming Markdown also accepts raw `/workspace/...` file
links. The client decodes the path once, rejects traversal, control characters,
backslashes, query/fragment suffixes and invalid thread identity, then links the
existing authenticated thread download route. Other URLs retain ReactMarkdown’s
default URL filtering. This display conversion adds no runtime filesystem grant.
