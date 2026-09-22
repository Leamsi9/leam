#!/usr/bin/env python3
"""Offline, deterministic publication guard. Reports locations, never matched values."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "openai-key": rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{50,})",
    "google-secret": rb"(?:GOCSPX-[A-Za-z0-9_-]{15,}|AIza[A-Za-z0-9_-]{30,})",
    "aws-access-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "slack-token": rb"xox[baprs]-[A-Za-z0-9-]{15,}",
    "jwt": rb"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}",
    "credential-in-url": rb"https?://[^\s/\"':]{2,}:[^\s/\"'@]{5,}@",
    "credential-literal": rb"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|bearer[_-]?token)[\"']?\s*[=:]\s*[\"']([A-Za-z0-9_./+=:-]{16,})[\"']",
    "private-tailnet-host": rb"\b[a-zA-Z0-9-]+\.tail[a-zA-Z0-9]+\.ts\.net",
    "personal-home-path": rb"/home/(?!user(?:/|\b)|runner(?:/|\b)|test(?:/|\b)|fixture(?:/|\b)|example(?:/|\b)|dev(?:/|\b)|you(?:/|\b))[a-zA-Z0-9_.-]+/",
}
# Deliberate non-production test literals only, and only in test source files.
FIXTURE_LITERALS = {
    b"local-browser-acceptance-only", b"long-password-for-tests",
    b"another-long-password", b"bootstrap-for-tests",
}

FIXTURE_MATCHES = {
    (row["path"], row["sha256"])
    for row in json.loads((ROOT / "scripts/public-fixture-allowlist.json").read_text())["matches"]
}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def private_path(path):
    p = Path(path)
    return (
        path.startswith(("data/", "backups/", "attachments/", "exports/", "tokenops/usage_data/", "docs/plans/feature/"))
        or p.name in {"accounts-key", "bootstrap-token", "auth.json", "credentials.json"}
        or (p.name.startswith(".env") and p.name != ".env.example")
        or p.name.startswith("client_secret")
        or p.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12", ".pfx", ".log", ".jsonl"}
    )


def scan(path, data, source, findings):
    if data.startswith(b"SQLite format 3"):
        findings.add((source, path, 0, "sqlite-database-payload"))
    if private_path(path):
        findings.add((source, path, 0, "private-artifact-path"))
    for kind, pattern in PATTERNS.items():
        for match in re.finditer(pattern, data):
            fixture = path.startswith(("tests/", "apps/web/tests/"))
            if kind == "credential-literal" and fixture and match.group(1) in FIXTURE_LITERALS:
                continue
            if kind == "credential-literal" and fixture and (path, hashlib.sha256(match.group()).hexdigest()) in FIXTURE_MATCHES:
                continue
            # This caller intentionally verifies private-path UI redaction.
            if kind == "personal-home-path" and path == "apps/web/tests/backlog.spec.ts" and match.group() == b"/home/" + b"private/":
                continue
            line = data.count(b"\n", 0, match.start()) + 1
            findings.add((source, path, line, kind))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Also inspect every blob and commit message reachable from HEAD")
    args = parser.parse_args()
    findings = set()
    paths = git("ls-files", "-z").decode().split("\0")
    if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet"], check=False).returncode:
        findings.add(("working-tree", "<index>", 0, "unstaged-tracked-changes-stage-before-audit"))
    files = 0
    for path in filter(None, paths):
        file = ROOT / path
        if file.is_symlink():
            findings.add(("working-tree", path, 0, "tracked-symlink"))
            continue
        if file.is_file():
            scan(path, file.read_bytes(), "working-tree", findings)
            files += 1
    history_blobs = 0
    if args.history:
        entries = {}
        for row in git("rev-list", "--objects", "HEAD").decode().splitlines():
            sha, _, path = row.partition(" ")
            entries.setdefault(sha, path)
        proc = subprocess.Popen(["git", "-C", str(ROOT), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for sha, path in entries.items():
                proc.stdin.write((sha + "\n").encode())
                proc.stdin.flush()
                header = proc.stdout.readline().split()
                if len(header) != 3:
                    raise RuntimeError("Unable to inspect Git object")
                data = proc.stdout.read(int(header[2]))
                proc.stdout.read(1)
                if header[1] == b"blob":
                    scan(path, data, sha[:12], findings)
                    history_blobs += 1
                elif header[1] == b"commit":
                    scan("<commit-message>", data.split(b"\n\n", 1)[-1], sha[:12], findings)
        finally:
            proc.stdin.close()
            proc.wait()
    result = {"trackedFiles": files, "historyBlobs": history_blobs, "findings": [dict(source=s, path=p, line=n, kind=k) for s, p, n, k in sorted(findings)]}
    print(json.dumps(result, indent=2))
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
