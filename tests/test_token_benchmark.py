"""Token accounting must not double-count inherited history or token subsets."""
import importlib.util
import json
from pathlib import Path
import sys
import shutil
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("token_benchmark", ROOT / "scripts/benchmark_tokens.py")
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)
sys.path.insert(0, str(ROOT / "src"))
from conductor_coordination.coordination_store import Store


class AccountingTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".codex" / "token-benchmark-tests"
        work.mkdir(parents=True, exist_ok=True)
        # tempfile uses mode 0700, which this SMB host maps to an inaccessible
        # ACL on Windows. Normal mkdir inherits the project permissions.
        self.base = work / uuid.uuid4().hex
        self.base.mkdir()
        self.addCleanup(shutil.rmtree, self.base)
        self.run = self.base / "run"
        self.run.mkdir()
        self.sessions = self.base / "sessions"
        self.sessions.mkdir()
        self.rows(self.run / "events.jsonl", [
            {"type": "thread.started", "thread_id": "root"},
            {"type": "turn.completed", "usage": {"input_tokens": 100}}])

    @staticmethod
    def rows(path, rows):
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def transcript(self, name, parent=None, requests=None):
        meta = {"id": name, "parent_thread_id": parent}
        rows = [{"type": "session_meta", "payload": meta}]
        for thread, response, amount in requests or [(name, name + "-response", 100)]:
            usage = {"input_tokens": amount, "cached_input_tokens": amount // 2,
                     "output_tokens": 10, "reasoning_output_tokens": 4, "total_tokens": amount + 10}
            rows.append({"type": "token_usage_record", "payload": {
                "thread_id": thread, "response_id": response, "usage": usage}})
        rows.append({"type": "event_msg", "payload": {"type": "task_complete"}})
        self.rows(self.sessions / ("rollout-" + name + ".jsonl"), rows)

    def test_descendants_inherited_records_and_replayed_responses(self):
        self.transcript("root")
        self.transcript("child", "root", [("root", "root-response", 100),
                        ("child", "child-response", 60), ("child", "child-response", 60)])
        self.transcript("grandchild", "child")
        self.transcript("unrelated")
        report = BENCH.collect(self.run, self.sessions)
        self.assertEqual(len(report["agents"]), 3)
        self.assertEqual(report["totals"]["input_tokens"], 260)
        self.assertEqual(report["totals"]["cached_input_tokens"], 130)
        self.assertEqual(report["totals"]["output_tokens"], 30)
        self.assertEqual(report["totals"]["reasoning_output_tokens"], 12)
        self.assertEqual(report["totals"]["input_plus_output_tokens"], 290)
        self.assertTrue(report["complete"])

    def test_missing_usage_is_not_reported_as_zero(self):
        self.transcript("root")
        self.transcript("child", "root", [("root", "inherited", 100)])
        with self.assertRaisesRegex(ValueError, "Missing per-response usage"):
            BENCH.collect(self.run, self.sessions)

    def test_missing_root_transcript_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Missing transcript"):
            BENCH.collect(self.run, self.sessions)

    def test_refuses_to_overwrite_a_run(self):
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            BENCH.prepare(self.run, ROOT / "skills/conductor-coordinator", 100)

    def comparison_pair(self):
        self.transcript("root")
        usage = BENCH.collect(self.run, self.sessions)
        paths = []
        for name in ("before", "after"):
            path = self.base / name
            BENCH.prepare(path, ROOT / "skills/conductor-coordinator", 100)
            BENCH.write(path / "execution.json", json.dumps({"exit_code": 0,
                "acceptance_exit_code": 0, "model": "fixed-model", "effort": "medium",
                "elapsed_seconds": 1}))
            BENCH.write(path / "usage.json", json.dumps(usage))
            paths.append(path)
        return paths

    def test_comparison_accepts_identical_controls(self):
        before, after = self.comparison_pair()
        result = BENCH.compare(before, after)
        self.assertEqual(result["acceptance"], "both passed")
        self.assertTrue(all(row["reduction_percent"] == 0 for row in result["metrics"]))

    def test_comparison_rejects_changed_acceptance_tests(self):
        before, after = self.comparison_pair()
        BENCH.write(after / "acceptance.py", "print('weakened test')\n")
        with self.assertRaisesRegex(ValueError, "Acceptance tests were modified"):
            BENCH.compare(before, after)

    def test_comparison_rejects_different_reasoning_effort(self):
        before, after = self.comparison_pair()
        execution = json.loads((after / "execution.json").read_text(encoding="utf-8"))
        execution["effort"] = "high"
        BENCH.write(after / "execution.json", json.dumps(execution))
        with self.assertRaisesRegex(ValueError, "Model or reasoning effort differs"):
            BENCH.compare(before, after)

    def test_comparison_rejects_different_worker_counts(self):
        before, after = self.comparison_pair()
        usage = json.loads((after / "usage.json").read_text(encoding="utf-8"))
        usage["agents"].append(dict(usage["agents"][0]))
        BENCH.write(after / "usage.json", json.dumps(usage))
        with self.assertRaisesRegex(ValueError, "Worker count differs"):
            BENCH.compare(before, after)

    def test_fixed_team_checks_roles_and_phase_order(self):
        names = ["slug_impl", "duration_impl", "slug_review", "duration_review", "integration_verify"]
        intervals = [("01", "03"), ("02", "04"), ("05", "07"), ("06", "08"), ("09", "10")]
        agents = [{"is_root": False, "parent_id": "root", "spawn_arguments": [],
                   "agent_path": "/root/" + name, "started_at": start, "completed_at": end}
                  for name, (start, end) in zip(names, intervals)]
        self.assertTrue(BENCH.validate_fixed_team(agents, "root")["valid"])
        # Inherited parent call text is not evidence of real nested workers.
        agents[0]["spawn_arguments"] = [{"task_name": "slug_impl"}]
        self.assertTrue(BENCH.validate_fixed_team(agents, "root")["valid"])
        agents[4]["parent_id"] = "a-worker"
        self.assertFalse(BENCH.validate_fixed_team(agents, "root")["valid"])
        agents[4]["parent_id"] = "root"
        agents[2]["started_at"] = "03"
        self.assertFalse(BENCH.validate_fixed_team(agents, "root")["valid"])

    def test_prepared_checks_preserve_suite_and_report_real_failure_without_cache(self):
        fixture = self.base / "prepared"
        BENCH.prepare(fixture, ROOT / "skills/conductor-coordinator", 100, True, True)
        result = subprocess.run([sys.executable, str(fixture / ".codex/verify.py"), "slug_impl"],
                                cwd=fixture, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)  # Original stubs must fail.
        receipt = json.loads((fixture / ".codex/receipts/slug_impl.json").read_text())
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["tests"], ["test_slug", "test_slug_type"])
        self.assertFalse(list(fixture.rglob("*.pyc")))
        self.assertEqual((fixture / "acceptance.py").read_text(encoding="utf-8"), BENCH.ACCEPTANCE)

    def test_prepared_scope_rejects_changed_immutable_test(self):
        fixture = self.base / "prepared"
        BENCH.prepare(fixture, ROOT / "skills/conductor-coordinator", 100, True, True)
        BENCH.write(fixture / "acceptance.py", "print('weakened')\n")
        result = subprocess.run([sys.executable, "-B", str(fixture / ".codex/verify.py"), "slug_review"],
                                cwd=fixture, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Immutable input changed: acceptance.py", result.stdout)

    def test_prepared_packets_include_operating_commands_and_read_only_roles(self):
        fixture = self.base / "prepared"
        BENCH.prepare(fixture, ROOT / "skills/conductor-coordinator", 100, True, True)
        packet = (fixture / ".codex/worker-briefs/duration_review.md").read_text(encoding="utf-8")
        self.assertIn("none (read-only verification)", packet)
        self.assertIn("-B '.codex/verify.py' duration_review", packet)
        self.assertIn("no local_mcp server", packet)
        self.assertEqual(len(list((fixture / ".codex/worker-briefs").glob("*.md"))), 5)


class CombinedCoverageTests(unittest.TestCase):
    def test_one_independent_worker_can_verify_then_integrate(self):
        # No SQLite file or network-share WAL is needed for this gate check.
        import contextlib
        import sqlite3
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)

        class MemoryStore(Store):
            @contextlib.contextmanager
            def _db(self):
                yield connection

        store = MemoryStore(ROOT / ".codex/unused-benchmark-database")
        epoch = store.claim("work", "manager", "cli", "claim", 900)["epoch"]
        sequence = 0

        def mutate(action, **payload):
            nonlocal sequence
            sequence += 1
            return store.mutate("work", "manager", epoch, f"op-{sequence}", action, payload)

        def task(name, kind, dependencies, covers):
            mutate("create", task_id=name, title=name, kind=kind, project_path="/project",
                   workspace_path="/project", captain_intent="Fixed task", spec="Bounded task",
                   definition_of_done=["Verified"], depends_on=dependencies, covers=covers, required=True)

        def finish(name, agent):
            mutate("assign", task_id=name, native_agent_id=agent)
            mutate("update", task_id=name, native_agent_id=agent, state="running", summary="", evidence=[])
            mutate("update", task_id=name, native_agent_id=agent, state="completed", summary="Verified",
                   evidence=[{"kind": "test", "reference": name, "summary": "Separate task evidence"}])

        task("implement", "implementation", [], [])
        task("review", "review", ["implement"], ["implement"])
        task("integrate", "integration", ["implement", "review"], ["implement"])
        finish("implement", "/root/author")
        finish("review", "/root/verifier")
        finish("integrate", "/root/verifier")
        self.assertEqual(mutate("complete", summary="Independently checked")["state"], "completed")


if __name__ == "__main__":
    unittest.main()
