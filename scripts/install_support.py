"""Transport and private storage for guided setup. No model or Git-host credentials."""
from __future__ import annotations

import csv
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request


def protect(path: Path) -> None:
    if os.name == 'nt':
        result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True, capture_output=True, text=True)
        sid = next(csv.reader(io.StringIO(result.stdout)))[1]
        rights = '(OI)(CI)(F)' if path.is_dir() else '(F)'
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + sid + ':' + rights], check=True, capture_output=True)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def private_write(path: Path, data: bytes, *, replace: bool = False) -> None:
    if path.is_symlink():
        raise ValueError('Refusing a symbolic-link private file')
    if path.exists() and not replace:
        if path.read_bytes() != data:
            raise ValueError('An existing private setup file differs; it was preserved')
        protect(path)
        return
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    protect(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    with os.fdopen(os.open(path, flags, 0o600), 'wb') as output:
        output.write(data)
    protect(path)


def local_database_path(identifier: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{8}', identifier):
        raise ValueError('Invalid local setup identifier')
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Conductor'
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'conductor'
    return (base / 'guided' / identifier / 'tasks.sqlite3').absolute()


def available_port(preferred: int = 48958) -> int:
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', preferred))
        except OSError:
            listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def health(url: str, credential: str, *, auth_errors: bool = False) -> bool:
    from urllib.parse import urlsplit
    endpoint = urlsplit(url)
    if endpoint.scheme not in ('http', 'https') or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path != '/mcp':
        raise ValueError('Use the service /mcp URL without a username, password, query or fragment')
    if endpoint.scheme == 'http' and endpoint.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Remote plaintext HTTP is not supported. Choose an SSH connection or HTTPS.')
    endpoint.port  # Validate a malformed port before making any request.
    request = urllib.request.Request(url[:-4] + '/healthz', headers={'Authorization': 'Bearer ' + credential})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=3) as response:
            result = json.loads(response.read(16000))
        return result.get('status') == 'ok' and result.get('protocol_version') == 'conductor-native-coordination/1'
    except urllib.error.HTTPError as error:
        if auth_errors and error.code in (401, 403):
            raise ValueError('The service rejected this connection file. Use its Conductor key file, not a Git-host token.') from None
        return False
    except (OSError, ValueError):
        return False


def validate_target(value: str) -> str:
    if value.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]+', value):
        raise ValueError('Enter an SSH login such as alice@linux-vm, without spaces or shell commands')
    return value


def validate_host(value: str) -> str:
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', value) and '..' not in value:
        return value
    try:
        address = ipaddress.ip_address(value.strip('[]'))
    except ValueError:
        raise ValueError('Enter only the Linux server IP address or host name, without a username or command') from None
    return '[' + str(address) + ']' if address.version == 6 else str(address)


def validate_user(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', value):
        raise ValueError('Enter only your Linux SSH username, without @ or a server address')
    return value


def ssh_executable() -> str:
    if os.name == 'nt':
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        for directory in ('System32', 'Sysnative'):
            executable = windows / directory / 'OpenSSH/ssh.exe'
            if executable.is_file():
                return str(executable)
        raise ValueError('Windows OpenSSH Client was not found. Enable the Windows optional feature OpenSSH Client, then resume this wizard. Git Bash SSH is not used for native Windows Python because its command-line quoting differs. No SSH settings were changed.')
    executable = shutil.which('ssh')
    if not executable:
        raise ValueError('OpenSSH is needed for this route. Install the SSH client, then rerun this wizard.')
    return executable


def ssh_options(session: dict, folder: Path) -> list[str]:
    known_hosts = (folder / 'ssh_known_hosts').as_posix().replace('"', '\\"')
    command = [ssh_executable(), '-o', 'ConnectTimeout=15', '-o', 'UserKnownHostsFile="' + known_hosts + '"', '-p', str(session.get('ssh_port', 22))]
    if session.get('ssh_key'):
        command += ['-i', session['ssh_key'], '-o', 'IdentitiesOnly=yes']
    return command


def _saved_private_keys() -> list[Path]:
    directory = Path.home() / '.ssh'
    if not directory.is_dir():
        return []
    defaults = {'id_rsa', 'id_ecdsa', 'id_ecdsa_sk', 'id_ed25519', 'id_ed25519_sk', 'id_xmss', 'id_dsa'}
    candidates = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.is_symlink() or path.name in defaults:
            continue
        try:
            with path.open('rb') as stream:
                header = stream.read(80)
        except OSError:
            continue
        if header.startswith(b'-----BEGIN ') and b'PRIVATE KEY-----' in header:
            candidates.append(path)
    return candidates[:12]


def discover_ssh_identity(session: dict, folder: Path) -> None:
    """Use existing SSH credentials before allowing an interactive password login."""
    if session.get('ssh_key'):
        return
    target = validate_target(session['ssh_target'])
    options = ssh_options(session, folder)
    known_hosts = folder / 'ssh_known_hosts'
    host = target.rsplit('@', 1)[-1]
    lookup = host if int(session.get('ssh_port', 22)) == 22 else f'[{host.strip("[]")}]:{session["ssh_port"]}'
    keygen = str(Path(ssh_executable()).with_name('ssh-keygen.exe')) if os.name == 'nt' else shutil.which('ssh-keygen')
    known = bool(keygen and known_hosts.is_file() and subprocess.run(
        [keygen, '-F', lookup, '-f', str(known_hosts)], capture_output=True
    ).returncode == 0)
    if not known:
        print('Confirm the Linux server fingerprint when SSH asks. This trust record belongs only to this setup.')
        subprocess.run(options + ['-o', 'PubkeyAuthentication=no', '-o', 'PasswordAuthentication=no',
                                  '-o', 'KbdInteractiveAuthentication=no', '-o', 'NumberOfPasswordPrompts=0',
                                  '-o', 'LogLevel=QUIET',
                                  target, 'true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, timeout=30)
    public_key_only = ['-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
                       '-o', 'KbdInteractiveAuthentication=no', '-o', 'StrictHostKeyChecking=yes']

    def accepts(candidate: dict) -> bool:
        result = subprocess.run(ssh_options(candidate, folder) + public_key_only + [target, 'true'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, timeout=30)
        if 'HOST IDENTIFICATION HAS CHANGED' in result.stderr or 'Host key verification failed' in result.stderr:
            raise ValueError('SSH could not verify this server fingerprint. Check the host before continuing; its saved trust record was preserved.')
        return result.returncode == 0

    if accepts(session):
        print('Your existing SSH configuration or agent signed in successfully.')
        return
    for path in _saved_private_keys():
        candidate = dict(session, ssh_key=str(path))
        if accepts(candidate):
            session['ssh_key'] = str(path)
            print('A saved SSH key signed in successfully. You do not need to enter its path.')
            return
    print('No saved SSH key was accepted. SSH will ask for your Linux password or key passphrase.')


def remote(session: dict, folder: Path, code: str, payload: bytes | None = None, timeout: int = 180) -> bytes:
    command = ssh_options(session, folder) + [validate_target(session['ssh_target']), 'python3 -c ' + shlex.quote(code)]
    # SSH owns authentication prompts. Captured output is never printed by this helper;
    # in particular, a downloaded service credential cannot reach terminal output.
    incoming = {'input': payload} if payload is not None else {'stdin': subprocess.DEVNULL}
    result = subprocess.run(command, **incoming, stdout=subprocess.PIPE, check=True, timeout=timeout)
    return result.stdout


PROBE = '''import json,platform,shutil,subprocess,sys,socket
missing=[]
if sys.version_info<(3,11): missing.append('Python 3.11 or newer')
if sys.platform!='linux': missing.append('Linux service host')
if not shutil.which('docker'): missing.append('Docker Engine with Compose')
else:
 r=subprocess.run(['docker','info','--format','{{.OSType}}'],capture_output=True,text=True)
 if r.returncode or r.stdout.strip()!='linux': missing.append('Access to a running Linux Docker daemon')
 r=subprocess.run(['docker','compose','version'],capture_output=True)
 if r.returncode: missing.append('Docker Compose')
s=socket.socket()
try: s.bind(('127.0.0.1',48958))
except OSError: s.bind(('127.0.0.1',0))
port=s.getsockname()[1];s.close()
print(json.dumps({'missing':missing,'port':port,'platform':platform.system()}))'''


def service_probe(session: dict, folder: Path, *, local: bool) -> dict:
    if local:
        result = subprocess.run([sys.executable, '-c', PROBE], check=True, capture_output=True)
        return json.loads(result.stdout)
    ssh_executable()
    discover_ssh_identity(session, folder)
    try:
        return json.loads(remote(session, folder, PROBE))
    except (subprocess.SubprocessError, ValueError) as error:
        raise ValueError('The Linux prerequisite check could not finish. Check the SSH login/host verification and that python3 (3.11+) is installed there. SSH messages appear above. No client profile was configured; rerun this wizard after fixing the host.') from None


def bundle(root: Path) -> bytes:
    names = ['install.sh', 'pyproject.toml', 'Dockerfile', 'compose.yaml', '.dockerignore', '.gitignore', '.gitattributes', 'UPSTREAM_LICENSE.txt', 'upstream-lock.json']
    for directory, suffixes in [('src', {'.py'}), ('scripts', {'.py'}), ('deploy', {'.py'}), ('skills', {'.md', '.yaml'})]:
        for path in (root / directory).rglob('*'):
            if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts:
                names.append(path.relative_to(root).as_posix())
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name in sorted(set(names)):
            path = root / name
            if not path.is_file() or path.is_symlink():
                raise ValueError('The reviewed installer package is incomplete or contains a symbolic link')
            archive.add(path, arcname=name, recursive=False)
    result = stream.getvalue()
    if len(result) > 5_000_000:
        raise ValueError('Setup package exceeds the expected size; no files were transferred')
    return result


def receive_code(identifier: str, digest: str) -> str:
    # Arguments are validated/serialized as data, not interpolated into shell code.
    assert re.fullmatch(r'[a-f0-9]{8}', identifier)
    return '''import hashlib,io,json,os,pathlib,tarfile,sys
os.umask(0o077)
identifier=IDENTIFIER;digest=DIGEST
base=pathlib.Path.home()/'.local/share/conductor-codex'/identifier
marker=base/'.codex/owner.json';root=base/'.codex/source'
data=sys.stdin.buffer.read(5000001)
if hashlib.sha256(data).hexdigest()!=digest: raise SystemExit('Package verification failed')
if base.exists():
 if not marker.is_file() or json.loads(marker.read_text())!={'id':identifier,'digest':digest}: raise SystemExit('Existing directory preserved; ownership or source revision differs')
else:
 root.mkdir(parents=True,mode=0o700)
 marker.write_text(json.dumps({'id':identifier,'digest':digest}))
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
 for item in archive.getmembers():
  target=root/item.name
  if not item.isfile() or not target.resolve().is_relative_to(root.resolve()): raise SystemExit('Unsafe package entry')
  content=archive.extractfile(item).read()
  if target.exists():
   if target.read_bytes()!=content: raise SystemExit('Existing source differs and was preserved')
  else:
   target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
   with target.open('xb') as output: output.write(content)
print(json.dumps({'source':str(root),'private':str(base/'.codex/private/service-token')}))
'''.replace('IDENTIFIER', repr(identifier)).replace('DIGEST', repr(digest))


def install_service(root: Path, session: dict, folder: Path, *, local: bool) -> bytes:
    archive = bundle(root)
    digest = hashlib.sha256(archive).hexdigest()
    receiver = receive_code(session['id'], digest)
    if local:
        result = subprocess.run([sys.executable, '-c', receiver], input=archive, check=True, capture_output=True)
        paths = json.loads(result.stdout)
    else:
        paths = json.loads(remote(session, folder, receiver, archive))
    session.update(remote_source=paths['source'], remote_token=paths['private'])
    command = ['bash', 'install.sh', 'server', '--project', session['project'], '--port', str(session['server_port'])]
    export = ['bash', 'install.sh', 'server', '--project', session['project'], '--export-token', paths['private']]
    if local:
        subprocess.run(command, cwd=paths['source'], check=True, stdin=subprocess.DEVNULL)
        if not Path(paths['private']).exists():
            subprocess.run(export, cwd=paths['source'], check=True, stdin=subprocess.DEVNULL)
        return Path(paths['private']).read_bytes()
    # Build output contains no connection key and can be shown as progress.
    remote_command = 'cd ' + shlex.quote(paths['source']) + ' && ' + shlex.join(command)
    subprocess.run(ssh_options(session, folder) + [session['ssh_target'], remote_command], check=True, stdin=subprocess.DEVNULL)
    code = 'import pathlib,subprocess;root=pathlib.Path(' + repr(paths['source']) + ');target=pathlib.Path(' + repr(paths['private']) + ');'
    code += 'subprocess.run(' + repr(export) + ',cwd=root,check=True) if not target.exists() else None'
    remote(session, folder, code)
    return remote(session, folder, 'from pathlib import Path;import sys;sys.stdout.write(Path(' + repr(paths['private']) + ').read_text())')


def tunnel(session: dict, folder: Path, credential: str) -> str:
    preferred = session.get('local_port', session['server_port'])
    url = 'http://127.0.0.1:' + str(preferred) + '/mcp'
    if health(url, credential):
        return url
    port = available_port(preferred)
    if 'local_port' in session and port != preferred:
        raise ValueError('The saved test connection number is now used by another process. It was not stopped and your profiles were not redirected. Free that number or start a separate setup.')
    session['local_port'] = port
    url = 'http://127.0.0.1:' + str(port) + '/mcp'
    command = ssh_options(session, folder) + ['-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3', '-n', '-N', '-L', f'127.0.0.1:{port}:127.0.0.1:{session["server_port"]}', session['ssh_target']]
    batch = subprocess.run(ssh_options(session, folder) + ['-o', 'BatchMode=yes', session['ssh_target'], 'true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE).returncode == 0
    if batch:
        command[1:1] = ['-o', 'BatchMode=yes']
        print('Your SSH key/agent login works. Starting the private connection in the background.')
    else:
        print('Opening your private SSH connection. SSH may ask for your Linux login or key passphrase.')
        if os.name == 'nt':
            print('Use the separate SSH window for that login, and leave it open while testing Codex.')
    flags = 0
    if os.name == 'nt':
        flags = subprocess.CREATE_NO_WINDOW if batch else subprocess.CREATE_NEW_CONSOLE
    process = subprocess.Popen(command, creationflags=flags)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            if health(url, credential, auth_errors=True):
                session['tunnel_pid'] = process.pid
                return url
        except ValueError:
            process.terminate()
            raise
        if process.poll() is not None:
            raise ValueError('SSH connection closed before the service answered. Check the Linux login/key and rerun this wizard.')
        time.sleep(1)
    process.terminate()
    raise ValueError('The SSH connection did not become ready. Complete the Linux login and check network access, then resume setup.')


BRIDGE_CHECK = '''import asyncio,json,sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def check():
 params=StdioServerParameters(command=sys.executable,args=['-m','conductor_coordination.client_bridge','--settings',sys.argv[1]])
 async with stdio_client(params) as streams:
  async with ClientSession(*streams) as session:
   await session.initialize()
   tools=await session.list_tools()
   assert len(tools.tools)==14
   result=await session.call_tool('coordination_health',{})
   assert not result.is_error
   print('Conductor connection verified: 14 tools and coordination health passed.')
asyncio.run(check())'''


def verify_bridge(executable: Path, settings: Path) -> None:
    result = subprocess.run([str(executable), '-c', BRIDGE_CHECK, str(settings)], capture_output=True, text=True, timeout=45)
    if result.returncode:
        diagnostic = result.stdout + '\n' + result.stderr
        try:
            saved = json.loads(settings.read_text())
            if saved.get('mode') == 'local':
                diagnostic = diagnostic[-12000:]
            else:
                token = Path(saved['token_file']).read_text().strip()
                diagnostic = diagnostic.replace(token, '[redacted]') if token else 'Credential unavailable; diagnostic omitted.'
        except (OSError, ValueError, KeyError):
            diagnostic = 'Credential unavailable; diagnostic omitted.'
        log = settings.parent / 'connection-check.log'
        private_write(log, diagnostic[-12000:].encode('utf-8'), replace=True)
        raise ValueError('The Conductor Codex connection did not pass. Client setup is not marked ready. A private diagnostic is saved at ' + str(log) + '. Rerun the wizard to retry.')
    print(result.stdout.strip())


def vscode_paths() -> tuple[list[str], str]:
    code = shutil.which('code')
    if os.name != 'nt':
        if not code:
            raise ValueError('VS Code was not found. Install it and enable its code command, then use the launch option again.')
        return [code], code
    candidates = []
    if code:
        candidates.append(Path(code).parent.parent / 'Code.exe')
    candidates.append(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Microsoft VS Code/Code.exe')
    for exe in candidates:
        locations = []
        wrapper = exe.parent / 'bin/code.cmd'
        if wrapper.is_file():
            for relative in re.findall(r'"%~dp0([^"]*cli\.js)"', wrapper.read_text()):
                path = (wrapper.parent / relative).resolve()
                if path.is_relative_to(exe.parent.resolve()):
                    locations.append(path)
        locations.append(exe.parent / 'resources/app/out/cli.js')
        for cli in locations:
            if exe.is_file() and cli.is_file():
                return [str(exe), str(cli)], str(exe)
    raise ValueError('VS Code was not found. Install it, then use the launch option again. Existing editor settings were not changed.')


def client_requirements(profiles: list[str]) -> list[str]:
    messages = []
    if 'vscode' in profiles:
        try:
            vscode_paths()
        except ValueError:
            messages.append('VS Code was not found. Install it from https://code.visualstudio.com/ before opening its test profile.')
    if 'desktop' in profiles:
        if os.name != 'nt':
            messages.append('Automatic isolated desktop launch is not verified for this OS. Use a separate test account/VM; profile preparation is still available.')
        else:
            messages.append('Automatic isolated Codex desktop launch is not supported by this installer. Use a separate Windows test account/VM, or set up your usual Codex profile and restart the app.')
    return messages


def launch(profile: str, session: dict, folder: Path) -> None:
    if session.get('mode') != 'isolated':
        raise ValueError('This launch command only opens isolated test profiles')
    info = session.get('clients', {}).get(profile)
    if not info:
        raise ValueError('That client profile has not passed setup yet. Resume the wizard first.')
    home, workspace = Path(info['home']), Path(info['workspace'])
    if not home.resolve().is_relative_to(Path(session['test_root']).resolve()) or not (home / 'config.toml').is_file():
        raise ValueError('Test profile ownership/path check failed; no application was launched')
    env = dict(os.environ, CODEX_HOME=str(home))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    data = home.parent / 'application-data'
    if profile == 'vscode':
        data.mkdir(mode=0o700, exist_ok=True)
        cli, executable = vscode_paths()
        extensions = home.parent / 'extensions'
        args = ['--user-data-dir', str(data), '--extensions-dir', str(extensions)]
        install_env = dict(env)
        if os.name == 'nt':
            install_env['ELECTRON_RUN_AS_NODE'] = '1'
        print('Installing the Codex extension into this test editor only. This may take a minute.')
        subprocess.run(cli + args + ['--install-extension', 'openai.chatgpt'], env=install_env, check=True)
        subprocess.Popen([executable, *args, '--new-window', str(workspace)], env=env)
        return
    raise ValueError('Automatic isolated Codex desktop launch is not supported. Use a separate Windows test account/VM, or set up your usual Codex profile and restart the app.')
