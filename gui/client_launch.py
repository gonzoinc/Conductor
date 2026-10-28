"""Launch test-only Codex clients from the Windows setup app."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


def _vscode_paths() -> tuple[list[str], str]:
    code = shutil.which("code")
    candidates = []
    if code:
        candidates.append(Path(code).parent.parent / "Code.exe")
    candidates.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/Code.exe")
    for executable in candidates:
        locations = []
        wrapper = executable.parent / "bin/code.cmd"
        if wrapper.is_file():
            for relative in re.findall(r'"%~dp0([^"]*cli\.js)"', wrapper.read_text()):
                path = (wrapper.parent / relative).resolve()
                if path.is_relative_to(executable.parent.resolve()):
                    locations.append(path)
        locations.append(executable.parent / "resources/app/out/cli.js")
        for cli in locations:
            if executable.is_file() and cli.is_file():
                return [str(executable), str(cli)], str(executable)
    raise ValueError("VS Code was not found. Install it, then use Open isolated VS Code again.")


def launch_isolated(profile: str, home: Path, workspace: Path) -> None:
    if os.name != "nt":
        raise ValueError("This setup app launches test clients only on Windows.")
    environment = dict(os.environ, CODEX_HOME=str(home))
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    data = home.parent / "application-data"
    if profile == "vscode":
        data.mkdir(mode=0o700, exist_ok=True)
        cli, executable = _vscode_paths()
        extensions = home.parent / "extensions"
        args = ["--user-data-dir", str(data), "--extensions-dir", str(extensions)]
        install_environment = dict(environment, ELECTRON_RUN_AS_NODE="1")
        subprocess.run(cli + args + ["--install-extension", "openai.chatgpt"],
                       env=install_environment, check=True, capture_output=True, text=True)
        subprocess.Popen([executable, *args, "--new-window", str(workspace)], env=environment)
        return
    if profile != "desktop":
        raise ValueError("Unknown Codex client profile.")
    raise ValueError("A separate Codex desktop window cannot be opened reliably while "
                     "Codex is already running. To use this app, set up the usual Codex "
                     "profile, restart Codex, then start a new chat.")
