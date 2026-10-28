#!/usr/bin/env python3
"""Embed the Windows client and skill into the one-file installer."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install-ssh-client.ps1"
FILES = (
    "client/conductor.ps1",
    "skills/conductor-coordinator/SKILL.md",
    "skills/conductor-coordinator/references/workflow.md",
    "skills/conductor-coordinator/references/actions.md",
)
START = "# BEGIN EMBEDDED CONDUCTOR FILES\n"
END = "# END EMBEDDED CONDUCTOR FILES"


def build(current: str) -> str:
    if current.count(START) != 1 or current.count(END) != 1:
        raise ValueError("Installer payload markers are missing or duplicated")
    entries = ["$embeddedFiles = @{"]
    for name in FILES:
        # Git may check out text with CRLF on Windows. Embed the same LF bytes
        # on every platform so the generated installer stays reproducible.
        source = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        encoded = base64.b64encode(source).decode("ascii")
        entries.append(f"    '{name}' = '{encoded}'")
    entries.append("}")
    before, rest = current.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n".join(entries) + "\n" + END + after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the payload is stale")
    args = parser.parse_args()
    current = INSTALLER.read_text(encoding="utf-8")
    generated = build(current)
    if args.check:
        if current != generated:
            parser.error("installer payload is stale; run this script without --check")
    elif current != generated:
        INSTALLER.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
