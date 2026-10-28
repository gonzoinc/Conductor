"""Narrow compatibility for installer-owned Firstmate candidate state only.

Never rewrite user paths, deployed services, credentials, or existing client data.
"""
import json

DEPLOYED_MESSAGE = 'An earlier Firstmate setup has deployed resources. Its service, data and profiles are preserved.'
from pathlib import Path


def adopt_guided_directory(path: Path, identity: dict) -> bool:
    legacy = path / '.firstmate-installer.json'
    if identity.get('purpose') != 'conductor-guided-setup' or not legacy.is_file() or legacy.is_symlink():
        return False
    expected = dict(identity, purpose='firstmate-guided-setup')
    if json.loads(legacy.read_text()) != expected:
        return False
    with (path / '.conductor-installer.json').open('x', encoding='utf-8') as output:
        json.dump(identity, output)
    return True


def session_kind(state: dict, source: Path) -> str:
    if state.get('version') != 1 or state.get('source') != str(source):
        raise ValueError('Saved setup belongs to another source/version; it was preserved')
    if state.get('project') == 'conductor-test-' + str(state.get('id')):
        return 'current'
    if state.get('project') != 'firstmate-test-' + str(state.get('id')):
        raise ValueError('Saved setup has an unknown project identity; existing files were preserved')
    if (state.get('mode') == 'isolated' and state.get('stage') in ('started', 'service host selected')
            and not any(state.get(key) for key in ('remote_source', 'token_file', 'clients'))):
        return 'pending'
    return 'deployed'


def migrate_pending(state: dict, source: Path) -> dict:
    if session_kind(state, source) != 'pending':
        raise ValueError('Only an undeployed owned setup can be resumed with Conductor')
    return dict(state, project='conductor-test-' + state['id'], product='Conductor')


def guard_client(codex_home: Path, skills_dir: Path, config: dict) -> None:
    # The old skill has a different name and can remain untouched beside the
    # Conductor skill. An old service/client still needs a deliberate migration.
    candidates = (codex_home / 'firstmate-coordination',)
    servers = config.get('mcp_servers', {})
    if any(path.exists() or path.is_symlink() for path in candidates) or (isinstance(servers, dict) and 'firstmate_coordination' in servers):
        raise ValueError('An existing Firstmate client was preserved. Create a separate isolated Conductor profile; automatic replacement is not supported.')
