"""Local stdio transport for an on-device ledger or a hosted ledger."""
from __future__ import annotations

import argparse
import atexit
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time
import urllib.parse

from mcp.server import MCPServer

from conductor_coordination import coordination_proxy


_SSH_TUNNEL = None


def ssh_tunnel(target: str, remote_port: int = 8012) -> str:
    """Keep a private loopback tunnel alive for this stdio bridge's lifetime."""
    global _SSH_TUNNEL
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", target):
        raise ValueError("SSH target must be a login@hostname or login@IPv4 address")
    if isinstance(remote_port, bool) or not isinstance(remote_port, int) or not 1 <= remote_port <= 65535:
        raise ValueError("SSH service port is invalid")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        local_port = available.getsockname()[1]
    command = ["ssh", "-N", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=5",
               "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", target]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = os.environ.copy()
    if os.name == "nt" and not environment.get("PROGRAMDATA"):
        # The MCP stdio launcher intentionally filters its inherited environment.
        # Windows OpenSSH exits before logging if PROGRAMDATA is absent.
        environment["PROGRAMDATA"] = str(Path(environment.get("SYSTEMDRIVE", "C:")) / "ProgramData")
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags, env=environment)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Conductor SSH connection failed; check VM access and its SSH host key")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                _SSH_TUNNEL = process
                atexit.register(process.terminate)
                return f"http://127.0.0.1:{local_port}/mcp"
        except OSError:
            time.sleep(0.1)
    process.terminate()
    raise TimeoutError("Conductor SSH connection did not become ready")


def validate_url(value: str) -> str:
    """Use HTTPS remotely, or HTTP through a local forwarding endpoint."""
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != "/mcp"):
        raise ValueError("Use an HTTP(S) service URL ending in /mcp, without credentials or query parameters")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Service URL has an invalid port") from None
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if not loopback:
            raise ValueError("Remote services require HTTPS; use an SSH tunnel for a plaintext service")
    return value


def ensure_private(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink() or not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Private Conductor path must be a regular file or directory of the expected type")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Client settings and credential must have private file permissions")
    if os.name == "nt":
        import win32api
        import win32con
        import win32security
        handle = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user_sid = win32security.GetTokenInformation(handle, win32security.TokenUser)[0]
        finally:
            handle.Close()
        allowed = {win32security.ConvertSidToStringSid(user_sid), "S-1-5-18", "S-1-5-32-544"}
        descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                       win32security.DACL_SECURITY_INFORMATION |
                                                       win32security.OWNER_SECURITY_INFORMATION)
        owner_sid = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        if owner_sid not in allowed:
            raise ValueError("Client file has an unexpected Windows owner")
        acl = descriptor.GetSecurityDescriptorDacl()
        if acl is None:
            raise ValueError("Client file has an unrestricted Windows ACL")
        for index in range(acl.GetAceCount()):
            ace = acl.GetAce(index)
            ace_sid = win32security.ConvertSidToStringSid(ace[-1])
            if (ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
                    and ace_sid not in allowed | {"S-1-3-4"}):
                raise ValueError("Client file permits access outside the current user or system administrators")


def read_private(path: Path) -> str:
    ensure_private(path)
    return path.read_text(encoding="utf-8")


def local_state_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "Conductor"
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "conductor"


def validate_local_database(path: Path, state_root: Path | None = None) -> Path:
    path = path.expanduser().absolute()
    root = (state_root or local_state_root()).expanduser().absolute()
    if len(root.parts) < 2:
        raise ValueError("Local state directory cannot be a drive or filesystem root")
    if not path.is_relative_to(root):
        raise ValueError("Local ledger must be inside the selected Conductor state directory")
    if any(parent.is_symlink() for parent in (path, *path.parents, root, *root.parents)):
        raise ValueError("Local ledger path cannot contain a symbolic link")
    if path.suffix != ".sqlite3":
        raise ValueError("Local ledger must use a .sqlite3 file")
    if os.name == "nt":
        if str(path).startswith(("\\\\", "//")) or str(root).startswith(("\\\\", "//")):
            raise ValueError("Local ledger must be on this computer, not a network share")
        drive = path.drive
        if not drive or ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") not in {2, 3, 6}:
            raise ValueError("Local ledger must be on a local drive")
    elif sys_platform_linux():
        mounts = Path("/proc/self/mountinfo")
        if mounts.is_file():
            resolved = path.resolve(strict=False)
            longest = (-1, "")
            for line in mounts.read_text(errors="replace").splitlines():
                fields = line.split(" - ", 1)
                if len(fields) != 2:
                    continue
                mount = Path(fields[0].split()[4].replace("\\040", " "))
                fs_type = fields[1].split()[0]
                if (resolved == mount or mount in resolved.parents) and len(str(mount)) > longest[0]:
                    longest = (len(str(mount)), fs_type)
            if longest[1] in {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "sshfs", "9p", "drvfs"}:
                raise ValueError("Local ledger must be on local storage, not a network mount")
    return path


def sys_platform_linux() -> bool:
    return os.sys.platform.startswith("linux")


def configure(settings_path: Path) -> dict:
    settings = json.loads(read_private(settings_path))
    mode = settings.get("mode", "hosted")
    if mode == "local":
        state_root = Path(settings["state_root"]) if "state_root" in settings else local_state_root()
        database = validate_local_database(Path(settings["db_path"]), state_root)
        ensure_private(state_root, directory=True)
        ensure_private(database.parent, directory=True)
        ensure_private(database)
        return {"mode": "local", "db_path": str(database)}
    if mode != "hosted":
        raise ValueError("Unknown Conductor client mode")
    url = validate_url(settings["url"])
    token_path = Path(settings["token_file"])
    if not token_path.is_absolute():
        raise ValueError("Credential path must be absolute")
    token = read_private(token_path).strip()
    if len(token) < 32 or any(char.isspace() for char in token):
        raise ValueError("Client credential is invalid")
    if "ssh_target" in settings:
        url = ssh_tunnel(settings["ssh_target"], settings.get("ssh_remote_port", 8012))
    os.environ["MCP_COORDINATION_URL"] = url
    os.environ["MCP_COORDINATION_TOKEN_FILE"] = str(token_path)
    return {"mode": "hosted"}


def build_server(connection: dict | None = None) -> MCPServer:
    if connection and connection["mode"] == "local":
        from conductor_coordination.coordination_dispatch import dispatch_action
        from conductor_coordination.coordination_errors import CoordinationError, tool_failure
        from conductor_coordination.coordination_store import Store
        store = Store(connection["db_path"])
        def local_dispatch(name: str, arguments: dict) -> dict:
            try:
                return dispatch_action(name, arguments, store, "local")
            except CoordinationError as error:
                raise tool_failure(error.code, str(error)) from None
        coordination_proxy._dispatch = local_dispatch
    else:
        coordination_proxy._dispatch = coordination_proxy._remote
    server = MCPServer("Conductor", version="0.4.0b1", instructions=(
        "First choose workload shape. For one coherent task, work directly, including "
        "investigation, changes, validation and cleanup; no ledger is needed. For multiple "
        "distinct workstreams, manage native workers and record verified results here. "
        "Read the conductor-coordinator skill and its relative workflow reference. "
        "This ledger cannot spawn, stop, wake or authenticate native workers. "
        "It grants no authority to deploy, publish or access unrelated resources."
    ))
    coordination_proxy.register_tools(server)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True, type=Path)
    args = parser.parse_args()
    try:
        connection = configure(args.settings)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(2, "Conductor client could not start: " + str(error) + "\n")
    build_server(connection).run(transport="stdio")


if __name__ == "__main__":
    main()
