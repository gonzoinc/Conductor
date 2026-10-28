"""Compatibility fixtures from the private Firstmate candidate; no live data used."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location('legacy_test_' + name, ROOT / ('scripts/' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
CORE, GUIDED, SUPPORT = load('bootstrap'), load('guided_install'), load('install_support')
LEGACY = CORE.compatibility()

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'FirstmateCodexAcceptance' / '.codex/source'
        self.base = self.root / '.codex/guided-install'
        self.folder = self.base / 'd491d2f6'
        self.folder.mkdir(parents=True)
        self.identity = {'purpose': 'conductor-guided-setup', 'source': str(self.root)}
        self.old_identity = dict(self.identity, purpose='firstmate-guided-setup')
        (self.base / '.firstmate-installer.json').write_text(json.dumps(self.old_identity))
        self.state = {'version': 1, 'source': str(self.root), 'id': 'd491d2f6',
                      'project': 'firstmate-test-d491d2f6', 'stage': 'service host selected',
                      'mode': 'isolated', 'clients': {}, 'ssh_target': 'user@example',
                      'ssh_port': 2222, 'ssh_key': 'C:/Users/example/.ssh/existing key'}
        (self.base / 'latest.json').write_text(json.dumps({'id': 'd491d2f6'}))
        self.save()
    def save(self):
        (self.folder / 'state.json').write_text(json.dumps(self.state))
    def start(self, answer):
        with patch.object(GUIDED, 'support', return_value=SUPPORT), patch('builtins.input', return_value=answer):
            wizard = GUIDED.Wizard(self.root, CORE)
            wizard.start_session()
            return wizard
    def test_pending_real_shape_preserves_answers_and_original_bytes(self):
        original = (self.folder / 'state.json').read_bytes()
        wizard = self.start('1')
        expected = dict(self.state, project='conductor-test-d491d2f6', product='Conductor')
        self.assertEqual(wizard.state, expected)
        self.assertEqual((self.folder / 'state.before-conductor.json').read_bytes(), original)
        self.assertEqual(json.loads((self.base / '.firstmate-installer.json').read_text()), self.old_identity)
        self.assertEqual(json.loads((self.base / '.conductor-installer.json').read_text()), self.identity)
        self.assertEqual(self.start('1').state, expected)
    def test_marker_requires_exact_owner_and_source(self):
        for identity in ({'purpose': 'unrelated', 'source': str(self.root)}, dict(self.old_identity, source='other')):
            (self.base / '.firstmate-installer.json').write_text(json.dumps(identity))
            with self.assertRaises(ValueError):
                CORE.owned_directory(self.base, self.identity)
            self.assertFalse((self.base / '.conductor-installer.json').exists())
    def test_deployed_state_is_untouched_and_fresh_setup_separate(self):
        self.state.update(remote_source='/home/user/firstmate-test-d491d2f6', token_file='/private/firstmate-token', clients={'desktop': {'home':'old-profile'}})
        self.save(); original=(self.folder / 'state.json').read_bytes()
        wizard=self.start('1')
        self.assertNotEqual(wizard.folder, self.folder)
        self.assertTrue(wizard.state['project'].startswith('conductor-test-'))
        self.assertEqual((self.folder / 'state.json').read_bytes(), original)
        self.assertFalse((self.folder / 'state.before-conductor.json').exists())
    def test_deployed_exit_preserves_pointer_and_state(self):
        self.state['token_file']='/private/token'; self.save()
        before=(self.base / 'latest.json').read_bytes()
        with self.assertRaises(GUIDED.Cancelled):self.start('2')
        self.assertEqual((self.base / 'latest.json').read_bytes(),before)
    def test_unknown_session_refused(self):
        self.state['project']='unrelated';self.save()
        with self.assertRaises(ValueError):self.start('1')
    def test_old_runtime_is_never_adopted(self):
        with self.assertRaises(ValueError):
            CORE.owned_directory(self.base, dict(self.identity, purpose='conductor-client-runtime'))
    def test_legacy_client_guard_preserves_config(self):
        home=self.root/'home';skills=self.root/'skills';home.mkdir()
        config={'mcp_servers':{'firstmate_coordination':{'command':'old'}}}
        with self.assertRaises(ValueError):LEGACY.guard_client(home,skills,config)
        self.assertEqual(config['mcp_servers']['firstmate_coordination']['command'],'old')
        LEGACY.guard_client(home,skills,{'mcp_servers':{'other':{}}})
        (skills/'firstmate-coordinator').mkdir(parents=True)
        (home/'skills/firstmate-coordinator').mkdir(parents=True)
        LEGACY.guard_client(home,skills,{})
        (home/'firstmate-coordination').mkdir()
        with self.assertRaises(ValueError):LEGACY.guard_client(home,skills,{})
