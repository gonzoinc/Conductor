import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from mcp.server.mcpserver.exceptions import ToolError

from conductor_coordination.coordination_store import Store, transition_policy


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.path = Path(self.temp.name)/"tasks.sqlite3"
        self.store = Store(self.path, lambda:self.now)
        self.claim = self.store.claim("work", "manager-one", "test", "claim-1", 60)
        self.epoch = self.claim["epoch"]
        self.seq=0

    def op(self, action, **payload):
        self.seq+=1
        return self.store.mutate("work","manager-one",self.epoch,f"operation-{self.seq}",action,payload)

    def create(self, task="task", kind="scout", dependencies=None, covers=None, **extra):
        return self.op("create",task_id=task,title="Task",kind=kind,project_path="/canonical/project",workspace_path="/worktrees/"+task,captain_intent="User's actual request",spec="Worker instructions",definition_of_done=["Expected result verified"],depends_on=dependencies or [],covers=covers or [],required=True,**extra)

    def complete(self, task="task", agent="/root/worker"):
        self.op("assign",task_id=task,native_agent_id=agent)
        self.op("update",task_id=task,native_agent_id=agent,state="running",summary="",evidence=[])
        return self.op("update",task_id=task,native_agent_id=agent,state="completed",summary="Worker completed with evidence",evidence=[{"kind":"test","reference":"artifact:checked-result","summary":"Independent evidence reference"}])

    def test_state_survives_reopen_and_wal_full_durability(self):
        self.create()
        reopened=Store(self.path,lambda:self.now)
        self.assertEqual(reopened.task("work","task")["state"],"queued")
        with reopened._db() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0],"wal")
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0],2)

    def test_active_owner_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError,"Another manager"):
            self.store.claim("work","manager-two","vscode","claim-2",60)

    def test_expired_lease_cannot_mutate_and_transfer_fences_old_owner(self):
        self.now+=61
        with self.assertRaisesRegex(ValueError,"expired"):
            self.op("renew",lease_seconds=60)
        new=self.store.claim("work","manager-two","vscode","claim-2",60)
        self.assertEqual(new["epoch"],self.epoch+1)
        with self.assertRaisesRegex(ValueError,"fenced"):
            self.op("renew",lease_seconds=60)

    def test_atomic_concurrent_claim_has_single_winner(self):
        self.now+=61
        winners=[]
        failures=[]
        def claim(manager):
            try: winners.append(self.store.claim("work",manager,"test",manager,60)["manager"])
            except ValueError as error: failures.append(str(error))
        threads=[threading.Thread(target=claim,args=("manager-a",)),threading.Thread(target=claim,args=("manager-b",))]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(len(winners),1)
        self.assertEqual(len(failures),1)

    def test_operation_replay_identical_and_different_payload_refused(self):
        original=self.store.mutate("work","manager-one",self.epoch,"same-op","renew",{"lease_seconds":60})
        self.now+=5
        self.assertEqual(self.store.mutate("work","manager-one",self.epoch,"same-op","renew",{"lease_seconds":60}),original)
        with self.assertRaisesRegex(ValueError,"different request"):
            self.store.mutate("work","manager-one",self.epoch,"same-op","renew",{"lease_seconds":90})

    def test_release_retry_is_idempotent_but_new_write_refused(self):
        result=self.store.mutate("work","manager-one",self.epoch,"release","release",{})
        self.assertEqual(self.store.mutate("work","manager-one",self.epoch,"release","release",{}),result)
        with self.assertRaises(ValueError):self.op("renew",lease_seconds=60)

    def test_dependencies_block_assignment_and_require_existing_nodes(self):
        self.create("first")
        self.create("second",dependencies=["first"])
        with self.assertRaisesRegex(ValueError,"dependencies"):
            self.op("assign",task_id="second",native_agent_id="/root/second")
        with self.assertRaisesRegex(ValueError,"Unknown task"):
            self.create("invalid",dependencies=["future"])
        self.complete("first")
        self.assertTrue(next(t for t in self.store.snapshot("work")["tasks"] if t["task_id"]=="second")["runnable"])

    def test_complete_needs_assignment_summary_and_evidence(self):
        self.create()
        self.op("assign",task_id="task",native_agent_id="/root/worker")
        with self.assertRaisesRegex(ValueError,"transition"):
            self.op("update",task_id="task",native_agent_id="/root/worker",state="completed",summary="done",evidence=[])
        self.op("update",task_id="task",native_agent_id="/root/worker",state="running",summary="",evidence=[])
        with self.assertRaisesRegex(ValueError,"evidence"):
            self.op("update",task_id="task",native_agent_id="/root/worker",state="completed",summary="done",evidence=[])

    def test_stale_worker_cannot_update_after_explicit_recovery(self):
        self.create()
        self.op("assign",task_id="task",native_agent_id="/root/old")
        with self.assertRaisesRegex(ValueError,"confirm"):
            self.op("recover",task_id="task",prior_native_agent_id="/root/old",confirmed_stopped=False,reason="unreachable")
        self.op("recover",task_id="task",prior_native_agent_id="/root/old",confirmed_stopped=True,reason="Exact native endpoint stopped")
        self.op("assign",task_id="task",native_agent_id="/root/new")
        with self.assertRaisesRegex(ValueError,"identity"):
            self.op("update",task_id="task",native_agent_id="/root/old",state="running",summary="",evidence=[])
        events=self.store.events("work")["events"]
        self.assertTrue(any(event["kind"]=="attempt_retired" for event in events))

    def test_observation_policy_dedupes_blocked_but_never_completes(self):
        self.create()
        self.op("assign",task_id="task",native_agent_id="/root/worker")
        def observe(state):return self.op("observe",task_id="task",native_agent_id="/root/worker",observed_state=state,detail="native observation")
        self.assertTrue(observe("blocked")["observation"]["actionable"])
        self.assertFalse(observe("blocked")["observation"]["actionable"])
        observe("working")
        self.assertTrue(observe("blocked")["observation"]["actionable"])
        self.assertEqual(observe("done")["state"],"assigned")
        self.assertEqual(observe("gone")["observation"]["policy"],"rescan")

    def test_conductor_policy_exact_mapping(self):
        self.assertEqual([transition_policy(s) for s in ("blocked","working","idle","done","gone","unexpected")],["actionable","absorb","defer","defer","rescan","fallback"])

    def test_manager_cannot_bind_self(self):
        self.create()
        with self.assertRaisesRegex(ValueError,"itself"):
            self.op("assign",task_id="task",native_agent_id="manager-one")

    def test_implementation_requires_independent_review_and_integration(self):
        self.create("implement",kind="implementation")
        self.complete("implement","/root/implementer")
        with self.assertRaisesRegex(ValueError,"independent"):
            self.op("complete",summary="Not verified")
        self.create("review",kind="review",dependencies=["implement"],covers=["implement"])
        self.complete("review","/root/reviewer")
        self.create("integrate",kind="integration",dependencies=["implement","review"],covers=["implement"])
        self.complete("integrate","/root/integrator")
        result=self.op("complete",summary="Implementation independently reviewed and integrated")
        self.assertEqual(result["state"],"completed")

    def test_same_worker_review_is_not_independent(self):
        self.create("implement",kind="implementation")
        self.complete("implement","/root/same")
        self.create("review",kind="review",dependencies=["implement"],covers=["implement"])
        self.complete("review","/root/same")
        self.create("integrate",kind="integration",dependencies=["implement","review"],covers=["implement"])
        self.complete("integrate","/root/integrator")
        with self.assertRaisesRegex(ValueError,"independent"):
            self.op("complete",summary="Cannot accept same worker review")

    def test_required_cancelled_task_prevents_completion(self):
        self.create()
        self.op("assign",task_id="task",native_agent_id="/root/worker")
        self.op("update",task_id="task",native_agent_id="/root/worker",state="cancelled",summary="scope removed",evidence=[])
        with self.assertRaisesRegex(ValueError,"required"):
            self.op("complete",summary="Not complete")

    def test_snapshot_pagination_and_briefs_omitted(self):
        for task in ("one","two","three"):self.create(task)
        first=self.store.snapshot("work",limit=1)
        self.assertEqual(len(first["tasks"]),1)
        self.assertNotIn("spec",first["tasks"][0])
        self.assertTrue(first["next_after_task_id"])
        second=self.store.snapshot("work",limit=1,after_task_id=first["next_after_task_id"])
        self.assertNotEqual(first["tasks"][0]["task_id"],second["tasks"][0]["task_id"])
        with self.assertRaises(ValueError):self.store.snapshot("work",limit=101)

    def test_evidence_bounds(self):
        self.create()
        self.op("assign",task_id="task",native_agent_id="/root/worker")
        with self.assertRaisesRegex(ValueError,"32 KB"):
            self.op("update",task_id="task",native_agent_id="/root/worker",state="running",summary="",evidence=[{"kind":"test","reference":"x","summary":"x"*33000}])

    def test_unassigned_optional_task_can_be_cancelled_without_spawning(self):
        self.create("scout")
        self.complete("scout")
        task=self.create("optional")
        # Optionality is explicit at creation; create a separate optional task.
        self.op("create",task_id="not-needed",title="Optional",kind="scout",project_path="/p",workspace_path="/w",captain_intent="Optional investigation",spec="No longer needed",definition_of_done=["Result"],depends_on=[],covers=[],required=False)
        result=self.op("update",task_id="not-needed",native_agent_id="",state="cancelled",summary="Not needed by accepted scope",evidence=[])
        self.assertEqual(result["state"],"cancelled")

    def test_normalized_windows_workspace_collision_refused(self):
        self.create("first",kind="implementation")
        self.create("second",kind="implementation")
        with self.store._db() as db:
            for task_id,path in (("first",r"C:\Work\Repo"),("second",r"c:/work/repo/")):
                task=self.store._task(db,"work",task_id)
                task["workspace_path"]=path
                self.store._save(db,task)
        self.op("assign",task_id="first",native_agent_id="/root/first")
        with self.assertRaisesRegex(ValueError,"separate workspaces"):
            self.op("assign",task_id="second",native_agent_id="/root/second")


    def test_global_ancestor_workspace_reservation_survives_manager_expiry(self):
        self.create("first",kind="implementation")
        self.op("assign",task_id="first",native_agent_id="/root/first")
        self.now+=61
        second=self.store.claim("other-work","other-manager","test","other-claim",60)
        params={"task_id":"second","title":"Other task","kind":"implementation","project_path":"/canonical/project","workspace_path":"/worktrees/first/subdir","captain_intent":"Other request","spec":"Implementation","definition_of_done":["Result"],"depends_on":[],"covers":[],"required":True}
        self.store.mutate("other-work","other-manager",second["epoch"],"other-create","create",params)
        with self.assertRaisesRegex(ValueError,"separate workspaces"):
            self.store.mutate("other-work","other-manager",second["epoch"],"other-assign","assign",{"task_id":"second","native_agent_id":"/root/second"})

    def test_failed_bound_worker_keeps_global_workspace_reserved(self):
        self.create("first",kind="implementation")
        self.op("assign",task_id="first",native_agent_id="/root/first")
        self.op("update",task_id="first",native_agent_id="/root/first",state="failed",summary="Worker reported failure, endpoint not confirmed stopped",evidence=[])
        self.create("second",kind="implementation")
        with self.store._db() as db:
            task=self.store._task(db,"work","second")
            task["workspace_path"]="/worktrees/first"
            self.store._save(db,task)
        with self.assertRaisesRegex(ValueError,"separate workspaces"):
            self.op("assign",task_id="second",native_agent_id="/root/second")

    def test_integration_worker_reserves_workspace_against_new_implementation(self):
        self.create("integration",kind="integration")
        self.op("assign",task_id="integration",native_agent_id="/root/integrator")
        self.create("implementation",kind="implementation")
        with self.store._db() as db:
            task=self.store._task(db,"work","implementation")
            task["workspace_path"]="/worktrees/integration/child"
            self.store._save(db,task)
        with self.assertRaisesRegex(ValueError,"separate workspaces"):
            self.op("assign",task_id="implementation",native_agent_id="/root/implementer")

    def test_integration_overlap_refused_but_separate_worktree_allowed(self):
        self.create("first",kind="integration")
        self.op("assign",task_id="first",native_agent_id="/root/first")
        self.create("second",kind="integration")
        with self.store._db() as db:
            task=self.store._task(db,"work","second")
            task["workspace_path"]="/worktrees/first/child"
            self.store._save(db,task)
        with self.assertRaisesRegex(ValueError,"separate workspaces"):
            self.op("assign",task_id="second",native_agent_id="/root/second")
        self.create("separate",kind="integration")
        result=self.op("assign",task_id="separate",native_agent_id="/root/separate")
        self.assertEqual(result["state"],"assigned")

    def test_prospective_task_size_rejected_before_creation_or_update_commit(self):
        # This payload fits individual field/request limits but exceeds total record size.
        parameters={"task_id":"large","title":"Large","kind":"scout","project_path":"/p","workspace_path":"/w","captain_intent":"中"*8000,"spec":"s"*12000,"definition_of_done":["d"*1000 for _ in range(20)],"depends_on":[],"covers":[],"required":True}
        with self.assertRaisesRegex(ValueError,"40 KB"):
            self.op("create",**parameters)
        with self.assertRaisesRegex(ValueError,"Unknown task"):
            self.store.task("work","large")
        self.create()
        self.op("assign",task_id="task",native_agent_id="/root/worker")
        self.op("update",task_id="task",native_agent_id="/root/worker",state="running",summary="",evidence=[])
        before=self.store.task("work","task")
        # Existing brief can fit the40KB cap while new bounded evidence crosses it.
        with self.store._db() as db:
            task=self.store._task(db,"work","task")
            task["spec"]="s"*12000
            task["captain_intent"]="i"*8000
            self.store._save(db,task)
        before=self.store.task("work","task")
        evidence=[{"kind":"test","reference":"r"*1900,"summary":"e"*1900} for _ in range(7)]
        with self.assertRaisesRegex(ValueError,"40 KB"):
            self.op("update",task_id="task",native_agent_id="/root/worker",state="completed",summary="Would exceed complete record limit",evidence=evidence)
        self.assertEqual(self.store.task("work","task"),before)


class HttpTests(unittest.TestCase):
    def setUp(self):
        from conductor_coordination import coordination_server as server
        self.server=server
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token_file=Path(self.temp.name)/"token"
        self.token_file.write_text("test-only-token-"+"x"*40)
        self.token_file.chmod(0o600)
        self.environment=patch.dict(os.environ,{"CONDUCTOR_DB":str(Path(self.temp.name)/"tasks.sqlite3")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_authentication_required_and_no_secret_in_response(self):
        from starlette.testclient import TestClient
        with TestClient(self.server.create_http_app(self.token_file)) as client:
            result=client.post("/mcp",json={})
            self.assertEqual(result.status_code,401)
            self.assertNotIn(self.token_file.read_text(),result.text)
            self.assertEqual(client.get("/healthz").status_code,401)

    def test_authenticated_protocol_lists_tools_and_reports_honest_capabilities(self):
        from starlette.testclient import TestClient
        headers={"Authorization":"Bearer "+self.token_file.read_text(),"Accept":"application/json, text/event-stream"}
        with TestClient(self.server.create_http_app(self.token_file)) as client:
            response=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}})
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json()["result"]["tools"]),14)
            response=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"coordination_health","arguments":{}}})
            data=json.loads(response.json()["result"]["content"][0]["text"])
            self.assertFalse(data["native_spawn"])
            self.assertFalse(data["unattended_execution"])
            self.assertEqual(client.get("/mcp",headers=headers).status_code,405)

    def test_large_request_rejected_before_mcp(self):
        from starlette.testclient import TestClient
        with TestClient(self.server.create_http_app(self.token_file)) as client:
            response=client.post("/mcp",headers={"Authorization":"Bearer "+self.token_file.read_text()},content=b"x"*120001)
        self.assertEqual(response.status_code,413)

    def test_service_refuses_broadly_readable_token_file(self):
        self.token_file.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,"permissions"):
            self.server.create_http_app(self.token_file)


class ProxyTests(unittest.TestCase):
    def test_gateway_error_does_not_echo_upstream_body_or_token(self):
        import urllib.error
        from conductor_coordination import coordination_proxy as proxy
        with tempfile.TemporaryDirectory() as directory:
            token_file=Path(directory)/"token"
            token="unit-test-token-"+"x"*40
            token_file.write_text(token)
            class Opener:
                def open(self,*args,**kwargs):raise urllib.error.HTTPError("http://service/mcp",403,token,{},io.BytesIO(token.encode()))
            with patch.dict(os.environ,{"MCP_COORDINATION_URL":"http://service:8012/mcp","MCP_COORDINATION_TOKEN_FILE":str(token_file)}), patch.object(proxy.urllib.request,"build_opener",return_value=Opener()):
                with self.assertRaises(ToolError) as caught:proxy._remote("coordination_health",{})
            self.assertIn("403",str(caught.exception))
            self.assertNotIn(token,str(caught.exception))

    def test_redirects_disabled(self):
        from conductor_coordination.coordination_proxy import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,"",{},"http://other"))


if __name__ == "__main__":unittest.main()
