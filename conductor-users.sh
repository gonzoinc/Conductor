#!/usr/bin/env bash
# Administer separate users and revocable keys on a VM installed with install-vm.sh.
set -euo pipefail

usage() {
  printf '%s\n' 'Usage: ./conductor-users.sh add USER | issue USER LABEL | export USER [LABEL] | revoke KEY_ID' >&2
  exit 2
}
(( $# >= 2 )) || usage
if (( EUID != 0 )); then
  exec sudo -- bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/conductor-users.sh" "$@"
fi

python=/opt/conductor/current/.venv/bin/python
db=/var/lib/conductor/tasks.sqlite3
config=/etc/conductor/conductor.yaml
keys=/var/lib/conductor/private/users
[[ -x "$python" && ( -f "$config" || -f "$db" ) && -d "$keys" && ! -L "$keys" ]] || {
  printf '%s\n' 'The VM Conductor service is not installed or its private state is unavailable.' >&2
  exit 1
}
service_user="$(stat -c %U /var/lib/conductor)"
[[ "$service_user" != root ]] || { printf '%s\n' 'Invalid Conductor service account.' >&2; exit 1; }
if [[ -f "$config" ]]; then
  account=(runuser -u "$service_user" -- env "CONDUCTOR_CONFIG=$config" "$python" -m conductor_coordination.accounts)
else
  account=(runuser -u "$service_user" -- "$python" -m conductor_coordination.accounts --db "$db")
fi

valid_user() { [[ "$1" =~ ^[a-z][a-z0-9_-]{0,62}$ && "$1" != owner ]]; }
valid_label() { [[ "$1" =~ ^[a-z][a-z0-9_-]{0,31}$ ]]; }
key_path() {
  if [[ "$2" == initial ]]; then printf '%s/%s.token' "$keys" "$1";
  else printf '%s/%s-%s.token' "$keys" "$1" "$2"; fi
}

case "$1" in
  add)
    (( $# == 2 )) || usage
    valid_user "$2" || usage
    "${account[@]}" create-user "$2"
    "${account[@]}" issue-key "$2" --label initial --output "$(key_path "$2" initial)"
    ;;
  issue)
    (( $# == 3 )) || usage
    valid_user "$2" && valid_label "$3" && [[ "$3" != initial ]] || usage
    "${account[@]}" issue-key "$2" --label "$3" --output "$(key_path "$2" "$3")"
    ;;
  export)
    (( $# == 2 || $# == 3 )) || usage
    name="$2"; label="${3:-initial}"
    valid_user "$name" && valid_label "$label" || usage
    login="${SUDO_USER:-}"
    [[ -n "$login" && "$login" != root ]] || {
      printf '%s\n' 'Run through sudo from the Linux account that should receive the private key file.' >&2
      exit 1
    }
    source="$(key_path "$name" "$label")"
    [[ -f "$source" && ! -L "$source" ]] || { printf '%s\n' 'That user key does not exist.' >&2; exit 1; }
    "${account[@]}" issue-key "$name" --label "$label" --output "$source" >/dev/null
    home="$(getent passwd "$login" | cut -d: -f6)"
    [[ -n "$home" && -d "$home" && ! -L "$home" ]] || { printf '%s\n' 'The invoking account has no safe home directory.' >&2; exit 1; }
    destination_dir="$home/.conductor-keys"
    [[ ! -L "$destination_dir" ]] || { printf '%s\n' 'The private key destination is linked.' >&2; exit 1; }
    install -d -o "$login" -g "$(id -gn "$login")" -m 0700 "$destination_dir"
    destination="$destination_dir/$name-$label.token"
    [[ ! -e "$destination" && ! -L "$destination" ]] || { printf '%s\n' 'The private key export already exists; it was preserved.' >&2; exit 1; }
    staging="$(mktemp "$destination_dir/.conductor-key.XXXXXX")"
    trap 'rm -f -- "$staging"' EXIT
    cat -- "$source" > "$staging"
    chown "$login:$(id -gn "$login")" "$staging"
    chmod 0600 "$staging"
    ln -- "$staging" "$destination"
    rm -- "$staging"
    trap - EXIT
    printf 'Private key exported to %s (contents not printed).\n' "$destination"
    ;;
  revoke)
    (( $# == 2 )) || usage
    [[ "$2" =~ ^[a-f0-9]{32}$ ]] || usage
    "${account[@]}" revoke-key "$2"
    ;;
  *) usage ;;
esac
