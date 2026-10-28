"""Behavioral checks for the single setup entry point; no live configuration changes."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('conductor_bootstrap', ROOT / 'scripts/bootstrap.py')
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)
SUPPORT_SPEC = importlib.util.spec_from_file_location('install_support', ROOT / 'scripts/install_support.py')
SUPPORT = importlib.util.module_from_spec(SUPPORT_SPEC)
SUPPORT_SPEC.loader.exec_module(SUPPORT)
MAC_SPEC = importlib.util.spec_from_file_location('install_macos', ROOT / 'scripts/install_macos.py')
MAC = importlib.util.module_from_spec(MAC_SPEC)
with patch.dict(sys.modules, {'bootstrap': BOOTSTRAP, 'install_support': SUPPORT}):
    MAC_SPEC.loader.exec_module(MAC)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name) / 'source with spaces').resolve()
        self.root.mkdir()
        self.normal = Path(self.temporary.name) / 'normal-user'
        (self.normal / '.codex').mkdir(parents=True)
        self.original = b'model = "keep-this-setting"\n'
        (self.normal / '.codex/config.toml').write_bytes(self.original)
        self.token = Path(self.temporary.name) / 'private-token'
        self.token.write_text('synthetic-test-credential-' + 'x' * 40)
        self.calls = []
        self.home = patch.object(BOOTSTRAP.Path, 'home', return_value=self.normal)
        self.home.start()
        self.addCleanup(self.home.stop)
        self.runner = patch.object(BOOTSTRAP, 'run', side_effect=self.fake_run)
        self.run_mock = self.runner.start()
        self.addCleanup(self.runner.stop)

    def fake_run(self, command, *, root, env, stage, quiet=False):
        self.calls.append((command, env, stage))
        if command[1:3] == ['-m', 'venv']:
            directory = Path(command[3])
            executable = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.touch()

    def client(self, *extra):
        BOOTSTRAP.setup(self.root, ['client', '--token-file', str(self.token), *extra])

    def test_isolated_default_preserves_normal_user_and_uses_project_skill(self):
        self.client('--profile', 'vscode')
        self.assertEqual((self.normal / '.codex/config.toml').read_bytes(), self.original)
        self.assertFalse((self.normal / '.agents').exists())
        command = self.calls[-1][0]
        home = Path(command[command.index('--codex-home') + 1])
        skills = Path(command[command.index('--skills-dir') + 1])
        self.assertTrue(home.is_relative_to(self.root / '.codex/conductor-acceptance/vscode'))
        self.assertTrue(skills.is_relative_to(self.root / '.codex/conductor-acceptance/vscode/workspace'))
        self.assertEqual(BOOTSTRAP.tomllib.loads((home / 'config.toml').read_text())['cli_auth_credentials_store'], 'file')

    def test_guided_mode_does_not_announce_readiness_before_bridge_verification(self):
        output = io.StringIO()
        with redirect_stdout(output):
            BOOTSTRAP.setup(self.root, ['client', '--token-file', str(self.token)], prepared_runtime=Path(sys.executable), guided_output=True)
        self.assertTrue(self.run_mock.call_args.kwargs['quiet'])
        self.assertNotIn('Client setup complete', output.getvalue())

    def test_explicit_user_configuration_is_opt_in(self):
        self.client('--configure-user')
        self.assertNotIn('--codex-home', self.calls[-1][0])
        self.assertNotIn('--skills-dir', self.calls[-1][0])
        self.assertFalse((self.root / '.codex/conductor-acceptance').exists())

    def test_repeated_isolated_setup_preserves_authentication_settings(self):
        self.client()
        config = self.root / '.codex/conductor-acceptance/desktop/home/config.toml'
        custom = config.read_text() + 'model = "keep-test-profile"\n'
        config.write_text(custom)
        self.client('--isolated')
        self.assertEqual(config.read_text(), custom)

    def test_missing_token_fails_before_runtime_or_profile_creation(self):
        self.token.unlink()
        with self.assertRaises(ValueError):
            self.client()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / '.codex').exists())

    def test_local_mode_needs_no_service_key_and_passes_selected_data_root(self):
        data = self.root / '.codex/local-data'
        database = data / 'tasks.sqlite3'
        BOOTSTRAP.setup(self.root, ['client', '--mode', 'local', '--local-state-root', str(data),
                                    '--db-path', str(database), '--profile', 'vscode'])
        command = self.calls[-1][0]
        self.assertEqual(command[command.index('--mode') + 1], 'local')
        self.assertEqual(command[command.index('--state-root') + 1], str(data))
        self.assertEqual(command[command.index('--db-path') + 1], str(database))
        self.assertNotIn('--token-file', command)
        self.assertNotIn('--url', command)

    def test_local_mode_rejects_database_outside_selected_root_before_install(self):
        with self.assertRaises(ValueError):
            BOOTSTRAP.setup(self.root, ['client', '--mode', 'local', '--local-state-root', str(self.root / 'data'),
                                        '--db-path', str(self.root / 'elsewhere/tasks.sqlite3')])
        self.assertEqual(self.calls, [])

    def test_frozen_gui_can_supply_real_python_for_runtime_creation(self):
        python = self.root / 'trusted-python.exe'
        python.touch()
        BOOTSTRAP.setup(self.root, ['client', '--mode', 'local'], python_executable=python)
        self.assertEqual(self.calls[0][0][0], str(python))
        self.assertEqual(self.calls[0][0][1:3], ['-m', 'venv'])

    def test_remote_plaintext_url_fails_before_installation(self):
        with self.assertRaises(ValueError):
            self.client('--url', 'http://coordination.example.com/mcp')
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / '.codex').exists())

    def test_existing_unowned_profile_is_preserved(self):
        profile = self.root / '.codex/conductor-acceptance/desktop'
        profile.mkdir(parents=True)
        sentinel = profile / 'user-file'
        sentinel.write_text('preserve')
        with self.assertRaises(ValueError):
            self.client()
        self.assertEqual(sentinel.read_text(), 'preserve')
        self.assertEqual(self.calls, [])

    def test_normal_codex_tree_cannot_be_selected_as_test_root(self):
        with self.assertRaises(ValueError):
            self.client('--test-root', str(self.normal / '.codex/test'))
        self.assertEqual(self.calls, [])

    def test_cancelled_dependency_install_stops_then_allows_retry(self):
        def cancel(command, **kwargs):
            self.fake_run(command, **kwargs)
            if kwargs['stage'] == 'Installing legacy MCP client dependencies':
                raise subprocess.CalledProcessError(130, command)
        self.run_mock.side_effect = cancel
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['bootstrap.py', str(self.root), 'client', '--token-file', str(self.token)]), redirect_stdout(stdout), redirect_stderr(stderr):
            result = BOOTSTRAP.main()
        self.assertEqual(result, 130)
        self.assertNotIn('Client setup complete', stdout.getvalue())
        self.assertIn('Setup stopped', stderr.getvalue())
        self.assertFalse((self.root / '.codex/conductor-acceptance').exists())
        self.run_mock.side_effect = self.fake_run
        self.client()
        self.assertEqual(self.calls[-1][2], 'Configuring isolated client')

    def test_unowned_runtime_is_not_reused(self):
        runtime = self.root / '.codex/conductor-venv'
        runtime.mkdir(parents=True)
        (runtime / 'sentinel').write_text('preserve')
        with self.assertRaises(ValueError):
            self.client()
        self.assertEqual((runtime / 'sentinel').read_text(), 'preserve')
        self.assertEqual(self.calls, [])

    def test_server_requires_linux_before_mutation(self):
        with patch.object(sys, 'platform', 'win32'), self.assertRaises(ValueError):
            BOOTSTRAP.setup(self.root, ['server'])
        self.assertEqual(self.calls, [])

    def test_server_waits_for_health_in_selected_project_without_global_env_change(self):
        with patch.object(sys, 'platform', 'linux'), patch.object(BOOTSTRAP.shutil, 'which', return_value='/docker'), patch.dict(os.environ, {'COMPOSE_PROJECT_NAME': 'existing-project'}):
            BOOTSTRAP.setup(self.root, ['server', '--project', 'isolated-test', '--port', '48958'])
            self.assertEqual(os.environ['COMPOSE_PROJECT_NAME'], 'existing-project')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1]['COMPOSE_PROJECT_NAME'], 'isolated-test')
        self.assertIn('--port', self.calls[0][0])
        self.assertIn('--wait', self.calls[1][0])

    def test_server_failure_does_not_proceed_to_start_or_report_ready(self):
        self.run_mock.side_effect = subprocess.CalledProcessError(77, ['docker'])
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'platform', 'linux'), patch.object(BOOTSTRAP.shutil, 'which', return_value='/docker'), patch.object(sys, 'argv', ['bootstrap.py', str(self.root), 'server']), redirect_stdout(stdout), redirect_stderr(stderr):
            result = BOOTSTRAP.main()
        self.assertEqual(result, 77)
        self.assertEqual(self.run_mock.call_count, 1)
        self.assertNotIn('Service ready', stdout.getvalue())

    def test_export_creates_new_private_parent_without_build_or_start(self):
        target = self.root / '.codex/private-export/service-token'
        with patch.object(sys, 'platform', 'linux'), patch.object(BOOTSTRAP.shutil, 'which', return_value='/docker'):
            BOOTSTRAP.setup(self.root, ['server', '--project', 'isolated-test', '--export-token', str(target)])
        self.assertTrue(target.parent.is_dir())
        if os.name != 'nt':
            self.assertEqual(target.parent.stat().st_mode & 0o077, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('--export-token', self.calls[0][0])
        self.assertEqual(self.calls[0][1]['COMPOSE_PROJECT_NAME'], 'isolated-test')

    def test_invalid_project_is_rejected_before_docker(self):
        with patch.object(sys, 'platform', 'linux'), self.assertRaises(ValueError):
            BOOTSTRAP.setup(self.root, ['server', '--project', '../existing'])
        self.assertEqual(self.calls, [])


class MacInstallerTests(unittest.TestCase):
    def test_shared_user_setup_is_verified_before_success(self):
        calls = []
        home = ROOT / '.codex/example-home'
        output = io.StringIO()
        with patch.object(sys, 'platform', 'darwin'), patch.object(os, 'geteuid', return_value=501, create=True), \
             patch.dict(os.environ, {'CODEX_HOME': str(home)}), \
             patch.object(BOOTSTRAP, 'prepare_runtime', return_value=ROOT / '.codex/conductor-venv/bin/python'), \
             patch.object(MAC.subprocess, 'run', side_effect=lambda *args, **kwargs: calls.append('install')) as install, \
             patch.object(MAC, 'verify_bridge', side_effect=lambda *args: calls.append('verify')) as verify, \
             redirect_stdout(output):
            MAC.setup(ROOT)
        self.assertEqual(calls, ['install', 'verify'])
        command = install.call_args.args[0]
        self.assertEqual(command[command.index('--mode') + 1], 'local')
        self.assertEqual(command[command.index('--codex-home') + 1], str(home))
        self.assertEqual(command[command.index('--skills-dir') + 1], str(Path.home() / '.agents/skills'))
        verify.assert_called_once_with(ROOT / '.codex/conductor-venv/bin/python',
                                       home / 'conductor-coordination/client.json')
        self.assertIn('setup complete', output.getvalue())
        self.assertIn('VS Code', output.getvalue())

    def test_failed_health_check_does_not_report_success(self):
        output = io.StringIO()
        with patch.object(sys, 'platform', 'darwin'), patch.object(os, 'geteuid', return_value=501, create=True), \
             patch.object(BOOTSTRAP, 'prepare_runtime', return_value=Path(sys.executable)), \
             patch.object(MAC.subprocess, 'run'), \
             patch.object(MAC, 'verify_bridge', side_effect=ValueError('health failed')), redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, 'health failed'):
                MAC.setup(ROOT)
        self.assertNotIn('setup complete', output.getvalue())

    def test_unsupported_platform_or_root_stops_before_changes(self):
        for platform, uid in [('linux', 501), ('darwin', 0)]:
            with self.subTest(platform=platform, uid=uid), patch.object(sys, 'platform', platform), \
                 patch.object(os, 'geteuid', return_value=uid, create=True), \
                 patch.object(BOOTSTRAP, 'prepare_runtime') as setup, patch.object(MAC, 'verify_bridge') as verify:
                with self.assertRaises(ValueError):
                    MAC.setup(ROOT)
                setup.assert_not_called()
                verify.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'macOS shell entry point')
    def test_help_and_invalid_arguments_do_not_install(self):
        result = subprocess.run(['bash', str(ROOT / 'install-macos.sh'), '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('VS Code', result.stdout)
        result = subprocess.run(['bash', str(ROOT / 'install-macos.sh'), '--unknown'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
