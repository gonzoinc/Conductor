"""Run only against an isolated, explicitly supplied MySQL/MariaDB test schema."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
from unittest.mock import patch
import uuid

from conductor_coordination.coordination_errors import CoordinationError
from conductor_coordination.host_config import open_store


@unittest.skipUnless(os.environ.get('CONDUCTOR_TEST_MYSQL_CONFIG'), 'No isolated MySQL test config')
class MySQLStoreIntegrationTest(unittest.TestCase):
    def test_accounts_keys_and_competing_managers(self):
        with patch.dict(os.environ, CONDUCTOR_CONFIG=os.environ['CONDUCTOR_TEST_MYSQL_CONFIG']):
            store = open_store()
        suffix = uuid.uuid4().hex[:10]
        alice = store.create_user('alice_' + suffix)
        bob = store.create_user('bob_' + suffix)
        key = store.issue_key('alice_' + suffix, 'test')
        self.assertEqual(store.principal_for_token(key['token']), alice['id'])
        self.assertTrue(store.revoke_key(key['key_id']))
        self.assertIsNone(store.principal_for_token(key['token']))
        workload = 'same-' + suffix
        self.assertEqual(store.claim(workload, 'alice-manager', 'desktop', 'alice-claim',
                                     principal_id=alice['id'])['id'], workload)
        self.assertEqual(store.claim(workload, 'bob-manager', 'vscode', 'bob-claim',
                                     principal_id=bob['id'])['id'], workload)
        self.assertEqual(store.snapshot(workload, principal_id=alice['id'])['workload']['manager'], 'alice-manager')
        self.assertEqual(store.snapshot(workload, principal_id=bob['id'])['workload']['manager'], 'bob-manager')

        def claim(manager):
            try:
                return store.claim('contested-' + suffix, manager, 'desktop', manager + '-claim',
                                   principal_id=alice['id'])['manager']
            except CoordinationError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(claim, ('manager-one', 'manager-two')))
        self.assertEqual(results.count('lease_conflict'), 1)
        self.assertEqual(sum(value.startswith('manager-') for value in results), 1)


if __name__ == '__main__':
    unittest.main()
