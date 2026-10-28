"""Expected coordination failures, kept separate from unexpected crashes."""
from __future__ import annotations

import json



class CoordinationError(ValueError):
    """An anticipated, safe-to-report validation or lifecycle rejection."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def tool_failure(code: str, message: str):
    """Use MCP's expected-error type so it doesn't hide actionable messages."""
    from mcp.server.mcpserver.exceptions import ToolError
    return ToolError(json.dumps({"error": {"code": code, "message": message}}, sort_keys=True))
