"""Exercise the unattended host install transitions without changing host services."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import io
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import install_host as host
from conductor_coordination.coordination_store import Store


def bundle(path: Path, payload: bytes = b'one') -> str:
    with tarfile.open(path, 'w:gz') as archive:
        for name, content in (
            ('pyproject.toml', b'[build-system]\n'),
            ('src/conductor_coordination/coordination_server.py', payload),
        ):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HostInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = host.Settings('root', self.root / 'app', self.root / 'state',
                                      '127.0.0.1', 8012, ('alice',), None)
        self.state = {'active': False, 'health': True, 'start_fail': False}
        self.unit = self.root / 'etc/systemd/system/conductor.service'
        self.env = self.root / 'etc/conductor/service.env'
        self.owner = self.root / 'etc/conductor/host-install.json'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(host, '_unit_path', return_value=self.unit))
        self.stack.enter_context(patch.object(host, '_env_path', return_value=self.env))
        self.stack.enter_context(patch.object(host, '_owner_path', return_value=self.owner))
        self.stack.enter_context(patch.object(host, '_systemd_running', return_value=True))
        self.stack.enter_context(patch.object(host, '_service_account', return_value=pwd.getpwnam('root')))
        self.stack.enter_context(patch.object(host, '_run', side_effect=self.run_command))
        self.stack.enter_context(patch.object(host, '_bootstrap_accounts', side_effect=self.bootstrap))
        self.stack.enter_context(patch.object(host, '_health', side_effect=self.health))

    def test_reserved_owner_cannot_be_an_initial_user(self):
        config = self.root / 'conductor.yaml'
        config.write_text('conductor:\n  state_dir: ' + str(self.root / 'state') + '\n  initial_users: [owner]\ndatabase:\n  engine: sqlite\n  mode: create\n  path: ' + str(self.root / 'state/tasks.sqlite3') + '\n')
        config.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'owner is reserved'):
            host.load_settings(config)

    def test_existing_sqlite_import_preserves_source(self):
        source = self.root / 'existing.sqlite3'
        Store(source).claim('prior', 'manager', 'desktop', 'claim')
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.root / 'fresh-state'
        config = self.root / 'conductor.yaml'
        config.write_text('conductor:\n  state_dir: ' + str(destination) + '\n  initial_users: [alice]\n'
                          'database:\n  engine: sqlite\n  mode: existing\n  path: ' + str(destination / 'tasks.sqlite3') +
                          '\n  source_path: ' + str(source) + '\n')
        config.chmod(0o600)
        settings = host.load_settings(config)
        destination.mkdir()
        host._import_existing_sqlite(settings, pwd.getpwnam('root'))
        self.assertEqual(Store(destination / 'tasks.sqlite3').snapshot('prior')['workload']['manager'], 'manager')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)

    def test_release_is_traversable_after_private_umask(self):
        release = self.settings.install_dir / 'releases' / 'candidate'
        old_mask = os.umask(0o077)
        try:
            package = release / '.venv/lib'
            package.mkdir(parents=True)
            program = package / 'python'
            program.write_text('fixture')
            program.chmod(0o700)
            data = package / 'metadata'
            data.write_text('fixture')
        finally:
            os.umask(old_mask)
        host._release_access(self.settings, release)
        for path in (self.settings.install_dir, release.parent, release, release / '.venv', package):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(program.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o644)

    def run_command(self, command, **kwargs):
        if command[:2] == ['systemctl', 'is-active']:
            return subprocess.CompletedProcess(command, 0 if self.state['active'] else 3)
        if command[:2] == ['systemctl', 'start']:
            self.state['active'] = True
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == ['systemctl', 'stop']:
            self.state['active'] = False
            return subprocess.CompletedProcess(command, 0)
        if '-m' in command and 'venv' in command:
            Path(command[-1], 'bin').mkdir(parents=True)
            Path(command[-1], 'bin/python').write_text('test')
        return subprocess.CompletedProcess(command, 0)

    def bootstrap(self, settings, python):
        database = settings.state_dir / 'tasks.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS probe(value TEXT)')
        for path in (settings.state_dir / 'private/owner.token',
                     settings.state_dir / 'private/health.token',
                     settings.state_dir / 'private/users/alice.token'):
            path.write_text('dummy')

    def health(self, settings, timeout=60):
        return self.state['active'] and self.state['health']

    def test_first_install_and_repeat_are_noninteractive_and_idempotent(self):
        archive = self.root / 'source.tar.gz'
        digest = bundle(archive)
        result = host.install(self.settings, archive, digest)
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(host.install(self.settings, archive, digest, check=True)['status'], 'ready')
        self.assertEqual(host.install(self.settings, archive, digest)['status'], 'unchanged')
        self.assertEqual(list((self.settings.state_dir / 'backups').iterdir()), [])

    def test_failed_upgrade_restores_prior_release_and_database(self):
        first, second = self.root / 'first.tar.gz', self.root / 'second.tar.gz'
        old_digest, new_digest = bundle(first), bundle(second, b'two')
        host.install(self.settings, first, old_digest)
        database = self.settings.state_dir / 'tasks.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('INSERT INTO probe(value) VALUES (?)', ('original',))
        self.state['health'] = False
        with self.assertRaisesRegex(RuntimeError, 'prior release.*restored'):
            host.install(self.settings, second, new_digest)
        self.state['health'] = True
        self.assertEqual(host._current_release(self.settings),
                         self.settings.install_dir / 'releases' / old_digest[:16])
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute('SELECT value FROM probe').fetchall(), [('original',)])
        self.assertTrue(self.state['active'])
        self.assertEqual(len(list((self.settings.state_dir / 'backups').glob('*.sqlite3'))), 1)

    def test_mismatched_hash_and_unsafe_archive_make_no_owned_install(self):
        archive = self.root / 'source.tar.gz'
        bundle(archive)
        with self.assertRaisesRegex(ValueError, 'SHA256 differs'):
            host.install(self.settings, archive, '0' * 64)
        self.assertFalse(self.owner.exists())
        with tarfile.open(archive, 'w:gz') as output:
            entry = tarfile.TarInfo('../escape')
            entry.size = 1
            output.addfile(entry, io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError, 'unsafe path'):
            host.install(self.settings, archive, hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertFalse((self.root / 'escape').exists())

    def test_backup_failure_restarts_previous_service(self):
        first, second = self.root / 'first.tar.gz', self.root / 'second.tar.gz'
        old_digest, new_digest = bundle(first), bundle(second, b'two')
        host.install(self.settings, first, old_digest)
        with patch.object(host, '_backup', side_effect=RuntimeError('backup failed')):
            with self.assertRaisesRegex(RuntimeError, 'backup failed'):
                host.install(self.settings, second, new_digest)
        self.assertTrue(self.state['active'])
        self.assertEqual(host._current_release(self.settings),
                         self.settings.install_dir / 'releases' / old_digest[:16])


if __name__ == '__main__':
    unittest.main()
