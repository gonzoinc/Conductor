#!/usr/bin/env python3
"""Install the portable skill and a credential-file-backed Codex MCP connection."""
from __future__ import annotations

import argparse
import csv
import getpass
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib

from conductor_coordination.client_bridge import local_state_root, read_private, validate_local_database, validate_url

SERVER_NAME = "conductor_coordination"
SKILL_NAME = "conductor-coordinator"


def private_permissions(path: Path) -> None:
    if os.name == "nt":
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True,
                                  text=True, capture_output=True).stdout
        sid = next(csv.reader(io.StringIO(identity)))[1]
        rights = "(OI)(CI)(F)" if path.is_dir() else "(F)"
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:{rights}"],
                       check=True, capture_output=True)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def safe_target(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Refusing to replace a symbolic link")


def atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".conductor-", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        private_permissions(temp_path)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def install(*, codex_home: Path, skills_dir: Path, url: str | None = None, token: str | None = None,
            source_root: Path, python: Path, mode: str = "hosted", db_path: Path | None = None,
            data_root: Path | None = None, ssh_target: str | None = None) -> dict:
    if mode == "hosted":
        url = validate_url(url or "")
        if not isinstance(token, str) or len(token) < 32 or any(char.isspace() for char in token):
            raise ValueError("Credential must contain at least 32 non-whitespace characters")
        if db_path is not None:
            raise ValueError("Hosted setup does not use a local database path")
        if ssh_target is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", ssh_target):
            raise ValueError("SSH target must be login@hostname or login@IPv4 address")
    elif mode == "local":
        if token is not None or url is not None or ssh_target is not None:
            raise ValueError("Local setup uses no service URL or credential")
        data_root = (data_root or local_state_root()).expanduser().absolute()
        db_path = validate_local_database(db_path or data_root / "tasks.sqlite3", data_root)
    else:
        raise ValueError("Unknown Conductor client mode")
    codex_home = codex_home.expanduser().absolute()
    skills_dir = skills_dir.expanduser().absolute()
    python = python.absolute()
    if not python.is_file():
        raise ValueError("Python executable does not exist")
    config_path = codex_home / "config.toml"
    settings_dir = codex_home / "conductor-coordination"
    token_path = settings_dir / "service-token"
    settings_path = settings_dir / "client.json"
    data_marker = data_root / ".conductor-local-owner.json" if mode == "local" else None
    marker_content = b'{"purpose":"conductor-local-state","version":1}\n'
    skill_source = source_root / "skills" / SKILL_NAME
    skill_target = skills_dir / SKILL_NAME
    legacy_skill = codex_home / "skills" / SKILL_NAME
    if legacy_skill != skill_target and legacy_skill.exists():
        raise ValueError("A legacy conductor-coordinator skill exists under the Codex home; review it before creating a second copy")
    for path in (codex_home, skills_dir, config_path, settings_dir, token_path, settings_path, skill_target):
        safe_target(path)
    if mode == "local":
        for path in (data_root, data_marker, db_path):
            safe_target(path)
        if data_root.exists() and not data_root.is_dir():
            raise ValueError("Selected local state path must be a directory")
        if db_path.exists() and not db_path.is_file():
            raise ValueError("Selected local ledger path must be a regular file")
        if data_root.exists() and not data_marker.is_file() and any(data_root.iterdir()):
            raise ValueError("Selected local state directory has unowned files; choose a new empty directory")
        if data_marker.exists() and data_marker.read_bytes() != marker_content:
            raise ValueError("Selected local state directory belongs to a different installation")
    original = config_path.read_bytes() if config_path.exists() else b""
    config = tomllib.loads(original.decode("utf-8"))
    spec = importlib.util.spec_from_file_location('conductor_legacy_install', source_root / 'scripts/legacy_install.py')
    compatibility = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compatibility)
    compatibility.guard_client(codex_home, skills_dir, config)
    expected = {"command": str(python), "args": ["-m", "conductor_coordination.client_bridge", "--settings", str(settings_path)],
                "startup_timeout_sec": 20}
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Existing mcp_servers is not a TOML table; review configuration manually")
    existing = servers.get(SERVER_NAME)
    if existing is not None and existing != expected:
        raise ValueError("A different conductor_coordination server already exists; review it manually before installing")
    table = ("\n[mcp_servers.conductor_coordination]\ncommand = " + json.dumps(expected["command"])
             + "\nargs = " + json.dumps(expected["args"]) + "\nstartup_timeout_sec = 20\n")
    updated = original if existing is not None else original + table.encode()
    parsed = tomllib.loads(updated.decode("utf-8"))
    if parsed.get("mcp_servers", {}).get(SERVER_NAME) != expected:
        raise ValueError("Cannot safely append a server to this configuration; configure it manually")
    hosted_settings = {"url": url, "token_file": str(token_path)}
    if ssh_target is not None:
        hosted_settings.update(ssh_target=ssh_target, ssh_remote_port=8012)
    desired_settings = (json.dumps({"mode": "local", "db_path": str(db_path), "state_root": str(data_root)} if mode == "local"
                                   else hosted_settings, indent=2) + "\n").encode()
    desired_files = [(settings_path, desired_settings)] if mode == "local" else [(token_path, token.encode()), (settings_path, desired_settings)]
    if mode == "local" and token_path.exists():
        raise ValueError("Existing hosted credential was preserved; use a fresh Codex profile for local mode")
    for path, content in desired_files:
        if path.exists() and path.read_bytes() != content:
            raise ValueError("Existing client settings or credential differ; review them manually before changing this installation")
    sources = {path.relative_to(skill_source): path.read_bytes() for path in skill_source.rglob("*") if path.is_file()}
    if not sources or Path("SKILL.md") not in sources:
        raise ValueError("Packaged skill is missing")
    if skill_target.exists():
        targets = {path.relative_to(skill_target): path.read_bytes() for path in skill_target.rglob("*") if path.is_file()}
        if targets != sources or any(path.is_symlink() for path in skill_target.rglob("*")):
            raise ValueError("A different conductor-coordinator skill is installed; preserve or remove it manually first")
    backup = codex_home / "config.toml.before-conductor"
    if updated != original and original and backup.exists():
        raise ValueError("Configuration backup already exists; review it before changing configuration")
    created_files = []
    created_dirs = []
    created_skill = False
    try:
        directories = [codex_home, skills_dir, settings_dir]
        if mode == "local":
            directories.extend([data_root, db_path.parent])
        for directory in directories:
            if not directory.exists():
                directory.mkdir(parents=True, mode=0o700)
                created_dirs.append(directory)
        private_permissions(settings_dir)
        if mode == "local":
            private_permissions(data_root)
            private_permissions(db_path.parent)
            if not data_marker.exists():
                atomic_write(data_marker, marker_content)
                created_files.append(data_marker)
            if not db_path.exists():
                from conductor_coordination.coordination_store import Store
                old_mask = os.umask(0o077)
                try:
                    Store(db_path)
                finally:
                    os.umask(old_mask)
                created_files.append(db_path)
            private_permissions(db_path)
        for path, content in desired_files:
            if not path.exists():
                atomic_write(path, content)
                created_files.append(path)
            private_permissions(path)
        if not skill_target.exists():
            created_skill = True
            shutil.copytree(skill_source, skill_target)
        if updated != original:
            if original:
                atomic_write(backup, original)
                created_files.append(backup)
            atomic_write(config_path, updated)
    except BaseException:
        if created_skill and skill_target.exists():
            shutil.rmtree(skill_target)
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return {"config": str(config_path), "skill": str(skill_target), "settings": str(settings_path),
            "changed_config": updated != original}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8012/mcp")
    parser.add_argument("--mode", choices=("hosted", "local"), default="hosted")
    parser.add_argument("--db-path", type=Path, help="Local SQLite ledger under this user's Conductor state directory")
    parser.add_argument("--state-root", type=Path, help="Selected local Conductor data directory")
    parser.add_argument("--token-file", type=Path, help="Read the privately transferred service token; omitted: hidden prompt")
    parser.add_argument("--ssh-target", help="Open a private SSH tunnel whenever this Codex client starts")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--skills-dir", type=Path, default=Path.home() / ".agents" / "skills")
    args = parser.parse_args()
    try:
        if importlib.util.find_spec("mcp") is None:
            raise ValueError("Install this package into the selected Python environment first")
        if args.mode == "local":
            if args.token_file:
                raise ValueError("Local mode does not use a token file")
            result = install(codex_home=args.codex_home, skills_dir=args.skills_dir, mode="local", db_path=args.db_path, data_root=args.state_root, ssh_target=args.ssh_target,
                             source_root=Path(__file__).resolve().parents[1], python=Path(sys.executable))
        else:
            if args.db_path:
                raise ValueError("Hosted mode does not use --db-path")
            token = read_private(args.token_file).strip() if args.token_file else getpass.getpass("Service token (hidden): ").strip()
            result = install(codex_home=args.codex_home, skills_dir=args.skills_dir, url=args.url, token=token, ssh_target=args.ssh_target,
                             source_root=Path(__file__).resolve().parents[1], python=Path(sys.executable))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, "Client setup stopped: " + str(error) + "\n")
    print(json.dumps(result, indent=2))
    print("Open a new Codex chat or reload the client when no work is active, then run the acceptance prompt.")


if __name__ == "__main__":
    main()
