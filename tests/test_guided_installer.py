"""First-time user journeys for the guided installer; no normal client is launched."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


GUIDED = load('guided_fixture', ROOT / 'scripts/guided_install.py')
SUPPORT = load('support_fixture', ROOT / 'scripts/install_support.py')
CORE = load('bootstrap_fixture', ROOT / 'scripts/bootstrap.py')


class FakeCore:
    owned_directory = staticmethod(CORE.owned_directory)
    compatibility = staticmethod(CORE.compatibility)

    def __init__(self, events):
        self.events = events

    def prepare_runtime(self, root):
        self.events.append('runtime')
        return Path(sys.executable)

    def setup(self, root, arguments, *, prepared_runtime, guided_output):
        self.events.append('configure')
        assert '--isolated' in arguments and '--configure-user' not in arguments
        assert guided_output
        profile = arguments[arguments.index('--profile') + 1]
        home = Path(arguments[arguments.index('--test-root') + 1]) / profile / 'home'
        home.mkdir(parents=True, mode=0o700)
        (home / 'config.toml').write_text('cli_auth_credentials_store = "file"\n')


class GuidedJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source with spaces'
        self.root.mkdir()
        self.events = []
        self.core = FakeCore(self.events)
        self.patchers = []
        self.mock(GUIDED, 'support', return_value=SUPPORT)
        self.mock(SUPPORT, 'client_requirements', return_value=[])
        self.wizard = GUIDED.Wizard(self.root, self.core)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.synthetic = 'synthetic-conductor-service-key-' + 'x' * 40

    def mock(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        self.patchers.append(patcher)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def run_answers(self, answers):
        with patch('builtins.input', side_effect=iter(answers)), redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            self.wizard.run()

    def test_first_run_exit_has_explanation_and_no_files(self):
        self.run_answers(['5'])
        output = self.stdout.getvalue()
        self.assertIn('Choose local for a ledger on this computer', output)
        self.assertIn('Choose hosted for a Linux Docker ledger', output)
        self.assertIn('A Git-host token is needed only', output)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.events, [])

    def test_prerequisite_explanation_does_not_create_a_session(self):
        self.run_answers(['4'])
        self.assertIn('Docker Engine/Compose', self.stdout.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_command_failure_explains_next_action_without_dumping_arguments(self):
        failure = subprocess.CalledProcessError(255, ['ssh', 'DO_NOT_DUMP_COMMAND_ARGUMENTS'])
        with patch.object(GUIDED.Wizard, 'run', side_effect=failure), redirect_stderr(self.stderr), self.assertRaises(ValueError):
            GUIDED.main(self.root, [], self.core)
        self.assertIn('required setup command stopped', self.stderr.getvalue())
        self.assertNotIn('DO_NOT_DUMP_COMMAND_ARGUMENTS', self.stderr.getvalue())

    def test_new_windows_user_without_linux_host_is_not_asked_for_a_token(self):
        with patch.object(sys, 'platform', 'win32'):
            with self.assertRaises(GUIDED.Cancelled):
                self.run_answers(['1', '3'])
        self.assertIn('A Linux service host', self.stdout.getvalue())
        self.assertNotIn('Connection file path', self.stdout.getvalue())
        self.assertEqual(self.events, [])
        self.assertFalse(any(self.root.rglob('service-token')))

    def test_fresh_local_route_prepares_clients_without_service_or_key(self):
        probe = self.mock(self.wizard.io, 'service_probe')
        install = self.mock(self.wizard.io, 'install_service')
        tunnel = self.mock(self.wizard.io, 'tunnel')
        bridge = self.mock(self.wizard.io, 'verify_bridge')
        with patch.object(sys, 'platform', 'win32'), patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'local-state')}):
            self.run_answers(['1', '1', '1', '3'])
        probe.assert_not_called()
        install.assert_not_called()
        tunnel.assert_not_called()
        self.assertEqual(bridge.call_count, 2)
        self.assertEqual(self.events.count('configure'), 2)
        self.assertEqual(self.wizard.state['service_kind'], 'on-device')
        self.assertNotIn('token_file', self.wizard.state)
        self.assertFalse(any(self.root.rglob('service-token')))
        self.assertIn('SETUP AND CONNECTION CHECKS PASSED', self.stdout.getvalue())

    def test_pause_during_ssh_questions_does_not_save_an_incomplete_host(self):
        with patch.object(sys, 'platform', 'win32'), patch('builtins.input', side_effect=iter(['1', '2', 'q'])), redirect_stdout(self.stdout):
            GUIDED.main(self.root, [], self.core)
        base = self.root / '.codex/guided-install'
        identifier = json.loads((base / 'latest.json').read_text())['id']
        state = json.loads((base / identifier / 'state.json').read_text())
        self.assertNotIn('service_kind', state)
        self.assertNotIn('ssh_target', state)
        self.assertEqual(self.events, [])
        self.assertIn('Progress is saved privately', self.stdout.getvalue())

    def test_missing_remote_prerequisite_stops_before_install_or_client(self):
        self.mock(self.wizard.io, 'service_probe', return_value={'missing': ['Docker Engine with Compose'], 'port': 48958})
        install = self.mock(self.wizard.io, 'install_service')
        with patch.object(sys, 'platform', 'win32'), self.assertRaises(GUIDED.Cancelled):
            self.run_answers(['1', '2', 'linux-vm', 'alice', ''])
        install.assert_not_called()
        self.assertEqual(self.events, [])
        self.assertNotIn('SETUP AND CONNECTION CHECKS PASSED', self.stdout.getvalue())
        self.assertIn('Enter the IP address or host name', self.stdout.getvalue())
        self.assertNotIn('private-key FILE path', self.stdout.getvalue())

    def test_existing_service_no_file_routes_back_to_service_first(self):
        with patch.object(sys, 'platform', 'win32'), self.assertRaises(GUIDED.Cancelled):
            self.run_answers(['2', '1', '3'])
        self.assertIn('Its key is generated automatically', self.stdout.getvalue())
        self.assertEqual(self.events, [])
        self.assertFalse(any(self.root.rglob('service-token')))

    def test_fresh_remote_flow_generates_key_before_profiles_and_does_not_echo_it(self):
        self.mock(self.wizard.io, 'service_probe', return_value={'missing': [], 'port': 48958})
        install = self.mock(self.wizard.io, 'install_service', return_value=self.synthetic.encode())
        self.mock(self.wizard.io, 'tunnel', return_value='http://127.0.0.1:48958/mcp')
        self.mock(self.wizard.io, 'health', side_effect=lambda *a, **k: self.events.append('health') or True)
        self.mock(self.wizard.io, 'verify_bridge', side_effect=lambda *a: self.events.append('bridge'))
        with patch.object(sys, 'platform', 'win32'):
            self.run_answers(['1', '2', 'linux-vm', 'alice', '', '1', '3'])
        install.assert_called_once()
        self.assertLess(self.events.index('health'), self.events.index('configure'))
        self.assertEqual(self.events.count('runtime'), 1)
        self.assertEqual(self.events.count('configure'), 2)
        self.assertEqual(self.events.count('bridge'), 2)
        self.assertEqual(set(self.wizard.state['clients']), {'desktop', 'vscode'})
        self.assertIn('SETUP AND CONNECTION CHECKS PASSED', self.stdout.getvalue())
        self.assertNotIn(self.synthetic, self.stdout.getvalue() + self.stderr.getvalue())
        state = (self.wizard.folder / 'state.json').read_text()
        self.assertNotIn(self.synthetic, state)
        self.assertEqual((self.wizard.folder / 'service-token').read_text(), self.synthetic)

    def test_bad_service_health_never_configures_clients_or_reports_ready(self):
        self.mock(self.wizard.io, 'service_probe', return_value={'missing': [], 'port': 48958})
        self.mock(self.wizard.io, 'install_service', return_value=self.synthetic.encode())
        self.mock(self.wizard.io, 'tunnel', return_value='http://127.0.0.1:48958/mcp')
        self.mock(self.wizard.io, 'health', return_value=False)
        with patch.object(sys, 'platform', 'win32'), self.assertRaises(ValueError):
            self.run_answers(['1', '2', 'linux-vm', 'alice', ''])
        self.assertEqual(self.events, [])
        self.assertEqual(self.wizard.state['clients'], {})
        self.assertNotIn('SETUP AND CONNECTION CHECKS PASSED', self.stdout.getvalue())

    def test_missing_client_app_can_pause_before_profile_configuration(self):
        self.mock(self.wizard.io, 'service_probe', return_value={'missing': [], 'port': 48958})
        self.mock(self.wizard.io, 'install_service', return_value=self.synthetic.encode())
        self.mock(self.wizard.io, 'tunnel', return_value='http://127.0.0.1:48958/mcp')
        self.mock(self.wizard.io, 'health', return_value=True)
        self.wizard.io.client_requirements.return_value = ['VS Code was not found. Install it before opening the test profile.']
        with patch.object(sys, 'platform', 'win32'), self.assertRaises(GUIDED.Cancelled):
            self.run_answers(['1', '2', 'linux-vm', 'alice', '', '1', '2'])
        self.assertEqual(self.events, [])
        self.assertFalse(self.wizard.state['clients'])
        self.assertIn('VS Code was not found', self.stdout.getvalue())

    def test_missing_existing_connection_file_does_not_download_or_configure(self):
        with self.assertRaises(ValueError):
            self.run_answers(['2', '2', '2', 'http://127.0.0.1:48958/mcp', str(self.root / 'not-a-file')])
        self.assertEqual(self.events, [])
        self.assertFalse((self.wizard.folder / 'service-token').exists())

    def test_wrong_existing_key_is_not_saved_as_a_successful_connection(self):
        file = self.root / 'incoming-key'
        file.write_text(self.synthetic)
        self.mock(self.wizard, 'read_credential', return_value=self.synthetic)
        self.mock(self.wizard.io, 'health', side_effect=ValueError('Service rejected the connection file'))
        with self.assertRaises(ValueError):
            self.run_answers(['2', '2', '2', 'http://127.0.0.1:48958/mcp', str(file)])
        self.assertFalse((self.wizard.folder / 'service-token').exists())
        self.assertEqual(self.events, [])

    def test_package_transfer_excludes_user_state_and_credentials(self):
        package = self.root / 'package'
        package.mkdir()
        names = ['install.sh', 'pyproject.toml', 'Dockerfile', 'compose.yaml', '.dockerignore', '.gitignore', '.gitattributes', 'UPSTREAM_LICENSE.txt', 'upstream-lock.json']
        for name in names:
            (package / name).write_text('reviewed source')
        (package / '.env').write_text(self.synthetic)
        (package / '.codex').mkdir()
        (package / '.codex/service-token').write_text(self.synthetic)
        (package / 'src').mkdir()
        (package / 'src/example.py').write_text('print("source")')
        payload = SUPPORT.bundle(package)
        self.assertNotIn(self.synthetic.encode(), payload)
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            self.assertNotIn('.env', archive.getnames())
            self.assertFalse(any(name.startswith('.codex/') for name in archive.getnames()))

    def test_ssh_target_refuses_options_and_shell_syntax(self):
        for target in ['-oProxyCommand=bad', 'alice@host; echo bad', 'alice@host space']:
            with self.assertRaises(ValueError):
                SUPPORT.validate_target(target)
        self.assertEqual(SUPPORT.validate_target('alice@linux-vm'), 'alice@linux-vm')

    def test_failed_bridge_diagnostic_is_private_and_credential_redacted(self):
        private = self.root / 'private'
        private.mkdir(mode=0o700)
        token = private / 'service-token'
        token.write_text(self.synthetic)
        settings = private / 'client.json'
        settings.write_text(json.dumps({'token_file': str(token)}))
        reply = SimpleNamespace(returncode=1, stdout='', stderr='SDK error with ' + self.synthetic)
        with patch.object(SUPPORT.subprocess, 'run', return_value=reply):
            # Avoid mocking the permission helper's native subprocess calls on Windows.
            with patch.object(SUPPORT, 'protect'):
                with self.assertRaises(ValueError):
                    SUPPORT.verify_bridge(Path(sys.executable), settings)
        diagnostic = (private / 'connection-check.log').read_text()
        self.assertNotIn(self.synthetic, diagnostic)
        self.assertIn('[redacted]', diagnostic)

    def test_reopening_prepared_profile_does_not_reinstall_runtime(self):
        runtime = self.root / '.codex/conductor-venv'
        CORE.owned_directory(runtime, {'purpose': 'conductor-client-runtime', 'source': str(self.root)})
        executable = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        executable.parent.mkdir(parents=True)
        executable.touch()
        self.wizard.use_prepared_runtime()
        self.assertEqual(self.wizard.prepare_tools(), executable)
        self.assertEqual(self.events, [])

    @unittest.skipUnless(os.name == 'nt', 'Windows editor layout test')
    def test_windows_editor_cli_is_found_in_current_versioned_layout(self):
        installation = self.root / 'editor'
        (installation / 'bin').mkdir(parents=True)
        executable = installation / 'Code.exe'
        executable.touch()
        cli = installation / 'revision123/resources/app/out/cli.js'
        cli.parent.mkdir(parents=True)
        cli.touch()
        wrapper = installation / 'bin/code.cmd'
        wrapper.write_text('"%~dp0..\\Code.exe" "%~dp0..\\revision123\\resources\\app\\out\\cli.js" %*')
        with patch.object(SUPPORT.shutil, 'which', return_value=str(wrapper)):
            command, result = SUPPORT.vscode_paths()
        self.assertEqual(Path(command[1]), cli)
        self.assertEqual(Path(result), executable)

    def test_saved_tunnel_port_is_not_redirected_or_another_process_killed(self):
        with patch.object(SUPPORT, 'health', return_value=False), patch.object(SUPPORT, 'available_port', return_value=48960), patch.object(SUPPORT.subprocess, 'Popen') as process:
            with self.assertRaises(ValueError):
                SUPPORT.tunnel({'local_port': 48958, 'server_port': 48958}, self.root, self.synthetic)
        process.assert_not_called()

    def test_vscode_launch_uses_separate_editor_and_codex_data(self):
        home = self.root / 'profiles/vscode/home'
        home.mkdir(parents=True)
        (home / 'config.toml').write_text('test')
        workspace = home.parent / 'workspace'
        state = {'mode': 'isolated', 'test_root': str(self.root / 'profiles'), 'clients': {'vscode': {'home': str(home), 'workspace': str(workspace)}}}
        before = os.environ.get('CODEX_HOME')
        with patch.object(SUPPORT, 'vscode_paths', return_value=(['editor-cli'], 'editor')), patch.object(SUPPORT.subprocess, 'run') as install, patch.object(SUPPORT.subprocess, 'Popen') as launch:
            SUPPORT.launch('vscode', state, self.root)
        self.assertEqual(install.call_args.kwargs['env']['CODEX_HOME'], str(home))
        args = launch.call_args.args[0]
        self.assertIn('--user-data-dir', args)
        self.assertIn('--extensions-dir', args)
        self.assertEqual(launch.call_args.kwargs['env']['CODEX_HOME'], str(home))
        self.assertNotIn('ELECTRON_RUN_AS_NODE', launch.call_args.kwargs['env'])
        self.assertEqual(os.environ.get('CODEX_HOME'), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows desktop package test')
    def test_unknown_desktop_build_does_not_launch_the_normal_app(self):
        home = self.root / 'profiles/desktop/home'
        home.mkdir(parents=True)
        (home / 'config.toml').write_text('test')
        state = {'mode': 'isolated', 'test_root': str(self.root / 'profiles'), 'clients': {'desktop': {'home': str(home), 'workspace': str(home.parent / 'workspace')}}}
        reply = SimpleNamespace(stdout=json.dumps({'Version': 'unverified-build', 'InstallLocation': 'unused'}))
        with patch.object(SUPPORT.subprocess, 'run', return_value=reply), patch.object(SUPPORT.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):
                SUPPORT.launch('desktop', state, self.root)
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
