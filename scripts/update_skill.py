#!/usr/bin/env python3
"""Update a known Conductor skill, backing it up without changing client state."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "skills/conductor-coordinator/"


def files(folder: Path) -> dict[str, bytes]:
    paths = [folder, *folder.rglob("*")]
    if any(path.is_symlink() for path in paths):
        raise ValueError("Skill contains a symbolic link; it was preserved")
    result = {}
    for path in paths[1:]:
        if not path.is_file():
            continue
        name = path.relative_to(folder).as_posix()
        data = path.read_bytes().replace(b"\r\n", b"\n")
        if name == ".conductor-ssh-skill" and data.strip() == b"conductor-ssh-client":
            continue
        result[name] = data
    return result


def known_version(installed: dict[str, bytes], root: Path) -> bool:
    git = ["git", "-c", "safe.directory=" + root.as_posix()]
    revisions = subprocess.check_output(git + ["log", "--all", "-100", "--format=%H", "--", PREFIX], cwd=root).decode().splitlines()
    for revision in revisions:
        names = subprocess.check_output(git + ["ls-tree", "-r", "--name-only", revision, "--", PREFIX], cwd=root).decode().splitlines()
        if {name[len(PREFIX):] for name in names} != set(installed):
            continue
        if all(subprocess.check_output(git + ["show", revision + ":" + name], cwd=root).replace(b"\r\n", b"\n") == installed[name[len(PREFIX):]] for name in names):
            return True
    return False


def update(target: Path, codex_home: Path, root: Path = ROOT) -> dict:
    if not target.is_dir():
        raise ValueError("No installed skill found; use the installation instructions first")
    current, desired = files(target), files(root / PREFIX)
    if current == desired:
        return {"status": "unchanged", "skill": str(target)}
    if not known_version(current, root):
        raise ValueError("Installed skill differs from known repository versions; custom files were preserved. Review them before updating.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup = codex_home / "conductor-updates" / stamp / "conductor-coordinator"
    backup.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(target, backup)
    staged = target.parent / (".conductor-update-" + stamp)
    retired = target.parent / (".conductor-old-" + stamp)
    try:
        shutil.copytree(root / PREFIX, staged)
        marker = target / ".conductor-ssh-skill"
        if marker.is_file():
            shutil.copy2(marker, staged / marker.name)
        target.rename(retired)
        try:
            staged.rename(target)
        except BaseException:
            retired.rename(target)
            raise
        shutil.rmtree(retired)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return {"status": "updated", "skill": str(target), "backup": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--skills-dir", type=Path)
    args = parser.parse_args()
    try:
        home = args.codex_home.expanduser().absolute()
        candidates = [args.skills_dir.expanduser().absolute() / "conductor-coordinator"] if args.skills_dir else [
            Path.home() / ".agents/skills/conductor-coordinator", home / "skills/conductor-coordinator"]
        found = [path for path in candidates if path.exists()]
        if len(found) != 1:
            raise ValueError("Expected one installed Conductor skill; use --skills-dir to select it explicitly")
        result = update(found[0], home)
        for key, value in result.items():
            print(f"{key}: {value}")
        print("Finish active work, then start new chats in Codex desktop and VS Code. No configuration, credentials or ledger data changed.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Skill update stopped: " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
