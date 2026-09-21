#!/usr/bin/env python3
"""Mechanical citation inventory checks for research sources and BibTeX files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BibEntry:
    key: str
    entry_type: str
    body: str
    source: Path


LATEX_CITE_COMMANDS = {
    "autocite",
    "cite",
    "citealp",
    "citealt",
    "citeauthor",
    "citep",
    "citet",
    "citeyear",
    "citeproc",
    "footcite",
    "fullcite",
    "nocite",
    "parencite",
    "textcite",
}


def clean_latex(value: str) -> str:
    replacements = {
        r"{\"o}": "o",
        r"{\"a}": "a",
        r"{\'e}": "e",
        r"{\AA}": "A",
        r"{\o}": "o",
        r"{\u{a}}": "a",
        r"\&": "&",
        r"\#": "#",
        r"\$": "$",
        "--": "-",
    }
    cleaned = value
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", "", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    return " ".join(cleaned.split())


def parse_bib_entries(path: Path) -> list[BibEntry]:
    text = path.read_text(encoding="utf-8")
    entries: list[BibEntry] = []
    start_pattern = re.compile(r"@([A-Za-z]+)\s*(?P<opener>[\{\(])\s*([^,\s]+)\s*,")
    position = 0
    while True:
        match = start_pattern.search(text, position)
        if match is None:
            break

        opener = match.group("opener")
        closer = "}" if opener == "{" else ")"
        depth = 1
        end = match.end()
        for index in range(match.end(), len(text)):
            character = text[index]
            if character == opener:
                depth += 1
            elif character == closer:
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        else:
            raise ValueError(f"Unclosed BibTeX entry for {match.group(3)} in {path}")

        entries.append(
            BibEntry(
                key=match.group(3).strip(),
                entry_type=match.group(1).lower(),
                body=text[match.end() : end - 1],
                source=path,
            )
        )
        position = end
    return entries


def parse_all_bib_entries(paths: list[Path]) -> list[BibEntry]:
    entries: list[BibEntry] = []
    for path in paths:
        entries.extend(parse_bib_entries(path))
    return entries


def parse_fields(entry: BibEntry) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    body = entry.body
    name_pattern = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s*=")
    while True:
        match = name_pattern.search(body, index)
        if match is None:
            break
        name = match.group(1).lower()
        value_start = match.end()
        while value_start < len(body) and body[value_start].isspace():
            value_start += 1
        if value_start >= len(body):
            break
        opener = body[value_start]
        if opener == "{":
            depth = 1
            cursor = value_start + 1
            while cursor < len(body):
                if body[cursor] == "{":
                    depth += 1
                elif body[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            fields[name] = clean_latex(body[value_start + 1 : cursor])
            index = cursor + 1
        elif opener == '"':
            cursor = value_start + 1
            escaped = False
            while cursor < len(body):
                if body[cursor] == '"' and not escaped:
                    break
                escaped = body[cursor] == "\\" and not escaped
                if body[cursor] != "\\":
                    escaped = False
                cursor += 1
            fields[name] = clean_latex(body[value_start + 1 : cursor])
            index = cursor + 1
        else:
            cursor = value_start
            while cursor < len(body) and body[cursor] not in ",\n":
                cursor += 1
            fields[name] = clean_latex(body[value_start:cursor])
            index = cursor + 1
    return fields


def field_names(entry: BibEntry) -> set[str]:
    return set(parse_fields(entry))


def strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cursor = 0
        while True:
            marker = line.find("%", cursor)
            if marker == -1:
                lines.append(line)
                break
            if marker > 0 and line[marker - 1] == "\\":
                cursor = marker + 1
                continue
            lines.append(line[:marker])
            break
    return "\n".join(lines)


def split_keys(raw_keys: str) -> list[str]:
    keys: list[str] = []
    for raw_key in raw_keys.split(","):
        key = raw_key.strip()
        if not key or key.startswith("#"):
            continue
        keys.append(key)
    return keys


def extract_latex_citation_keys(text: str) -> tuple[list[str], bool]:
    keys: list[str] = []
    nocite_all = False
    command_pattern = "|".join(sorted(LATEX_CITE_COMMANDS, key=len, reverse=True))
    pattern = re.compile(
        rf"\\(?P<command>{command_pattern})\*?(?:\s*\[[^\]]*\])*\s*\{{(?P<keys>[^{{}}]+)\}}"
    )
    for match in pattern.finditer(strip_comments(text)):
        command = match.group("command")
        for key in split_keys(match.group("keys")):
            if command == "nocite" and key == "*":
                nocite_all = True
                continue
            keys.append(key)
    return keys, nocite_all


def extract_pandoc_citation_keys(text: str) -> list[str]:
    keys: list[str] = []
    for bracket in re.finditer(r"\[(?P<body>[^\[\]]{0,1000})\]", text):
        body = bracket.group("body")
        if "@" not in body:
            continue
        keys.extend(re.findall(r"(?<![\w/])@([A-Za-z0-9_:.\/-]+)", body))
    return keys


def extract_source_citation_keys(paths: list[Path]) -> tuple[list[str], bool]:
    keys: list[str] = []
    nocite_all = False
    for path in paths:
        text = path.read_text(encoding="utf-8")
        latex_keys, has_nocite_all = extract_latex_citation_keys(text)
        keys.extend(latex_keys)
        keys.extend(extract_pandoc_citation_keys(text))
        nocite_all = nocite_all or has_nocite_all
    return keys, nocite_all


def missing_required_fields(entries: list[BibEntry]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for entry in entries:
        fields = field_names(entry)
        required = {"title", "year"}
        if entry.entry_type not in {"proceedings"}:
            if not ({"author", "editor", "institution", "organization"} & fields):
                required.add("author_or_owner")
        absent = sorted(field for field in required if field not in fields)
        if absent:
            missing[entry.key] = absent
    return missing


def build_payload(
    source_paths: list[Path],
    bib_paths: list[Path],
    require_nocite_all: bool = False,
    fail_unused_bib: bool = False,
) -> dict[str, object]:
    entries = parse_all_bib_entries(bib_paths)
    bib_keys = [entry.key for entry in entries]
    explicit_keys, nocite_all = extract_source_citation_keys(source_paths)
    duplicates = sorted({key for key in bib_keys if bib_keys.count(key) > 1})
    missing_from_bib = sorted({key for key in explicit_keys if key not in bib_keys})
    used_keys = set(bib_keys if nocite_all else explicit_keys)
    unused_bib_keys = sorted(set(bib_keys) - used_keys)

    locatorless = []
    for entry in entries:
        fields = field_names(entry)
        if not ({"doi", "url", "isbn"} & fields):
            locatorless.append(entry.key)

    missing_fields = missing_required_fields(entries)
    failures = [duplicates, missing_from_bib, missing_fields]
    if require_nocite_all and not nocite_all:
        failures.append(["nocite_all_missing"])
    if fail_unused_bib:
        failures.append(unused_bib_keys)
    passed = all(not failure for failure in failures)

    return {
        "passed": passed,
        "sources": [str(path) for path in source_paths],
        "bibliographies": [str(path) for path in bib_paths],
        "bib_entry_count": len(entries),
        "explicit_citation_keys": sorted(set(explicit_keys)),
        "explicit_citation_key_count": len(set(explicit_keys)),
        "nocite_all": nocite_all,
        "duplicates": duplicates,
        "missing_from_bib": missing_from_bib,
        "unused_bib_keys": unused_bib_keys,
        "missing_required_fields": missing_fields,
        "locatorless_bib_keys": locatorless,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--bib", action="append", type=Path, required=True)
    parser.add_argument("--require-nocite-all", action="store_true")
    parser.add_argument("--fail-unused-bib", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = build_payload(
            source_paths=args.source,
            bib_paths=args.bib,
            require_nocite_all=args.require_nocite_all,
            fail_unused_bib=args.fail_unused_bib,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should report audit failure.
        print(f"citation inventory error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"bib_entry_count={payload['bib_entry_count']}")
        print(f"nocite_all={payload['nocite_all']}")
        print(f"explicit_citation_key_count={payload['explicit_citation_key_count']}")
        print(f"locatorless_bib_keys={', '.join(payload['locatorless_bib_keys'])}")
        if payload["duplicates"]:
            print("duplicates=" + ", ".join(payload["duplicates"]), file=sys.stderr)
        if payload["missing_from_bib"]:
            print("missing_from_bib=" + ", ".join(payload["missing_from_bib"]), file=sys.stderr)
        if payload["missing_required_fields"]:
            print(
                "missing_required_fields="
                + json.dumps(payload["missing_required_fields"], sort_keys=True),
                file=sys.stderr,
            )
        if args.fail_unused_bib and payload["unused_bib_keys"]:
            print("unused_bib_keys=" + ", ".join(payload["unused_bib_keys"]), file=sys.stderr)
        if args.require_nocite_all and not payload["nocite_all"]:
            print("nocite_all_missing=true", file=sys.stderr)
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
