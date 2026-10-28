"""Per-user isolation, key lifecycle and actual hosted MCP authorization."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from conductor_coordination import accounts, coordination_server
from conductor_coordination.coordination_dispatch import dispatch_action
from conductor_coordination.coordination_errors import CoordinationError
from conductor_coordination.coordination_store import Store


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.db)
        self.alice = self.store.create_user('alice')
        self.bob = self.store.create_user('bob')

    def test_two_users_get_private_workloads_with_same_public_id(self):
        a = self.store.claim('same-work', 'alice-manager', 'desktop', 'a-claim', principal_id=self.alice['id'])
        b = self.store.claim('same-work', 'bob-manager', 'vscode', 'b-claim', principal_id=self.bob['id'])
        self.assertEqual((a['id'], b['id']), ('same-work', 'same-work'))
        self.assertEqual(a['epoch'], 1)
        self.assertEqual(b['epoch'], 1)
        self.assertEqual(self.store.snapshot('same-work', principal_id=self.alice['id'])['workload']['manager'], 'alice-manager')
        self.assertEqual(self.store.snapshot('same-work', principal_id=self.bob['id'])['workload']['manager'], 'bob-manager')
        self.assertEqual([row['id'] for row in self.store.workloads(principal_id=self.alice['id'])['workloads']], ['same-work'])
        self.assertEqual([row['id'] for row in self.store.workloads(principal_id=self.bob['id'])['workloads']], ['same-work'])
        self.assertNotIn(self.alice['id'], json.dumps(self.store.snapshot('same-work', principal_id=self.alice['id'])))

    def test_cross_user_reads_return_same_not_found_as_missing_workload(self):
        self.store.claim('private', 'alice-manager', 'desktop', 'a-claim', principal_id=self.alice['id'])
        for workload in ('private', 'missing'):
            with self.subTest(workload=workload):
                with self.assertRaisesRegex(CoordinationError, 'Unknown workload'):
                    self.store.snapshot(workload, principal_id=self.bob['id'])
                with self.assertRaisesRegex(CoordinationError, 'Unknown workload'):
                    self.store.events(workload, principal_id=self.bob['id'])
                with self.assertRaisesRegex(CoordinationError, 'Unknown task'):
                    self.store.task(workload, 'task', principal_id=self.bob['id'])
        self.assertEqual(self.store.workloads(principal_id=self.bob['id'])['workloads'], [])

    def test_all_named_workload_routes_enforce_principal(self):
        self.store.claim('private', 'alice-manager', 'desktop', 'a-claim', principal_id=self.alice['id'])
        identity = {'workload_id': 'private', 'manager_id': 'alice-manager',
                    'epoch': 1, 'operation_id': 'bob-probe'}
        requests = {
            'coordination_renew': identity | {'lease_seconds': 300},
            'coordination_release': identity,
            'coordination_create_task': identity | {'task_id': 'task', 'title': 'Probe', 'kind': 'scout',
                'project_path': '/project', 'workspace_path': '/workspace', 'captain_intent': 'Probe',
                'spec': 'Probe', 'definition_of_done': ['Done'], 'depends_on': [], 'covers': [], 'required': True},
            'coordination_assign': identity | {'task_id': 'task', 'native_agent_id': '/root/bob'},
            'coordination_update': identity | {'task_id': 'task', 'native_agent_id': '/root/bob',
                'state': 'running', 'summary': '', 'evidence': []},
            'coordination_observe': identity | {'task_id': 'task', 'native_agent_id': '/root/bob',
                'observed_state': 'working', 'detail': ''},
            'coordination_recover': identity | {'task_id': 'task', 'prior_native_agent_id': '/root/bob',
                'confirmed_stopped': True, 'reason': 'Stopped'},
            'coordination_complete': identity | {'summary': 'Done'},
            'coordination_snapshot': {'workload_id': 'private'},
            'coordination_task': {'workload_id': 'private', 'task_id': 'task'},
            'coordination_events': {'workload_id': 'private'},
        }
        for name, arguments in requests.items():
            with self.subTest(name=name), self.assertRaises(CoordinationError):
                dispatch_action(name, arguments, self.store, self.bob['id'])
        self.assertEqual(dispatch_action('coordination_workloads', {}, self.store, self.bob['id'])['workloads'], [])
        self.assertEqual(self.store.snapshot('private', principal_id=self.alice['id'])['workload']['manager'], 'alice-manager')

    def test_keys_are_distinct_hashed_and_revoked_immediately(self):
        first = self.store.issue_key('alice', 'desktop')
        second = self.store.issue_key('alice', 'vscode')
        self.assertNotEqual(first['token'], second['token'])
        self.assertEqual(self.store.principal_for_token(first['token']), self.alice['id'])
        self.assertEqual(self.store.principal_for_token(second['token']), self.alice['id'])
        self.assertNotIn(first['token'], self.db.read_bytes().decode(errors='ignore'))
        self.assertTrue(self.store.revoke_key(first['key_id']))
        self.assertFalse(self.store.revoke_key(first['key_id']))
        self.assertIsNone(self.store.principal_for_token(first['token']))
        self.assertEqual(Store(self.db).principal_for_token(second['token']), self.alice['id'])

    def test_existing_pre_account_database_becomes_owner_only(self):
        old = Path(self.temp.name) / 'old.sqlite3'
        with sqlite3.connect(old) as db:
            db.execute('CREATE TABLE workloads(id TEXT PRIMARY KEY,manager TEXT,client TEXT,epoch INTEGER,expires REAL,state TEXT,summary TEXT,updated REAL)')
            db.execute("INSERT INTO workloads VALUES('before','manager','desktop',1,0,'open','',0)")
        migrated = Store(old)
        migrated.ensure_owner_key('old-private-service-token-' + 'x' * 40)
        self.assertEqual(migrated.snapshot('before', principal_id='owner')['workload']['id'], 'before')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            migrated.create_user('owner')
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            migrated.issue_key('owner', 'extra')
        with self.assertRaisesRegex(CoordinationError, 'Unknown workload'):
            migrated.snapshot('before', principal_id=self.alice['id'])

    def test_workspace_conflict_is_scoped_to_account(self):
        for user, manager, op in ((self.alice, 'a-manager', 'a'), (self.bob, 'b-manager', 'b')):
            self.store.claim('work', manager, 'desktop', op + '-claim', principal_id=user['id'])
            self.store.mutate('work', manager, 1, op + '-create', 'create', {
                'task_id': 'task', 'title': 'Work', 'kind': 'implementation', 'project_path': '/project',
                'workspace_path': '/same/workspace', 'captain_intent': 'Implement', 'spec': 'Implement',
                'definition_of_done': ['Done'], 'depends_on': [], 'covers': [], 'required': True,
            }, principal_id=user['id'])
        self.store.mutate('work', 'a-manager', 1, 'a-assign', 'assign',
                          {'task_id': 'task', 'native_agent_id': '/root/alice'}, principal_id=self.alice['id'])
        self.store.mutate('work', 'b-manager', 1, 'b-assign', 'assign',
                          {'task_id': 'task', 'native_agent_id': '/root/bob'}, principal_id=self.bob['id'])
        self.assertEqual(self.store.task('work', 'task', principal_id=self.bob['id'])['state'], 'assigned')
        self.store.claim('other-work', 'a-manager', 'desktop', 'other-claim', principal_id=self.alice['id'])
        self.store.mutate('other-work', 'a-manager', 1, 'other-create', 'create', {
            'task_id': 'task', 'title': 'Other work', 'kind': 'implementation', 'project_path': '/project',
            'workspace_path': '/same/workspace', 'captain_intent': 'Implement', 'spec': 'Implement',
            'definition_of_done': ['Done'], 'depends_on': [], 'covers': [], 'required': True,
        }, principal_id=self.alice['id'])
        with self.assertRaisesRegex(CoordinationError, 'separate workspaces'):
            self.store.mutate('other-work', 'a-manager', 1, 'other-assign', 'assign',
                              {'task_id': 'task', 'native_agent_id': '/root/alice-other'}, principal_id=self.alice['id'])


class HostedAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.owner = root / 'owner.token'
        self.health = root / 'health.token'
        self.owner.write_text('owner-private-token-' + 'o' * 40)
        self.health.write_text('health-private-token-' + 'h' * 40)
        self.owner.chmod(0o600)
        self.health.chmod(0o600)
        self.environment = patch.dict(os.environ, {'CONDUCTOR_DB': str(root / 'tasks.sqlite3'),
                                                    'CONDUCTOR_HEALTH_TOKEN_FILE': str(self.health)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = Store(root / 'tasks.sqlite3')
        self.store.ensure_owner_key(self.owner.read_text())
        self.alice = self.store.create_user('alice')
        self.bob = self.store.create_user('bob')
        self.alice_key = self.store.issue_key('alice', 'desktop')
        self.bob_key = self.store.issue_key('bob', 'vscode')

    @staticmethod
    def _call(client, key, name, arguments):
        return client.post('/mcp', headers={'Authorization': 'Bearer ' + key,
                                            'Accept': 'application/json, text/event-stream'},
                           json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': name, 'arguments': arguments}})

    def test_health_key_cannot_call_mcp_and_revocation_takes_effect(self):
        with TestClient(coordination_server.create_http_app(self.owner)) as client:
            self.assertEqual(client.get('/healthz', headers={'Authorization': 'Bearer ' + self.health.read_text()}).status_code, 200)
            self.assertEqual(self._call(client, self.health.read_text(), 'coordination_health', {}).status_code, 401)
            self.assertEqual(self._call(client, self.alice_key['token'], 'coordination_health', {}).status_code, 200)
            self.store.revoke_key(self.alice_key['key_id'])
            self.assertEqual(self._call(client, self.alice_key['token'], 'coordination_health', {}).status_code, 401)
            self.assertEqual(client.get('/healthz', headers={'Authorization': 'Bearer ' + self.alice_key['token']}).status_code, 401)

    def test_actual_mcp_concurrent_clients_remain_isolated(self):
        with TestClient(coordination_server.create_http_app(self.owner)) as client:
            def claim(key, manager):
                response = self._call(client, key, 'coordination_claim', {
                    'workload_id': 'same', 'manager_id': manager, 'client': 'desktop',
                    'operation_id': manager + '-claim', 'lease_seconds': 300,
                })
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()['result']
                self.assertFalse(result.get('isError', False), result)
                return json.loads(result['content'][0]['text'])
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = list(pool.map(lambda entry: claim(*entry),
                                     ((self.alice_key['token'], 'alice-manager'),
                                      (self.bob_key['token'], 'bob-manager'))))
            self.assertEqual((a['id'], b['id']), ('same', 'same'))
            for key, expected in ((self.alice_key['token'], 'alice-manager'),
                                  (self.bob_key['token'], 'bob-manager')):
                response = self._call(client, key, 'coordination_snapshot', {'workload_id': 'same'})
                result = json.loads(response.json()['result']['content'][0]['text'])
                self.assertEqual(result['workload']['manager'], expected)


@unittest.skipIf(os.name == 'nt', 'Host-local account CLI uses Linux private modes')
class AccountCliTests(unittest.TestCase):
    def test_noninteractive_private_file_issue_is_idempotent_and_never_prints_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            database = root / 'tasks.sqlite3'
            owner = root / 'owner.key'
            health = root / 'health.key'
            user = root / 'alice.key'
            out = io.StringIO()
            with redirect_stdout(out):
                accounts.main(['--db', str(database), 'bootstrap', '--owner-token-file', str(owner), '--health-token-file', str(health)])
                accounts.main(['--db', str(database), 'create-user', 'alice'])
                accounts.main(['--db', str(database), 'create-user', 'alice'])
                accounts.main(['--db', str(database), 'issue-key', 'alice', '--label', 'desktop', '--output', str(user)])
                accounts.main(['--db', str(database), 'issue-key', 'alice', '--label', 'desktop', '--output', str(user)])
            self.assertTrue(user.is_file())
            self.assertEqual(user.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(user.read_text(), out.getvalue())
            self.assertEqual(Store(database).principal_for_token(user.read_text()), Store(database).create_user('alice')['id'])
