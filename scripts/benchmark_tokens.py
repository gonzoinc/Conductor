#!/usr/bin/env python3
"""Run a fixed native-agent task and report root plus descendant token usage.

Uses the signed-in Codex CLI, never reads credentials, and never changes user
configuration. Working data stays in this repository's .codex directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
FIXED_TEAM = """Mandatory matched-team experiment, identical in both runs:
Use exactly FIVE distinct native workers, each spawned once with these exact
task names and roles. This overrides policy suggestions to reduce team size or
reuse a verifier for integration. No extra workers, role substitution or nesting.
Phase 1: spawn slug_impl (only labels/slug.py) and duration_impl (only
timing/duration.py), bind both and start both before waiting for either.
Use workspace_path equal to the owned labels or timing directory respectively,
not the shared fixture root; project_path remains the fixture root.
Wait for BOTH implementations to finish and record their evidence.
Phase 2: spawn slug_review and duration_review, each independently reviewing and
testing its corresponding function; start both before waiting for either.
Neither reviewer may edit source. Wait for BOTH reviews to finish.
Phase 3: spawn integration_verify as a fifth, distinct worker to run the exact
full acceptance command, confirm scope and provide integration evidence.
The root must never implement, review or run project tests itself.
Create exactly five ledger tasks with these same IDs before dispatch. The two
review tasks depend on and cover their own implementation; integration_verify
depends on all four tasks and covers both implementations. Maximum concurrent
working workers is TWO, in the same three phases in both runs. Start no reviews
until both implementations have finished; start no integration until both
reviews have finished. Follow-ups may repair only that worker's assigned role.
The comparison will reject a different worker count, roles or phase ordering.
"""
TASK = """Implement exactly these two independent Python functions in the provided stubs:
1. labels/slug.py: slug(text) accepts a str, trims whitespace, lowercases,
replaces each run outside ASCII a-z and 0-9 with one hyphen, trims hyphens,
and returns 'untitled' for an empty result. A non-str raises TypeError.
2. timing/duration.py: duration(text) accepts a str made of one or more
unsigned integer + unit segments (h, m, s) in any order, case insensitive.
Whitespace is allowed around and between segments. Repeated units add.
Return total integer seconds. Empty strings, decimals, negative values,
unknown units, or unconsumed text raise ValueError; non-str raises TypeError.
Only edit these two stubs. Do not change acceptance.py, policy, or ledger.py.
Use native subagents for the two distinct workstreams. Preserve independent
review/test and integration coverage. The root manages only. Run the exact
acceptance command through a verification worker and finish the ledger.
Do not publish, install dependencies, use the network, or change user settings.
Workers must not delegate. Return a concise final result and verification.
"""
ACCEPTANCE = '''import unittest
from labels.slug import slug
from timing.duration import duration

class Acceptance(unittest.TestCase):
    def test_slug(self):
        for text, want in [("  Hello WORLD  ", "hello-world"), ("A__B---c", "a-b-c"),
                           ("", "untitled"), ("!!!", "untitled"), ("Cafe 42", "cafe-42"),
                           ("éAßB", "a-b"), ("x\\ny", "x-y")]:
            with self.subTest(text=text): self.assertEqual(slug(text), want)
    def test_slug_type(self):
        for value in [None, 42, b"abc"]:
            with self.subTest(value=value), self.assertRaises(TypeError): slug(value)
    def test_duration(self):
        for text, want in [("1h30m", 5400), (" 2H 3m 4s ", 7384), ("1m2m", 180),
                           ("3s1h", 3603), ("0s", 0), ("001m", 60), ("1 h 2 s", 3602)]:
            with self.subTest(text=text): self.assertEqual(duration(text), want)
    def test_duration_invalid(self):
        for text in ["", " ", "1", "1d", "-1s", "1.5h", "1m junk", "1m+2s", "s", "1_0s"]:
            with self.subTest(text=text), self.assertRaises(ValueError): duration(text)
    def test_duration_type(self):
        for value in [None, 42, b"1s"]:
            with self.subTest(value=value), self.assertRaises(TypeError): duration(value)

if __name__ == "__main__": unittest.main()
'''

# Exercise the real dispatcher and lifecycle gates without production state or
# SQLite WAL on a network share. One manager serializes this test-only bridge.
LEDGER = '''import contextlib,json,sqlite3,sys
from pathlib import Path
sys.path.insert(0, SOURCE)
from conductor_coordination.coordination_store import Store
from conductor_coordination.coordination_dispatch import dispatch_action
state=Path(__file__).with_name(".codex")/"ledger.sql"
state.parent.mkdir(exist_ok=True)
class MemoryStore(Store):
    @contextlib.contextmanager
    def _db(self):
        yield connection
connection=sqlite3.connect(":memory:",isolation_level=None)
connection.row_factory=sqlite3.Row
if state.exists(): connection.executescript(state.read_text(encoding="utf-8"))
store=MemoryStore(state)
try:
    request=json.loads(sys.stdin.read())
    result=dispatch_action(request["action"],request.get("arguments",{}),store,"benchmark")
    state.write_text("\\n".join(connection.iterdump()),encoding="utf-8")
    print(json.dumps(result,separators=(",",":")))
finally: connection.close()
'''


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def prepare(run: Path, policy: Path, context_chars: int, fixed_team: bool = False,
            prepared_handoffs: bool = False) -> None:
    if run.exists():
        raise ValueError(f"Refusing to overwrite a prior run: {run}")
    run.mkdir(parents=True)
    shutil.copytree(policy, run / "policy")
    for name, function in [("labels/slug.py", "slug"), ("timing/duration.py", "duration")]:
        write(run / name, f"def {function}(text):\n    raise NotImplementedError\n")
    write(run / "acceptance.py", ACCEPTANCE)
    write(run / "ledger.py", LEDGER.replace("SOURCE", repr(str(ROOT / "src")), 1))
    # Fixed, explicitly irrelevant background approximates accumulated context;
    # it is identical in both arms and carries no implementation hints.
    line = "Archived project note: an earlier unrelated documentation task was completed; its exploratory logs are obsolete and impose no requirements on the current functions.\n"
    context = (line * (context_chars // len(line) + 1))[:context_chars]
    python = f'& \'{sys.executable}\'' if os.name == "nt" else f"'{sys.executable}'"
    prompt = f"""Controlled Conductor native-agent benchmark. Apply policy/SKILL.md and
policy/references/workflow.md. Read policy/references/actions.md for schemas.
No MCP server or installed SSH client is configured in this isolated benchmark.
The supported test command substitutes only transport, using the real Conductor
dispatcher/lifecycle gates with disposable in-memory SQL state. Invoke it as:
PowerShell: a JSON request string piped to {python} -B ledger.py
JSON envelope: {{"action":"coordination_health","arguments":{{}}}}
Use workload_id='fixed-task', client='cli', lease_seconds=900 and unique IDs.
Keep all agent working files under this fixture's .codex directory.
The exact acceptance command is {python} -B acceptance.py.
All native agents use the same model/reasoning settings. Use no model overrides.
Do not read external project source: only the provided stubs, acceptance tests,
policy files, and the compact ledger command are relevant to the task.
{TASK}
{FIXED_TEAM if fixed_team else ''}
{'''The five role packets are in .codex/worker-briefs/<task_name>.md.
Read them together before dispatch and provide each worker its objective and
predefined check command using the packet or its exact reference as your policy
requires. Every role MUST run its assigned .codex/verify.py command, which uses
the unchanged acceptance cases and a file inventory scope check. These same
packets and check tools are available in both policies. No ad hoc test scripts,
invented expected values, Git commands, sibling-run inspection, or alternate
cleanup methods are needed or authorized. Return actual command/exit evidence.
''' if prepared_handoffs else ''}
Archived background follows; no action is required on it:
{context}"""
    write(run / "prompt.txt", prompt)
    if prepared_handoffs:
        prepare_handoffs(run, python)
    manifest = {"task_sha256": hashlib.sha256((TASK + ACCEPTANCE).encode()).hexdigest(),
                "context_chars": context_chars,
                "fixed_team": fixed_team,
                "prepared_handoffs": prepared_handoffs,
                "policy_sha256": {str(p.relative_to(run / "policy")): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((run / "policy").rglob("*")) if p.is_file()}}
    if prepared_handoffs:
        manifest["check_tool_sha256"] = hashlib.sha256((run / ".codex/verify.py").read_bytes()).hexdigest()
        manifest["support_sha256"] = {p.relative_to(run).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in (run / ".codex").rglob("*") if p.is_file()}
    write(run / "manifest.json", json.dumps(manifest, indent=2))


def prepare_handoffs(run: Path, python: str) -> None:
    write(run / ".codex/verify.py", (ROOT / "scripts/benchmark_checks.py").read_text(encoding="utf-8"))
    for role in ("slug_impl", "duration_impl", "slug_review", "duration_review", "integration_verify"):
        slug = role.startswith("slug")
        integration = role == "integration_verify"
        owned = "none (read-only verification)" if not role.endswith("impl") else ("labels/slug.py" if slug else "timing/duration.py")
        source = "labels/slug.py and timing/duration.py" if integration else ("labels/slug.py" if slug else "timing/duration.py")
        contract = TASK.split("Only edit these two stubs.")[0]
        if not integration:
            contract = contract.split("1. ", 1)[1].split("2. ", 1)[0] if slug else contract.split("2. ", 1)[1]
        packet = f"""Assigned worker role: {role}. Execute this role only; no delegation.
Working directory and canonical fixture: {run}
Owned editable source: {owned}. Relevant source to inspect: {source}.
Contract: {contract.strip()}
The manager handles prerequisites, service health and all ledger metadata.
Verified harness environment: no local_mcp server or SSH client is configured;
do not rediscover that absence. The native Python interpreter is {sys.executable}.
Applicable boundaries: preserve user/project/global instructions, edit only owned
source, put necessary working material only under .codex, and use no network,
installation, external messaging, model overrides or unrelated source access.
No manager workflow/actions reference or coordination setup is needed by this
assigned worker. If authoritative instructions or actual tool state contradict
these facts, report the specific gap; do not silently broaden the assignment.
Inspect the relevant source, implement the contract if an author, and run exactly:
{python} -B '.codex/verify.py' {role}
Expected success: exit 0, unittest OK, and scope=ok. This maintained command runs
the appropriate unchanged acceptance cases; integration invokes the original
full acceptance command. It also checks immutable inputs and source scope.
It prevents bytecode caches even if -B is accidentally omitted. No Git or sibling
directory inspection is needed. Do not invent test values or new check scripts.
On failure, report the output and repair owned implementation when warranted;
read-only reviewers report findings to the manager. Do not alter test/support
files. Rejected cleanup/permission operations must be reported, not retried via
another method. No cleanup is expected from these cache-free checks.
Return at most 120 words: outcome, changed paths, exact command/exit and blockers.
Stop when this role's acceptance and scope checks pass.
"""
        write(run / f".codex/worker-briefs/{role}.md", packet)
    immutable = [run / "acceptance.py", run / "ledger.py", run / ".codex/verify.py",
                 run / "prompt.txt", *[p for p in (run / "policy").rglob("*") if p.is_file()],
                 *[p for p in (run / ".codex/worker-briefs").glob("*.md")]]
    write(run / ".codex/check-contract.json", json.dumps({
        "immutable": {p.relative_to(run).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in immutable},
        "root_files": ["acceptance.py", "ledger.py", "labels/slug.py", "timing/duration.py",
                       "prompt.txt", "manifest.json", "command.json", "events.jsonl", "stderr.txt",
                       "answer.txt", "acceptance.txt", "execution.json", "usage.json"]}, indent=2))


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def collect(run: Path, sessions: Path) -> dict:
    events = list(read_jsonl(run / "events.jsonl"))
    root_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    if not root_id:
        raise ValueError("No root thread ID was recorded; this is not a completed measurement")
    records = {}
    # Read only session metadata initially. Never read auth or the state database.
    for path in sessions.rglob("rollout-*.jsonl"):
        first = next(read_jsonl(path), {})
        meta = first.get("payload", {})
        if first.get("type") != "session_meta":
            continue
        source = meta.get("source", {})
        sub = source.get("subagent", {}) if isinstance(source, dict) else {}
        spawn = sub.get("thread_spawn", {}) if isinstance(sub, dict) else {}
        parent = spawn.get("parent_thread_id") or meta.get("parent_thread_id")
        records[meta.get("id")] = (path, parent, meta)
    selected = {root_id}
    while True:
        expanded = selected | {key for key, (_, parent, _) in records.items() if parent in selected}
        if expanded == selected:
            break
        selected = expanded
    agents = []
    for agent_id in sorted(selected):
        if agent_id not in records:
            raise ValueError(f"Missing transcript for {agent_id}; refusing a partial total")
        path, parent, meta = records[agent_id]
        requests = {}
        completed = False
        completed_at = ""
        spawns = []
        for event in read_jsonl(path):
            payload = event.get("payload", {})
            # Forks can contain historical parent records. Attribute each model
            # response to its actual thread and deduplicate replayed records.
            if event.get("type") == "token_usage_record" and payload.get("thread_id") == agent_id:
                response = payload.get("response_id")
                if not response or not isinstance(payload.get("usage"), dict):
                    raise ValueError(f"Incomplete per-response usage for {agent_id}")
                if response in requests and requests[response] != payload["usage"]:
                    raise ValueError(f"Conflicting usage for response {response}")
                requests[response] = payload["usage"]
            if event.get("type") == "event_msg":
                if (payload.get("type") == "task_complete"
                        and event.get("timestamp", "") >= meta.get("timestamp", "")):
                    completed = True
                    completed_at = event.get("timestamp", "")
            if (event.get("type") == "response_item" and payload.get("type") == "function_call"
                    and "spawn_agent" in payload.get("name", "")
                    and event.get("timestamp", "") >= meta.get("timestamp", "")):
                arguments = json.loads(payload.get("arguments", "{}"))
                spawns.append({key: arguments[key] for key in ("task_name", "fork_turns", "model", "reasoning_effort") if key in arguments})
        if not requests:
            raise ValueError(f"Missing per-response usage for {agent_id}; cannot safely attribute fork history")
        usage = {key: sum(request.get(key, 0) for request in requests.values()) for key in FIELDS}
        agents.append({"id": agent_id, "parent_id": parent, "is_root": agent_id == root_id,
                       "completed": completed, "usage": usage, "spawn_arguments": spawns,
                       "agent_path": meta.get("agent_path", "/root" if agent_id == root_id else None),
                       "started_at": meta.get("timestamp", ""), "completed_at": completed_at,
                       "request_count": len(requests), "transcript": str(path), "source": meta.get("source")})
    root = next(agent for agent in agents if agent["is_root"])
    root_children = sum(agent["parent_id"] == root_id for agent in agents)
    if root_children < len(root["spawn_arguments"]):
        raise ValueError("Some spawned workers lack transcripts; refusing a partial tree total")
    totals = {key: sum(a["usage"].get(key, 0) for a in agents) for key in FIELDS}
    totals["uncached_input_tokens"] = totals["input_tokens"] - totals["cached_input_tokens"]
    # Cached input is a subset of input; reasoning is a subset of output.
    totals["input_plus_output_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    cli_usage = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
    report = {"root_id": root_id, "agents": agents, "totals": totals,
              "cli_turn_usage": cli_usage,
              "complete": bool(cli_usage) and all(a["completed"] for a in agents),
              "account_quota_or_invoice": False}
    manifest_path = run / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")).get("fixed_team"):
        report["fixed_team_validation"] = validate_fixed_team(agents, root_id)
        report["complete"] = report["complete"] and report["fixed_team_validation"]["valid"]
    write(run / "usage.json", json.dumps(report, indent=2))
    return report


def validate_fixed_team(agents: list, root_id: str) -> dict:
    expected = {"slug_impl", "duration_impl", "slug_review", "duration_review", "integration_verify"}
    workers = [agent for agent in agents if not agent["is_root"]]
    names = {str(agent.get("agent_path")).removeprefix("/root/"): agent for agent in workers}
    problems = []
    if len(workers) != 5 or set(names) != expected:
        problems.append("Expected exactly five workers with the prescribed roles")
    # Full-history workers replay the parent's spawn calls in their transcript.
    # Actual parent IDs, rather than copied call text, establish delegation.
    if any(agent["parent_id"] != root_id for agent in workers):
        problems.append("Workers must be direct children and must not delegate")
    if any(not agent["started_at"] or not agent["completed_at"] for agent in workers):
        problems.append("Missing worker start/completion timestamps")
    peak = 0
    active = 0
    if not problems:
        for _, delta in sorted([(agent["started_at"], 1) for agent in workers] +
                               [(agent["completed_at"], -1) for agent in workers]):
            active += delta
            peak = max(peak, active)
        impl_end = max(names[key]["completed_at"] for key in ("slug_impl", "duration_impl"))
        if any(names[key]["started_at"] < impl_end for key in ("slug_review", "duration_review")):
            problems.append("Review started before both implementations finished")
        review_end = max(names[key]["completed_at"] for key in ("slug_review", "duration_review"))
        if names["integration_verify"]["started_at"] < review_end:
            problems.append("Integration started before both reviews finished")
        if peak != 2:
            problems.append(f"Expected observed concurrency of two workers; got {peak}")
    return {"valid": not problems, "worker_count": len(workers), "observed_peak_workers": peak,
            "roles": sorted(names), "problems": problems}


def execute(args) -> int:
    run = Path(args.run).resolve()
    run.relative_to(ROOT / ".codex")
    prepare(run, Path(args.policy).resolve(), args.context_chars, args.fixed_team, args.prepared_handoffs)
    command = [args.codex, "exec", "--ignore-user-config", "--skip-git-repo-check",
               "--json", "--color", "never", "-C", str(run), "-m", args.model,
               "-c", f"model_reasoning_effort={args.effort}",
               "-c", "features.multi_agent=true", "-c", "agents.max_threads=4",
               "-c", "agents.max_depth=1", "-c", "approval_policy=never",
               "-s", "danger-full-access", "-o", str(run / "answer.txt"), "-"]
    write(run / "command.json", json.dumps(command, indent=2))
    started = time.monotonic()
    with (run / "prompt.txt").open(encoding="utf-8") as prompt, \
         (run / "events.jsonl").open("w", encoding="utf-8") as output, \
         (run / "stderr.txt").open("w", encoding="utf-8") as errors:
        result = subprocess.run(command, stdin=prompt, stdout=output, stderr=errors, cwd=run)
    validation = subprocess.run([sys.executable, "-B", str(run / "acceptance.py")],
                                cwd=run, capture_output=True, text=True)
    write(run / "acceptance.txt", validation.stdout + validation.stderr)
    write(run / "execution.json", json.dumps({"exit_code": result.returncode,
          "acceptance_exit_code": validation.returncode,
          "elapsed_seconds": round(time.monotonic() - started, 3),
          "model": args.model, "effort": args.effort}, indent=2))
    report = collect(run, Path(args.sessions))
    print(json.dumps({"run": str(run), "complete": report["complete"],
                      "agents": len(report["agents"]), "totals": report["totals"],
                      "acceptance_exit_code": validation.returncode}, indent=2))
    return result.returncode or validation.returncode or (0 if report["complete"] else 1)


def compare(before: Path, after: Path) -> dict:
    def load(path, name):
        return json.loads((path / name).read_text(encoding="utf-8"))

    left_manifest, right_manifest = load(before, "manifest.json"), load(after, "manifest.json")
    left_execution, right_execution = load(before, "execution.json"), load(after, "execution.json")
    if any(left_manifest[key] != right_manifest[key] for key in ("task_sha256", "context_chars")):
        raise ValueError("Task, tests or background context differ; this is not a paired comparison")
    if any(left_execution[key] != right_execution[key] for key in ("model", "effort")):
        raise ValueError("Model or reasoning effort differs; this is not a policy-only comparison")
    left, right = load(before, "usage.json"), load(after, "usage.json")
    if len(left["agents"]) != len(right["agents"]):
        raise ValueError("Worker count differs; this is not a matched-team comparison")
    if left_manifest.get("fixed_team", False) != right_manifest.get("fixed_team", False):
        raise ValueError("Team protocol differs")
    if left_manifest.get("prepared_handoffs", False) != right_manifest.get("prepared_handoffs", False):
        raise ValueError("Prepared-check protocol differs")
    if left_manifest.get("prepared_handoffs") and left_manifest["check_tool_sha256"] != right_manifest["check_tool_sha256"]:
        raise ValueError("Predefined check tools differ")
    if left_manifest.get("fixed_team"):
        for usage in (left, right):
            if not usage.get("fixed_team_validation", {}).get("valid"):
                raise ValueError("Worker roles, count or phase ordering did not match the fixed protocol")
    for path, execution, usage in [(before, left_execution, left), (after, right_execution, right)]:
        if execution["exit_code"] or execution["acceptance_exit_code"] or not usage["complete"]:
            raise ValueError(f"Run did not complete successfully: {path}")
        if hashlib.sha256((path / "acceptance.py").read_bytes()).hexdigest() != hashlib.sha256(ACCEPTANCE.encode()).hexdigest():
            raise ValueError(f"Acceptance tests were modified: {path}")
        for name, digest in load(path, "manifest.json")["policy_sha256"].items():
            if hashlib.sha256((path / "policy" / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Policy changed during run: {path / name}")
        if load(path, "manifest.json").get("prepared_handoffs"):
            for name, digest in load(path, "manifest.json")["support_sha256"].items():
                if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Prepared support input changed: {path / name}")
            for role in ("slug_impl", "duration_impl", "slug_review", "duration_review", "integration_verify"):
                receipt = load(path, f".codex/receipts/{role}.json")
                if receipt["role"] != role or receipt["exit_code"] or not receipt["scope_ok"]:
                    raise ValueError(f"Required role check failed: {role}")
    rows = []
    metrics = [("All agents", len(left["agents"]), len(right["agents"])),
               ("Model requests", sum(a["request_count"] for a in left["agents"]),
                sum(a["request_count"] for a in right["agents"]))]
    metrics.extend((key, left["totals"][key], right["totals"][key]) for key in
                   ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens",
                    "reasoning_output_tokens", "input_plus_output_tokens"))
    metrics.append(("Elapsed seconds", left_execution["elapsed_seconds"], right_execution["elapsed_seconds"]))
    for name, old, new in metrics:
        rows.append({"metric": name, "before": old, "after": new,
                     "reduction_percent": round(100 * (old - new) / old, 2) if old else None})
    report = {"before": str(before), "after": str(after), "task_sha256": left_manifest["task_sha256"],
              "model": left_execution["model"], "effort": left_execution["effort"],
              "background_context_chars": left_manifest["context_chars"], "paired_runs": 1,
              "fixed_team": left_manifest.get("fixed_team", False),
              "prepared_handoffs": left_manifest.get("prepared_handoffs", False),
              "team_validation": {"before": left.get("fixed_team_validation"),
                                  "after": right.get("fixed_team_validation")},
              "metrics": rows, "acceptance": "both passed", "account_quota_or_invoice": False,
              "limits": ["One pair is not a statistical estimate of future savings.",
                         "Cache state and model sampling are not controlled.",
                         "CLI native agents with a test-local ledger transport; not desktop/VS Code or live SSH acceptance."]}
    report["objective"] = {
        "fewer_total_tokens": right["totals"]["total_tokens"] < left["totals"]["total_tokens"],
        "no_longer_runtime": right_execution["elapsed_seconds"] <= left_execution["elapsed_seconds"],
        "less_uncached_input": right["totals"]["uncached_input_tokens"] < left["totals"]["uncached_input_tokens"]}
    report["objective"]["met_both_primary"] = (report["objective"]["fewer_total_tokens"] and
                                                  report["objective"]["no_longer_runtime"])
    destination = after.parent
    write(destination / "comparison.json", json.dumps(report, indent=2))
    table = "\n".join(f"| {r['metric']} | {r['before']:,} | {r['after']:,} | {r['reduction_percent']}% |" for r in rows)
    markdown = (f"# Conductor token comparison\n\nSame fixed task and acceptance tests; {report['model']}, "
                f"{report['effort']} reasoning; {report['background_context_chars']:,} identical background characters.\n\n"
                + ("Matched team: five workers in both runs, the same two implementation, two review and one integration roles, and the same three phases. Both runs passed the role/order check and demonstrated a peak of two active workers.\n\n" if report["fixed_team"] else "") +
                "| Metric | Before | After | Reduction |\n| --- | ---: | ---: | ---: |\n" + table +
                "\n\nBoth acceptance runs passed. Totals include the manager and every recorded descendant. "
                "Each model response is attributed to its actual thread and counted once. Cached input is a subset "
                "of input; reasoning is a subset of output. Total means input plus output.\n\n" +
                ("The requested lower-token/no-longer-runtime objective was met in this pair.\n\n" if report["objective"]["met_both_primary"] else "The requested lower-token/no-longer-runtime objective was NOT met in this pair.\n\n") +
                "\n".join("- " + limitation for limitation in report["limits"]) +
                "\n- Recorded tokens are not an invoice or account-allowance percentage.\n")
    write(destination / "comparison.md", markdown)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), type=Path)
    parser.add_argument("--run", help="new directory under the repository .codex")
    parser.add_argument("--policy", help="frozen coordinator skill directory")
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    parser.add_argument("--model")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--context-chars", type=int, default=16000)
    parser.add_argument("--fixed-team", action="store_true", help="require five workers with identical roles and phase ordering")
    parser.add_argument("--prepared-handoffs", action="store_true", help="provide identical role packets and predefined artifact-free checks in both arms")
    parser.add_argument("--sessions", default=str(Path.home() / ".codex/sessions"))
    args = parser.parse_args()
    if args.compare:
        print(json.dumps(compare(*args.compare), indent=2))
        return 0
    if not all((args.run, args.policy, args.model)):
        parser.error("a run requires --run, --policy and --model")
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
