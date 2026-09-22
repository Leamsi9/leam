# Private generated resources

Resources extends the existing Artifacts domain and immutable `artifact:<id>`
settings records. Existing Markdown/HTML URLs and records remain readable, listed
and covered by product backup/restore. No filesystem path, remote URL, provider
credential or arbitrary fetch is accepted over HTTP or companion tools.

`POST /api/artifacts` accepts `id,title,kind`, exactly one of UTF-8 `content` for
markdown/html/text or `contentBase64` for binary, optional `filename`, and optional
`source:{surface,threadId,turnId,toolCallId}` with bounded strings. Unknown fields
are rejected. Source references are caller-supplied provenance, not authorization
or evidence that a linked operation succeeded. Kind is markdown/html/text/png/jpeg/
webp/pdf/docx/xlsx/pptx. SVG, legacy/macro Office and unknown binary types are rejected.
Raster signatures, PDF framing and existing bounded Office package validation are
shared with attachments; signatures are not malware scanning or full image decode.

IDs use lowercase letters/digits/hyphens, max96. Existing IDs are immutable. Exact
same bytes/title/kind/filename/source return the original publication; differing
payload returns409. Legacy records normalize missing filename/source so old exact
retries still work. BEGIN IMMEDIATE serializes quota checks and insertion. Raw
payload maximum10MiB/file,128MiB aggregate,1000records; base64 overhead is not
counted as user file size. Existing records are never deleted to fit a quota.
HTTP envelope is bounded15MiB on this exact authenticated route; all unrelated
body limits stay unchanged. Quota rejection is413; invalid payload422.

`GET /api/artifacts?q=&kind=&cursor=&limit=` returns
`{items,nextCursor,total,storage:{bytes,maxBytes,maxFileBytes}}`, default30/max100.
Search matches title/explicit filename case-insensitively for SQLite-supported
case folding. Kind accepts exact formats and `image`/`document` families. Keyset
pagination orders publishedAt descending, then immutable ID descending. Newer
publications do not shift already-issued cursor pages. Cursor is opaque bounded
base64 JSON, no raw SQL; malformed cursors fail422. Contents never enter list rows.

Metadata: `id,title,kind,filename,mimeType,bytes,sha256,publishedAt,source,previewUrl,
downloadUrl,url`. publishedAt uses Unix seconds. `url` remains `/?artifact=<id>`.
`GET /api/artifacts/<id>` adds content only for Markdown/plain text. HTML source and
base64 bytes remain absent. HTML preview uses the existing sandbox CSP allowing
inline scripts/styles but no same-origin privilege, network subresources, forms
or connections; the frontend iframe must also retain `sandbox="allow-scripts"`.
It is not a guarantee that every user-initiated external navigation is impossible.
Raster previews return explicit image MIME with nosniff and sandbox CSP. PDF and
Office are download-only; no embedded document execution. All downloads are
attachment/octet-stream, with bounded safe filenames and UTF-8 disposition support.
All routes retain installation authentication, mutation Origin checks and no-store.

`Artifacts.publish(...,content_base64=...,filename=...,source=...)`, `.list(...)`,
`.get(...)` and `.metadata(...)` are shared domain operations. CLI
`python -m leam_api.artifacts --data-dir <private-dir> --id <id> --title <title>
--kind <kind> --file <explicit-local-file>` can publish reviewed generated files;
optional --filename/--source-surface/--source-thread/--source-turn retain provenance.
Only this operator CLI accepts local paths; they are never persisted as source.
Publishing existing image bytes does not provide an image-generation engine.
