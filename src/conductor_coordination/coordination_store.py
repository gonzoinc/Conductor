"""Durable, fenced task metadata for native Codex workers.

Firstmate-derived transition policy: copyright (c) 2026 Kun Chen, MIT.
See upstream-lock.json and UPSTREAM_LICENSE.txt for provenance.
This store never executes code, reads project files, or controls native agents.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import ntpath
import posixpath
import re
import sqlite3
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from conductor_coordination.coordination_errors import CoordinationError

PROTOCOL_VERSION = "conductor-native-coordination/1"
UPSTREAM_REVISION = "6c87d7bb7d986dae2869cbbf090875ccdda4ea1a"
MAX_TASK_BYTES = 40_000
KINDS = {"scout", "implementation", "review", "test", "integration"}
TERMINAL = {"completed", "cancelled"}
STATES = {"queued", "assigned", "running", "blocked", "needs_decision", "validating", "completed", "failed", "cancelled"}
EDGES = {
    "queued": {"cancelled"},
    "assigned": {"running", "blocked", "needs_decision", "failed", "cancelled"},
    "running": {"blocked", "needs_decision", "validating", "completed", "failed", "cancelled"},
    "blocked": {"running", "needs_decision", "failed", "cancelled"},
    "needs_decision": {"running", "blocked", "failed", "cancelled"},
    "validating": {"running", "blocked", "needs_decision", "completed", "failed", "cancelled"},
    "failed": set(), "completed": set(), "cancelled": set(),
}


def transition_policy(status: str) -> str:
    """Direct Python adaptation of the upstream fm_transition_policy mapping."""
    return {"blocked": "actionable", "working": "absorb", "idle": "defer", "done": "defer", "gone": "rescan"}.get(status, "fallback")


def _text(value, name, limit=2000, allow_empty=False):
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise CoordinationError(f"{name} must be {'0' if allow_empty else '1'}..{limit} characters")
    if "\x00" in value:
        raise CoordinationError(f"{name} contains a null character")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise CoordinationError(f"{name} must be a short identifier (letters/digits/_.:-)")
    return value


def _native_id(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_/][A-Za-z0-9_./:-]{0,255}", value):
        raise CoordinationError(f"{name} must be a native agent/session identifier")
    return value


def _items(values, name):
    if not isinstance(values, list) or len(values) > 50:
        raise CoordinationError(f"{name} must be a list with at most 50 entries")
    result = [_identifier(value, name) for value in values]
    if len(set(result)) != len(result):
        raise CoordinationError(f"{name} contains duplicates")
    return result


def _workspace_key(value):
    if value.startswith(("\\\\", "//")) or re.match(r"^[A-Za-z]:", value):
        return ntpath.normcase(ntpath.normpath(value))
    return posixpath.normpath(value)


def _workspaces_overlap(left, right):
    left_key, right_key = _workspace_key(left), _workspace_key(right)
    windows_left = left.startswith(("\\\\", "//")) or bool(re.match(r"^[A-Za-z]:", left))
    windows_right = right.startswith(("\\\\", "//")) or bool(re.match(r"^[A-Za-z]:", right))
    if windows_left != windows_right:
        return False
    module = ntpath if windows_left else posixpath
    try:
        common = module.commonpath([left_key, right_key])
    except ValueError:
        return False
    return common in {left_key, right_key}


def _bounded_task(task):
    if len(json.dumps(task).encode()) > MAX_TASK_BYTES:
        raise CoordinationError("Task metadata exceeds 40 KB; shorten brief/evidence and use references")


class Store:
    storage_kind = "sqlite"

    def __init__(self, database, clock=time.time):
        self.database = Path(database)
        self.clock = clock
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS workloads (
                id TEXT PRIMARY KEY, manager TEXT NOT NULL, client TEXT NOT NULL,
                epoch INTEGER NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                workload TEXT NOT NULL, id TEXT NOT NULL, document TEXT NOT NULL,
                PRIMARY KEY(workload,id), FOREIGN KEY(workload) REFERENCES workloads(id));
            CREATE TABLE IF NOT EXISTS operations (
                workload TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(workload,id));
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, workload TEXT NOT NULL,
                at REAL NOT NULL, kind TEXT NOT NULL, task TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS user_keys (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                label TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                created REAL NOT NULL, revoked REAL,
                FOREIGN KEY(user_id) REFERENCES users(id));
            ''')

    @staticmethod
    def _key(workload_id, principal_id):
        _identifier(workload_id, "workload_id")
        _identifier(principal_id, "principal_id")
        return workload_id if principal_id == "owner" else principal_id + "\x1f" + workload_id

    @staticmethod
    def _visible(result, principal_id):
        if principal_id == "owner":
            return result
        prefix = principal_id + "\x1f"
        def clean(value):
            if isinstance(value, list):
                return [clean(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = dict(value)
            for field in ("workload_id", "workload"):
                if isinstance(result.get(field), str) and result[field].startswith(prefix):
                    result[field] = result[field][len(prefix):]
            if ("epoch" in result and "manager" in result and
                    isinstance(result.get("id"), str) and result["id"].startswith(prefix)):
                result["id"] = result["id"][len(prefix):]
            for field in ("workload", "workloads", "tasks", "events"):
                if isinstance(result.get(field), (list, dict)):
                    result[field] = clean(result[field])
            return result
        return clean(result)

    def ensure_owner_key(self, token):
        """Bind a pre-existing or freshly initialized service token to the owner account."""
        if len(token) < 32 or any(char.isspace() for char in token):
            raise ValueError("Invalid owner credential")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT OR IGNORE INTO users(id,name,created) VALUES('owner','owner',?)", (self.clock(),))
                row = db.execute("SELECT id,user_id FROM user_keys WHERE token_hash=?", (digest,)).fetchone()
                if row is None:
                    # An existing owner token may be added during a guarded upgrade.
                    db.execute("INSERT INTO user_keys VALUES(?,?,?,?,?,NULL)",
                               (secrets.token_hex(16), "owner", "initial owner key", digest, self.clock()))
                elif row["user_id"] != "owner":
                    raise ValueError("Owner credential already belongs to another user")
                db.commit()
            except Exception:
                db.rollback()
                raise

    def create_user(self, name):
        _identifier(name, "username")
        if name == "owner":
            raise ValueError("owner is reserved for service bootstrap")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)", (secrets.token_hex(16), name, self.clock()))
                user_id = db.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()["id"]
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"id": user_id, "name": name}

    def issue_key(self, username, label):
        _identifier(username, "username")
        if username == "owner":
            raise ValueError("owner keys require explicit service bootstrap")
        label = _text(label, "key label", 200)
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        key_id = secrets.token_hex(16)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                user = db.execute("SELECT id FROM users WHERE name=?", (username,)).fetchone()
                if user is None:
                    raise CoordinationError("Unknown user")
                db.execute("INSERT INTO user_keys VALUES(?,?,?,?,?,NULL)",
                           (key_id, user["id"], label, digest, self.clock()))
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"key_id": key_id, "token": token}

    def key_info(self, token):
        if not isinstance(token, str) or len(token) < 32:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            row = db.execute("SELECT user_keys.id,user_keys.user_id,users.name,user_keys.label,user_keys.revoked,user_keys.token_hash "
                             "FROM user_keys JOIN users ON users.id=user_keys.user_id "
                             "WHERE token_hash=?", (digest,)).fetchone()
        if not row or not hmac.compare_digest(digest, row["token_hash"]):
            return None
        result = dict(row)
        result.pop("token_hash")
        return result

    def revoke_key(self, key_id):
        _identifier(key_id, "key_id")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT revoked FROM user_keys WHERE id=?", (key_id,)).fetchone()
                if row is None:
                    raise CoordinationError("Unknown key")
                changed = db.execute("UPDATE user_keys SET revoked=? WHERE id=? AND revoked IS NULL",
                                     (self.clock(), key_id)).rowcount
                db.commit()
            except Exception:
                db.rollback()
                raise
        return bool(changed)

    def principal_for_token(self, token):
        if not isinstance(token, str) or len(token) < 32:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            row = db.execute("SELECT user_id,token_hash FROM user_keys WHERE token_hash=? AND revoked IS NULL",
                             (digest,)).fetchone()
        return row["user_id"] if row and hmac.compare_digest(digest, row["token_hash"]) else None

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def _event(self, db, workload, kind, task="", detail=None):
        db.execute("INSERT INTO events(workload,at,kind,task,detail) VALUES(?,?,?,?,?)", (workload, self.clock(), kind, task, json.dumps(detail or {}, sort_keys=True)))

    def _workload(self, db, workload):
        row = db.execute("SELECT * FROM workloads WHERE id=?", (workload,)).fetchone()
        if row is None:
            raise CoordinationError("Unknown workload; a rejected claim does not create one. Correct the claim arguments before requesting its snapshot", "workload_not_found")
        return dict(row)

    def _fence(self, db, workload, manager, epoch):
        row = self._workload(db, workload)
        if row["manager"] != manager or row["epoch"] != epoch or row["expires"] <= self.clock():
            raise CoordinationError("Manager ownership is absent, expired, or fenced; acquire a new lease", "lease_expired_or_fenced")
        if row["state"] != "open":
            raise CoordinationError("Workload is already completed")
        return row

    def _task(self, db, workload, task):
        row = db.execute("SELECT document FROM tasks WHERE workload=? AND id=?", (workload, task)).fetchone()
        if row is None:
            raise CoordinationError("Unknown task", "task_not_found")
        return json.loads(row[0])

    def _save(self, db, task):
        task["updated"] = self.clock()
        _bounded_task(task)
        db.execute("UPDATE tasks SET document=? WHERE workload=? AND id=?", (json.dumps(task, sort_keys=True), task["workload_id"], task["task_id"]))

    def _dedupe(self, db, workload, op, action, payload, function):
        _identifier(op, "operation_id")
        digest = hashlib.sha256(json.dumps([action, payload], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = db.execute("SELECT digest,result FROM operations WHERE workload=? AND id=?", (workload, op)).fetchone()
        if row:
            if row[0] != digest:
                raise CoordinationError("operation_id was already used with a different request")
            return json.loads(row[1])
        result = function()
        db.execute("INSERT INTO operations VALUES(?,?,?,?)", (workload, op, digest, json.dumps(result, sort_keys=True)))
        return result

    def claim(self, workload_id, manager_id, client, operation_id, lease_seconds=300, *, principal_id="owner"):
        workload_id = self._key(workload_id, principal_id)
        _native_id(manager_id, "manager_id")
        if client not in {"desktop", "vscode", "cli", "test"}:
            raise CoordinationError("client must be desktop, vscode, or cli", "invalid_client")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 30 <= lease_seconds <= 900:
            raise CoordinationError("lease_seconds must be an integer from 30 to 900; omit it to use the 300-second default", "invalid_lease")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = db.execute("SELECT * FROM workloads WHERE id=?", (workload_id,)).fetchone()
                if old and old["state"] != "open":
                    raise CoordinationError("Workload is already completed")
                if old and old["expires"] > self.clock() and old["manager"] != manager_id:
                    raise CoordinationError("Another manager owns the active lease", "lease_conflict")
                def apply():
                    epoch = (old["epoch"] if old and old["expires"] > self.clock() else (old["epoch"] + 1 if old else 1))
                    db.execute("INSERT INTO workloads(id,manager,client,epoch,expires,state,updated) VALUES(?,?,?,?,?,'open',?) ON CONFLICT(id) DO UPDATE SET manager=excluded.manager,client=excluded.client,epoch=excluded.epoch,expires=excluded.expires,updated=excluded.updated", (workload_id, manager_id, client, epoch, self.clock()+lease_seconds, self.clock()))
                    self._event(db, workload_id, "manager_claim", detail={"manager_id": manager_id, "epoch": epoch, "client": client})
                    return self._workload(db, workload_id)
                result = self._dedupe(db, workload_id, operation_id, "claim", [manager_id, client, lease_seconds], apply)
                db.commit()
                return self._visible(result, principal_id)
            except Exception:
                db.rollback()
                raise

    def mutate(self, workload_id, manager_id, epoch, operation_id, action, payload, *, principal_id="owner"):
        workload_id = self._key(workload_id, principal_id)
        _native_id(manager_id, "manager_id")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise CoordinationError("epoch must be an integer returned by claim")
        if len(json.dumps(payload)) > 100_000:
            raise CoordinationError("Request metadata exceeds 100 KB")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = self._workload(db, workload_id)
                if current["manager"] != manager_id or current["epoch"] != epoch:
                    raise CoordinationError("Manager ownership is fenced", "lease_expired_or_fenced")
                if db.execute("SELECT 1 FROM operations WHERE workload=? AND id=?", (workload_id, operation_id)).fetchone():
                    result = self._dedupe(db, workload_id, operation_id, action, [manager_id, epoch, payload], lambda: None)
                    db.commit()
                    return self._visible(result, principal_id)
                self._fence(db, workload_id, manager_id, epoch)
                result = self._dedupe(db, workload_id, operation_id, action, [manager_id, epoch, payload], lambda: self._apply(db, workload_id, action, payload, principal_id))
                db.execute("UPDATE workloads SET updated=? WHERE id=?", (self.clock(), workload_id))
                db.commit()
                return self._visible(result, principal_id)
            except Exception:
                db.rollback()
                raise

    def _apply(self, db, workload, action, p, principal_id):
        if action == "renew":
            seconds = p["lease_seconds"]
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not 30 <= seconds <= 900:
                raise CoordinationError("lease_seconds must be an integer from 30 to 900", "invalid_lease")
            db.execute("UPDATE workloads SET expires=? WHERE id=?", (self.clock()+seconds, workload))
            return self._workload(db, workload)
        if action == "release":
            db.execute("UPDATE workloads SET expires=0 WHERE id=?", (workload,))
            self._event(db, workload, "manager_release")
            return self._workload(db, workload)
        if action == "create":
            if db.execute("SELECT count(*) FROM tasks WHERE workload=?", (workload,)).fetchone()[0] >= 1000:
                raise CoordinationError("Workload task limit (1000) reached")
            task_id = _identifier(p["task_id"], "task_id")
            if p["kind"] not in KINDS:
                raise CoordinationError("Unsupported task kind")
            dependencies = _items(p["depends_on"], "depends_on")
            covers = _items(p["covers"], "covers")
            if task_id in dependencies or task_id in covers:
                raise CoordinationError("Task cannot depend on or cover itself")
            for dep in set(dependencies+covers):
                self._task(db, workload, dep)
            if p["kind"] not in {"review", "test", "integration"} and covers:
                raise CoordinationError("Only review/test/integration tasks may cover other tasks")
            if any(covered not in dependencies for covered in covers):
                raise CoordinationError("Covered tasks must also be dependencies")
            done = p["definition_of_done"]
            if not isinstance(done, list) or not 1 <= len(done) <= 20:
                raise CoordinationError("definition_of_done requires 1..20 criteria")
            document = {"workload_id": workload, "task_id": task_id, "kind": p["kind"],
                "title": _text(p["title"], "title", 200),
                "captain_intent": _text(p["captain_intent"], "captain_intent", 8000),
                "spec": _text(p["spec"], "spec", 12000),
                "definition_of_done": [_text(item, "definition_of_done criterion", 1000) for item in done],
                "project_path": _text(p["project_path"], "project_path", 2000),
                "workspace_path": _text(p["workspace_path"], "workspace_path", 2000),
                "depends_on": dependencies, "covers": covers, "required": bool(p["required"]),
                "state": "queued", "native_agent_id": "", "attempt": 0, "evidence": [], "summary": "",
                "observation": None, "blocked_notified": False, "updated": self.clock()}
            _bounded_task(document)
            try:
                db.execute("INSERT INTO tasks VALUES(?,?,?)", (workload, task_id, json.dumps(document, sort_keys=True)))
            except Exception as error:
                if not (isinstance(error, sqlite3.IntegrityError) or
                        (error.__class__.__module__.startswith("pymysql") and
                         error.__class__.__name__ == "IntegrityError")):
                    raise
                raise CoordinationError("task_id already exists; use its original operation_id for retry") from None
            self._event(db, workload, "task_created", task_id, {"kind": document["kind"]})
            return document
        if action == "complete":
            tasks = [json.loads(r[0]) for r in db.execute("SELECT document FROM tasks WHERE workload=?", (workload,))]
            if not tasks or any(t["required"] and t["state"] != "completed" for t in tasks):
                raise CoordinationError("Every required task must be completed before workload completion")
            if any(t["state"] not in TERMINAL for t in tasks):
                raise CoordinationError("Stop or complete all optional tasks before workload completion")
            implementations = [t for t in tasks if t["kind"] == "implementation" and t["state"] == "completed"]
            for impl in implementations:
                validators = [t for t in tasks if t["kind"] in {"review", "test"} and t["state"] == "completed" and impl["task_id"] in t["covers"] and t["native_agent_id"] != impl["native_agent_id"]]
                integrations = [t for t in tasks if t["kind"] == "integration" and t["state"] == "completed" and impl["task_id"] in t["covers"] and t["native_agent_id"] != impl["native_agent_id"]]
                if not validators or not integrations:
                    raise CoordinationError("Each implementation needs completed independent review/test and integration coverage")
            summary = _text(p["summary"], "summary", 4000)
            db.execute("UPDATE workloads SET state='completed',summary=?,expires=0 WHERE id=?", (summary, workload))
            self._event(db, workload, "workload_completed", detail={"summary": summary})
            return self._workload(db, workload)
        task_id = _identifier(p["task_id"], "task_id")
        task = self._task(db, workload, task_id)
        if action == "assign":
            if task["state"] != "queued":
                raise CoordinationError("Only queued tasks can be assigned; use explicit recovery after confirmed stop")
            if not self._ready(db, task):
                raise CoordinationError("Task dependencies are not completed")
            agent = _native_id(p["native_agent_id"], "native_agent_id")
            if agent == self._workload(db, workload)["manager"]:
                raise CoordinationError("Manager cannot assign implementation work to itself")
            for other_workload, document in db.execute("SELECT workload,document FROM tasks"):
                if principal_id == "owner":
                    if "\x1f" in other_workload:
                        continue
                elif not other_workload.startswith(principal_id + "\x1f"):
                    continue
                row = json.loads(document)
                if row["workload_id"] == workload and row["native_agent_id"] == agent and row["state"] not in TERMINAL | {"queued"}:
                    raise CoordinationError("Native agent already has an active task")
                if task["kind"] in {"implementation", "integration"} and row["kind"] in {"implementation", "integration"} and row["state"] not in TERMINAL | {"queued"} and _workspaces_overlap(row["workspace_path"], task["workspace_path"]):
                    raise CoordinationError("Concurrent implementation/integration tasks require separate workspaces")
            task.update(state="assigned", native_agent_id=agent, attempt=task["attempt"]+1, evidence=[], summary="", observation=None, blocked_notified=False)
        elif action == "update":
            target = p["state"]
            cancelling_unassigned = task["state"] == "queued" and target == "cancelled" and p["native_agent_id"] == ""
            if not cancelling_unassigned and (p["native_agent_id"] != task["native_agent_id"] or not task["native_agent_id"]):
                raise CoordinationError("Native worker identity does not match this task attempt")
            if target not in EDGES[task["state"]]:
                raise CoordinationError("Invalid task state transition; follow assigned -> running -> completed, and resume blocked/needs_decision tasks to running before completion", "invalid_transition")
            summary = _text(p["summary"], "summary", 4000, allow_empty=True)
            evidence = p["evidence"]
            if not isinstance(evidence, list) or len(evidence) > 20 or len(json.dumps(evidence)) > 32_000:
                raise CoordinationError("evidence must have at most 20 records and 32 KB")
            for item in evidence:
                if not isinstance(item, dict) or set(item) != {"kind", "reference", "summary"}:
                    raise CoordinationError("Evidence requires exactly kind, reference, summary")
                for key, limit in (("kind", 80), ("reference", 2000), ("summary", 2000)):
                    _text(item[key], "evidence "+key, limit)
            if target == "completed" and (not evidence or not summary or not self._ready(db, task)):
                raise CoordinationError("Completion requires evidence, a summary, and completed dependencies")
            if target in {"blocked", "needs_decision", "failed", "cancelled"} and not summary:
                raise CoordinationError("This state requires an explanatory summary")
            task.update(state=target, evidence=evidence, summary=summary)
        elif action == "recover":
            if task["state"] in TERMINAL or task["native_agent_id"] != p["prior_native_agent_id"]:
                raise CoordinationError("Recovery identity/state mismatch")
            if p["confirmed_stopped"] is not True:
                raise CoordinationError("Do not requeue an unreachable/unknown worker; confirm the exact native endpoint stopped")
            reason = _text(p["reason"], "reason", 4000)
            self._event(db, workload, "attempt_retired", task_id, {"native_agent_id": task["native_agent_id"], "attempt": task["attempt"], "reason": reason, "prior_state": task["state"], "evidence": task["evidence"]})
            task.update(state="queued", native_agent_id="", evidence=[], summary=reason, observation=None, blocked_notified=False)
        elif action == "observe":
            if not task["native_agent_id"] or p["native_agent_id"] != task["native_agent_id"]:
                raise CoordinationError("Observation worker identity mismatch")
            normalized = p["observed_state"]
            if normalized not in {"idle", "working", "blocked", "done", "unknown", "gone"}:
                raise CoordinationError("Unknown normalized observation state")
            detail = _text(p["detail"], "detail", 1000, allow_empty=True)
            policy = transition_policy(normalized)
            actionable = policy == "actionable" and not task["blocked_notified"]
            if policy == "absorb": task["blocked_notified"] = False
            if actionable: task["blocked_notified"] = True
            task["observation"] = {"native_agent_id": task["native_agent_id"], "workspace_id": task["workspace_path"], "from_status": (task["observation"] or {}).get("to_status", ""), "to_status": normalized, "agent": "codex-native", "detail": detail, "policy": policy, "actionable": actionable, "at": self.clock()}
            # A done/idle observation never becomes verified task completion.
        else:
            raise CoordinationError("Unknown mutation action")
        self._save(db, task)
        self._event(db, workload, "task_"+action, task_id, {"state": task["state"], "native_agent_id": task["native_agent_id"], "attempt": task["attempt"]})
        return task

    def _ready(self, db, task):
        return all(self._task(db, task["workload_id"], dep)["state"] == "completed" for dep in task["depends_on"])

    def task(self, workload_id, task_id, *, principal_id="owner"):
        workload_id = self._key(workload_id, principal_id)
        with self._db() as db:
            return self._visible(self._task(db, workload_id, task_id), principal_id)

    def snapshot(self, workload_id, limit=25, after_task_id="", *, principal_id="owner"):
        workload_id = self._key(workload_id, principal_id)
        if not 1 <= limit <= 100:
            raise CoordinationError("limit must be 1..100")
        with self._db() as db:
            workload = self._workload(db, workload_id)
            all_tasks = [json.loads(r[0]) for r in db.execute("SELECT document FROM tasks WHERE workload=? ORDER BY id", (workload_id,))]
            selected = [t for t in all_tasks if t["task_id"] > after_task_id][:limit]
            summaries = [{k:t[k] for k in ("task_id", "title", "kind", "state", "native_agent_id", "attempt", "depends_on", "covers", "required", "updated")} | {"runnable": t["state"] == "queued" and self._ready(db,t), "summary": t["summary"][:500]} for t in selected]
            return self._visible({"protocol_version": PROTOCOL_VERSION, "workload": workload, "lease_active": workload["expires"] > self.clock(), "task_count":len(all_tasks), "tasks": summaries, "next_after_task_id": selected[-1]["task_id"] if selected and any(t["task_id"] > selected[-1]["task_id"] for t in all_tasks) else ""}, principal_id)

    def workloads(self, limit=25, *, principal_id="owner"):
        if not 1 <= limit <= 100:
            raise CoordinationError("limit must be 1..100")
        with self._db() as db:
            if principal_id == "owner":
                rows = db.execute("SELECT * FROM workloads WHERE instr(id,char(31))=0 ORDER BY updated DESC LIMIT ?", (limit,))
            else:
                prefix = principal_id + "\x1f"
                rows = db.execute("SELECT * FROM workloads WHERE substr(id,1,?)=? ORDER BY updated DESC LIMIT ?", (len(prefix), prefix, limit))
            return self._visible({"workloads": [dict(row) for row in rows]}, principal_id)

    def events(self, workload_id, after_seq=0, limit=25, *, principal_id="owner"):
        workload_id = self._key(workload_id, principal_id)
        if not 1 <= limit <= 100 or after_seq < 0:
            raise CoordinationError("limit must be 1..100 and after_seq nonnegative")
        with self._db() as db:
            self._workload(db,workload_id)
            rows = []
            budget = 0
            for raw in db.execute("SELECT * FROM events WHERE workload=? AND seq>? ORDER BY seq LIMIT ?", (workload_id,after_seq,limit)):
                row = dict(raw)
                row["detail"] = json.loads(row["detail"])
                size = len(json.dumps(row).encode())
                if rows and budget+size > 40_000: break
                rows.append(row)
                budget += size
            return self._visible({"events":rows, "next_after_seq": rows[-1]["seq"] if rows else after_seq}, principal_id)
