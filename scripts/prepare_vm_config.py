"""Create the first host YAML from guided VM questions; leave existing YAML untouched."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def ask(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{label}{suffix}: ").strip()
    if not answer and default is not None:
        return default
    if not answer:
        raise ValueError(f"{label} is required")
    return answer


def quoted(value: str) -> str:
    return json.dumps(value)


def create_config(first_user: str) -> str:
    print("\nChoose where Conductor keeps task and account data.")
    print("  1. Create a new private SQLite database on this VM (one-person default)")
    print("  2. Import an existing Conductor SQLite database into private VM storage")
    print("  3. Use an existing MySQL database (shared or HA service)")
    choice = ask("Choose 1, 2, or 3", "1")
    if choice not in {"1", "2", "3"}:
        raise ValueError("Choose 1, 2, or 3")
    state = Path("/var/lib/conductor")
    lines = ["# Root-owned; Salt can manage this file without prompts.", "conductor:",
             "  service_user: conductor", "  install_dir: /opt/conductor",
             f"  state_dir: {state}", "  bind: 127.0.0.1", "  port: 8012",
             f"  initial_users: [{quoted(first_user)}]", "database:"]
    if choice in {"1", "2"}:
        lines += ["  engine: sqlite", f"  mode: {'create' if choice == '1' else 'existing'}",
                  f"  path: {state / 'tasks.sqlite3'}"]
        if choice == "2":
            source = Path(ask("Existing SQLite database file to import")).expanduser()
            if not source.is_absolute() or not source.is_file() or source.is_symlink():
                raise ValueError("The existing database must be an absolute regular file")
            lines.append(f"  source_path: {quoted(str(source))}")
            print("The existing file will be preserved. Conductor will use a checked copy.")
    else:
        print("The MySQL database and application account must already exist.")
        print("Provide a TLS CA file for TCP. The password is read from a private file, never entered here.")
        transport = ask("MySQL host name or IP (or type socket)")
        lines += ["  engine: mysql", "  mode: existing"]
        if transport == "socket":
            lines.append(f"  unix_socket: {quoted(ask('MySQL Unix socket file'))}")
        else:
            lines += [f"  host: {quoted(transport)}", f"  port: {int(ask('MySQL port', '3306'))}",
                      f"  ssl_ca: {quoted(ask('TLS CA certificate file'))}"]
        lines += [f"  name: {quoted(ask('MySQL database name'))}",
                  f"  user: {quoted(ask('MySQL application username'))}",
                  f"  password_file: {quoted(ask('Private MySQL password FILE'))}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-user", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", args.initial_user):
        raise ValueError("Invalid initial account name")
    content = create_config(args.initial_user)
    with args.output.open("w", encoding="utf-8") as target:
        target.write(content)


if __name__ == "__main__":
    main()
