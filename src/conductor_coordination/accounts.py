"""Host-local account administration. Secret keys are only written to private files."""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import stat

from conductor_coordination.coordination_store import Store
from conductor_coordination.host_config import open_store


def _private_path(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Private key path must not contain a symbolic link")
    if not path.parent.is_dir() or stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ValueError("Create a private parent directory (mode 0700) first")
    return path


def _read_existing(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Existing key path must be a regular file")
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_uid != os.geteuid():
        raise ValueError("Existing key file must belong to this service account and be mode 0600")
    token = path.read_text().strip()
    if len(token) < 32 or any(char.isspace() for char in token):
        raise ValueError("Existing key file is invalid")
    return token


def _write_new(path: Path, token: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(token)


def _bootstrap_file(value: str) -> str:
    path = _private_path(value)
    if path.exists():
        return _read_existing(path)
    token = secrets.token_urlsafe(48)
    _write_new(path, token)
    return token


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("CONDUCTOR_DB", "/state/data/tasks.sqlite3")))
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Create owner and health key files privately; safe to repeat")
    bootstrap.add_argument("--owner-token-file", required=True)
    bootstrap.add_argument("--health-token-file", required=True)
    create = commands.add_parser("create-user", help="Create or find a user")
    create.add_argument("username")
    issue = commands.add_parser("issue-key", help="Issue a user key to a private file; safe to repeat")
    issue.add_argument("username")
    issue.add_argument("--label", required=True)
    issue.add_argument("--output", required=True)
    revoke = commands.add_parser("revoke-key", help="Revoke one key ID; safe to repeat")
    revoke.add_argument("key_id")
    args = parser.parse_args(argv)
    try:
        os.umask(0o077)
        store = open_store() if os.environ.get("CONDUCTOR_CONFIG") else Store(args.db)
        if args.command == "bootstrap":
            owner = _bootstrap_file(args.owner_token_file)
            health = _bootstrap_file(args.health_token_file)
            if hmac.compare_digest(owner, health):
                raise ValueError("Owner and health credentials must be different")
            store.ensure_owner_key(owner)
            result = {"status": "ready", "owner_token_file": args.owner_token_file,
                      "health_token_file": args.health_token_file}
        elif args.command == "create-user":
            result = store.create_user(args.username)
        elif args.command == "issue-key":
            path = _private_path(args.output)
            if path.exists():
                existing = store.key_info(_read_existing(path))
                if not existing or existing["name"] != args.username or existing["label"] != args.label or existing["revoked"] is not None:
                    raise ValueError("Existing output file is not an active key for that user and label; preserved")
                result = {"status": "existing", "key_id": existing["id"], "file": str(path)}
            else:
                issued = store.issue_key(args.username, args.label)
                try:
                    _write_new(path, issued["token"])
                except Exception:
                    store.revoke_key(issued["key_id"])
                    raise
                result = {"status": "created", "key_id": issued["key_id"], "file": str(path)}
        else:
            changed = store.revoke_key(args.key_id)
            result = {"status": "revoked" if changed else "already revoked", "key_id": args.key_id}
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "Account operation failed: " + str(error) + "\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
