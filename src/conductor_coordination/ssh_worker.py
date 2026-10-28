"""Run one bounded ledger operation for an SSH-authenticated VM login."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

from conductor_coordination.coordination_dispatch import dispatch_action
from conductor_coordination.coordination_errors import CoordinationError
from conductor_coordination.host_config import open_store


MAX_REQUEST = 120_000
MAX_RESPONSE = 200_000


def account_name(login: str) -> str:
    if re.fullmatch(r'[a-z][a-z0-9_-]{0,62}', login) and login != 'owner':
        return login
    return 'ssh_' + hashlib.sha256(login.encode('utf-8')).hexdigest()[:24]


def handle(raw: bytes, login: str) -> dict:
    if len(raw) > MAX_REQUEST:
        raise CoordinationError('Request exceeds 120 KB', 'request_too_large')
    try:
        request = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise CoordinationError('Request must be UTF-8 JSON', 'invalid_request') from None
    if (not isinstance(request, dict) or set(request) != {'action', 'arguments'}
            or not isinstance(request['action'], str) or not isinstance(request['arguments'], dict)):
        raise CoordinationError('Request needs action and arguments', 'invalid_request')
    store = open_store()
    principal = store.create_user(account_name(login))['id']
    try:
        result = dispatch_action(request['action'], request['arguments'], store, principal)
    except TypeError:
        raise CoordinationError('Invalid arguments for this action', 'invalid_request') from None
    if request['action'] == 'coordination_health':
        result = {'status': 'ok', 'protocol_version': result['protocol_version'],
                  'persistence': result['persistence']}
    return {'ok': True, 'result': result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login', required=True)
    login = parser.parse_args().login
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        response = handle(raw, login)
    except CoordinationError as error:
        response = {'ok': False, 'error': {'code': error.code, 'message': str(error)}}
    except (OSError, ValueError, KeyError):
        response = {'ok': False, 'error': {'code': 'service_error', 'message': 'Conductor could not complete the request'}}
    encoded = json.dumps(response, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(encoded) > MAX_RESPONSE:
        encoded = b'{"ok":false,"error":{"code":"response_too_large","message":"Narrow the requested page"}}'
    sys.stdout.buffer.write(encoded + b'\n')
    return 0 if response['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
