"""Private online SQLite snapshots and no-overwrite isolated restore."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from conductor_coordination.deployment import reject_symlinks, require_local

def copy_database(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    reject_symlinks(source)
    reject_symlinks(destination)
    require_local(destination)
    if not source.is_file(): raise RuntimeError('Source database is missing')
    if source.stat().st_mode & 0o077: raise RuntimeError('Source database must be private')
    if not destination.parent.is_dir(): raise RuntimeError('Destination directory must already exist')
    if destination.parent.stat().st_mode & 0o077: raise RuntimeError('Destination directory must be private')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(destination)) as copied:
                original.backup(copied)
                if copied.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise RuntimeError('Snapshot integrity check failed')
    except BaseException:
        destination.unlink()
        raise
    return destination

def backup(source, directory):
    directory = Path(directory)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    return copy_database(source, directory / ('tasks-' + stamp + '.sqlite3'))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    snapshot = commands.add_parser('backup')
    snapshot.add_argument('--source', default=os.environ.get('CONDUCTOR_DB', '/state/data/tasks.sqlite3'))
    snapshot.add_argument('--directory', default=os.environ.get('CONDUCTOR_BACKUP_DIR', '/backups'))
    restore = commands.add_parser('restore', help='Create a separate verified database; never overwrite a live file')
    restore.add_argument('--source', required=True)
    restore.add_argument('--destination', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        path = backup(args.source, args.directory) if args.action == 'backup' else copy_database(args.source, args.destination)
    except (OSError, RuntimeError, sqlite3.Error):
        parser.exit(1, 'Database copy failed; check private local paths, source integrity, and a non-existing destination.\n')
    print('Verified private database copy created: ' + str(path))

if __name__ == '__main__': main()
