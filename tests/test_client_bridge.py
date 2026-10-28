"""Real stdio MCP initialization/tool discovery with a bounded HTTP test service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class BridgeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_stdio_session_discovers_and_forwards_fourteen_tools(self):
        token = "stdio-session-test-" + "y" * 40
        observations = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                observations.append((self.path, self.headers.get("Authorization")))
                if self.path != "/mcp" or self.headers.get("Authorization") != "Bearer " + token:
                    self.send_error(401)
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                result = {"protocol_version": "conductor-native-coordination/1", "native_spawn": False}
                body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}], "isError": False}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                credential = root / "token"
                settings = root / "client.json"
                credential.write_text(token)
                settings.write_text(json.dumps({"url": f"http://127.0.0.1:{httpd.server_port}/mcp",
                                                 "token_file": str(credential.absolute())}))
                if os.name == "nt":
                    import importlib.util
                    installer_path = Path(__file__).resolve().parents[1] / "scripts/install_client.py"
                    spec = importlib.util.spec_from_file_location("bridge_installer", installer_path)
                    installer = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(installer)
                    installer.private_permissions(credential)
                    installer.private_permissions(settings)
                else:
                    credential.chmod(0o600)
                    settings.chmod(0o600)
                params = StdioServerParameters(command=sys.executable,
                    args=["-m", "conductor_coordination.client_bridge", "--settings", str(settings)],
                    env=dict(os.environ))
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        initial = await session.initialize()
                        self.assertEqual(initial.server_info.name, "Conductor")
                        listed = await session.list_tools()
                        self.assertEqual(len(listed.tools), 14)
                        self.assertIn("coordination_health", {tool.name for tool in listed.tools})
                        health = await session.call_tool("coordination_health", {})
                        self.assertFalse(health.is_error)
                        content = json.loads(health.content[0].text)
                        self.assertFalse(content["native_spawn"])
                        before = len(observations)
                        invalid = await session.call_tool("coordination_claim", {
                            "workload_id": "validation-only", "manager_id": "test-manager",
                            "client": "desktop", "operation_id": "invalid-lease", "lease_seconds": 3600})
                        self.assertTrue(invalid.is_error)
                        self.assertEqual(len(observations), before)
                self.assertEqual(observations, [("/mcp", "Bearer " + token)])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
