#!/usr/bin/env bash
# Conductor CLI: use the current user's SSH config, agent and jump hosts.
set -euo pipefail
if (( $# < 1 || $# > 2 )); then
  printf '%s\n' 'Usage: conductor.sh coordination_action [JSON_ARGUMENT_OBJECT]' >&2
  exit 2
fi
action="$1"
arguments="${2:-{}}"
[[ "$action" =~ ^coordination_[a-z_]+$ ]] || { printf '%s\n' 'Invalid action name' >&2; exit 2; }
host_file="${CONDUCTOR_HOST_FILE:-$HOME/.codex/conductor-client/host.txt}"
[[ -f "$host_file" ]] || { printf '%s\n' 'Conductor host is not configured' >&2; exit 2; }
host="$(tr -d '\r\n' < "$host_file")"
[[ "$host" =~ ^([A-Za-z_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}$ ]] || { printf '%s\n' 'Invalid Conductor SSH host' >&2; exit 2; }
request="$(printf '{"action":"%s","arguments":%s}' "$action" "$arguments")"
(( ${#request} <= 120000 )) || { printf '%s\n' 'Conductor request exceeds 120 KB' >&2; exit 2; }
printf '%s' "$request" | ssh -T -o ConnectTimeout=10 "$host" sudo -n /usr/local/lib/conductor-command
