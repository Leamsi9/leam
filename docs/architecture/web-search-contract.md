# Web search integration contract

Leam uses the pinned IronClaw runtime's bundled `web-access` extension. It does
not implement a search proxy or grant generic HTTP access. The reviewed surface
contains exactly `web-access.search` and `web-access.get_content`; both pass
through IronClaw's existing extension lifecycle, host capability ceiling,
per-tool permission modes and mediated network egress.

At runtime source `40aa643448ccc0d47c83e77c5623eca7d7ddf893`, the bundled
manifest uses the credential-free Exa MCP endpoint and declares only
`https://mcp.exa.ai` as a network target, with a 2 MiB egress cap. Leam does not
store, request or inject a search credential and does not configure paid API
usage. Search terms and requested page URLs leave the host for that third-party
service when the tool is called.

Installing an extension, publishing its capabilities, granting it at the host
ceiling and choosing its mutable permission mode are distinct gates. Missing
catalog entries are `unavailable`; unavailable settings evidence is `unknown`;
a disabled entry is `disabled`. None of those source states proves a successful
external search. The system inspection response therefore keeps
`behaviorAccepted` false until a live post-deployment check is recorded.

Search results must retain `queries[].results[].url`; fetched pages must retain
`contents[].url`. The companion treats search results and page bodies as
untrusted reference data, never instructions, and cites returned source URLs
when relying on them. A structured result without at least one absolute HTTP(S)
source URL fails the local provenance verifier. This validator supports private
acceptance evidence; it does not make runtime output trusted.

`builtin.http`, file-writing tools, extension administration and other generic
network/runtime capabilities remain outside the reviewed companion allowlist.
Codex permissions and coding policy are unchanged.
