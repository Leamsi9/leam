# Public product source update

Base: public commit 545b8e2. Dedicated branch `prepare/public-product-update-20260921`. Follow substantive protocol 0.0.19 and the source publication contract.

1. Export only reviewed application source, synthetic tests, contracts and app artwork into public history. Preserve public installation guidance, licenses/notices, protocol hashes and runtime pin. Private source identities, per-file hashes and transformations stay in a private receipt.
2. Audit file contents, image metadata and commit metadata for private data. Run exported backend callers, client build and selected controlled browser fixtures.
3. Obtain independent review, record a clean checkpoint and phase gate. Publishing and runtime-source availability are separate operator actions; this plan does not authorize pushing or changing services.

No private plans or Git history are imported. The existing runtime pin remains unchanged. No original-code license is added: third-party licenses remain authoritative and a product license remains an owner decision. Source verification is not deployment or actual account/device acceptance.

Source verification: 62 backend callers and 43 controlled browser callers passed; the client build passed with the existing large-chunk advisory. All production source/assets match the reviewed export source. The only test transform replaces an installation-specific screenshot destination with Playwright-owned output. Pattern/known-value scans found no private content, and vendored protocol hashes remain unchanged. The master artwork retains standard signed content provenance. Independent publication review and runtime-source availability remain separate final steps.
