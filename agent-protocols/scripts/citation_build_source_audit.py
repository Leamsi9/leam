#!/usr/bin/env python3
"""Build a citation source-audit ledger from BibTeX metadata.

The generated ledger is conservative. Network lookups can establish online
metadata evidence, but source-content and quote statuses should come from manual
support ledgers or human review. Use citation_check_audit_ledger.py for the
deterministic gates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

from citation_inventory import BibEntry, clean_latex, parse_all_bib_entries, parse_fields


USER_AGENT = "agent-protocols-citation-audit/0.1"
URL_TITLE_SIMILARITY_THRESHOLD = 0.50


@dataclass(frozen=True)
class AuditRow:
    key: str
    citation: str
    locator: str
    metadata: str
    content: str
    quote: str
    evidence: str
    notes: str


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_latex(value).lower()).strip()


def token_similarity(left: str, right: str) -> float:
    left_tokens = set(normalize(left).split())
    right_tokens = set(normalize(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def fetch_json(url: str, timeout: int = 20) -> tuple[int | None, Any | None, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response), ""
    except Exception as exc:  # noqa: BLE001 - audit generation should keep going.
        return None, None, str(exc)


def fetch_page_title(url: str, timeout: int = 20) -> tuple[int | None, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            chunk = response.read(300_000)
        if "text/html" not in content_type:
            return status, "", content_type
        text = chunk.decode("utf-8", errors="ignore")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if not title_match:
            title_match = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                text,
                re.IGNORECASE | re.DOTALL,
            )
        title = unescape(" ".join(title_match.group(1).split())) if title_match else ""
        return status, title, ""
    except Exception as exc:  # noqa: BLE001 - audit generation should keep going.
        return None, "", str(exc)


def arxiv_metadata(
    entry: BibEntry,
    fields: dict[str, str],
    arxiv_id: str,
    evidence: str,
    locator: str,
) -> AuditRow:
    api_url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    request = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
    title = fields.get("title", "")
    year = fields.get("year", "")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            xml_text = response.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry_node = root.find("atom:entry", ns)
        if entry_node is None:
            return AuditRow(entry.key, f"{title} ({year})", locator, "blocked", "todo", "not_applicable", evidence, "arXiv lookup returned no entry")
        online_title = " ".join((entry_node.findtext("atom:title", "", ns) or "").split())
        published = entry_node.findtext("atom:published", "", ns)
        online_year = published[:4] if published else ""
        similarity = token_similarity(title, online_title)
        year_ok = (not year) or (not online_year) or year == online_year
        metadata = "verified" if similarity >= 0.75 and year_ok else "needs_review"
        notes = f"arXiv title similarity {similarity:.2f}; online year {online_year or 'n/a'}"
        return AuditRow(entry.key, f"{title} ({year})", locator, metadata, "todo", "not_applicable", evidence, notes)
    except Exception as exc:  # noqa: BLE001 - audit generation should keep going.
        return AuditRow(entry.key, f"{title} ({year})", locator, "blocked", "todo", "not_applicable", evidence, f"arXiv lookup failed: {exc}")


def doi_metadata(entry: BibEntry, fields: dict[str, str]) -> AuditRow:
    doi = fields["doi"].strip()
    arxiv_match = re.search(r"10\.48550/arXiv\.([0-9.]+)", doi, re.IGNORECASE)
    if arxiv_match:
        return arxiv_metadata(entry, fields, arxiv_match.group(1), f"https://doi.org/{doi}", f"doi:{doi}")

    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    status, payload, error = fetch_json(url)
    title = fields.get("title", "")
    year = fields.get("year", "")
    if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
        message = payload.get("message", {})
        online_title = (message.get("title") or [""])[0]
        published = (
            message.get("published-print")
            or message.get("published-online")
            or message.get("published")
            or {}
        )
        year_parts = published.get("date-parts") or []
        online_year = str(year_parts[0][0]) if year_parts and year_parts[0] else ""
        similarity = token_similarity(title, online_title)
        year_ok = (not year) or (not online_year) or year == online_year
        metadata = "verified" if similarity >= 0.75 and year_ok else "needs_review"
        notes = f"Crossref title similarity {similarity:.2f}; online year {online_year or 'n/a'}"
        return AuditRow(entry.key, f"{title} ({year})", f"doi:{doi}", metadata, "todo", "not_applicable", f"https://doi.org/{doi}", notes)
    return AuditRow(entry.key, f"{title} ({year})", f"doi:{doi}", "blocked", "todo", "not_applicable", f"https://doi.org/{doi}", f"Crossref lookup failed: {error}")


def url_metadata(entry: BibEntry, fields: dict[str, str]) -> AuditRow:
    url = fields["url"].strip()
    title = fields.get("title", "")
    year = fields.get("year", "")
    arxiv_match = re.search(r"arxiv\.org/abs/([0-9.]+)", url, re.IGNORECASE)
    if arxiv_match:
        return arxiv_metadata(entry, fields, arxiv_match.group(1), url, f"url:{url}")

    status, page_title, error = fetch_page_title(url)
    if status is not None and 200 <= status < 400:
        similarity = token_similarity(title, page_title)
        metadata = "verified" if page_title and similarity >= URL_TITLE_SIMILARITY_THRESHOLD else "online"
        notes = f"HTTP {status}; page title similarity {similarity:.2f}; page title: {page_title or 'n/a'}"
    else:
        metadata = "blocked"
        notes = f"HTTP lookup failed: {error}"
    return AuditRow(entry.key, f"{title} ({year})", f"url:{url}", metadata, "todo", "not_applicable", url, notes)


def no_locator_metadata(entry: BibEntry, fields: dict[str, str]) -> AuditRow:
    title = fields.get("title", "")
    year = fields.get("year", "")
    return AuditRow(entry.key, f"{title} ({year})", "no-direct-locator", "todo", "todo", "not_applicable", "", "Needs publisher, catalogue, DOI, URL, ISBN, or manual metadata override")


def load_toml_rows(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    rows: dict[str, dict[str, str]] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        rows[key] = {str(field): str(field_value) for field, field_value in value.items()}
    return rows


def apply_metadata_override(row: AuditRow, override: dict[str, str]) -> AuditRow:
    return AuditRow(
        key=row.key,
        citation=override.get("citation", row.citation),
        locator=override.get("locator", row.locator),
        metadata=override.get("metadata", row.metadata),
        content=override.get("content", row.content),
        quote=override.get("quote", row.quote),
        evidence=override.get("evidence", row.evidence),
        notes=override.get("notes", row.notes),
    )


def apply_content_support(row: AuditRow, support: dict[str, str]) -> AuditRow:
    note = support.get("note", "").strip()
    notes = row.notes
    if note:
        notes = f"{notes} Content check: {note}"
    return AuditRow(
        key=row.key,
        citation=row.citation,
        locator=row.locator,
        metadata=row.metadata,
        content=support.get("status", row.content),
        quote=support.get("quote", row.quote),
        evidence=support.get("evidence") or row.evidence,
        notes=notes,
    )


def build_row(entry: BibEntry) -> AuditRow:
    fields = parse_fields(entry)
    if fields.get("doi"):
        return doi_metadata(entry, fields)
    if fields.get("url"):
        return url_metadata(entry, fields)
    return no_locator_metadata(entry, fields)


def manual_override_base(entry: BibEntry, override: dict[str, str]) -> AuditRow | None:
    if "metadata" not in override:
        return None
    fields = parse_fields(entry)
    title = fields.get("title", "")
    year = fields.get("year", "")
    if fields.get("doi"):
        locator = f"doi:{fields['doi'].strip()}"
        evidence = f"https://doi.org/{fields['doi'].strip()}"
    elif fields.get("url"):
        locator = f"url:{fields['url'].strip()}"
        evidence = fields["url"].strip()
    else:
        locator = "no-direct-locator"
        evidence = ""
    return AuditRow(
        key=entry.key,
        citation=f"{title} ({year})",
        locator=locator,
        metadata=override["metadata"],
        content="todo",
        quote="not_applicable",
        evidence=evidence,
        notes="Manual metadata override.",
    )


def build_rows(
    bib_paths: list[Path],
    metadata_overrides: dict[str, dict[str, str]],
    content_support: dict[str, dict[str, str]],
    limit: int | None = None,
    sleep: float = 0.2,
) -> list[AuditRow]:
    entries = parse_all_bib_entries(bib_paths)
    rows: list[AuditRow] = []
    for entry in entries[:limit]:
        override = metadata_overrides.get(entry.key, {})
        row = manual_override_base(entry, override) or build_row(entry)
        if override:
            row = apply_metadata_override(row, override)
        if entry.key in content_support:
            row = apply_content_support(row, content_support[entry.key])
        rows.append(row)
        time.sleep(sleep)
    return rows


def render(rows: list[AuditRow]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        for label, status in {
            "metadata": row.metadata,
            "content": row.content,
            "quote": row.quote,
        }.items():
            key = f"{label}={status}"
            counts[key] = counts.get(key, 0) + 1

    lines = [
        "# Citation Source Audit",
        "",
        "Generated working ledger for citation verification.",
        "",
        "Status values:",
        "",
        "- `verified`: checked and supported by evidence.",
        "- `online`: URL resolves, but exact metadata attribution still needs review.",
        "- `needs_review`: lookup resolves but metadata needs manual review.",
        "- `blocked`: lookup failed, contradicted local metadata, or source support failed.",
        "- `todo`: not yet checked.",
        "- `not_applicable`: this level does not apply to the row.",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status in sorted(counts):
        lines.append(f"| {status} | {counts[status]} |")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Key | Citation | Locator | Metadata | Content | Quote | Evidence | Notes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        cells = [
            row.key,
            row.citation,
            row.locator,
            row.metadata,
            row.content,
            row.quote,
            row.evidence,
            row.notes,
        ]
        escaped = [cell.replace("|", r"\|").replace("\n", " ") for cell in cells]
        lines.append("| " + " | ".join(escaped) + " |")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bib", action="append", type=Path, required=True)
    parser.add_argument("--metadata-overrides", type=Path)
    parser.add_argument("--content-support", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=0.2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows = build_rows(
            bib_paths=args.bib,
            metadata_overrides=load_toml_rows(args.metadata_overrides),
            content_support=load_toml_rows(args.content_support),
            limit=args.limit,
            sleep=args.sleep,
        )
        rendered = render(rows)
    except Exception as exc:  # noqa: BLE001 - CLI should report audit failure.
        print(f"citation audit build error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
