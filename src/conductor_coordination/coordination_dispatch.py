"""Shared backend dispatch for hosted principals and a private local ledger."""
from __future__ import annotations

import json

from conductor_coordination.coordination_errors import CoordinationError
from conductor_coordination.coordination_store import PROTOCOL_VERSION, UPSTREAM_REVISION, Store


def dispatch_action(name: str, arguments: dict, store: Store, principal_id: str) -> dict:
    if name == "coordination_health":
        result = {
            "protocol_version": PROTOCOL_VERSION,
            "role": "durable-task-metadata",
            "persistence": ("mysql-innodb-shared" if store.storage_kind == "mysql" else
                            "sqlite-wal-local-disk"),
            "durability": ("InnoDB transactions; serialized mutations" if store.storage_kind == "mysql" else
                           "SQLite synchronous=FULL; transactional mutations"),
            "native_spawn": False,
            "unattended_execution": False,
            "manager_role_enforcement": "ownership lease and fencing; model role remains client policy",
            "upstream_revision": UPSTREAM_REVISION,
            "limits": {"lease_seconds": {"minimum": 30, "maximum": 900, "default": 300},
                       "page_records": 100, "task_evidence_bytes": 32000,
                       "task_record_bytes": 40000, "request_bytes": 120000,
                       "result_bytes": 50000, "response_bytes": 200000},
            "tools": ["coordination_health", "coordination_claim", "coordination_renew",
                      "coordination_release", "coordination_create_task", "coordination_assign",
                      "coordination_update", "coordination_observe", "coordination_recover",
                      "coordination_complete", "coordination_snapshot", "coordination_task",
                      "coordination_workloads", "coordination_events"],
        }
        if store.storage_kind == "sqlite":
            result["sqlite_durability"] = "synchronous=FULL; transactional mutations"
    elif name == "coordination_claim":
        result = store.claim(**arguments, principal_id=principal_id)
    elif name == "coordination_snapshot":
        result = store.snapshot(**arguments, principal_id=principal_id)
    elif name == "coordination_task":
        result = store.task(**arguments, principal_id=principal_id)
    elif name == "coordination_workloads":
        result = store.workloads(**arguments, principal_id=principal_id)
    elif name == "coordination_events":
        result = store.events(**arguments, principal_id=principal_id)
    else:
        actions = {"coordination_renew": "renew", "coordination_release": "release",
                   "coordination_create_task": "create", "coordination_assign": "assign",
                   "coordination_update": "update", "coordination_observe": "observe",
                   "coordination_recover": "recover", "coordination_complete": "complete"}
        if name not in actions:
            raise CoordinationError("Unknown coordination operation", "unknown_operation")
        args = dict(arguments)
        identity = {key: args.pop(key) for key in ("workload_id", "manager_id", "epoch", "operation_id")}
        result = store.mutate(**identity, action=actions[name], payload=args, principal_id=principal_id)
    if len(json.dumps(result).encode()) > 50_000:
        raise CoordinationError("Coordination result exceeds 50 KB; narrow the page limit. Check operation_id before retrying writes.", "result_too_large")
    return result
