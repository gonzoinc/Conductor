#!/usr/bin/env python3
"""Fail a public release when tracked files contain common private data."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_FILE = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\.(?!example$).+)?|(?:service|health|mysql)-password|"
    r"(?:service|health)-token|client\.json|conductor\.yaml|[^/]+\.(?:token|pem|key|p12|pfx|sqlite|sqlite3|db))$"
)
PRIVATE_TEXT = {
    "private network address": re.compile(
        r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)"
        r"\.\d{1,3}\.\d{1,3}(?![\d.])"
    ),
    "private key block": re.compile(
        r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"
    ),
    "credential signature": re.compile(
        r"(?i)\b(?:glpat-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
        r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|"
        r"xox[baprs]-[A-Za-z0-9-]{16,})\b"
    ),
    "personal Windows path": re.compile(
        r"(?i)\b[A-Z]:[/\\]Users[/\\](?!(?:example|test|alice|bob)[/\\])[^/\\\s'\"]+"
    ),
    "personal Mac path": re.compile(
        r"/Us" + r"ers/(?!(?:example|test|alice|bob)/)[^/\s'\"]+"
    ),
    "internal host name": re.compile(
        r"(?i)\b(?:[a-z][a-z0-9]*-(?:lab|orch|stg)|[a-z0-9.-]+\.(?:stg|lan|local))\b"
    ),
    "self-hosted GitLab address": re.compile(
        r"(?i)\bgitlab\.(?!com\b)[a-z0-9.-]+\.[a-z]{2,}\b"
    ),
    "credential in URL": re.compile(
        r"(?i)https?://[^\s'\"/]+:[^\s'\"/@]+@(?!(?:example\.com|localhost)\b)"
    ),
}


def findings_for(name: str, content: str) -> list[str]:
    findings = []
    if PRIVATE_FILE.search(name.replace("\\", "/")):
        findings.append(f"{name}: private file name")
    for number, line in enumerate(content.splitlines(), 1):
        # The generated one-file installer is checked against its source by
        # build_windows_installer.py --check. Scan those source files instead.
        if name == "install-ssh-client.ps1" and len(line) > 2000:
            continue
        for label, pattern in PRIVATE_TEXT.items():
            if pattern.search(line):
                findings.append(f"{name}:{number}: {label}")
    return findings


def main() -> int:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "ls-files", "-z"],
        cwd=ROOT, capture_output=True, check=True
    )
    names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    findings = []
    for name in names:
        path = ROOT / name
        if not path.is_file():
            continue
        findings.extend(findings_for(name, path.read_text(encoding="utf-8", errors="replace")))
    if findings:
        print("Public-release guard found private material:")
        for finding in findings:
            print("  " + finding)
        return 1
    print(f"Public-release guard passed for {len(names)} tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
