"""Regression coverage at both real MCP tool boundaries."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

from conductor_coordination import coordination_proxy as api
from conductor_coordination import coordination_server as server
from conductor_coordination.coordination_store import Store


class ErrorContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.token = "fake-error-contract-token-" + "x" * 40
        self.token_file = self.path / "token"
        self.token_file.write_text(self.token)
        self.token_file.chmod(0o600)
        environment = patch.dict(os.environ, {"CONDUCTOR_DB": str(self.path / "tasks.sqlite3")})
        environment.start()
        self.addCleanup(environment.stop)
        # Other tests may have imported the server before this test module.
        dispatch = patch.object(api, "_dispatch", server.dispatch)
        dispatch.start()
        self.addCleanup(dispatch.stop)

    def call(self, name, arguments):
        with TestClient(server.create_http_app(self.token_file)) as client:
            return client.post("/mcp", headers={"Authorization": "Bearer " + self.token, "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})

    def claim(self, workload="work", manager="manager", seconds=300):
        return self.call("coordination_claim", {"workload_id": workload, "manager_id": manager, "client": "desktop", "operation_id": "claim", "lease_seconds": seconds})

    def test_tool_schema_advertises_client_enum_and_lease_bounds(self):
        with TestClient(server.create_http_app(self.token_file)) as client:
            reply = client.post("/mcp", headers={"Authorization": "Bearer " + self.token, "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).json()
        tools = {tool["name"]: tool for tool in reply["result"]["tools"]}
        for name in ("coordination_claim", "coordination_renew"):
            lease = tools[name]["inputSchema"]["properties"]["lease_seconds"]
            self.assertEqual((lease["minimum"], lease["maximum"], lease["default"]), (30, 900, 300))
        self.assertEqual(tools["coordination_claim"]["inputSchema"]["properties"]["client"]["enum"], ["desktop", "vscode", "cli"])

    def test_original_3600_second_request_is_actionable_and_creates_nothing(self):
        reply = self.claim(seconds=3600).json()["result"]
        self.assertTrue(reply["isError"])
        self.assertIn("900", reply["content"][0]["text"])
        self.assertIn("lease_seconds", reply["content"][0]["text"])
        missing = self.call("coordination_snapshot", {"workload_id": "work"}).json()["result"]
        self.assertTrue(missing["isError"])
        self.assertIn('"code": "workload_not_found"', missing["content"][0]["text"])
        self.assertIn("rejected claim does not create", missing["content"][0]["text"])

    def test_accepted_lease_boundaries_and_default_work(self):
        for seconds in (30, 300, 900):
            result = self.claim("work-" + str(seconds), seconds=seconds).json()["result"]
            self.assertFalse(result.get("isError", False))
        result = self.call("coordination_claim", {"workload_id": "default", "manager_id": "manager", "client": "desktop", "operation_id": "default"}).json()["result"]
        self.assertFalse(result.get("isError", False))

    def test_domain_validation_is_expected_but_unknown_exceptions_stay_masked(self):
        context = server.REQUEST_PRINCIPAL.set('owner')
        try:
            with self.assertRaises(ToolError) as error:
                server.dispatch("coordination_claim", {"workload_id": "work", "manager_id": "manager", "client": "desktop", "operation_id": "claim", "lease_seconds": 3600})
        finally:
            server.REQUEST_PRINCIPAL.reset(context)
        self.assertIn('"code": "invalid_lease"', str(error.exception))
        self.assertIn("300-second default", str(error.exception))
        with patch.object(server, "_dispatch_action", side_effect=RuntimeError("private-crash-detail")):
            result = self.call("coordination_health", {}).json()["result"]
        self.assertTrue(result["isError"])
        self.assertNotIn("private-crash-detail", json.dumps(result))

    def test_conflicting_manager_gets_actionable_lease_conflict(self):
        self.assertFalse(self.claim().json()["result"].get("isError", False))
        result = self.claim(manager="other-manager").json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn('"code": "lease_conflict"', result["content"][0]["text"])

    def test_safe_error_survives_backend_and_gateway_sdk_layers(self):
        backend = self.call("coordination_snapshot", {"workload_id": "missing"}).content
        gateway = MCPServer("Gateway error contract")
        api.register_tools(gateway)
        class Opener:
            def open(self, *args, **kwargs):
                return io.BytesIO(backend)
        with patch.dict(os.environ, {"MCP_COORDINATION_URL": "http://fixed-service:8012/mcp", "MCP_COORDINATION_TOKEN_FILE": str(self.token_file)}), patch.object(api, "_dispatch", api._remote), patch.object(api.urllib.request, "build_opener", return_value=Opener()):
            with TestClient(gateway.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True, host="127.0.0.1"), base_url="http://127.0.0.1:8000") as client:
                response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "coordination_snapshot", "arguments": {"workload_id": "missing"}}})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn('"code": "workload_not_found"', result["content"][0]["text"])
        self.assertNotIn(self.token, json.dumps(result))

    def test_gateway_redacts_credential_in_anticipated_upstream_error(self):
        body = json.dumps({"result": {"isError": True, "content": [{"type": "text", "text": "Rejected " + self.token}]}}).encode()
        class Opener:
            def open(self, *args, **kwargs):
                return io.BytesIO(body)
        with patch.dict(os.environ, {"MCP_COORDINATION_URL": "http://fixed-service:8012/mcp", "MCP_COORDINATION_TOKEN_FILE": str(self.token_file)}), patch.object(api.urllib.request, "build_opener", return_value=Opener()):
            with self.assertRaises(ToolError) as caught:
                api._remote("coordination_health", {})
        self.assertNotIn(self.token, str(caught.exception))
        self.assertIn("[redacted]", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
