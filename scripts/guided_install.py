"""First-time setup wizard behind `bash install.sh`; advanced CLI remains available."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys


class Cancelled(Exception):
    pass


def support(root: Path):
    spec = importlib.util.spec_from_file_location('conductor_install_support', root / 'scripts/install_support.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ask(message: str, default: str = '') -> str:
    suffix = ' [' + default + ']' if default else ''
    try:
        value = input(message + suffix + ': ').strip()
    except EOFError:
        raise Cancelled()
    if value.lower() in ('q', 'quit', 'exit'):
        raise Cancelled()
    return value or default


def choose(message: str, options: list[str], default: str = '1') -> str:
    print('\n' + message)
    for index, text in enumerate(options, 1):
        print('  ' + str(index) + '. ' + text)
    while True:
        value = ask('Choose a number (q exits safely)', default)
        if value.isdigit() and 1 <= int(value) <= len(options):
            return value
        print('Choose one of the numbers shown. No installation step has been started by that answer.')


def file_path(value: str) -> Path:
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    if os.name == 'nt' and re.match(r'^/[a-zA-Z]/', value):
        value = subprocess.run(['cygpath.exe', '-aw', value], check=True, capture_output=True, text=True).stdout.strip()
    return Path(value).expanduser().absolute()


def connection_guide() -> None:
    print('\nA Conductor connection file is created by the Conductor SERVICE during setup.')
    print('It is not a Git-host token, a Codex sign-in, or text you must invent or paste.')
    print('If you do not have a service yet, choose New installation. Its key is generated automatically.')
    print('If someone else manages the service, ask them for its /mcp address and private service-token file.')
    print('On that Linux host they can use: bash install.sh server --export-token "$HOME/.local/share/conductor-private/service-token"')
    print('They must use the same --project name as their service, then transfer that FILE privately to this computer.')


class Wizard:
    def __init__(self, root: Path, core):
        self.root, self.core = root.resolve(), core
        self.io = support(root)
        self.state: dict = {}
        self.folder: Path | None = None
        self.runtime: Path | None = None

    def start_session(self) -> None:
        base = self.root / '.codex/guided-install'
        self.core.owned_directory(base, {'purpose': 'conductor-guided-setup', 'source': str(self.root)})
        self.io.protect(base)
        pointer = base / 'latest.json'
        if pointer.exists():
            identifier = json.loads(pointer.read_text())['id']
            if not re.fullmatch(r'[a-f0-9]{8}', identifier):
                raise ValueError('Saved setup identifier is invalid; existing files were preserved')
            previous = base / identifier / 'state.json'
            if previous.is_file():
                old = json.loads(previous.read_text())
                kind = self.core.compatibility().session_kind(old, self.root)
                if kind == 'deployed':
                    print('\n' + self.core.compatibility().DEPLOYED_MESSAGE)
                    if choose('Create a separate Conductor setup?', ['Create a separate fresh Conductor setup', 'Exit without changes']) == '2':
                        raise Cancelled()
                else:
                    print('\nA previous guided setup was found. Last stage: ' + old.get('stage', 'started'))
                    if choose('Continue it or start another separate test?', ['Continue this setup', 'Start a separate fresh setup']) == '1':
                        if kind == 'pending':
                            backup = previous.parent / 'state.before-conductor.json'
                            if not backup.exists():
                                self.io.private_write(backup, previous.read_bytes())
                            old = self.core.compatibility().migrate_pending(old, self.root)
                        self.state, self.folder = old, previous.parent
                        self.save()
                        return
        identifier = secrets.token_hex(4)
        self.folder = base / identifier
        self.folder.mkdir(mode=0o700)
        self.io.protect(self.folder)
        self.state = {'version': 1, 'source': str(self.root), 'id': identifier, 'project': 'conductor-test-' + identifier, 'stage': 'started', 'mode': 'isolated', 'clients': {}}
        self.io.private_write(pointer, json.dumps({'id': identifier}).encode(), replace=True)
        self.save()

    def save(self, stage: str | None = None) -> None:
        if stage:
            self.state['stage'] = stage
        self.io.private_write(self.folder / 'state.json', json.dumps(self.state, indent=2).encode(), replace=True)

    def prerequisites(self, missing: list[str]) -> None:
        print('\nThe service cannot be installed on this host yet. Missing:')
        for item in missing:
            print('  - ' + item)
        print('Use a Linux VM/server with Python 3.11+, Docker Engine and Docker Compose.')
        print('The Linux login must be allowed to use its running Docker daemon.')
        print('Docker installation guide: https://docs.docker.com/engine/install/')
        print('This wizard does not install operating-system packages or change your existing servers automatically.')
        print('After preparing that host, rerun the same installer command and choose Continue.')
        raise Cancelled()

    def prepare_tools(self) -> Path:
        if self.runtime is None:
            print('\nPreparing the local connection tools. Package downloads may take a few minutes.')
            print('Keep this window open. A failed/cancelled download is safe to retry; no client is marked ready.')
            self.runtime = self.core.prepare_runtime(self.root)
        return self.runtime

    def use_prepared_runtime(self) -> None:
        runtime = self.root / '.codex/conductor-venv'
        marker = runtime / '.conductor-installer.json'
        expected = {'purpose': 'conductor-client-runtime', 'source': str(self.root)}
        executable = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not marker.is_file() or json.loads(marker.read_text()) != expected or not executable.is_file():
            raise ValueError('The prepared client runtime is missing or belongs to another checkout. Resume setup to repair it; no application was launched.')
        self.runtime = executable

    def read_credential(self, path: Path) -> str:
        executable = self.prepare_tools()
        code = 'from pathlib import Path;import sys;from conductor_coordination.client_bridge import read_private;sys.stdout.write(read_private(Path(sys.argv[1])).strip())'
        result = subprocess.run([str(executable), '-c', code, str(path)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('That connection file could not be read privately. It must be a private Conductor service-token file, not a Git-host token or a symbolic link. Existing permissions were not changed.')
        credential = result.stdout
        if len(credential) < 32 or any(c.isspace() for c in credential):
            raise ValueError('That file is not a valid Conductor service connection key')
        return credential

    def service_first(self, *, server_only: bool = False) -> tuple[str, Path] | None:
        if self.state.get('service_kind') == 'on-device':
            if server_only:
                raise ValueError('A local on-device ledger cannot be installed as a remote Linux service')
            self.configure()
            return None
        if self.state.get('remote_source') and self.state.get('token_file'):
            print('\nResuming the service already created by this setup. It will not be rebuilt or replaced.')
            token = Path(self.state['token_file'])
            credential = self.read_credential(token)
            if server_only:
                print('The saved service connection file is: ' + str(token))
                return None
            if self.state['service_kind'] == 'ssh':
                url = self.io.tunnel(self.state, self.folder, credential)
            else:
                url = 'http://127.0.0.1:' + str(self.state['server_port']) + '/mcp'
            if not self.io.health(url, credential, auth_errors=True):
                raise ValueError('The saved service is not answering. Check its host/SSH connection, then resume; no client profile was selected.')
            self.state['url'] = url
            self.save('saved service connection verified')
            return url, token
        print('\nStep 1 - Choose where Conductor will keep its task ledger.')
        print('Local mode stores metadata on this computer and starts with Codex. No VM or Docker is needed.')
        print('Hosted mode runs the metadata service in Docker on a Linux host for trusted teams.')
        if not self.state.get('service_kind'):
            options = ['On this computer (local, no Docker or VM)',
                       'Another Linux VM/server, reached through SSH',
                       'I do not have a suitable Linux host for hosted mode']
            if sys.platform == 'linux' and server_only:
                options = ['This Linux computer (hosted Docker service)',
                           'Another Linux VM/server, reached through SSH',
                           'I do not have a suitable Linux host for hosted mode']
            elif sys.platform == 'linux':
                options.insert(2, 'This Linux computer (hosted Docker service)')
            elif server_only:
                options = options[1:]
            choice = choose('Where should it run?', options)
            selected = options[int(choice) - 1]
            if selected.startswith('I do not'):
                self.prerequisites(['A Linux service host'])
            if selected.startswith('On this computer'):
                database = self.io.local_database_path(self.state['id'])
                self.state.update(service_kind='on-device', local_db_path=str(database),
                                  local_state_root=str(database.parents[2]))
                self.save('local ledger selected')
                self.configure()
                return None
            local = selected.startswith('This Linux')
            details = {'service_kind': 'local' if local else 'ssh'}
            if not local:
                print('Enter the IP address or host name of the Linux server where Conductor will run.')
                print('Then enter your Linux SSH username. This is your server login, not a Git-host account.')
                print('Conductor will try your existing SSH configuration, agent and saved keys automatically.')
                host = self.io.validate_host(ask('Linux server IP address or host name'))
                user = self.io.validate_user(ask('Linux SSH username'))
                details['ssh_target'] = self.io.validate_target(user + '@' + host)
                port = ask('SSH port (keep 22 unless your Linux login uses another)', '22')
                if not port.isdigit() or not 1 <= int(port) <= 65535:
                    raise ValueError('SSH port must be a number from 1 to 65535')
                details['ssh_port'] = int(port)
            self.state.update(details)
            self.save('service host selected')
        local = self.state['service_kind'] == 'local'
        print('\nStep 2 - Checking the Linux host before installing anything.')
        if not local:
            print('SSH may ask you to verify this host or enter your Linux login/key passphrase.')
            print('Its host record is kept with this isolated setup, not in your normal known-hosts file.')
        probe = self.io.service_probe(self.state, self.folder, local=local)
        if probe['missing']:
            self.prerequisites(probe['missing'])
        self.state.setdefault('server_port', probe['port'])
        print('Host checks passed. A separate service project will be used: ' + self.state['project'])
        print('Its private connection number is ' + str(self.state['server_port']) + '; other deployments are not selected.')
        self.save('service prerequisites passed')
        print('\nStep 3 - Installing the service and generating its connection key automatically.')
        print('The key will be transferred as a private file. You do not need to create, copy or paste a token.')
        credential = self.io.install_service(self.root, self.state, self.folder, local=local).decode().strip()
        if len(credential) < 32 or any(c.isspace() for c in credential):
            raise ValueError('Service setup did not return a valid connection key; no client was configured')
        token = self.folder / 'service-token'
        self.io.private_write(token, credential.encode())
        self.state['token_file'] = str(token)
        self.save('service installed; connection file transferred privately')
        if server_only:
            print('\nSERVICE READY. Its connection key FILE is:')
            print(token)
            print('On the Codex computer run this same installer and choose Connect to an existing service.')
            print('Transfer this file securely. Do not paste its contents into chat or a shell command.')
            print('The service listens on Linux loopback port ' + str(self.state['server_port']) + '. Remote clients need SSH or HTTPS.')
            return None
        print('\nStep 4 - Verifying a private connection before changing any Codex profile.')
        if local:
            url = 'http://127.0.0.1:' + str(self.state['server_port']) + '/mcp'
        else:
            url = self.io.tunnel(self.state, self.folder, credential)
        if not self.io.health(url, credential):
            raise ValueError('The service is not reachable with its generated key. No client is marked ready. Check its health/network and resume setup.')
        self.state['url'] = url
        self.save('service connection verified')
        print('Connection verified. The service accepted its own key; no Git-host token was used.')
        return url, token

    def existing(self) -> tuple[str, Path]:
        connection_guide()
        answer = choose('Do you already have a RUNNING Conductor service and its private connection file?', ['No - guide me through creating the service first', 'Yes - I have both'])
        if answer == '1':
            if self.state.get('token_file'):
                print('This setup already verified a different service. Start a separate fresh setup to create another one; the saved connection is preserved.')
                raise Cancelled()
            for key in ('service_kind', 'server_port', 'local_port', 'ssh_target', 'ssh_key', 'url'):
                self.state.pop(key, None)
            return self.service_first()
        print('\nChoose the service connection, not your Git repository address.')
        route = choose('How will this computer reach that service?', ['SSH to the Linux service host (recommended)', 'Existing HTTPS address or a tunnel I already run'])
        if route == '1':
            self.state['service_kind'] = 'ssh'
            self.state['ssh_target'] = self.io.validate_target(ask('Linux SSH login, for example alice@linux-vm'))
            port = ask('SSH port', '22')
            service_port = ask('Conductor connection number on that Linux host', '8012')
            if not all(p.isdigit() and 1 <= int(p) <= 65535 for p in (port, service_port)):
                raise ValueError('Connection numbers must be between 1 and 65535')
            self.state.update(ssh_port=int(port), server_port=int(service_port))
        else:
            self.state['service_kind'] = 'existing-url'
            self.state['url'] = ask('Conductor /mcp URL, for example https://coordination.example.com/mcp')
        print('Now choose the private service-token FILE supplied by that service. Never paste the key itself.')
        path = file_path(ask('Connection file path (q cancels safely)'))
        if not path.is_file():
            connection_guide()
            raise ValueError('The file was not found. No client profile was configured. Resume after obtaining the file, or start the service-first route.')
        credential = self.read_credential(path)
        token = self.folder / 'service-token'
        url = self.io.tunnel(self.state, self.folder, credential) if route == '1' else self.state['url']
        if not self.io.health(url, credential, auth_errors=True):
            raise ValueError('That service did not accept the connection file. Check the host/address and use its Conductor file, not a Git-host token. No client profile was configured.')
        self.io.private_write(token, credential.encode(), replace=not bool(self.state.get('clients')))
        self.state['token_file'] = str(token)
        self.state['url'] = url
        self.save('service connection verified')
        print('The existing service and its connection file are verified.')
        return url, token

    def configure(self, url: str | None = None, token: Path | None = None) -> None:
        print('\nStep 5 - Choose the Codex clients to prepare.')
        choice = choose('Which will you use?', ['Desktop and VS Code', 'Desktop only', 'VS Code only'])
        profiles = {'1': ['desktop', 'vscode'], '2': ['desktop'], '3': ['vscode']}[choice]
        print('This wizard prepares ISOLATED test profiles. Your normal Codex settings and global skills are not selected.')
        requirements = self.io.client_requirements(profiles)
        if requirements:
            print('Before opening the selected clients:')
            for requirement in requirements:
                print('  - ' + requirement)
            if choose('How would you like to continue?', ['Prepare the connection profiles now; install/use the client apps later', 'Pause and finish these prerequisites first'], '2') == '2':
                raise Cancelled()
        self.state['test_root'] = str(self.folder / 'profiles')
        executable = self.prepare_tools()
        for profile in profiles:
            print('\nPreparing the ' + profile + ' test profile...')
            command = ['client', '--isolated', '--profile', profile, '--test-root', self.state['test_root']]
            if self.state.get('service_kind') == 'on-device':
                command += ['--mode', 'local', '--db-path', self.state['local_db_path'],
                            '--local-state-root', self.state.get('local_state_root') or str(Path(self.state['local_db_path']).parents[2])]
            else:
                command += ['--url', url, '--token-file', str(token)]
            self.core.setup(self.root, command, prepared_runtime=executable, guided_output=True)
            home = Path(self.state['test_root']) / profile / 'home'
            self.io.verify_bridge(executable, home / 'conductor-coordination/client.json')
            self.state['clients'][profile] = {'home': str(home), 'workspace': str(home.parent / 'workspace')}
            self.save(profile + ' profile and real MCP connection verified')
        self.save('profiles ready; actual frontend acceptance still required')
        print('\nSETUP AND CONNECTION CHECKS PASSED.')
        print('The selected local MCP connections answered successfully.')
        print('Native worker availability must still be checked inside each real client; the service cannot add it.')
        print('\nStep 6 - Open an isolated client, sign in, and use its disposable test workspace.')
        self.finish()

    def finish(self) -> None:
        for profile, info in self.state.get('clients', {}).items():
            print(profile + ' test workspace: ' + info['workspace'])
        print('When ready, run the connection, direct-task and delegated-task prompts in docs/acceptance.md.')
        print('Do not start by opening your normal app and assuming it uses this test configuration.')
        profiles = list(self.state.get('clients', {}))
        if profiles:
            options = ['Open isolated ' + p + ' now' for p in profiles] + ['Finish now; open a test profile later']
            answer = choose('Open a test client now?', options, str(len(options)))
            if int(answer) <= len(profiles):
                self.io.launch(profiles[int(answer) - 1], self.state, self.folder)
        print('To reopen this setup, run the same installer command with no arguments.')
        print('For a direct isolated launch: bash install.sh launch --profile desktop (or vscode).')
        if self.state.get('service_kind') == 'ssh':
            print('Keep the dedicated SSH connection window/session running while using these test profiles.')

    def run(self) -> None:
        print('''
Welcome to Conductor - guided setup

Conductor helps Codex track work shared across its native agents.
One task stays with the main agent; distinct workstreams can use workers.

Choose local for a ledger on this computer with no VM or Docker.
Choose hosted for a Linux Docker ledger that trusted users can connect to.
Codex itself runs the agents in either mode.

You do NOT need to create a Conductor token before starting.
Hosted setup creates the connection key and transfers it privately.
A Git-host token is needed only for private source code; it is never the Conductor key.

Default: separate test ledger/profiles. Normal Codex configuration is preserved.
You can enter q at a question to stop safely. Resume using this same command.
''')
        choice = choose('Where are you starting?', ['New installation - guide me through the service and clients', 'Connect to a service that is already running', 'Install only the Linux service for another computer', 'Explain prerequisites; do not install anything', 'Exit'])
        if choice == '5':
            return
        if choice == '4':
            print('This computer needs Python 3.11+, Bash (Git Bash on Windows), your own Codex sign-in and a client with native worker tools.')
            print('Local mode needs no Docker or remote host. Hosted mode needs Linux with Docker Engine/Compose and possibly SSH.')
            print('The wizard checks these before installation. It does not require project copies or model credentials on the service.')
            print('Prepare that host first, then rerun and choose New installation. No connection key is needed yet.')
            return
        self.start_session()
        if self.state.get('route') and self.state['route'] != choice:
            print('Continuing the previous setup route. Choose a separate fresh setup to change its route.')
        choice = self.state.setdefault('route', choice)
        self.save()
        if self.state.get('clients') and self.state['stage'].startswith('profiles ready'):
            self.use_prepared_runtime()
            if self.state.get('service_kind') == 'on-device':
                first = next(iter(self.state['clients'].values()))
                self.io.verify_bridge(self.runtime, Path(first['home']) / 'conductor-coordination/client.json')
            else:
                credential = self.read_credential(Path(self.state['token_file']))
                if self.state.get('service_kind') == 'ssh':
                    self.state['url'] = self.io.tunnel(self.state, self.folder, credential)
                if not self.io.health(self.state['url'], credential):
                    raise ValueError('The saved service connection is unavailable; check its host or SSH connection before opening a test client')
            self.finish()
            return
        if choice == '2':
            result = self.existing()
        else:
            result = self.service_first(server_only=choice == '3')
        if result is not None:
            self.configure(*result)


def main(root: Path, arguments: list[str], core) -> None:
    wizard = Wizard(root, core)
    try:
        if arguments and arguments[0] == 'launch':
            parser = argparse.ArgumentParser(prog='install.sh launch')
            parser.add_argument('--profile', choices=('desktop', 'vscode'), default='desktop')
            args = parser.parse_args(arguments[1:])
            base = root / '.codex/guided-install'
            identifier = json.loads((base / 'latest.json').read_text())['id']
            if not re.fullmatch(r'[a-f0-9]{8}', identifier):
                raise ValueError('Saved setup identifier is invalid')
            folder = base / identifier
            state = json.loads((folder / 'state.json').read_text())
            if state.get('source') != str(root.resolve()):
                raise ValueError('Saved setup belongs to another checkout')
            if core.compatibility().session_kind(state, root.resolve()) != 'current':
                raise ValueError('An earlier setup was preserved. Run the guided installer to create a separate Conductor profile.')
            wizard.use_prepared_runtime()
            if state.get('service_kind') == 'on-device':
                info = state.get('clients', {}).get(args.profile)
                if not info:
                    raise ValueError('That local client profile has not passed setup yet')
                wizard.io.verify_bridge(wizard.runtime, Path(info['home']) / 'conductor-coordination/client.json')
            else:
                credential = wizard.read_credential(Path(state['token_file']))
                if state.get('service_kind') == 'ssh':
                    state['url'] = wizard.io.tunnel(state, folder, credential)
                if not wizard.io.health(state['url'], credential):
                    raise ValueError('The service connection is not ready; run the guided installer to repair it before launching')
            wizard.io.launch(args.profile, state, folder)
        else:
            wizard.run()
    except Cancelled:
        print('\nSetup paused. It has NOT been reported as complete.')
        if wizard.folder:
            wizard.save()
            print('Progress is saved privately. Run the same command again to continue.')
        print('No normal Codex configuration was selected by this wizard.')
    except KeyboardInterrupt:
        if wizard.folder:
            wizard.save()
            print('\nCompleted setup steps were saved privately. Resume with the same installer command.', file=sys.stderr)
        return
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            explanation = 'A required setup command stopped (exit ' + str(error.returncode) + '). Read its message above, resolve that prerequisite, and resume setup.'
        elif isinstance(error, subprocess.TimeoutExpired):
            explanation = 'A connection check timed out. Check the service/network or finish the SSH login, then resume setup.'
        else:
            explanation = str(error)
        print('\nSETUP NOT COMPLETE: ' + explanation, file=sys.stderr)
        if wizard.folder:
            wizard.save()
            print('Completed steps were preserved. Run the same installer command again to resume.', file=sys.stderr)
        raise ValueError('Guided setup stopped before reporting success. Follow the explanation above and resume with the same command.') from None
