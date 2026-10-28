#!/usr/bin/env bash
# Install the SSH-command ledger from a clean VM checkout. No pip or MCP service.
set -euo pipefail
if (( $# != 0 )); then printf '%s\n' 'Usage: ./install-ssh-vm.sh' >&2; exit 2; fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if (( EUID != 0 )); then exec sudo -- bash "$root/install-ssh-vm.sh"; fi
command -v git >/dev/null || { printf '%s\n' 'Git is required; clone the repository first.' >&2; exit 1; }
repo=(git -c "safe.directory=$root" -C "$root")
"${repo[@]}" rev-parse --verify HEAD >/dev/null
"${repo[@]}" diff --quiet HEAD -- || { printf '%s\n' 'Commit or restore tracked changes before installing.' >&2; exit 1; }
for file in scripts/install_ssh_host.py scripts/install_host.py scripts/ssh_command.py scripts/prepare_vm_config.py src/conductor_coordination/ssh_worker.py pyproject.toml; do
  "${repo[@]}" cat-file -e "HEAD:$file" || { printf 'Missing release file: %s\n' "$file" >&2; exit 1; }
done

if command -v apt-get >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-yaml sudo
else
  python3 -c 'import yaml' || { printf '%s\n' 'Install your OS python3-yaml package first.' >&2; exit 1; }
  command -v visudo >/dev/null || { printf '%s\n' 'Install your OS sudo package first.' >&2; exit 1; }
fi

config=/etc/conductor/conductor.yaml
if [[ ! -d /etc/conductor ]]; then install -d -o root -g root -m 0700 /etc/conductor; fi
if [[ ! -d /var/cache/conductor ]]; then install -d -o root -g root -m 0700 /var/cache/conductor; fi
if [[ ! -e "$config" ]]; then
  first_user="${CONDUCTOR_FIRST_USER:-${SUDO_USER:-admin}}"
  stage="$(mktemp /etc/conductor/.conductor.yaml.XXXXXX)"
  trap 'rm -f -- "$stage"' EXIT
  python3 "$root/scripts/prepare_vm_config.py" --initial-user "$first_user" --output "$stage"
  chmod 0600 "$stage"
  ln -- "$stage" "$config"
  rm -- "$stage"
  trap - EXIT
fi
engine="$(python3 -c 'import yaml;print(yaml.safe_load(open("/etc/conductor/conductor.yaml"))["database"]["engine"])')"
if [[ "$engine" == mysql ]]; then
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-pymysql
  else
    python3 -c 'import pymysql' || { printf '%s\n' 'Install your OS python3-pymysql package first.' >&2; exit 1; }
  fi
fi
cache=/var/cache/conductor
stage="$(mktemp "$cache/.ssh-source.XXXXXX")"
trap 'rm -f -- "$stage"' EXIT
"${repo[@]}" archive --format=tar HEAD pyproject.toml src scripts/ssh_command.py | gzip -n > "$stage"
digest="$(sha256sum "$stage" | cut -d ' ' -f 1)"
bundle="$cache/conductor-ssh-$digest.tar.gz"
if [[ -e "$bundle" ]]; then
  cmp -s -- "$stage" "$bundle" || { printf '%s\n' 'Existing release bundle differs; preserved.' >&2; exit 1; }
else
  chmod 0600 "$stage"
  ln -- "$stage" "$bundle"
fi
rm -- "$stage"
trap - EXIT
python3 "$root/scripts/install_ssh_host.py" --config "$config" --bundle "$bundle" --sha256 "$digest"
printf '%s\n' 'Conductor SSH command is ready. Authorized SSH logins use their existing SSH identity; no client token or Python install is needed.'
