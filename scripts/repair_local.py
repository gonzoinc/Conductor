"""Explicit, offline local-ledger repair. Stop both clients before using this tool."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from install_client import atomic_write, private_permissions
from conductor_coordination.client_bridge import ensure_private, read_private, validate_local_database
from conductor_coordination.state_paths import validate_shared_windows_path

MARKER = ".conductor-local-owner.json"
JOURNAL = ".conductor-repair.json"


def snapshot(connection: sqlite3.Connection) -> list:
    """Compare schema and every row, including history and SQLite sequence state."""
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Ledger integrity check failed")
    schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    tables = []
    for kind, name, _, _ in schema:
        if kind == "table":
            quoted = '"' + name.replace('"', '""') + '"'
            rows = connection.execute("SELECT * FROM " + quoted).fetchall()
            tables.append((name, sorted(repr(row) for row in rows)))
    if not {"workloads", "tasks", "operations", "events"}.issubset({name for name, _ in tables}):
        raise ValueError("Source has no ledger tables; refusing an empty replacement")
    return [schema, tables]


def source_candidates(configured: Path) -> list[Path]:
    """Report legacy candidates; never select one automatically."""
    candidates = [configured]
    legacy = Path.home() / "AppData/Local"
    if configured.is_relative_to(legacy):
        suffix = configured.relative_to(legacy)
        packages = legacy / "Packages"
        if packages.is_dir():
            candidates.extend(packages.glob("*/LocalCache/Local/" + suffix.as_posix()))
    unique = []
    for path in candidates:
        if path.is_file() and not any(path.samefile(other) for other in unique):
            unique.append(path)
    return unique


def rollback(journal: Path, *, clients_stopped: bool) -> None:
    if not clients_stopped:
        raise ValueError("Stop Desktop, VS Code and all ledger writers before repair or rollback")
    state = json.loads(read_private(journal))
    if state.get("purpose") != "conductor-local-repair" or state.get("version") != 1 or not isinstance(state.get("settings"), list):
        raise ValueError("Invalid Conductor repair journal")
    # Preflight every setting before restoring any of them.
    for item in state["settings"]:
        current = read_private(Path(item["path"]))
        if current not in (item["before"], item["after"]):
            raise ValueError("Client settings changed after repair; refusing to overwrite them")
    for item in state["settings"]:
        atomic_write(Path(item["path"]), item["before"].encode())
    # Keep both databases and the journal: writes after cutover must never be discarded.


def migrate(source: Path, destination: Path, settings_paths: list[Path], *, clients_stopped: bool) -> Path:
    if not clients_stopped:
        raise ValueError("Stop Desktop, VS Code and all ledger writers before repair or rollback")
    source = source.expanduser().absolute()
    destination = destination.expanduser().absolute()
    validate_shared_windows_path(destination)
    database = validate_local_database(destination / "tasks.sqlite3", destination)
    if destination.exists():
        raise ValueError("Repair destination must not exist; existing files were preserved")
    if not settings_paths or len(set(settings_paths)) != len(settings_paths):
        raise ValueError("Supply each affected client settings file exactly once")
    ensure_private(source)
    source_root = None
    changes = []
    for path in settings_paths:
        path = path.expanduser().absolute()
        before = read_private(path)
        setting = json.loads(before)
        if setting.get("mode") != "local":
            raise ValueError("Repair only accepts existing local client settings")
        configured = Path(setting["db_path"])
        configured_root = Path(setting.get("state_root", configured.parent))
        validate_local_database(configured, configured_root)
        candidates = source_candidates(configured)
        if len(candidates) != 1 or not candidates[0].samefile(source):
            raise ValueError(f"Source is missing, ambiguous, or does not match {path}; candidates: {candidates}")
        relative = configured.relative_to(configured_root)
        physical_root = source.parents[len(relative.parts) - 1]
        if source_root is not None and source_root != physical_root:
            raise ValueError("Client settings disagree on source state root")
        source_root = physical_root
        setting.update(db_path=str(database), state_root=str(destination))
        changes.append({"path": str(path), "before": before, "after": json.dumps(setting, indent=2) + "\n"})
    validate_local_database(source, source_root)
    ensure_private(source_root, directory=True)
    ensure_private(source.parent, directory=True)
    marker_text = read_private(source_root / MARKER)
    if json.loads(marker_text) != {"purpose": "conductor-local-state", "version": 1}:
        raise ValueError("Source ownership marker is invalid")
    # Explicit URI modes prevent sqlite3 from creating a missing source database.
    with closing(sqlite3.connect(source.as_uri() + "?mode=rw", uri=True, timeout=5)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            expected = snapshot(original)
            destination.mkdir(mode=0o700)
            private_permissions(destination)
            journal = destination / JOURNAL
            atomic_write(journal, (json.dumps({"purpose": "conductor-local-repair", "version": 1, "source": str(source), "settings": changes}, indent=2) + "\n").encode())
            try:
                with closing(sqlite3.connect(database)) as copied:
                    private_permissions(database)
                    original.backup(copied)
                    if snapshot(copied) != expected:
                        raise ValueError("Destination schema or task history differs from source")
                atomic_write(destination / MARKER, marker_text.encode())
                for item in changes:
                    if read_private(Path(item["path"])) != item["before"]:
                        raise ValueError("Client settings changed during repair")
                    atomic_write(Path(item["path"]), item["after"].encode())
            except BaseException:
                rollback(journal, clients_stopped=True)
                raise
            finally:
                lock.rollback()
    return journal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--settings", type=Path, action="append", default=[])
    parser.add_argument("--rollback", type=Path, help="Repair journal to restore original settings; preserves both ledgers")
    parser.add_argument("--clients-stopped", action="store_true", help="Confirm all clients and ledger writers are stopped")
    args = parser.parse_args()
    try:
        if args.rollback:
            rollback(args.rollback, clients_stopped=args.clients_stopped)
        elif args.source and args.destination:
            journal = migrate(args.source, args.destination, args.settings, clients_stopped=args.clients_stopped)
            print(f"Ledger copied and settings switched. Rollback journal: {journal}")
            print("Keep the original ledger. Actual Desktop and VS Code acceptance is still pending.")
        else:
            parser.error("Supply --source, --destination and --settings, or --rollback")
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"Repair stopped: {error}\n")


if __name__ == "__main__":
    main()
