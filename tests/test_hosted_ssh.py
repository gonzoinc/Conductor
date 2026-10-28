"""Bounded checks for the per-client SSH tunnel setup."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from conductor_coordination import client_bridge


class HostedSshTests(unittest.TestCase):
    def test_rejects_untrusted_target_before_launch(self):
        with patch.object(client_bridge.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):
                client_bridge.ssh_tunnel('-oProxyCommand=bad')
            launch.assert_not_called()

    def test_opens_loopback_forwarding_with_noninteractive_host_verification(self):
        class Running:
            def poll(self):
                return None

            def terminate(self):
                pass

        process = Running()
        with patch.object(client_bridge.subprocess, 'Popen', return_value=process) as launch, \
             patch.object(client_bridge.socket, 'create_connection', return_value=nullcontext()), \
             patch.object(client_bridge.atexit, 'register'):
            url = client_bridge.ssh_tunnel('alice@vm.example', 8012)
        self.assertRegex(url, r'^http://127\.0\.0\.1:\d+/mcp$')
        command = launch.call_args.args[0]
        self.assertEqual(command[0], 'ssh')
        self.assertIn('BatchMode=yes', command)
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertEqual(command[-1], 'alice@vm.example')
        self.assertTrue(command[command.index('-L') + 1].startswith('127.0.0.1:'))
        self.assertTrue(command[command.index('-L') + 1].endswith(':127.0.0.1:8012'))


if __name__ == '__main__':
    unittest.main()
