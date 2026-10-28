#!/usr/bin/env bash
# Local, current-user setup for Codex desktop and its VS Code extension.
set -euo pipefail

if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
  cat <<'HELP'
Usage: bash install-macos.sh

Install the local Conductor ledger and skill into the current user's normal
Codex profile, shared by Codex desktop and the Codex VS Code extension.
Requires macOS, Python 3.11+ (with venv/pip), and internet access for packages.
No sudo, VM, Docker, SSH, service key, or GUI is needed.

Keep this checkout in place: the installed connection uses its private venv.
CODEX_HOME selects a custom Codex profile; both clients must use that profile.
CONDUCTOR_PYTHON selects an alternative Python executable (path or command).
HELP
  exit 0
fi
if [[ $# -ne 0 ]]; then
  printf '%s\n' 'Usage: bash install-macos.sh (or --help)' >&2
  exit 2
fi
if [[ "$(uname -s)" != Darwin ]]; then
  printf '%s\n' 'This installer requires macOS. See README.md for other platforms.' >&2
  exit 1
fi
if [[ "$EUID" -eq 0 ]]; then
  printf '%s\n' 'Run this script as your normal user, without sudo.' >&2
  exit 1
fi

conductor_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
conductor_python="${CONDUCTOR_PYTHON:-python3}"
if ! command -v "$conductor_python" >/dev/null 2>&1 ||
   ! "$conductor_python" -B -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  printf '%s\n' 'Python 3.11+ with venv is required. Install it, then rerun; use CONDUCTOR_PYTHON to select its executable. No Codex settings were changed.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
exec "$conductor_python" -B "$conductor_root/scripts/install_macos.py"
