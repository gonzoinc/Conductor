#!/usr/bin/python3
"""Enroll the authenticated SSH login without accepting a caller-supplied identity."""
from __future__ import annotations

import grp
import hashlib
import hmac
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

import yaml


CONFIG = Path('/etc/conductor/conductor.yaml')


def private_directory(path: Path, uid: int, gid: int) -> int:
    if path.is_symlink():
        raise ValueError('Private export directory may not be linked')
    if not path.exists():
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise ValueError('Private export directory must belong to the SSH login and be mode 0700')
    return descriptor


def main() -> None:
    if os.geteuid() != 0 or not os.environ.get('SUDO_USER'):
        raise ValueError('Run this helper only through the VM enrollment sudo rule')
    login = os.environ['SUDO_USER']
    identity = pwd.getpwnam(login)
    if identity.pw_uid == 0:
        raise ValueError('A regular SSH login is required')
    if CONFIG.is_symlink() or not CONFIG.is_file() or CONFIG.stat().st_uid != 0:
        raise ValueError('The root-owned Conductor VM config is unavailable')
    document = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))
    settings = document['conductor']
    group_name = settings.get('enrollment_group', 'conductor-enroll')
    group = grp.getgrnam(group_name)
    if group.gr_gid not in os.getgrouplist(login, identity.pw_gid):
        raise ValueError('This SSH login is not in the VM Conductor enrollment group')

    service_user = settings.get('service_user', 'conductor')
    install = Path(settings.get('install_dir', '/opt/conductor'))
    state = Path(settings.get('state_dir', '/var/lib/conductor'))
    account = login if re.fullmatch(r'[a-z][a-z0-9_-]{0,62}', login) and login != 'owner' else (
        'ssh_' + hashlib.sha256(login.encode('utf-8')).hexdigest()[:24])
    source = state / 'private/users' / (account + '.token')
    python = install / 'current/.venv/bin/python'
    command = ['runuser', '-u', service_user, '--', 'env', 'CONDUCTOR_CONFIG=' + str(CONFIG),
               str(python), '-m', 'conductor_coordination.accounts']
    for arguments in (['create-user', account],
                      ['issue-key', account, '--label', 'initial', '--output', str(source)]):
        result = subprocess.run(command + arguments, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode:
            raise RuntimeError('Conductor account enrollment failed: ' + result.stderr.strip()[:300])
    if source.is_symlink() or not source.is_file():
        raise ValueError('Conductor service key is unavailable')
    secret = source.read_bytes()
    if len(secret.strip()) < 32:
        raise ValueError('Conductor service key is invalid')

    home = Path(identity.pw_dir)
    if home.is_symlink() or not home.is_dir():
        raise ValueError('The SSH login has no safe home directory')
    directory = home / '.conductor-keys'
    descriptor = private_directory(directory, identity.pw_uid, identity.pw_gid)
    name = account + '-initial.token'
    try:
        try:
            output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=descriptor)
        except FileExistsError:
            existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                info = os.fstat(existing)
                data = os.read(existing, 256)
            finally:
                os.close(existing)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != identity.pw_uid
                    or stat.S_IMODE(info.st_mode) != 0o600 or not hmac.compare_digest(data, secret)):
                raise ValueError('Existing exported key differs or is not private; preserved')
        else:
            with os.fdopen(output, 'wb') as stream:
                stream.write(secret)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchown(stream.fileno(), identity.pw_uid, identity.pw_gid)
                os.fchmod(stream.fileno(), 0o600)
    finally:
        os.close(descriptor)
    print(str(directory / name))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit('Conductor enrollment stopped: ' + str(error)) from None
