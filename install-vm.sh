#!/usr/bin/env bash
# Install the multi-user Conductor service from a clean Git checkout on this VM.
set -euo pipefail

if (( $# != 0 )); then
  printf '%s\n' 'Usage: ./install-vm.sh (run from a clean Conductor Git checkout)' >&2
  exit 2
fi

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if (( EUID != 0 )); then
  if ! command -v sudo >/dev/null 2>&1; then
    printf '%s\n' 'sudo is required to install the Conductor system service.' >&2
    exit 1
  fi
  exec sudo -- bash "$source_dir/install-vm.sh"
fi

command -v git >/dev/null 2>&1 || { printf '%s\n' 'Install Git and clone this repository first.' >&2; exit 1; }
command -v gzip >/dev/null 2>&1 || { printf '%s\n' 'gzip is required to package this checkout.' >&2; exit 1; }
if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3
  else
    printf '%s\n' 'Python 3 is required to create the VM configuration.' >&2
    exit 1
  fi
fi
git_repo=(git -c "safe.directory=$source_dir" -C "$source_dir")
"${git_repo[@]}" rev-parse --verify HEAD >/dev/null
if ! "${git_repo[@]}" diff --quiet HEAD --; then
  printf '%s\n' 'Tracked files have local changes. Commit or restore them before installing a pinned release.' >&2
  exit 1
fi
for required in install-host.sh conductor-users.sh scripts/install_host.py scripts/prepare_vm_config.py scripts/enroll_ssh_user.py pyproject.toml UPSTREAM_LICENSE.txt upstream-lock.json; do
  "${git_repo[@]}" cat-file -e "HEAD:$required" || { printf 'Missing release file: %s\n' "$required" >&2; exit 1; }
done

first_user="${CONDUCTOR_FIRST_USER:-${SUDO_USER:-admin}}"
if [[ "$first_user" == root ]]; then first_user=admin; fi
if [[ ! "$first_user" =~ ^[a-z][a-z0-9_-]{0,62}$ || "$first_user" == owner ]]; then
  printf '%s\n' 'The initial Conductor username must be lowercase letters, digits, _ or -, starting with a letter; owner is reserved.' >&2
  exit 1
fi

cache=/var/cache/conductor
config_dir=/etc/conductor
config="$config_dir/conductor.yaml"
config_stage=
bundle_stage=
cleanup() {
  if [[ -n "$config_stage" ]]; then rm -f -- "$config_stage"; fi
  if [[ -n "$bundle_stage" ]]; then rm -f -- "$bundle_stage"; fi
}
trap cleanup EXIT
for path in "$cache" "$config_dir" "$config"; do
  if [[ -L "$path" ]]; then printf 'Refusing linked path: %s\n' "$path" >&2; exit 1; fi
done
install -d -o root -g root -m 0700 "$cache"
if [[ ! -d "$config_dir" ]]; then
  install -d -o root -g root -m 0700 "$config_dir"
fi
if [[ ! -e "$config" ]]; then
  umask 077
  config_stage="$(mktemp "$config_dir/.conductor.yaml.XXXXXX")"
  python3 "$source_dir/scripts/prepare_vm_config.py" --initial-user "$first_user" --output "$config_stage"
  chmod 0600 "$config_stage"
  ln -- "$config_stage" "$config"
  rm -- "$config_stage"
  config_stage=
else
  if [[ ! -f "$config" ]]; then printf '%s\n' 'Existing install config is not a regular file.' >&2; exit 1; fi
fi

bundle_stage="$(mktemp "$cache/.conductor-source.XXXXXX")"
"${git_repo[@]}" archive --format=tar HEAD README.md UPSTREAM_LICENSE.txt upstream-lock.json pyproject.toml install-host.sh conductor-users.sh scripts/install_host.py scripts/enroll_ssh_user.py src | gzip -n > "$bundle_stage"
digest="$(sha256sum "$bundle_stage" | cut -d ' ' -f 1)"
bundle="$cache/conductor-source-$digest.tar.gz"
if [[ -e "$bundle" ]]; then
  if [[ ! -f "$bundle" || ! -O "$bundle" ]] || ! cmp -s -- "$bundle_stage" "$bundle"; then
    printf '%s\n' 'Existing release bundle differs from this checkout; it was preserved.' >&2
    exit 1
  fi
else
  chmod 0600 "$bundle_stage"
  ln -- "$bundle_stage" "$bundle"
  rm -- "$bundle_stage"
  bundle_stage=
fi

printf '%s\n' 'Installing and checking the Conductor service. This may take a few minutes.'
bash "$source_dir/install-host.sh" --config "$config" --bundle "$bundle" --sha256 "$digest"

printf '\n%s\n' 'Conductor is running as a multi-user service.'
printf 'Service: conductor.service (settings in %s)\n' "$config"
printf '%s\n' 'Configured accounts and database: /etc/conductor/conductor.yaml'
printf 'Private account keys: /var/lib/conductor/private/users/\n'
printf '%s\n' 'Authorized SSH logins receive a private key on first client connection. See docs/accounts.md to manage access or revoke keys.'
