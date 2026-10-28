"""Install a pinned Conductor bundle as a private, noninteractive systemd service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import grp
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler


import yaml


SERVICE = 'conductor.service'


@dataclass(frozen=True)
class Settings:
    service_user: str
    install_dir: Path
    state_dir: Path
    bind: str
    port: int
    initial_users: tuple[str, ...]
    wheelhouse: Path | None
    database: dict | None = None
    config_path: Path | None = None
    enrollment_group: str = 'conductor-enroll'


def _local_path(value: str, name: str) -> Path:
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a local absolute directory')
    path = Path(value)
    if not path.is_absolute() or path == Path('/') or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f'{name} must be a local absolute directory without symbolic links')
    if path.as_posix().startswith('//'):
        raise ValueError(f'{name} must use local storage')
    return path


def _filesystem_type(path: Path) -> str:
    """Find the longest mounted parent, including before a new state directory exists."""
    resolved = path
    while not resolved.exists():
        resolved = resolved.parent
    mounted = ('/', '')
    for line in Path('/proc/mounts').read_text().splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount = fields[1].replace('\\040', ' ')
        if (resolved == Path(mount) or Path(mount) in resolved.parents) and len(mount) > len(mounted[0]):
            mounted = (mount, fields[2])
    return mounted[1]


def load_settings(path: Path) -> Settings:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Config must be a regular root-owned file')
    mode = path.stat()
    if mode.st_uid != 0 or stat.S_IMODE(mode.st_mode) & 0o037:
        raise ValueError('Config must belong to root and be mode 0600 or 0640')
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or set(document) != {'conductor', 'database'}:
        raise ValueError('Config needs conductor and database mappings')
    if not isinstance(document['conductor'], dict) or not isinstance(document['database'], dict):
        raise ValueError('Config sections must be mappings')
    values = document['conductor']
    allowed = {'service_user', 'install_dir', 'state_dir', 'bind', 'port', 'initial_users', 'wheelhouse', 'enrollment_group'}
    if set(values) - allowed:
        raise ValueError('Config has unknown fields; review the example before installing')
    user = values.get('service_user', 'conductor')
    if not isinstance(user, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', user):
        raise ValueError('service_user must be a simple Linux account name')
    enrollment_group = values.get('enrollment_group', 'conductor-enroll')
    if not isinstance(enrollment_group, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', enrollment_group):
        raise ValueError('enrollment_group must be a simple Linux group name')
    install_dir = _local_path(values.get('install_dir', '/opt/conductor'), 'install_dir')
    state_dir = _local_path(values.get('state_dir', '/var/lib/conductor'), 'state_dir')
    if install_dir == state_dir or install_dir in state_dir.parents or state_dir in install_dir.parents:
        raise ValueError('Install and state directories must be separate')
    database = document['database']
    if database.get('engine') not in {'sqlite', 'mysql'} or database.get('mode') not in {'create', 'existing'}:
        raise ValueError('database.engine must be sqlite or mysql and mode must be create or existing')
    if database['engine'] == 'mysql':
        if database['mode'] != 'existing':
            raise ValueError('Provision MySQL with Salt first, then select existing mode')
        allowed_database = {'engine', 'mode', 'host', 'port', 'name', 'user', 'password_file', 'ssl_ca', 'unix_socket'}
        for required in ('name', 'user', 'password_file'):
            if not isinstance(database.get(required), str) or not database[required]:
                raise ValueError(f'MySQL {required} is required')
        if bool(database.get('host')) == bool(database.get('unix_socket')):
            raise ValueError('MySQL requires exactly one host or unix_socket')
        if database.get('host') and not database.get('ssl_ca'):
            raise ValueError('MySQL TCP requires ssl_ca')
    else:
        allowed_database = {'engine', 'mode', 'path', 'source_path'}
        if not isinstance(database.get('path'), str):
            raise ValueError('SQLite path is required')
    if set(database) - allowed_database:
        raise ValueError('Config has unknown database fields')
    if database['engine'] == 'sqlite':
        db_path = Path(database['path'])
        if db_path.parent != state_dir:
            raise ValueError('SQLite database must be inside the private state directory')
        if _filesystem_type(state_dir) in {'nfs', 'nfs4', 'cifs', 'smb3', '9p', 'fuse.sshfs'}:
            raise ValueError('SQLite state must be on a local filesystem')
        if database['mode'] == 'existing':
            source = Path(database.get('source_path', ''))
            if not source.is_absolute() or source.is_symlink() or not source.is_file():
                raise ValueError('Selected existing SQLite source does not exist')
            with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as existing:
                columns = {row[1] for row in existing.execute("PRAGMA table_info(workloads)")}
                if not {'id', 'manager', 'client', 'epoch', 'expires', 'state', 'summary', 'updated'} <= columns:
                    raise ValueError('Existing SQLite file is not a Conductor database')
        if database['mode'] == 'create' and db_path.exists() and not _owner_path().is_file():
            raise ValueError('Selected new SQLite database already exists; use existing mode')
    bind = values.get('bind', '127.0.0.1')
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        raise ValueError('bind must be an IP address') from None
    if not address.is_loopback:
        raise ValueError('The direct service must bind to loopback; use SSH or a TLS proxy for remote clients')
    port = values.get('port', 8012)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('port must be 1..65535')
    names = values.get('initial_users', [])
    if not isinstance(names, list) or not names or len(names) > 100 or any(
        not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,62}', name) for name in names
    ) or len(set(names)) != len(names) or 'owner' in names:
        raise ValueError('initial_users requires 1..100 distinct simple usernames; owner is reserved')
    wheelhouse = values.get('wheelhouse')
    if wheelhouse is not None:
        wheelhouse = _local_path(wheelhouse, 'wheelhouse')
        if not wheelhouse.is_dir():
            raise ValueError('wheelhouse must already exist')
    return Settings(user, install_dir, state_dir, str(address), port, tuple(names), wheelhouse, database, path, enrollment_group)


def _run(command: list[str], *, env=None, capture=False, accepted=(0,)) -> subprocess.CompletedProcess:
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode not in accepted:
        raise RuntimeError(f'{Path(command[0]).name} stopped with exit {result.returncode}; inspect host prerequisites and service logs')
    return result


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_bundle(bundle: Path, destination: Path) -> None:
    destination.mkdir(mode=0o755)
    seen: set[str] = set()
    with tarfile.open(bundle, 'r:*') as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            parts = tuple(part for part in relative.parts if part != '.')
            if not parts:
                continue
            if relative.is_absolute() or '..' in parts or member.name.startswith('/') or '\\' in member.name:
                raise ValueError('Bundle contains an unsafe path')
            name = '/'.join(parts)
            if name in seen or not (member.isfile() or member.isdir()):
                raise ValueError('Bundle contains a duplicate or unsupported entry')
            seen.add(name)
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError('Bundle file has no contents')
                with target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)
    if not (destination / 'pyproject.toml').is_file() or not (destination / 'src/conductor_coordination/coordination_server.py').is_file():
        raise ValueError('Bundle does not contain the Conductor service package')


def _release_access(settings: Settings, release: Path) -> None:
    """Let the unprivileged service traverse code/venv without exposing state."""
    for directory in (settings.install_dir, settings.install_dir / 'releases', release):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Service release directory is missing or linked')
        directory.chmod(0o755)
    for root, directories, files in os.walk(release, followlinks=False):
        for name in directories:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(0o755)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                executable = bool(stat.S_IMODE(path.stat().st_mode) & 0o111)
                path.chmod(0o755 if executable else 0o644)


def _service_account(settings: Settings) -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam(settings.service_user)
    except KeyError:
        _run(['useradd', '--system', '--user-group', '--home-dir', str(settings.state_dir), '--no-create-home',
              '--shell', '/usr/sbin/nologin', settings.service_user])
        account = pwd.getpwnam(settings.service_user)
    if account.pw_uid == 0:
        raise ValueError('Service account may not be root')
    return account


def _private_state(settings: Settings, account: pwd.struct_passwd) -> None:
    for directory in (settings.state_dir, settings.state_dir / 'private', settings.state_dir / 'private/users', settings.state_dir / 'backups'):
        if directory.is_symlink():
            raise ValueError('Service data directory may not be a symbolic link')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        os.chown(directory, account.pw_uid, account.pw_gid)


def _config_access(settings: Settings, account: pwd.struct_passwd) -> None:
    if not settings.config_path:
        return
    config = settings.config_path
    if config.parent.is_symlink() or config.is_symlink():
        raise ValueError('Config paths may not be symbolic links')
    if config.parent == Path('/etc/conductor'):
        os.chown(config.parent, 0, account.pw_gid)
        config.parent.chmod(0o750)
        os.chown(config, 0, account.pw_gid)
        config.chmod(0o640)
    _run(['runuser', '-u', settings.service_user, '--', 'test', '-r', str(config)])
    if settings.database and settings.database['engine'] == 'mysql':
        password = Path(settings.database['password_file'])
        if password.parent == config.parent and password.stat().st_uid == 0:
            os.chown(password, 0, account.pw_gid)
            password.chmod(0o640)
        _run(['runuser', '-u', settings.service_user, '--', 'test', '-r', str(password)])
        if settings.database.get('ssl_ca'):
            _run(['runuser', '-u', settings.service_user, '--', 'test', '-r', settings.database['ssl_ca']])


def _import_existing_sqlite(settings: Settings, account: pwd.struct_passwd) -> None:
    database = settings.database
    if not database or database['engine'] != 'sqlite' or database['mode'] != 'existing':
        return
    destination = Path(database['path'])
    if destination.exists():
        return
    source = Path(database['source_path'])
    stage = destination.with_name('.conductor-import.sqlite3')
    if stage.exists():
        raise ValueError('Previous database import staging file exists; review it before retrying')
    try:
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as original, sqlite3.connect(stage) as copied:
            original.backup(copied)
            if copied.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('Imported SQLite database failed integrity check')
        os.chown(stage, account.pw_uid, account.pw_gid)
        stage.chmod(0o600)
        os.replace(stage, destination)
    finally:
        stage.unlink(missing_ok=True)


def _as_service(settings: Settings, python: Path, *arguments: str) -> None:
    environment = dict(os.environ)
    if settings.config_path:
        environment['CONDUCTOR_CONFIG'] = str(settings.config_path)
    _run(['runuser', '-u', settings.service_user, '--', str(python), '-m', 'conductor_coordination.accounts',
          '--db', str(settings.state_dir / 'tasks.sqlite3'), *arguments], env=environment)


def _bootstrap_accounts(settings: Settings, python: Path) -> None:
    private = settings.state_dir / 'private'
    _as_service(settings, python, 'bootstrap', '--owner-token-file', str(private / 'owner.token'),
                '--health-token-file', str(private / 'health.token'))
    for name in settings.initial_users:
        _as_service(settings, python, 'create-user', name)
        _as_service(settings, python, 'issue-key', name, '--label', 'initial', '--output',
                    str(private / 'users' / (name + '.token')))


def _unit(settings: Settings) -> str:
    python = settings.install_dir / 'current/.venv/bin/python'
    primary_group = grp.getgrgid(pwd.getpwnam(settings.service_user).pw_gid).gr_name
    return f'''[Unit]
Description=Conductor coordination ledger
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={settings.service_user}
Group={primary_group}
WorkingDirectory={settings.install_dir / 'current'}
EnvironmentFile=/etc/conductor/service.env
ExecStart={python} -m conductor_coordination.coordination_server
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths={settings.state_dir}
CapabilityBoundingSet=
LockPersonality=yes

[Install]
WantedBy=multi-user.target
'''


def _environment(settings: Settings) -> str:
    private = settings.state_dir / 'private'
    database_line = (f'CONDUCTOR_CONFIG={settings.config_path}\n' if settings.config_path else
                     f'CONDUCTOR_DB={settings.state_dir / "tasks.sqlite3"}\n')
    if settings.config_path:
        database_line += f'CONDUCTOR_CONFIG_SHA256={_sha(settings.config_path)}\n'
    return (database_line +
            f'CONDUCTOR_TOKEN_FILE={private / "owner.token"}\n'
            f'CONDUCTOR_HEALTH_TOKEN_FILE={private / "health.token"}\n'
            f'MCP_HOST={settings.bind}\nMCP_PORT={settings.port}\n'
            'PYTHONDONTWRITEBYTECODE=1\n')


def _write_if_changed(path: Path, content: str, mode: int) -> bool:
    if path.is_symlink():
        raise ValueError(f'Refusing symbolic link at {path}')
    if path.exists() and path.read_text() == content and stat.S_IMODE(path.stat().st_mode) == mode:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.conductor-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def _backup(settings: Settings, account: pwd.struct_passwd) -> Path | None:
    if settings.database and settings.database['engine'] == 'mysql':
        return None  # Database backups are managed by the MySQL administrator.
    database = settings.state_dir / 'tasks.sqlite3'
    if not database.is_file():
        return None
    destination = settings.state_dir / 'backups' / ('tasks-before-' + time.strftime('%Y%m%d-%H%M%S') + '.sqlite3')
    with sqlite3.connect(database) as source, sqlite3.connect(destination) as target:
        source.backup(target)
        integrity = target.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity != 'ok':
        destination.unlink(missing_ok=True)
        raise RuntimeError('Consistent backup integrity check failed')
    destination.chmod(0o600)
    os.chown(destination, account.pw_uid, account.pw_gid)
    return destination


def _restore_backup(settings: Settings, account: pwd.struct_passwd, backup: Path) -> None:
    database = settings.state_dir / 'tasks.sqlite3'
    temporary = settings.state_dir / '.tasks-restore.sqlite3'
    if temporary.exists():
        raise RuntimeError('Restore staging file already exists; preserve it for inspection')
    shutil.copy2(backup, temporary)
    temporary.chmod(0o600)
    os.chown(temporary, account.pw_uid, account.pw_gid)
    for suffix in ('-wal', '-shm'):
        Path(str(database) + suffix).unlink(missing_ok=True)
    os.replace(temporary, database)


def _health(settings: Settings, timeout=60) -> bool:
    key = (settings.state_dir / 'private/health.token').read_text().strip()
    host = '[' + settings.bind + ']' if ':' in settings.bind else settings.bind
    request = Request(f'http://{host}:{settings.port}/healthz', headers={'Authorization': 'Bearer ' + key})
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=2) as response:
                answer = json.loads(response.read(16000))
            if answer.get('status') == 'ok':
                return True
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return False


def _service_can_read_config(settings: Settings) -> bool:
    """Health-only requests do not open the ledger; check the service files too."""
    if not settings.config_path:
        return True
    paths = [settings.config_path]
    if settings.database and settings.database['engine'] == 'mysql':
        paths.append(Path(settings.database['password_file']))
        if settings.database.get('ssl_ca'):
            paths.append(Path(settings.database['ssl_ca']))
    for path in paths:
        result = subprocess.run(['runuser', '-u', settings.service_user, '--', 'test', '-r', str(path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode:
            return False
    return True


def _enrollment_paths() -> tuple[Path, Path]:
    return Path('/usr/local/lib/conductor-enroll'), Path('/etc/sudoers.d/conductor-enroll')


def _enrollment_ready(settings: Settings, release: Path) -> bool:
    if not settings.config_path:
        return True
    helper, rule = _enrollment_paths()
    source = release / 'scripts/enroll_ssh_user.py'
    if not source.is_file() or not helper.is_file() or not rule.is_file():
        return False
    if helper.read_text(encoding='utf-8') != source.read_text(encoding='utf-8') or rule.read_text() != (
            f'%{settings.enrollment_group} ALL=(root) NOPASSWD: {helper}\n'):
        return False
    try:
        group = grp.getgrnam(settings.enrollment_group)
    except KeyError:
        return False
    for name in settings.initial_users:
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            continue
        if account.pw_uid != 0 and group.gr_gid not in os.getgrouplist(name, account.pw_gid):
            return False
    return True


def _enrollment_access(settings: Settings, release: Path) -> None:
    if not settings.config_path:
        return
    try:
        group = grp.getgrnam(settings.enrollment_group)
    except KeyError:
        _run(['groupadd', '--system', settings.enrollment_group])
        group = grp.getgrnam(settings.enrollment_group)
    for name in settings.initial_users:
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            continue
        if account.pw_uid != 0 and group.gr_gid not in os.getgrouplist(name, account.pw_gid):
            _run(['usermod', '-aG', settings.enrollment_group, name])
    helper, rule = _enrollment_paths()
    source = release / 'scripts/enroll_ssh_user.py'
    if not source.is_file():
        raise ValueError('Release bundle is missing the SSH enrollment helper')
    helper.parent.mkdir(parents=True, exist_ok=True)
    _write_if_changed(helper, source.read_text(encoding='utf-8'), 0o755)
    _write_if_changed(rule, f'%{settings.enrollment_group} ALL=(root) NOPASSWD: {helper}\n', 0o440)
    _run(['visudo', '-cf', str(rule)])


def _current_release(settings: Settings) -> Path | None:
    current = settings.install_dir / 'current'
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise ValueError('Existing current release is not an installer-owned link')
    resolved = current.resolve(strict=True)
    if settings.install_dir / 'releases' not in resolved.parents:
        raise ValueError('Existing release link points outside owned release directory')
    return resolved


def _point_current(settings: Settings, release: Path) -> None:
    current = settings.install_dir / 'current'
    temporary = settings.install_dir / '.current-new'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('A prior temporary release link exists; review it before retrying')
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, current)


def _unit_path() -> Path:
    return Path('/etc/systemd/system') / SERVICE


def _env_path() -> Path:
    return Path('/etc/conductor/service.env')


def _owner_path() -> Path:
    return Path('/etc/conductor/host-install.json')


def _systemd_running() -> bool:
    return Path('/run/systemd/system').is_dir() and bool(shutil.which('systemctl'))


def _owned_roots(settings: Settings, *, create: bool) -> bool:
    marker = _owner_path()
    legacy = {'install_dir': str(settings.install_dir), 'state_dir': str(settings.state_dir),
              'service_user': settings.service_user}
    expected = dict(legacy)
    if settings.database:
        expected['database'] = (('sqlite:' + settings.database['path']) if settings.database['engine'] == 'sqlite'
                                else ('mysql:' + settings.database['name']))
    if marker.is_symlink():
        raise ValueError('Host ownership marker may not be a symbolic link')
    if marker.is_file():
        current = json.loads(marker.read_text())
        migrating_legacy = (current == legacy and settings.database and
                            settings.database['engine'] == 'sqlite' and
                            settings.database['path'] == str(settings.state_dir / 'tasks.sqlite3'))
        if current != expected and not migrating_legacy:
            raise ValueError('Existing Conductor host ownership differs; preserve its files')
        if migrating_legacy and create:
            _write_if_changed(marker, json.dumps(expected, sort_keys=True) + '\n', 0o600)
        return True
    if settings.install_dir.exists() or settings.state_dir.exists():
        raise ValueError('Selected install/state directory already exists without Conductor ownership; preserved')
    if not create:
        return False
    marker.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _write_if_changed(marker, json.dumps(expected, sort_keys=True) + '\n', 0o600)
    return True


@contextmanager
def _lock():
    path = Path('/run/lock/conductor-install.lock')
    with path.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def install(settings: Settings, bundle: Path, expected_sha: str, *, check=False) -> dict:
    if not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha):
        raise ValueError('Expected SHA256 must be exactly 64 hex characters')
    if bundle.is_symlink() or not bundle.is_file():
        raise ValueError('Bundle must be a regular local file')
    actual_sha = _sha(bundle)
    if actual_sha.lower() != expected_sha.lower():
        raise ValueError('Bundle SHA256 differs from the pinned value; no system change was made')
    expected_sha = expected_sha.lower()
    release = settings.install_dir / 'releases' / expected_sha[:16]
    unit_path = _unit_path()
    env_path = _env_path()
    owned = _owned_roots(settings, create=False)
    old = _current_release(settings)
    ready = (old == release and (release / '.conductor-bundle-sha256').is_file()
             and owned
             and (release / '.conductor-bundle-sha256').read_text().strip() == expected_sha
             and all((settings.state_dir / 'private/users' / (name + '.token')).is_file()
                     for name in settings.initial_users)
             and unit_path.is_file() and unit_path.read_text() == _unit(settings)
             and env_path.is_file() and env_path.read_text() == _environment(settings)
             and _service_can_read_config(settings)
             and _enrollment_ready(settings, release)
             and _run(['systemctl', 'is-active', '--quiet', SERVICE], accepted=(0, 3, 4)).returncode == 0
             and _health(settings, timeout=3))
    if check:
        if ready:
            return {'status': 'ready', 'release_sha256': expected_sha}
        raise RuntimeError('Conductor service or pinned release is not ready')
    if ready:
        return {'status': 'unchanged', 'release_sha256': expected_sha}
    if not _systemd_running():
        raise RuntimeError('This host must run systemd; use a supported Linux VM or the advanced Compose route')
    _owned_roots(settings, create=True)
    account = _service_account(settings)
    _private_state(settings, account)
    _config_access(settings, account)
    _import_existing_sqlite(settings, account)
    releases = settings.install_dir / 'releases'
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    settings.install_dir.chmod(0o755)
    releases.chmod(0o755)
    if release.exists():
        marker = release / '.conductor-bundle-sha256'
        if not marker.is_file() or marker.read_text().strip() != expected_sha:
            raise ValueError('Existing release directory has a different identity; preserved')
    else:
        staged = releases / ('.installing-' + expected_sha[:16])
        if staged.exists():
            raise ValueError('Incomplete owned staging directory exists; review it before retrying')
        try:
            _extract_bundle(bundle, staged)
            _run([sys.executable, '-m', 'venv', str(staged / '.venv')])
            pip = [str(staged / '.venv/bin/python'), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input']
            if settings.wheelhouse:
                pip += ['--no-index', '--find-links', str(settings.wheelhouse)]
            package = str(staged) + ('[hosted,legacy-mcp]' if settings.config_path else '[legacy-mcp]')
            _run(pip + [package], env=dict(os.environ, PIP_NO_INPUT='1'))
            (staged / '.conductor-bundle-sha256').write_text(expected_sha + '\n')
            _release_access(settings, staged)
            staged.rename(release)
        except Exception:
            # This exact staging directory was created by this invocation.
            shutil.rmtree(staged, ignore_errors=True)
            raise
    _release_access(settings, release)
    active = _run(['systemctl', 'is-active', '--quiet', SERVICE], accepted=(0, 3, 4)).returncode == 0
    previous_unit = unit_path.read_text() if unit_path.is_file() else None
    previous_env = env_path.read_text() if env_path.is_file() else None
    private = settings.state_dir / 'private'
    expected_tokens = (private / 'owner.token', private / 'health.token',
                       *(private / 'users' / (name + '.token') for name in settings.initial_users))
    existing_tokens = {path for path in expected_tokens if path.exists()}
    if active:
        _run(['systemctl', 'stop', SERVICE])
    try:
        backup = _backup(settings, account) if old else None
    except Exception:
        if active:
            _run(['systemctl', 'start', SERVICE])
        raise
    try:
        _bootstrap_accounts(settings, release / '.venv/bin/python')
        if old != release:
            _point_current(settings, release)
        unit_changed = _write_if_changed(unit_path, _unit(settings), 0o644)
        env_changed = _write_if_changed(env_path, _environment(settings), 0o600)
        if unit_changed or env_changed:
            _run(['systemctl', 'daemon-reload'])
        _run(['systemctl', 'enable', SERVICE])
        _run(['systemctl', 'start', SERVICE])
        if not _health(settings):
            raise RuntimeError('Installed service did not pass health')
        _enrollment_access(settings, release)
    except Exception as error:
        if old:
            try:
                _run(['systemctl', 'stop', SERVICE], accepted=(0, 3, 4))
                if backup:
                    _restore_backup(settings, account, backup)
                for path in expected_tokens:
                    if path not in existing_tokens and path.is_file() and not path.is_symlink():
                        path.unlink()
                if _current_release(settings) != old:
                    _point_current(settings, old)
                for path, content, mode in ((unit_path, previous_unit, 0o644), (env_path, previous_env, 0o600)):
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        _write_if_changed(path, content, mode)
                _run(['systemctl', 'daemon-reload'])
                if active:
                    _run(['systemctl', 'start', SERVICE])
            except Exception:
                raise RuntimeError('New service failed; automatic restore also failed. Preserve the backup and inspect service logs before retrying.') from error
            raise RuntimeError('New service failed; prior release and consistent database backup were restored.') from error
        raise
    return {'status': 'installed' if old is None else 'updated', 'release_sha256': expected_sha,
            'backup': str(backup) if backup else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--check', action='store_true', help='Exit 0 only when the pinned service is healthy')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.exit(1, 'Host setup must run as root.\n')
    os.umask(0o077)
    try:
        settings = load_settings(args.config)
        with _lock():
            result = install(settings, args.bundle, args.sha256, check=args.check)
    except (OSError, ValueError, KeyError, RuntimeError, tarfile.TarError) as error:
        parser.exit(1, 'Conductor host setup stopped: ' + str(error) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
