#!/usr/bin/env bash
# One setup entry point; Windows uses Git Bash and native Windows Python.
set -euo pipefail

fm_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
fm_args=("$@")
case "$(uname -s)" in
  MINGW*|MSYS*)
    if command -v py.exe >/dev/null 2>&1; then
      fm_python=(py.exe -3)
    elif command -v python.exe >/dev/null 2>&1; then
      fm_python=(python.exe)
    else
      printf '%s\n' 'Guided setup needs native Windows Python 3.11+. Install it from https://www.python.org/downloads/windows/ and rerun this command. No Codex settings were changed.' >&2
      exit 1
    fi
    for ((fm_i=0; fm_i<${#fm_args[@]}; fm_i++)); do
      case "${fm_args[fm_i]}" in
        --token-file|--test-root)
          if ((fm_i+1 < ${#fm_args[@]})); then
            fm_i=$((fm_i+1))
            fm_args[fm_i]="$(cygpath -aw -- "${fm_args[fm_i]}")"
          fi
          ;;
        --token-file=*|--test-root=*)
          fm_key="${fm_args[fm_i]%%=*}"
          fm_value="${fm_args[fm_i]#*=}"
          fm_args[fm_i]="$fm_key=$(cygpath -aw -- "$fm_value")"
          ;;
      esac
    done
    fm_bootstrap="$(cygpath -aw -- "$fm_root/scripts/bootstrap.py")"
    fm_root="$(cygpath -aw -- "$fm_root")"
    export MSYS2_ARG_CONV_EXCL='*'
    ;;
  *)
    if command -v python3 >/dev/null 2>&1; then
      fm_python=(python3)
    else
      printf '%s\n' 'Guided setup needs Python 3.11+. Install it using your OS package manager or https://www.python.org/downloads/, then rerun this command. No Codex settings were changed.' >&2
      exit 1
    fi
    fm_bootstrap="$fm_root/scripts/bootstrap.py"
    ;;
esac
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
if ! "${fm_python[@]}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  printf '%s\n' 'Guided setup needs Python 3.11 or newer. Update Python and rerun the same command. No Codex settings were changed.' >&2
  exit 1
fi
exec "${fm_python[@]}" "$fm_bootstrap" "$fm_root" "${fm_args[@]}"
