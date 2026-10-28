"""Two native stdio bridge processes share one local ledger without a VM."""
from contextlib import AsyncExitStack
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conductor_coordination.coordination_store import Store


@unittest.skipIf(os.name == 'nt' and importlib.util.find_spec('win32security') is None,
                 'Windows client ACL check needs pywin32')
class LocalBridgeConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_bridge_processes_share_state_and_fence_one_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / 'state'
            state.mkdir(mode=0o700)
            database = state / 'tasks.sqlite3'
            old_mask = os.umask(0o077)
            try:
                Store(database)
            finally:
                os.umask(old_mask)
            database.chmod(0o600)
            settings = root / 'local.json'
            settings.write_text(json.dumps({'mode': 'local', 'state_root': str(state),
                                            'db_path': str(database)}))
            settings.chmod(0o600)
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'conductor_coordination.client_bridge', '--settings', str(settings)],
                env=dict(os.environ))
            async with AsyncExitStack() as stack:
                first_io = await stack.enter_async_context(stdio_client(params))
                second_io = await stack.enter_async_context(stdio_client(params))
                first = await stack.enter_async_context(ClientSession(*first_io))
                second = await stack.enter_async_context(ClientSession(*second_io))
                await first.initialize()
                await second.initialize()
                self.assertEqual(len((await first.list_tools()).tools), 14)
                self.assertEqual(len((await second.list_tools()).tools), 14)
                health = await second.call_tool('coordination_health', {})
                self.assertFalse(health.is_error)
                first_claim = await first.call_tool('coordination_claim', {
                    'workload_id': 'shared', 'manager_id': 'desktop-manager',
                    'client': 'desktop', 'operation_id': 'claim-desktop'})
                self.assertFalse(first_claim.is_error)
                conflict = await second.call_tool('coordination_claim', {
                    'workload_id': 'shared', 'manager_id': 'vscode-manager',
                    'client': 'vscode', 'operation_id': 'claim-vscode'})
                self.assertTrue(conflict.is_error)
                independent = await second.call_tool('coordination_claim', {
                    'workload_id': 'independent', 'manager_id': 'vscode-manager',
                    'client': 'vscode', 'operation_id': 'claim-independent'})
                self.assertFalse(independent.is_error)
                shared = await second.call_tool('coordination_snapshot', {'workload_id': 'shared'})
                self.assertFalse(shared.is_error)
                self.assertEqual(json.loads(shared.content[0].text)['workload']['manager'], 'desktop-manager')
            async with stdio_client(params) as resumed_io:
                async with ClientSession(*resumed_io) as resumed:
                    await resumed.initialize()
                    saved = await resumed.call_tool('coordination_snapshot', {'workload_id': 'independent'})
                    self.assertFalse(saved.is_error)
                    self.assertEqual(json.loads(saved.content[0].text)['workload']['manager'], 'vscode-manager')
