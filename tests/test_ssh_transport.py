"""SSH subprocess boundary checks, including Git Bash -> native Windows Python."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ssh_transport_fixture', ROOT / 'scripts/install_support.py')
SUPPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUPPORT)


class SshTransportTests(unittest.TestCase):
    def test_saved_nondefault_key_is_selected_without_user_entering_its_path(self):
        session = {'ssh_target': 'alice@192.0.2.10', 'ssh_port': 22}
        results = [subprocess.CompletedProcess([], 0),
                   subprocess.CompletedProcess([], 255, stderr='Permission denied (publickey).'),
                   subprocess.CompletedProcess([], 0, stderr='')]
        with patch.object(SUPPORT.Path, 'is_file', return_value=True), \
             patch.object(SUPPORT, 'ssh_executable', return_value='/usr/bin/ssh'), \
             patch.object(SUPPORT, '_saved_private_keys', return_value=[Path('/home/alice/.ssh/lab-key')]), \
             patch.object(SUPPORT.subprocess, 'run', side_effect=results) as run:
            SUPPORT.discover_ssh_identity(session, Path('isolated setup'))
        self.assertEqual(Path(session['ssh_key']), Path('/home/alice/.ssh/lab-key'))
        self.assertEqual(run.call_count, 3)
        self.assertNotIn('-i', run.call_args_list[1].args[0])
        self.assertIn('-i', run.call_args_list[2].args[0])

    def test_host_and_username_are_separate_inputs(self):
        self.assertEqual(SUPPORT.validate_host('192.0.2.10'), '192.0.2.10')
        self.assertEqual(SUPPORT.validate_user('alice'), 'alice')
        with self.assertRaisesRegex(ValueError, 'without a username'):
            SUPPORT.validate_host('alice@192.0.2.10')
        with self.assertRaisesRegex(ValueError, 'without @'):
            SUPPORT.validate_user('alice@192.0.2.10')

    def test_options_keep_selected_executable_and_known_host_path_as_one_argument(self):
        with patch.object(SUPPORT, 'ssh_executable', return_value='selected-openssh'):
            command = SUPPORT.ssh_options({'ssh_port': 2222}, Path('setup folder with spaces'))
        self.assertEqual(command[0], 'selected-openssh')
        option = next(argument for argument in command if argument.startswith('UserKnownHostsFile='))
        self.assertEqual(option, 'UserKnownHostsFile="setup folder with spaces/ssh_known_hosts"')
        self.assertEqual(command[-2:], ['-p', '2222'])

    @unittest.skipIf(os.name == 'nt', 'POSIX SSH lookup')
    def test_posix_uses_resolved_system_ssh(self):
        with patch.object(SUPPORT.shutil, 'which', return_value='/usr/bin/custom-ssh'):
            self.assertEqual(SUPPORT.ssh_executable(), '/usr/bin/custom-ssh')

    @unittest.skipIf(os.name == 'nt', 'POSIX SSH lookup')
    def test_missing_posix_ssh_has_actionable_error(self):
        with patch.object(SUPPORT.shutil, 'which', return_value=None), self.assertRaisesRegex(ValueError, 'Install the SSH client'):
            SUPPORT.ssh_executable()

    @unittest.skipUnless(os.name == 'nt', 'Native Windows SSH selection')
    def test_windows_does_not_select_git_ssh_from_path(self):
        with tempfile.TemporaryDirectory() as directory:
            windows = Path(directory) / 'Windows'
            executable = windows / 'System32/OpenSSH/ssh.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict(os.environ, {'SystemRoot': str(windows)}), patch.object(SUPPORT.shutil, 'which', return_value='Git/usr/bin/ssh.exe') as lookup:
                self.assertEqual(Path(SUPPORT.ssh_executable()), executable)
                lookup.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Native Windows SSH selection')
    def test_missing_native_windows_ssh_does_not_fall_back_to_git(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'SystemRoot': directory}), patch.object(SUPPORT.shutil, 'which', return_value='Git/usr/bin/ssh.exe') as lookup:
                with self.assertRaisesRegex(ValueError, 'Windows optional feature OpenSSH Client'):
                    SUPPORT.ssh_executable()
                lookup.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Actual Git Bash/Windows subprocess boundary')
    def test_real_entrypoint_handles_known_hosts_with_and_without_spaces(self):
        bash = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git/bin/bash.exe'
        if not bash.is_file():
            self.skipTest('Git Bash is not installed')
        if not (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/OpenSSH/ssh.exe').is_file():
            self.skipTest('Windows OpenSSH Client is not installed')
        bootstrap = '''import importlib.util,json,pathlib,subprocess,sys
spec=importlib.util.spec_from_file_location('real_support',sys.argv[2])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
command=module.ssh_options({},pathlib.Path(sys.argv[3]))
assert pathlib.Path(command[0]).parent.name=='OpenSSH',command[0]
result=subprocess.run(command+['-G','-T','example.invalid'],capture_output=True,text=True)
assert result.returncode==0,result.stderr
assert any(line.startswith('userknownhostsfile ') for line in result.stdout.splitlines())
print('SSH configuration accepted')
'''
        with tempfile.TemporaryDirectory() as directory:
            for name in ('plain', 'folder with spaces'):
                with self.subTest(path=name):
                    root = Path(directory) / name
                    (root / 'scripts').mkdir(parents=True)
                    shutil.copyfile(ROOT / 'install.sh', root / 'install.sh')
                    (root / 'scripts/bootstrap.py').write_text(bootstrap, encoding='utf-8')
                    result = subprocess.run([str(bash), (root / 'install.sh').as_posix(), (ROOT / 'scripts/install_support.py').as_posix(), (root / 'known-hosts').as_posix()], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('SSH configuration accepted', result.stdout)


if __name__ == '__main__':
    unittest.main()
