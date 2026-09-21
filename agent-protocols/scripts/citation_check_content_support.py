#!/usr/bin/env python3
"""Check or scaffold citation content-support ledgers for every BibTeX key."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from citation_inventory import BibEntry, parse_all_bib_entries


PASSING_STATUSES = {"verified", "not_applicable"}
DEFAULT_STATUS = "todo"
SCHEMAS = {"basic", "substance"}
SUBSTANCE_FIELDS = (
    "support_type",
    "claim_context",
    "source_locator",
    "evidence_note",
    "risk",
)
SUPPORT_TYPES = {
    "direct_support",
    "contextual_support",
    "method_support",
    "partial_support",
    "misplaced",
    "unsupported",
    "blocked",
    "not_applicable",
}
PASSING_SUPPORT_TYPES = {"direct_support", "contextual_support", "method_support"}
RISK_LEVELS = {"low", "medium", "high", "blocked", "not_applicable"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bib", action="append", type=Path, required=True)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "Create or update the support ledger before checking it. Existing rows "
            "are preserved, and missing BibTeX keys are seeded with status=todo."
        ),
    )
    parser.add_argument(
        "--merge-from",
        action="append",
        type=Path,
        default=[],
        help=(
            "Existing content-support ledger to use as seed evidence before the "
            "--support file is applied. May be passed more than once."
        ),
    )
    parser.add_argument(
        "--schema",
        choices=sorted(SCHEMAS),
        default="basic",
        help=(
            "Ledger schema to enforce. 'basic' requires status and note. "
            "'substance' also requires agent substance-review fields."
        ),
    )
    return parser.parse_args()


def load_support_payload(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    raw_payload = tomllib.loads(path.read_text(encoding="utf-8"))
    payload: dict[str, dict[str, str]] = {}
    for key, value in raw_payload.items():
        if isinstance(value, dict):
            payload[str(key)] = {
                str(field): str(field_value) for field, field_value in value.items()
            }
    return payload


def toml_string(value: str) -> str:
    return json.dumps(value)


def render_support_payload(
    entries: list[BibEntry],
    payload: dict[str, dict[str, str]],
    *,
    schema: str = "basic",
) -> str:
    lines = [
        "# Manual content-support ledger for the source_content gate.",
        "# Each row records why the source content supports the way the manuscript uses it.",
        "# The --update mode preserves existing evidence and seeds missing rows as todo.",
    ]
    if schema == "substance":
        lines.extend(
            [
                "# Substance schema fields make the agent relevance judgement auditable.",
                "# support_type: direct_support | contextual_support | method_support | partial_support | misplaced | unsupported | blocked | not_applicable",
                "# risk: low | medium | high | blocked | not_applicable",
            ]
        )
    lines.append("")
    for entry in entries:
        row = dict(payload.get(entry.key, {}))
        status = row.pop("status", DEFAULT_STATUS).strip() or DEFAULT_STATUS
        note = row.pop("note", "").strip()
        lines.append(f"[{entry.key}]")
        lines.append(f"status = {toml_string(status)}")
        lines.append(f"note = {toml_string(note)}")
        if schema == "substance":
            for field in SUBSTANCE_FIELDS:
                value = row.pop(field, "").strip()
                lines.append(f"{field} = {toml_string(value)}")
        for field in sorted(row):
            lines.append(f"{field} = {toml_string(row[field])}")
        lines.append("")
    return "\n".join(lines)


def update_support_ledger(
    entries: list[BibEntry],
    support_path: Path,
    merge_from: list[Path],
    *,
    schema: str = "basic",
) -> None:
    payload: dict[str, dict[str, str]] = {}
    for seed_path in merge_from:
        if seed_path.exists():
            payload.update(load_support_payload(seed_path))
    if support_path.exists():
        payload.update(load_support_payload(support_path))
    support_path.parent.mkdir(parents=True, exist_ok=True)
    support_path.write_text(
        render_support_payload(entries, payload, schema=schema),
        encoding="utf-8",
    )


def check_substance_fields(payload: dict[str, dict[str, str]]) -> list[str]:
    missing_fields: list[str] = []
    bad_support_type: list[str] = []
    bad_risk: list[str] = []
    inconsistent_support_type: list[str] = []

    for key, value in payload.items():
        for field in SUBSTANCE_FIELDS:
            if not str(value.get(field, "")).strip():
                missing_fields.append(f"{key}.{field}")

        support_type = str(value.get("support_type", "")).strip()
        risk = str(value.get("risk", "")).strip()
        status = str(value.get("status", "")).strip()

        if support_type and support_type not in SUPPORT_TYPES:
            bad_support_type.append(f"{key}.{support_type}")
        if risk and risk not in RISK_LEVELS:
            bad_risk.append(f"{key}.{risk}")
        if status == "verified" and support_type and support_type not in PASSING_SUPPORT_TYPES:
            inconsistent_support_type.append(f"{key}.{support_type}")
        if status == "not_applicable" and support_type and support_type != "not_applicable":
            inconsistent_support_type.append(f"{key}.{support_type}")

    messages: list[str] = []
    if missing_fields:
        messages.append("missing_substance_fields=" + ", ".join(sorted(missing_fields)))
    if bad_support_type:
        messages.append("bad_support_type_values=" + ", ".join(sorted(bad_support_type)))
    if bad_risk:
        messages.append("bad_risk_values=" + ", ".join(sorted(bad_risk)))
    if inconsistent_support_type:
        messages.append(
            "inconsistent_support_type_keys="
            + ", ".join(sorted(inconsistent_support_type))
        )
    return messages


def check_content_support(
    bib_paths: list[Path],
    support_path: Path,
    *,
    update: bool = False,
    merge_from: list[Path] | None = None,
    schema: str = "basic",
) -> tuple[bool, list[str]]:
    entries = parse_all_bib_entries(bib_paths)
    if update:
        update_support_ledger(entries, support_path, merge_from or [], schema=schema)
    if not support_path.exists():
        return False, [f"missing content support ledger: {support_path}"]

    bib_keys = {entry.key for entry in entries}
    payload = load_support_payload(support_path)
    support_keys = set(payload)

    messages: list[str] = []
    missing = sorted(bib_keys - support_keys)
    extra = sorted(support_keys - bib_keys)
    bad_status: list[str] = []
    missing_note: list[str] = []
    for key, value in payload.items():
        if not isinstance(value, dict):
            bad_status.append(key)
            continue
        status = str(value.get("status", "")).strip()
        note = str(value.get("note", "")).strip()
        if status not in PASSING_STATUSES:
            bad_status.append(key)
        if not note:
            missing_note.append(key)

    if missing:
        messages.append("missing_content_keys=" + ", ".join(missing))
    if extra:
        messages.append("extra_content_keys=" + ", ".join(extra))
    if bad_status:
        messages.append("bad_content_status_keys=" + ", ".join(sorted(bad_status)))
    if missing_note:
        messages.append("missing_content_note_keys=" + ", ".join(sorted(missing_note)))
    if schema == "substance":
        messages.extend(check_substance_fields(payload))

    if messages:
        return False, messages
    return True, [f"content_support_count={len(support_keys)}", "content_support_status=complete"]


def main() -> int:
    args = parse_args()
    try:
        passed, messages = check_content_support(
            args.bib,
            args.support,
            update=args.update,
            merge_from=args.merge_from,
            schema=args.schema,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should report audit failure.
        print(f"content support check error: {exc}", file=sys.stderr)
        return 1

    stream = sys.stdout if passed else sys.stderr
    for message in messages:
        print(message, file=stream)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
