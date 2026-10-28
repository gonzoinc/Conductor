"""Verify an SSH-backed hosted Conductor connection before changing Codex settings."""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
import json

from conductor_coordination.client_bridge import read_private, ssh_tunnel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-target', required=True)
    parser.add_argument('--token-file', required=True, type=Path)
    args = parser.parse_args()
    token = read_private(args.token_file).strip()
    url = ssh_tunnel(args.ssh_target).removesuffix('/mcp') + '/healthz'
    request = Request(url, headers={'Authorization': 'Bearer ' + token})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
            if json.load(response).get('status') != 'ok':
                raise RuntimeError('The VM did not return a healthy Conductor service')
    except HTTPError as error:
        if error.code == 401:
            parser.exit(1, 'The VM rejected the exported Conductor key; ask its administrator to check that key.\n')
        if error.code == 500:
            parser.exit(1, 'The VM service failed while checking this user key; inspect conductor.service logs on the VM.\n')
        parser.exit(1, f'The VM returned HTTP {error.code} during connection verification.\n')
    except (URLError, OSError, RuntimeError, ValueError) as error:
        parser.exit(1, 'The Conductor VM connection failed: ' + str(error) + '\n')
    print('Conductor service and private connection key verified.')


if __name__ == '__main__':
    main()
