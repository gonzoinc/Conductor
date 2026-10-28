"""Reproducibly package the Windows GUI from a maintained source tree.

Run with: py -3 gui/build_windows.py PATH_TO_CONDUCTOR_SOURCE
Build scratch and output stay in the active repository's primary .codex directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
import zipfile


GUI = Path(__file__).resolve().parent
LOCK = json.loads((GUI / "runtime-lock.json").read_text(encoding="utf-8"))


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_zip(source: Path, output: Path) -> None:
    prefixes = {"src", "skills"}
    roots = {"README.md", "pyproject.toml", "UPSTREAM_LICENSE.txt", "upstream-lock.json"}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        candidates = [source / name for name in roots]
        candidates.extend(source / "scripts" / name for name in
                          ("bootstrap.py", "install_client.py", "legacy_install.py"))
        for name in prefixes:
            folder = source / name
            if folder.is_dir():
                candidates.extend(folder.rglob("*"))
        for path in sorted(candidates):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source)
            if "__pycache__" not in relative.parts and path.suffix != ".pyc":
                archive.write(path, relative.as_posix())
    with zipfile.ZipFile(output) as archive:
        required = {"scripts/bootstrap.py", "scripts/install_client.py", "pyproject.toml"}
        if not required.issubset(archive.namelist()):
            raise RuntimeError("The selected source tree is incomplete.")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: py -3 gui/build_windows.py PATH_TO_CONDUCTOR_SOURCE")
    source = Path(sys.argv[1]).resolve()
    build = Path(os.environ.get("CONDUCTOR_BUILD_ROOT", str(source / ".codex/gui-build"))).resolve()
    build.mkdir(parents=True, exist_ok=True)
    archive = build / "uv.zip"
    if not archive.exists():
        url = f"https://github.com/astral-sh/uv/releases/download/{LOCK['uv_version']}/{LOCK['uv_asset']}"
        with urlopen(url, timeout=90) as response, archive.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
    if checksum(archive) != LOCK["uv_archive_sha256"]:
        raise RuntimeError("The downloaded uv archive failed its pinned SHA256 check.")
    with zipfile.ZipFile(archive) as package, package.open("uv.exe") as original:
        uv = build / "uv.exe"
        with uv.open("wb") as target:
            while chunk := original.read(1024 * 1024):
                target.write(chunk)
    if checksum(uv) != LOCK["uv_exe_sha256"]:
        raise RuntimeError("uv.exe failed its pinned SHA256 check.")
    source_archive = build / "conductor-source.zip"
    build_source_zip(source, source_archive)
    packaged_lock = dict(LOCK, source_archive_sha256=checksum(source_archive))
    (build / "runtime-lock.json").write_text(json.dumps(packaged_lock, indent=2) + "\n", encoding="utf-8")
    venv = build / "builder-venv"
    python = venv / "Scripts/python.exe"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                    f"pyinstaller=={LOCK['pyinstaller_version']}"], check=True)
    add = lambda item: str(build / item) + ";."
    (build / "pyinstaller-cache").mkdir(exist_ok=True)
    (build / "tmp").mkdir(exist_ok=True)
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(build / "pyinstaller-cache"),
                       TEMP=str(build / "tmp"), TMP=str(build / "tmp"), TMPDIR=str(build / "tmp"))
    subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                    "--name", "ConductorSetup", "--distpath", str(build / "dist"), "--workpath", str(build / "work"),
                    "--specpath", str(build), "--add-data", add("uv.exe"), "--add-data", add("conductor-source.zip"),
                    "--add-data", add("runtime-lock.json"), str(GUI / "setup_app.py")], check=True,
                   env=environment)
    print(build / "dist/ConductorSetup.exe")


if __name__ == "__main__":
    main()
