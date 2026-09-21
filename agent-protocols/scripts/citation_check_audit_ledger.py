#!/usr/bin/env python3
"""Check citation source-audit markdown ledgers against BibTeX keys and statuses."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from citation_inventory import parse_all_bib_entries


LEVEL_COLUMNS = {
    "metadata": "Metadata",
    "content": "Content",
    "quote": "Quote",
}
PASSING_STATUSES = {
    "metadata": {"verified"},
    "content": {"verified", "not_applicable"},
    "quote": {"verified", "not_applicable"},
}


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current = []
    escaped = False
    for character in stripped:
        if character == "|" and not escaped:
            cells.append("".join(current).strip().replace(r"\|", "|"))
            current = []
            continue
        current.append(character)
        escaped = character == "\\" and not escaped
        if character != "\\":
            escaped = False
    cells.append("".join(current).strip().replace(r"\|", "|"))
    return cells


def parse_ledger(path: Path) -> dict[str, dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = None
    headers: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith("| Key |"):
            header_index = index
            headers = split_markdown_row(line)
            break
    if header_index is None:
        raise ValueError(f"ledger lacks row table header: {path}")

    rows: dict[str, dict[str, str]] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = split_markdown_row(line)
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        key = row.get("Key", "")
        if key:
            rows[key] = row
    return rows


def levels_to_check(level: str) -> list[str]:
    if level == "all":
        return ["metadata", "content", "quote"]
    return [level]


def check_ledger(bib_paths: list[Path], ledger_path: Path, level: str) -> tuple[bool, list[str]]:
    bib_keys = {entry.key for entry in parse_all_bib_entries(bib_paths)}
    rows = parse_ledger(ledger_path)
    row_keys = set(rows)
    messages: list[str] = []

    missing = sorted(bib_keys - row_keys)
    extra = sorted(row_keys - bib_keys)
    if missing:
        messages.append("missing_ledger_keys=" + ", ".join(missing))
    if extra:
        messages.append("extra_ledger_keys=" + ", ".join(extra))

    for selected in levels_to_check(level):
        column = LEVEL_COLUMNS[selected]
        allowed = PASSING_STATUSES[selected]
        bad = sorted(
            key
            for key, row in rows.items()
            if key in bib_keys and row.get(column, "").strip() not in allowed
        )
        if bad:
            messages.append(f"bad_{selected}_status_keys=" + ", ".join(bad))
        else:
            messages.append(f"{selected}_status=complete")

    failed = bool(missing or extra or any(message.startswith("bad_") for message in messages))
    return not failed, messages


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bib", action="append", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--level", choices=["metadata", "content", "quote", "all"], required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        passed, messages = check_ledger(args.bib, args.ledger, args.level)
    except Exception as exc:  # noqa: BLE001 - CLI should report audit failure.
        print(f"audit ledger check error: {exc}", file=sys.stderr)
        return 1

    stream = sys.stdout if passed else sys.stderr
    for message in messages:
        print(message, file=stream)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
