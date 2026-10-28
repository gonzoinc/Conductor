"""Install Conductor's SSH command without pip, MCP or a background process."""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys

from install_host import (_config_access, _extract_bundle, _import_existing_sqlite,
                          _private_state, _service_account, _service_can_read_config,
                          _sha, _write_if_changed,
                          load_settings)


COMMAND = Path('/usr/local/lib/conductor-command')
RULE = Path('/etc/sudoers.d/conductor-command')


def _run(command: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f'{Path(command[0]).name} failed with exit {result.returncode}')
    return result


def _release(settings, digest: str) -> Path:
    return settings.install_dir / 'ssh-releases' / digest[:16]


def _active(settings) -> Path | None:
    link = settings.install_dir / 'ssh-current'
    if not link.is_symlink():
        if link.exists():
            raise ValueError('Existing ssh-current is not an installer-owned link')
        return None
    target = link.resolve(strict=True)
    if target.parent != settings.install_dir / 'ssh-releases':
        raise ValueError('Existing ssh-current points outside the release directory')
    return target


def _group_ready(settings) -> bool:
    try:
        group = grp.getgrnam(settings.enrollment_group)
    except KeyError:
        return False
    for name in settings.initial_users:
        try:
            login = pwd.getpwnam(name)
        except KeyError:
            continue
        if login.pw_uid != 0 and group.gr_gid not in os.getgrouplist(name, login.pw_gid):
            return False
    return True


def install(settings, bundle: Path, digest: str, *, check=False) -> dict:
    if not bundle.is_file() or bundle.is_symlink() or _sha(bundle) != digest:
        raise ValueError('Pinned release bundle is unavailable or differs from SHA256')
    release = _release(settings, digest)
    source = release / 'scripts/ssh_command.py'
    rule_text = f'%{settings.enrollment_group} ALL=(root) NOPASSWD: {COMMAND}\n'
    ready = (_active(settings) == release and source.is_file()
             and (release / '.conductor-bundle-sha256').is_file()
             and (release / '.conductor-bundle-sha256').read_text().strip() == digest
             and COMMAND.is_file() and COMMAND.read_text() == source.read_text()
             and RULE.is_file() and RULE.read_text() == rule_text and _group_ready(settings)
             and _service_can_read_config(settings))
    if check:
        if not ready:
            raise RuntimeError('Pinned SSH command release is not ready')
        return {'status': 'ready', 'release_sha256': digest}
    if ready:
        return {'status': 'unchanged', 'release_sha256': digest}
    account = _service_account(settings)
    _private_state(settings, account)
    _config_access(settings, account)
    _import_existing_sqlite(settings, account)
    releases = settings.install_dir / 'ssh-releases'
    if releases.is_symlink():
        raise ValueError('Release directory may not be linked')
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    settings.install_dir.chmod(0o755)
    releases.chmod(0o755)
    if not release.exists():
        staging = releases / ('.installing-' + digest[:16])
        if staging.exists():
            raise ValueError('Previous release staging directory exists')
        try:
            _extract_bundle(bundle, staging)
            if not (staging / 'scripts/ssh_command.py').is_file():
                raise ValueError('Bundle is missing SSH command')
            (staging / '.conductor-bundle-sha256').write_text(digest + '\n')
            for root, directories, files in os.walk(staging, followlinks=False):
                for name in directories:
                    (Path(root) / name).chmod(0o755)
                for name in files:
                    (Path(root) / name).chmod(0o644)
            staging.rename(release)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    if not source.is_file() or (release / '.conductor-bundle-sha256').read_text().strip() != digest:
        raise ValueError('Existing release directory has a different identity; preserved')
    try:
        grp.getgrnam(settings.enrollment_group)
    except KeyError:
        _run(['groupadd', '--system', settings.enrollment_group])
    group = grp.getgrnam(settings.enrollment_group)
    for name in settings.initial_users:
        try:
            login = pwd.getpwnam(name)
        except KeyError:
            continue
        if login.pw_uid != 0 and group.gr_gid not in os.getgrouplist(name, login.pw_gid):
            _run(['usermod', '-aG', settings.enrollment_group, name])
    _write_if_changed(COMMAND, source.read_text(encoding='utf-8'), 0o755)
    _write_if_changed(RULE, rule_text, 0o440)
    _run(['visudo', '-cf', str(RULE)])
    _run(['runuser', '-u', settings.service_user, '--', 'env',
          'CONDUCTOR_CONFIG=' + str(settings.config_path),
          'PYTHONPATH=' + str(release / 'src'), '/usr/bin/python3', '-c',
          'from conductor_coordination.host_config import open_store; open_store()'])
    current = settings.install_dir / 'ssh-current'
    stage_link = settings.install_dir / '.ssh-current-new'
    if stage_link.exists() or stage_link.is_symlink():
        raise ValueError('Previous release link staging exists')
    stage_link.symlink_to(release, target_is_directory=True)
    os.replace(stage_link, current)
    return {'status': 'installed', 'release_sha256': digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root through sudo or Salt')
    try:
        settings = load_settings(args.config)
        result = install(settings, args.bundle, args.sha256.lower(), check=args.check)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, 'Conductor SSH install stopped: ' + str(error) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
