"""Check a GUI build's packaged source against the exact source tree used to build it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(source: Path, build: Path) -> tuple[int, str]:
    lock = json.loads((build / "runtime-lock.json").read_text(encoding="utf-8"))
    archive = build / "conductor-source.zip"
    assert digest(archive) == lock["source_archive_sha256"], "source archive differs from packaged lock"
    assert digest(build / "uv.exe") == lock["uv_exe_sha256"], "uv differs from packaged lock"
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert len(names) == len(set(names)), "duplicate archive member"
        for name in names:
            file = source / name
            assert file.is_file() and not file.is_symlink(), f"missing source file: {name}"
            assert hashlib.sha256(package.read(name)).hexdigest() == digest(file), f"source mismatch: {name}"
    return len(names), lock["source_archive_sha256"]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: py -3 gui/verify_build.py SOURCE_DIR BUILD_DIR")
    count, source_hash = verify(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"Verified {count} packaged source files; archive SHA256 {source_hash}")
