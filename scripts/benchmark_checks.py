#!/usr/bin/env python3
"""Fixed role checks, copied into a benchmark fixture's .codex directory."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

# Also prevent artifacts when a caller forgets the interpreter's -B switch.
sys.dont_write_bytecode = True
ROLES = {"slug_impl": ["test_slug", "test_slug_type"],
         "slug_review": ["test_slug", "test_slug_type"],
         "duration_impl": ["test_duration", "test_duration_invalid", "test_duration_type"],
         "duration_review": ["test_duration", "test_duration_invalid", "test_duration_type"],
         "integration_verify": []}


def scope_errors(root: Path) -> list[str]:
    contract = json.loads((root / ".codex/check-contract.json").read_text(encoding="utf-8"))
    errors = []
    for name, digest in contract["immutable"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"Immutable input changed: {name}")
    # Fixture inventories are checked without querying a containing Git repo or
    # opening a sibling run. Working artifacts are allowed only under .codex.
    allowed = set(contract["root_files"])
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] in {".codex", "policy"}:
            continue
        if path.is_file() and relative.as_posix() not in allowed:
            errors.append(f"Unexpected file outside .codex: {relative.as_posix()}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=ROLES)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    errors = scope_errors(root)
    if errors:
        print("\n".join(errors))
        return 1
    if args.role == "integration_verify":
        # Preserve the original exact acceptance command and its exit status.
        result = subprocess.run([sys.executable, "-B", "acceptance.py"], cwd=root)
        code = result.returncode
    else:
        sys.path.insert(0, str(root))
        spec = importlib.util.spec_from_file_location("fixed_acceptance", root / "acceptance.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(
            "Acceptance." + name, module) for name in ROLES[args.role])
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        code = 0 if result.wasSuccessful() else 1
    receipts = root / ".codex/receipts"
    receipts.mkdir(exist_ok=True)
    (receipts / (args.role + ".json")).write_text(json.dumps({
        "role": args.role, "scope_ok": True, "exit_code": code,
        "tests": ROLES[args.role] or ["full_acceptance"]}), encoding="utf-8")
    print(f"role={args.role} scope=ok exit={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
