"""Container initialization and an authenticated healthcheck without token output."""
from __future__ import annotations
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import urllib.request

UID = GID = 10001

def reject_symlinks(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise RuntimeError('State and credential paths may not contain symlinks')

def require_local(path):
    """Refuse known network filesystems for SQLite and private credentials."""
    mounts = Path('/proc/mounts')
    if not mounts.exists(): return
    path = str(Path(path).absolute())
    best = ('', '')
    for line in mounts.read_text().splitlines():
        parts = line.split()
        if len(parts) < 3: continue
        mount = parts[1].replace('\\040', ' ')
        if (path == mount or path.startswith(mount.rstrip('/') + '/')) and len(mount) > len(best[0]):
            best = (mount, parts[2])
    if best[1].startswith(('nfs', 'cifs', 'smb', 'fuse.sshfs')):
        raise RuntimeError('State must use local disk, not a network filesystem')

def initialize(state=Path('/state'), backups=Path('/backups')):
    from conductor_coordination.coordination_server import _read_token
    if os.geteuid() != 0: raise RuntimeError('Initialization requires its temporary root container')
    os.umask(0o077)
    for directory in [state, state / 'data', backups]:
        reject_symlinks(directory)
        require_local(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        os.chown(directory, UID, GID)
    token = state / 'service-token'
    reject_symlinks(token)
    if token.exists():
        if not stat.S_ISREG(token.stat().st_mode): raise RuntimeError('Credential must be a regular file')
        _read_token(token)
        if token.stat().st_uid != UID: raise RuntimeError('Existing credential has unexpected owner; no replacement performed')
    else:
        fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output: output.write(secrets.token_urlsafe(48))
        os.chown(token, UID, GID)
    health_token = state / 'health-token'
    reject_symlinks(health_token)
    if health_token.exists():
        if not stat.S_ISREG(health_token.stat().st_mode): raise RuntimeError('Health credential must be a regular file')
        _read_token(health_token)
        if health_token.stat().st_uid != UID: raise RuntimeError('Existing health credential has unexpected owner')
    else:
        fd = os.open(health_token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output: output.write(secrets.token_urlsafe(48))
        os.chown(health_token, UID, GID)
    from conductor_coordination.coordination_store import Store
    if hmac.compare_digest(_read_token(token), _read_token(health_token)):
        raise RuntimeError('Owner and health credentials must be different')
    Store(state / 'data/tasks.sqlite3').ensure_owner_key(_read_token(token))
    for database_file in (state / 'data').glob('tasks.sqlite3*'):
        reject_symlinks(database_file)
        if not database_file.is_file(): raise RuntimeError('Database path must be a regular file')
        os.chmod(database_file, 0o600)
        os.chown(database_file, UID, GID)
    print('Private state and credential ready; existing credential preserved.')

def health():
    token = Path(os.environ.get('CONDUCTOR_HEALTH_TOKEN_FILE', '/state/health-token')).read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:' + os.environ.get('MCP_PORT', '8012') + '/healthz', headers={'Authorization':'Bearer '+token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        if json.load(response).get('status') != 'ok': raise RuntimeError('Service is not ready')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init','health'])
    args = parser.parse_args()
    try:
        if args.action == 'init': initialize()
        else: health()
    except Exception:
        parser.exit(1, 'Initialization or health check failed; check private paths, ownership, permissions and service readiness.\n')

if __name__ == '__main__': main()
