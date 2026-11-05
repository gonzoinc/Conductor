"""Shared, dependency-free local state paths for installers and the bridge."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def local_state_root() -> Path:
    if os.name == "nt":
        return Path.home() / ".conductor"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "conductor"


def validate_shared_windows_path(path: Path) -> None:
    """Reject known virtualized locations; client acceptance still remains required."""
    if os.name != "nt":
        return
    value = path.expanduser().absolute()
    appdata = Path.home() / "AppData"
    if value == appdata or value.is_relative_to(appdata):
        raise ValueError(f"Conductor task data at {value} may resolve differently in packaged Desktop and VS Code. "
                         "Choose a local folder outside AppData, such as ~/.conductor; use explicit repair for existing data.")
    if any(part.is_symlink() or (part.exists() and part.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
           for part in (value, *value.parents)):
        raise ValueError("Shared task data cannot use symbolic links or junctions")
