"""Implementation of install.sh; requires only the Python standard library."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib


def run(command: list[str], *, root: Path, env: dict[str, str], stage: str, quiet: bool = False) -> None:
    print(stage + '...', flush=True)
    subprocess.run(command, cwd=root, env=env, check=True, **({"stdout": subprocess.PIPE} if quiet else {}))


def compatibility():
    import importlib.util
    spec = importlib.util.spec_from_file_location('conductor_legacy_install', Path(__file__).with_name('legacy_install.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned_directory(path: Path, identity: dict) -> None:
    """Allow repeat setup only in a directory previously created by this entry point."""
    marker = path / '.conductor-installer.json'
    if path.is_symlink() or marker.is_symlink():
        raise ValueError('Refusing a symbolic-link installation directory')
    if path.exists():
        if not marker.exists() and compatibility().adopt_guided_directory(path, identity):
            return
        if not marker.is_file() or json.loads(marker.read_text()) != identity:
            raise ValueError('Installation directory already exists and is not owned by this setup: ' + str(path))
        return
    path.mkdir(parents=True, mode=0o700)
    with marker.open('x', encoding='utf-8') as output:
        json.dump(identity, output)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog='install.sh', description='Conductor setup. Client mode is isolated by default.')
    modes = result.add_subparsers(dest='mode', required=True)
    server = modes.add_parser('server', help='Build, initialize and start a Linux Docker Compose service')
    server.add_argument('--project', default='conductor-codex')
    server.add_argument('--port', type=int)
    server.add_argument('--bind')
    server.add_argument('--export-token', type=Path, metavar='PRIVATE_FILE')
    client = modes.add_parser('client', help='Create a Python environment and configure the client')
    client.add_argument('--mode', choices=('hosted', 'local'), default='hosted', help='Hosted uses a service key; local uses private SQLite on this computer')
    client.add_argument('--token-file', type=Path, help='Hosted private service credential file; never the token itself')
    client.add_argument('--url', help='Hosted /mcp endpoint; defaults to local SSH forwarding')
    client.add_argument('--ssh-target', help='SSH login and VM address; the client opens its own tunnel')
    client.add_argument('--db-path', type=Path, help='Local ledger file under the current user state directory')
    client.add_argument('--local-state-root', type=Path, help='Choose an empty local directory for Conductor data')
    client.add_argument('--profile', choices=('desktop', 'vscode'), default='desktop')
    client.add_argument('--test-root', type=Path, help='Isolated test directory; default: checkout/.codex/conductor-acceptance')
    target = client.add_mutually_exclusive_group()
    target.add_argument('--isolated', action='store_true', help='Use an isolated test profile (the default)')
    target.add_argument('--configure-user', action='store_true', help='Explicitly install into normal user configuration instead of an isolated test profile')
    modes.add_parser('doctor', help='Report interpreter and source locations without changing configuration')
    modes.add_parser('wizard', help='Guided first-time setup (the default when no arguments are provided)')
    modes.add_parser('launch', help='Reopen an isolated client prepared by the wizard')
    return result


def prepare_runtime(root: Path, env: dict[str, str] | None = None, python_executable: Path | None = None) -> Path:
    root = root.resolve()
    env = dict(env or os.environ, PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    runtime = root / '.codex/conductor-venv'
    identity = {'purpose': 'conductor-client-runtime', 'source': str(root)}
    owned_directory(runtime, identity)
    temporary = root / '.codex/conductor-tmp'
    temporary.mkdir(mode=0o700, exist_ok=True)
    env.update({name: str(temporary) for name in ('TMPDIR', 'TEMP', 'TMP')})
    executable = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.is_file():
        bootstrap_python = str(python_executable or sys.executable)
        run([bootstrap_python, '-m', 'venv', str(runtime)], root=root, env=env, stage='Creating client Python environment')
    run([str(executable), '-m', 'pip', 'install', '--no-cache-dir', str(root) + '[legacy-mcp]'], root=root, env=env, stage='Installing legacy MCP client dependencies')
    return executable


def setup(root: Path, arguments: list[str], *, prepared_runtime: Path | None = None, guided_output: bool = False,
          python_executable: Path | None = None) -> None:
    if not arguments or arguments[0] in ('wizard', 'launch'):
        import importlib.util
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('conductor_guided_install', root / 'scripts/guided_install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main(root, arguments if arguments and arguments[0] == 'launch' else arguments[1:], SimpleNamespace(owned_directory=owned_directory, prepare_runtime=prepare_runtime, setup=setup, compatibility=compatibility))
        return
    args = parser().parse_args(arguments)
    if sys.version_info < (3, 11):
        raise ValueError('Python 3.11+ is required')
    root = root.resolve()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    if args.mode == 'doctor':
        print(json.dumps({'platform': sys.platform, 'python': sys.executable, 'version': sys.version.split()[0], 'source': str(root)}, indent=2))
        return
    if args.mode == 'server':
        if sys.platform != 'linux':
            raise ValueError('Server mode requires Linux with Docker Compose; Windows client mode uses native Python through Git Bash')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.project):
            raise ValueError('Invalid Compose project name')
        if not shutil.which('docker'):
            raise ValueError('Docker with Compose is required; setup does not install or change system packages')
        env['COMPOSE_PROJECT_NAME'] = args.project
        command = [sys.executable, str(root / 'deploy/setup.py')]
        if args.export_token:
            target = args.export_token.expanduser().absolute()
            if any(part.is_symlink() for part in (target, *target.parents)):
                raise ValueError('Credential destination may not contain symbolic links')
            if not target.parent.exists():
                target.parent.mkdir(parents=True, mode=0o700)
            command += ['--export-token', str(target)]
        else:
            if args.port is not None:
                command += ['--port', str(args.port)]
            if args.bind is not None:
                command += ['--bind', args.bind]
        run(command, root=root, env=env, stage='Preparing service' if not args.export_token else 'Exporting private credential')
        if not args.export_token:
            run(['docker', 'compose', 'up', '-d', '--wait', '--wait-timeout', '90'], root=root, env=env, stage='Starting service and waiting for health')
            print('Service ready. Compose project: ' + args.project)
        return

    token_file = None
    database = None
    if args.mode == 'hosted':
        if args.local_state_root:
            raise ValueError('Hosted mode does not use --local-state-root')
        if args.db_path:
            raise ValueError('Hosted mode does not use --db-path')
        if not args.token_file:
            raise ValueError('Hosted mode needs a private service credential file using --token-file')
        token_file = args.token_file.expanduser().absolute()
        if not token_file.is_file() or token_file.is_symlink():
            raise ValueError('Provide an existing private credential file using --token-file')
        # Validate before creating runtime files; the installed bridge also validates transport.
        from urllib.parse import urlsplit
        args.url = args.url or 'http://127.0.0.1:8012/mcp'
        url = urlsplit(args.url)
        if url.scheme not in ('http', 'https') or url.username or url.password or url.query or url.fragment or url.path != '/mcp':
            raise ValueError('URL must be an HTTP(S) /mcp endpoint without embedded credentials, query or fragment')
        if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Remote HTTP is refused; use an SSH tunnel or HTTPS')
        if not url.hostname:
            raise ValueError('URL must include a host')
    else:
        if args.token_file or args.url or args.ssh_target:
            raise ValueError('Local mode does not use a service URL or token file')
        if os.name == 'nt':
            default_root = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Conductor'
        else:
            default_root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'conductor'
        state_root = (args.local_state_root or default_root).expanduser().absolute()
        database = (args.db_path or state_root / 'tasks.sqlite3').expanduser().absolute()
        # The installed client performs the full local-disk and private-path checks.
        # This entry point must also work before its dependencies are installed.
        if not database.is_relative_to(state_root) or database.suffix != '.sqlite3':
            raise ValueError('Local ledger must be a .sqlite3 file inside the selected local state directory')
    if args.configure_user and args.test_root:
        raise ValueError('--test-root cannot be combined with --configure-user')
    profile = None
    if not args.configure_user:
        test_root = (args.test_root or root / '.codex/conductor-acceptance').expanduser().resolve()
        normal_roots = [(Path.home() / '.codex').resolve(), (Path.home() / '.agents').resolve()]
        inherited = os.environ.get('CODEX_HOME')
        if inherited:
            normal_roots.append(Path(inherited).expanduser().resolve())
        if any(test_root == normal or test_root.is_relative_to(normal) for normal in normal_roots):
            raise ValueError('An isolated test root cannot be inside an existing user Codex or skills directory')
        profile = test_root / args.profile
        if profile.is_symlink() or (profile.exists() and not (profile / '.conductor-installer.json').is_file()):
            raise ValueError('Test profile already exists without this installer ownership marker')

    executable = prepared_runtime or prepare_runtime(root, env, python_executable)
    if not executable.is_file():
        raise ValueError('The prepared client Python environment is missing; resume setup to recreate it')
    command = [str(executable), str(root / 'scripts/install_client.py')]
    if args.mode == 'local':
        command += ['--mode', 'local', '--db-path', str(database), '--state-root', str(state_root)]
    else:
        command += ['--url', args.url, '--token-file', str(token_file)]
        if args.ssh_target:
            command += ['--ssh-target', args.ssh_target]
    if profile is not None:
        owned_directory(profile, {'purpose': 'conductor-isolated-client', 'source': str(root), 'profile': args.profile})
        home = profile / 'home'
        workspace = profile / 'workspace'
        home.mkdir(mode=0o700, exist_ok=True)
        workspace.mkdir(mode=0o700, exist_ok=True)
        config = home / 'config.toml'
        if config.is_symlink():
            raise ValueError('Refusing a symbolic-link test configuration')
        if not config.exists():
            with config.open('x', encoding='utf-8') as output:
                output.write('cli_auth_credentials_store = "file"\n')
        elif tomllib.loads(config.read_text()).get('cli_auth_credentials_store') != 'file':
            raise ValueError('Isolated profile must retain file-based authentication; existing settings were preserved')
        command += ['--codex-home', str(home), '--skills-dir', str(workspace / '.agents/skills')]
    if guided_output:
        run(command, root=root, env=env, stage='Writing the isolated client connection files', quiet=True)
        return
    run(command, root=root, env=env, stage='Configuring isolated client' if profile else 'Configuring user client')
    print('Client setup complete.', flush=True)
    if profile:
        print(json.dumps({'profile': args.profile, 'codex_home': str(profile / 'home'), 'workspace': str(profile / 'workspace'), 'note': 'Launch the client with this Codex home; desktop also needs separate app data. No clients were launched.'}, indent=2))


def main() -> int:
    try:
        setup(Path(sys.argv[1]), sys.argv[2:])
        return 0
    except subprocess.CalledProcessError as error:
        print('Setup stopped: a required command failed (exit ' + str(error.returncode) + '). Rerun the same command after resolving the displayed error.', file=sys.stderr)
        return error.returncode if 0 < error.returncode < 256 else 1
    except KeyboardInterrupt:
        print('\nSetup interrupted. It was not reported as complete.', file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print('Setup stopped: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
