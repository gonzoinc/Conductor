"""The SSH command uses OS login identity without importing MCP."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conductor_coordination.coordination_errors import CoordinationError
from conductor_coordination.ssh_worker import MAX_REQUEST, account_name, handle


def request(action: str, **arguments) -> bytes:
    return json.dumps({'action': action, 'arguments': arguments}).encode()


class SSHWorkerTest(unittest.TestCase):
    def test_per_login_namespace_and_idempotent_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'CONDUCTOR_DB': str(Path(folder) / 'tasks.sqlite3')}, clear=False):
                data = request('coordination_claim', workload_id='release', manager_id='manager-1',
                               client='cli', operation_id='claim-1')
                first = handle(data, 'alice')
                repeat = handle(data, 'alice')
                self.assertEqual(first, repeat)
                self.assertEqual(first['result']['epoch'], 1)
                self.assertEqual(len(handle(request('coordination_workloads'), 'bob')['result']['workloads']), 0)
                self.assertEqual(len(handle(request('coordination_workloads'), 'alice')['result']['workloads']), 1)
                with self.assertRaises(CoordinationError):
                    handle(request('coordination_snapshot', workload_id='release'), 'bob')

    def test_request_validation_and_login_mapping(self):
        self.assertTrue(account_name('Alice@example').startswith('ssh_'))
        self.assertEqual(account_name('alice'), 'alice')
        with self.assertRaisesRegex(CoordinationError, '120 KB'):
            handle(b'x' * (MAX_REQUEST + 1), 'alice')
        with self.assertRaisesRegex(CoordinationError, 'action and arguments'):
            handle(b'{}', 'alice')


if __name__ == '__main__':
    unittest.main()
