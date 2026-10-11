#!/usr/bin/env python3
"""Block secrets and personal data before they reach this public repo.

Scans files that git would publish and fails on:
  - secret-shaped tokens and private keys,
  - personal-looking data (real e-mail addresses, Teams/Jira links, home paths),
  - any entry from a local denylist (names, hosts, handles) that is never committed.

Usage:
  python3 scripts/privacy_check.py            # tracked + untracked, not ignored
  python3 scripts/privacy_check.py --staged   # only staged files (pre-commit hook)

A line that must keep a sample value can carry the marker `privacy:allow`.
The denylist path comes from PRIVACY_DENYLIST_FILE (default .privacy-denylist).
Matches are always printed masked. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "privacy:allow"
MAX_BYTES = 1_000_000

PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "api_token": re.compile(
        r"\b(?:sk-(?:proj-|ant-api\d{2}-)?[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|"
        r"AIza[0-9A-Za-z_\-]{20,})"
    ),
    "secret_assignment": re.compile(
        r"(?i)\b[A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD|TOKEN)[A-Z0-9_]*\s*[=:]\s*['\"]?"
        r"(?![<$])(?=[A-Za-z0-9_\-+/=]*\d)[A-Za-z0-9_\-+/=]{20,}(?![\w(])"
    ),
    "url_credentials": re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:(?![<*$])[^\s@/]{3,}@"),
    "email": re.compile(
        r"\b[A-Za-z0-9._%+\-]+@(?!(?:example|users\.noreply\.github)\.(?:com|org|net)\b)"
        r"(?!anthropic\.com\b)[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]*[A-Za-z]\b"
    ),
    "teams_link": re.compile(r"teams\.microsoft\.com/l/"),
    "jira_link": re.compile(r"\b[\w\-]+\.atlassian\.net\b"),
    "home_path": re.compile(r"/(?:Users|home)/(?!<)[A-Za-z0-9._\-]+/"),
}


# Development defaults and documented placeholders are not credentials.
_PLACEHOLDER_HOSTS = frozenset({"localhost", "127.0.0.1", "db", "host", "postgres"})
_PLACEHOLDER_PASSWORDS = frozenset({"password", "memory"})


def _is_placeholder_url(line: str, match: re.Match[str]) -> bool:
    password = match.group(0).rsplit(":", 1)[-1].rstrip("@").lower()
    host = re.match(r"[^\s:/?#]*", line[match.end():]).group(0).lower()
    return host in _PLACEHOLDER_HOSTS or password in _PLACEHOLDER_PASSWORDS


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    match: str


def mask(value: str) -> str:
    value = value.strip()
    return (value[:2] + "***") if len(value) > 2 else "***"


def load_denylist(path: str) -> list[str]:
    file = Path(path)
    if not file.is_file():
        return []
    entries = []
    for raw in file.read_text(encoding="utf-8").splitlines():
        entry = raw.strip()
        if entry and not entry.startswith("#"):
            entries.append(entry.lower())
    return entries


def scan_text(path: str, text: str, denylist: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if rule == "url_credentials" and _is_placeholder_url(line, match):
                    continue
                findings.append(Finding(path, number, rule, match.group(0)))
        lowered = line.lower()
        for entry in denylist:
            if entry in lowered:
                findings.append(Finding(path, number, "denylist", entry))
    return findings


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def files_to_scan(staged: bool) -> list[str]:
    if staged:
        names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    else:
        names = _git("ls-files") + _git("ls-files", "--others", "--exclude-standard")
    return sorted(set(names))


def scan_file(path: str, denylist: list[str]) -> list[Finding]:
    file = Path(path)
    if not file.is_file() or file.stat().st_size > MAX_BYTES:
        return []
    data = file.read_bytes()
    if b"\0" in data[:8192]:
        return []
    return scan_text(path, data.decode("utf-8", errors="replace"), denylist)


def run_gitleaks(staged: bool) -> int:
    if not shutil.which("gitleaks"):
        return 0
    args = ["gitleaks", "protect", "--staged", "--redact"] if staged else [
        "gitleaks", "detect", "--no-git", "--redact", "--source", "."]
    return subprocess.run(args, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staged", action="store_true", help="scan staged files only")
    args = parser.parse_args(argv)

    denylist = load_denylist(os.environ.get("PRIVACY_DENYLIST_FILE", ".privacy-denylist"))
    files = files_to_scan(args.staged)
    findings = [f for path in files for f in scan_file(path, denylist)]
    for f in findings:
        print(f"✗ {f.path}:{f.line}  {f.rule}  {mask(f.match)}")
    leaks = run_gitleaks(args.staged)
    print(f"privacy-check: {len(files)} files · denylist {len(denylist)} entries · "
          f"{len(findings)} findings" + (" · gitleaks failed" if leaks else ""))
    return 1 if findings or leaks else 0


if __name__ == "__main__":
    sys.exit(main())
