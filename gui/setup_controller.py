"""Installation engine shared by the graphical installer and its tests."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
import sys
import threading
from typing import Callable
import zipfile

from client_launch import launch_isolated


PYTHON_VERSION = "3.12.15"
OWNERSHIP = {"product": "conductor-codex", "purpose": "windows-gui-install", "version": 1}


@dataclass(frozen=True)
class SetupConfig:
    mode: str
    install_dir: Path
    state_dir: Path | None
    profiles: tuple[str, ...]
    isolated: bool = True


class SetupCancelled(Exception):
    pass


def completed_setup(install_dir: Path, config: SetupConfig) -> None:
    """Save non-secret choices so the GUI can show next steps again later."""
    receipt = install_dir / ".conductor-gui-result.json"
    if receipt.is_symlink():
        raise ValueError("The saved setup result cannot be a symbolic link.")
    details = {"purpose": "conductor-gui-result", "version": 1, "mode": config.mode,
               "profiles": list(config.profiles), "isolated": config.isolated,
               "state_dir": str(config.state_dir) if config.state_dir else None}
    receipt.write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")


def installed_setup(install_dir: Path) -> SetupConfig:
    """Read a GUI-owned installation, including older isolated installs."""
    install_dir = _local_path(install_dir)
    marker = install_dir / ".conductor-gui-install.json"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("No Conductor GUI installation was found in that folder.")
    identity = json.loads(marker.read_text(encoding="utf-8"))
    if any(identity.get(key) != value for key, value in OWNERSHIP.items()):
        raise ValueError("That folder is not a Conductor GUI installation.")
    receipt = install_dir / ".conductor-gui-result.json"
    if receipt.is_file() and not receipt.is_symlink():
        details = json.loads(receipt.read_text(encoding="utf-8"))
        if details.get("purpose") != "conductor-gui-result" or details.get("version") != 1:
            raise ValueError("The saved Conductor setup result is invalid.")
        profiles = tuple(details["profiles"])
        if not profiles or any(profile not in ("desktop", "vscode") for profile in profiles):
            raise ValueError("The saved Conductor client selection is invalid.")
        if details.get("mode") != "local" or not isinstance(details.get("isolated"), bool):
            raise ValueError("This Windows setup app opens local installations only.")
        return SetupConfig(mode=details["mode"], install_dir=install_dir,
                           state_dir=Path(details["state_dir"]) if details.get("state_dir") else None,
                           profiles=profiles, isolated=bool(details["isolated"]))
    profiles = []
    settings = []
    for profile in ("desktop", "vscode"):
        folder = install_dir / "test-profiles" / profile
        profile_marker = folder / ".conductor-installer.json"
        client = folder / "home/conductor-coordination/client.json"
        if not profile_marker.is_file() or not client.is_file():
            continue
        owner = json.loads(profile_marker.read_text(encoding="utf-8"))
        if owner != {"purpose": "conductor-isolated-client", "source": str(install_dir / "source"), "profile": profile}:
            raise ValueError(f"The {profile} test profile does not belong to this Conductor installation.")
        profiles.append(profile)
        settings.append(json.loads(client.read_text(encoding="utf-8")))
    if not profiles:
        raise ValueError("No complete, matching test profiles were found. Run setup again to finish them.")
    mode = settings[0].get("mode", "hosted")
    if mode != "local":
        raise ValueError("This Windows setup app opens local installations only.")
    common = ("state_root", "db_path")
    if any(item.get("mode", "hosted") != mode or
           any(item.get(key) != settings[0].get(key) for key in common) for item in settings[1:]):
        raise ValueError("The installed test profiles point to different Conductor services.")
    state = Path(settings[0]["state_root"])
    return SetupConfig(mode=mode, install_dir=install_dir, state_dir=state,
                       profiles=tuple(profiles), isolated=True)


def launch_test_profile(install_dir: Path, profile: str, resources: Path) -> None:
    """Open an owned test frontend with its separate Codex/app data."""
    config = installed_setup(install_dir)
    if not config.isolated or profile not in config.profiles:
        raise ValueError("That isolated client was not installed here.")
    lock = json.loads((resources / "runtime-lock.json").read_text(encoding="utf-8"))
    source_name, test_root_name = _ensure_owned(install_dir, lock["source_archive_sha256"])
    source = install_dir / source_name
    test_root = install_dir / test_root_name
    home = test_root / profile / "home"
    workspace = test_root / profile / "workspace"
    marker = test_root / profile / ".conductor-installer.json"
    expected_owner = {"purpose": "conductor-isolated-client", "source": str(source),
                      "profile": profile}
    if not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != expected_owner:
        raise ValueError("That test profile does not belong to this Conductor installation.")
    if not home.resolve().is_relative_to(test_root.resolve()) or not (home / "config.toml").is_file():
        raise ValueError("The test profile is incomplete; no Codex client was opened.")
    launch_isolated(profile, home, workspace)


def _local_path(path: Path) -> Path:
    value = path.expanduser().absolute()
    if str(value).startswith(("\\\\", "//")):
        raise ValueError("Choose a folder on this computer, not a network share.")
    if any(part.is_symlink() for part in (value, *value.parents)):
        raise ValueError("Choose a folder without symbolic links.")
    return value


def validate(config: SetupConfig) -> SetupConfig:
    if os.name != "nt":
        raise ValueError("This setup app is for Windows. Use the Linux host installer on Linux.")
    if config.mode != "local":
        raise ValueError("This Windows setup app installs Conductor locally only.")
    _local_path(config.install_dir)
    if not config.profiles or any(profile not in ("desktop", "vscode") for profile in config.profiles):
        raise ValueError("Choose Codex desktop, VS Code, or both.")
    if len(set(config.profiles)) != len(config.profiles):
        raise ValueError("A Codex profile was selected more than once.")
    if config.state_dir is None:
        raise ValueError("Choose where Conductor should keep its local task data.")
    state = _local_path(config.state_dir)
    install = _local_path(config.install_dir)
    if state == install or state.is_relative_to(install) or install.is_relative_to(state):
        raise ValueError("Choose separate folders for the app and task data.")
    return config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_owned(path: Path, source_digest: str) -> tuple[str, str]:
    marker = path / ".conductor-gui-install.json"
    identity = dict(OWNERSHIP, source_sha256=source_digest)
    if path.exists():
        if path.is_symlink() or not marker.is_file() or marker.is_symlink():
            raise ValueError(f"This folder is unowned or belongs to another Conductor version: {path}. Choose a new folder; existing files were preserved.")
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved == identity:
            return "source", "test-profiles"
        if (isinstance(saved, dict) and all(saved.get(key) == value for key, value in OWNERSHIP.items())
                and re.fullmatch(r"[0-9a-f]{64}", str(saved.get("source_sha256", "")))):
            suffix = source_digest[:12]
            return f"source-{suffix}", f"test-profiles-{suffix}"
        raise ValueError(f"This folder is unowned or belongs to another Conductor version: {path}. Choose a new folder; existing files were preserved.")
    path.mkdir(parents=True)
    marker.write_text(json.dumps(identity), encoding="utf-8")
    return "source", "test-profiles"


def _archive_members(package: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for member in package.infolist():
        if member.is_dir():
            continue
        relative = Path(member.filename)
        if relative.is_absolute() or ".." in relative.parts or member.filename.startswith(("/", "\\")) or "\\" in member.filename:
            raise ValueError("The setup package contains an unsafe file path.")
        if member.filename in members or stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("The setup package contains duplicate or linked files.")
        members[member.filename] = member
    if not {"scripts/bootstrap.py", "scripts/install_client.py", "pyproject.toml"}.issubset(members):
        raise ValueError("The setup package is missing required source files.")
    return members


def _verify_source(target: Path, package: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]) -> None:
    if target.is_symlink() or not target.is_dir():
        raise ValueError("The existing source folder is not a normal directory.")
    expected = set(members)
    for name, member in members.items():
        path = target / name
        if any(part.is_symlink() for part in (path, *path.parents) if part == target or target in part.parents):
            raise ValueError(f"Installed source contains a linked path: {name}")
        if not path.is_file():
            raise ValueError(f"Installed source is missing a packaged file: {name}")
        with package.open(member) as bundled:
            expected_digest = hashlib.sha256(bundled.read()).hexdigest()
        if _sha256(path) != expected_digest:
            raise ValueError(f"Installed source differs from this setup package: {name}. Choose a new folder; existing files were preserved.")
    for folder, names, files in os.walk(target, followlinks=False):
        parent = Path(folder)
        for name in list(names):
            path = parent / name
            if path.is_symlink():
                raise ValueError(f"Installed source contains a linked folder: {path.relative_to(target)}")
            if name == "__pycache__":
                raise ValueError(f"Installed source contains an unexpected bytecode folder: {path.relative_to(target)}")
            if name == ".codex" or name.endswith(".egg-info"):
                names.remove(name)
        for name in files:
            path = parent / name
            relative = path.relative_to(target)
            if path.is_symlink():
                raise ValueError(f"Installed source contains an unexpected file: {relative}. Choose a new folder; existing files were preserved.")
            # Earlier setup versions let setuptools place copies of packaged modules
            # under build/lib. Accept only byte-identical copies of packaged source.
            parts = relative.parts
            if len(parts) == 4 and parts[:3] == ("build", "lib", "conductor_coordination"):
                source_name = f"src/conductor_coordination/{parts[3]}"
                if source_name in members and parts[3].endswith(".py"):
                    with package.open(members[source_name]) as bundled:
                        if _sha256(path) == hashlib.sha256(bundled.read()).hexdigest():
                            continue
            if relative.as_posix() not in expected:
                raise ValueError(f"Installed source contains an unexpected file: {relative}. Choose a new folder; existing files were preserved.")


def _extract_source(archive: Path, destination: Path, expected_digest: str,
                    source_name: str = "source") -> None:
    if _sha256(archive) != expected_digest:
        raise ValueError("The bundled Conductor source failed its integrity check.")
    target = destination / source_name
    with zipfile.ZipFile(archive) as package:
        members = _archive_members(package)
        if target.exists() or target.is_symlink():
            _verify_source(target, package, members)
            return
        target.mkdir()
        try:
            for name, member in members.items():
                output = target / name
                output.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, output.open("xb") as sink:
                    shutil.copyfileobj(source, sink)
            _verify_source(target, package, members)
        except Exception:
            shutil.rmtree(target)
            raise


def _run(command: list[str], *, env: dict[str, str], cancel: threading.Event, progress: Callable[[str], None]) -> None:
    if cancel.is_set():
        raise SetupCancelled()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    recent: deque[str] = deque(maxlen=20)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel.is_set():
                process.terminate()
                raise SetupCancelled()
            message = line.rstrip()
            recent.append(message)
            progress(message)
        exit_code = process.wait()
        if exit_code != 0:
            detail = "\n".join(recent).strip() or "The command produced no diagnostic output."
            raise RuntimeError(f"Setup step exited with code {exit_code}.\n{detail}")
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.terminate()
            process.wait()


def install(config: SetupConfig, resources: Path, progress: Callable[[str], None], cancel: threading.Event) -> Path:
    """Install only in selected folders; no normal Codex profile unless explicitly selected."""
    validate(config)
    install_dir = _local_path(config.install_dir)
    lock = json.loads((resources / "runtime-lock.json").read_text(encoding="utf-8"))
    archive = resources / "conductor-source.zip"
    uv_source = resources / "uv.exe"
    if _sha256(archive) != lock["source_archive_sha256"]:
        raise ValueError("The bundled Conductor source failed its integrity check.")
    if _sha256(uv_source) != lock["uv_exe_sha256"]:
        raise ValueError("The packaged Python downloader failed its integrity check.")
    source_name, test_root_name = _ensure_owned(install_dir, lock["source_archive_sha256"])
    _extract_source(archive, install_dir, lock["source_archive_sha256"], source_name)
    source = install_dir / source_name
    uv = install_dir / "uv.exe"
    if uv.exists() or uv.is_symlink():
        if uv.is_symlink() or not uv.is_file() or _sha256(uv) != lock["uv_exe_sha256"]:
            raise ValueError("The existing Python downloader differs from this setup package. Choose a new folder; existing files were preserved.")
    else:
        shutil.copy2(uv_source, uv)
        if _sha256(uv) != lock["uv_exe_sha256"]:
            raise ValueError("The copied Python downloader failed its integrity check.")
    state = install_dir / "python"
    cache = install_dir / "download-cache"
    state.mkdir(exist_ok=True)
    cache.mkdir(exist_ok=True)
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(state), UV_CACHE_DIR=str(cache), UV_PYTHON_BIN_DIR=str(install_dir / "python-bin"),
               UV_NO_PROGRESS="1", PYTHONDONTWRITEBYTECODE="1")
    progress("Installing a private Python runtime for Conductor. This may take a few minutes.")
    _run([str(uv), "python", "install", PYTHON_VERSION], env=env, cancel=cancel, progress=progress)
    import subprocess as sp
    python = Path(sp.check_output([str(uv), "python", "find", PYTHON_VERSION, "--managed-python"], env=env, text=True,
                                  creationflags=sp.CREATE_NO_WINDOW if os.name == "nt" else 0).strip())
    if not python.is_file() or not python.is_relative_to(state):
        raise RuntimeError("The private Python installation could not be found.")
    for profile in config.profiles:
        if cancel.is_set():
            raise SetupCancelled()
        progress(f"Configuring Codex {profile}.")
        arguments = ["client", "--mode", config.mode, "--profile", profile]
        if config.isolated:
            arguments += ["--test-root", str(install_dir / test_root_name)]
        else:
            arguments += ["--configure-user"]
        assert config.state_dir is not None
        arguments += ["--local-state-root", str(_local_path(config.state_dir)), "--db-path", str(_local_path(config.state_dir) / "tasks.sqlite3")]
        _run([str(python), str(source / "scripts/bootstrap.py"), str(source), *arguments],
             env=env, cancel=cancel, progress=progress)
    progress("Conductor setup is complete.")
    return install_dir
