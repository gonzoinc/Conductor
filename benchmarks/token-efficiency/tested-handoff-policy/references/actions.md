# Conductor command arguments

Pass an `-Arguments` PowerShell hashtable to `conductor.ps1 -Action NAME`. On Bash, pass a JSON object as the second argument to `conductor.sh NAME`. Output is one JSON result. A nonzero exit means no success was confirmed; for uncertain writes, inspect using the same operation ID before retrying.

Every protected write uses `workload_id`, `manager_id`, integer `epoch`, and unique `operation_id` unless noted. Use the exact returned epoch. The VM derives your account from the current SSH login.

For command transport, explicitly include `depends_on`, `covers` and `required` on task creation (use `[]`, `[]`, `true` when appropriate), and `summary` and `evidence` on every update (use `""` and `[]` when there is no report yet). These fields must be sent even though MCP clients may supply defaults. Build JSON with the shell's JSON serializer or pass a request file; avoid embedding quoted commands in hand-escaped JSON strings.

| Action | Additional arguments |
| --- | --- |
| `coordination_health` | none |
| `coordination_claim` | `workload_id`, `manager_id`, `client` (`desktop`, `vscode`, `cli`), `operation_id`; optional `lease_seconds` (30–900, default 300). No epoch. |
| `coordination_renew` | protected write fields; optional `lease_seconds` |
| `coordination_release` | protected write fields |
| `coordination_create_task` | protected write fields; `task_id`, `title`, `kind` (`scout`, `implementation`, `review`, `test`, `integration`), `project_path`, `workspace_path`, `captain_intent`, `spec`, `definition_of_done` array; optional `depends_on` and `covers` arrays, `required` boolean |
| `coordination_assign` | protected write fields; `task_id`, exact `native_agent_id` returned by native spawn |
| `coordination_update` | protected write fields; `task_id`, `native_agent_id`, `state`; optional `summary`, `evidence` array of `{kind,reference,summary}` |
| `coordination_observe` | protected write fields; `task_id`, `native_agent_id`, `observed_state`; optional `detail` |
| `coordination_recover` | protected write fields; `task_id`, `prior_native_agent_id`, `confirmed_stopped`, `reason` |
| `coordination_complete` | protected write fields; `summary` |
| `coordination_snapshot` | `workload_id`; optional `limit`, `after_task_id` |
| `coordination_task` | `workload_id`, `task_id` |
| `coordination_workloads` | optional `limit` |
| `coordination_events` | `workload_id`; optional `after_seq`, `limit` |

Example PowerShell claim:

```powershell
& "$HOME\.codex\conductor-client\conductor.ps1" -Action coordination_claim -Arguments @{workload_id='my-work';manager_id='my-session';client='desktop';operation_id='claim-1'}
```
