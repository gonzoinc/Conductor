#!/usr/bin/env bash
# Noninteractive Conductor host entry point for direct execution or Salt.
set -euo pipefail

if (( EUID != 0 )); then
  printf '%s\n' 'Conductor host setup must run as root (for example through Salt).' >&2
  exit 1
fi

check_only=0
for argument in "$@"; do
  if [[ "$argument" == --check ]]; then check_only=1; fi
done

python_ready() {
  command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import sys,venv,ensurepip,yaml; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1
}

if ! python_ready; then
  if (( check_only )); then exit 1; fi
  if [[ -x /usr/bin/apt-get ]] && [[ -f /etc/os-release ]]; then
    # Supported Debian/Ubuntu hosts can supply the required runtime without prompts.
    . /etc/os-release
    case "${ID:-}" in
      debian|ubuntu)
        export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-yaml
        ;;
    esac
  fi
  if ! python_ready; then
    printf '%s\n' 'Python 3.11+ with venv/ensurepip and python3-yaml is required. Install them and rerun.' >&2
    exit 1
  fi
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
helper="${CONDUCTOR_HOST_INSTALL_HELPER:-$script_dir/scripts/install_host.py}"
if [[ ! -f "$helper" ]]; then
  printf '%s\n' 'The Conductor host installer helper is missing; stage the complete release package.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
exec python3 "$helper" "$@"
