#!/usr/bin/python3
"""Root-owned, narrow sudo entry point for the authenticated SSH login."""
from __future__ import annotations

import grp
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

import yaml


CONFIG = Path('/etc/conductor/conductor.yaml')
MAX_REQUEST = 120_000
MAX_RESPONSE = 200_000


def fail(code: str, message: str) -> int:
    print(json.dumps({'ok': False, 'error': {'code': code, 'message': message}}, separators=(',', ':')))
    return 1


def main() -> int:
    if len(sys.argv) != 1 or os.geteuid() != 0 or not os.environ.get('SUDO_USER'):
        return fail('unauthorized', 'Invoke Conductor through the VM SSH login')
    login = os.environ['SUDO_USER']
    try:
        identity = pwd.getpwnam(login)
        if identity.pw_uid == 0:
            return fail('unauthorized', 'A regular SSH login is required')
        info = CONFIG.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o037:
            return fail('configuration', 'The VM configuration is not private and root-owned')
        settings = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))['conductor']
        group = grp.getgrnam(settings.get('enrollment_group', 'conductor-enroll'))
        if group.gr_gid not in os.getgrouplist(login, identity.pw_gid):
            return fail('forbidden', 'This SSH login is not authorized for Conductor')
        service_user = settings.get('service_user', 'conductor')
        install = Path(settings.get('install_dir', '/opt/conductor'))
        source = install / 'ssh-current/src'
        if not source.is_dir() or source.is_symlink():
            return fail('configuration', 'The SSH command release is unavailable')
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            return fail('request_too_large', 'Request exceeds 120 KB')
        result = subprocess.run(
            ['runuser', '-u', service_user, '--', 'env', 'CONDUCTOR_CONFIG=' + str(CONFIG),
             'PYTHONPATH=' + str(source), '/usr/bin/python3',
             '-m', 'conductor_coordination.ssh_worker', '--login', login],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
        if len(result.stdout) > MAX_RESPONSE or result.returncode not in {0, 1}:
            return fail('service_error', 'Conductor could not complete the request')
        if not result.stdout:
            return fail('service_error', 'Conductor returned no response')
        sys.stdout.buffer.write(result.stdout)
        return result.returncode
    except (OSError, KeyError, ValueError, subprocess.TimeoutExpired):
        return fail('service_error', 'Conductor could not complete the request')


if __name__ == '__main__':
    raise SystemExit(main())
