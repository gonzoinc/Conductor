"""Build and initialize the private Docker volumes; never display a credential."""
from __future__ import annotations
import argparse
import ipaddress
import os
from pathlib import Path
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-token', type=Path, metavar='PRIVATE_FILE', help='Export from running service without printing; refuses overwrite')
    parser.add_argument('--port', type=int, help='Host port; default8012 or existing .env')
    parser.add_argument('--bind', help='Host bind address; default127.0.0.1 or existing .env')
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    if args.bind is not None:
        try: ipaddress.ip_address(args.bind)
        except ValueError: parser.error('bind must be an IP address')
    root = Path(__file__).resolve().parents[1]
    if args.export_token is not None:
        target = args.export_token.expanduser().absolute()
        if any(part.is_symlink() for part in [target, *target.parents]): parser.error('credential destination may not contain symlinks')
        if not target.parent.is_dir() or target.parent.stat().st_mode & 0o077:
            parser.error('create a private destination directory first (mode0700)')
        if target.exists(): parser.error('credential destination exists; refusing overwrite')
        try:
            result = subprocess.run(['docker','compose','exec','-T','service','python','-c',"from pathlib import Path; import sys; sys.stdout.write(Path('/state/service-token').read_text())"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            credential = result.stdout.decode().strip()
            if len(credential) < 32 or any(char.isspace() for char in credential): raise ValueError('invalid credential')
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output: output.write(credential)
        except (OSError, ValueError, subprocess.CalledProcessError):
            parser.exit(1, 'Private credential export failed; check service readiness and destination.\n')
        print('Credential exported privately; contents were not displayed.')
        return
    env_file = root / '.env'
    if args.port is not None or args.bind is not None:
        desired = '# Conductor deployment settings; no credentials\n'
        desired += 'CONDUCTOR_PORT=' + str(args.port or 8012) + '\n'
        desired += 'CONDUCTOR_BIND=' + (args.bind or '127.0.0.1') + '\n'
        if env_file.is_symlink(): parser.error('.env may not be a symlink')
        if env_file.exists() and env_file.read_text() != desired:
            parser.error('.env already differs; edit it deliberately or keep existing settings by omitting --port/--bind')
        if not env_file.exists():
            fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output: output.write(desired)
    try:
        subprocess.run(['docker','compose','version'], cwd=root, check=True)
        subprocess.run(['docker','compose','build','service'], cwd=root, check=True)
        subprocess.run(['docker','compose','run','--rm','--no-deps','--user','0:0','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','service','python','-m','conductor_coordination.deployment','init'], cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError):
        parser.exit(1, 'Setup failed; inspect Docker output. Existing credentials were not intentionally replaced.\n')
    print('Private state initialized. Start: docker compose up -d')
    print('Verify: docker compose ps')
    print('Export privately: python3 deploy/setup.py --export-token /your/private/service-token')

if __name__ == '__main__': main()
