"""v121.4: the control layer (04-deployment/atta_control) over the existing runner, self-healer, record diagnostic
and Coolify hand-off.

These run the REAL maintenance.heal(), known_fixes, capability adapter, repair_actions, app_runner fleet machinery,
resilience report, record_diagnostic and llm_repair tool loop. What is replaced is only what needs a Docker host,
a Coolify server or the Anthropic API: the per-app start+check (as in test_v121_2_parallel_isolation.py), the
re-qualification a healing round triggers (pipeline.requalify, which would start every app), and the model's
responses (a scripted client that asks for real repair actions).
Run: python3 -m pytest -q tests/test_v121_4_control_layer.py
"""
import http.server, importlib, json, multiprocessing, os, shutil, socket, stat, subprocess, sys, tempfile, threading
import time, types, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEP = HERE / "04-deployment"
sys.path.insert(0, str(DEP))

from atta_control import gate, policy as pol, records, report, deploy  # noqa: E402
from atta_control.store import EventStore, append_chained, verify_chain, read_events, GENESIS  # noqa: E402
import record_diagnostic as rd  # noqa: E402

MODULES = ("app_runner", "system_watcher", "journey_author", "pipeline", "builds", "coolify_handoff", "coolify_provision",
           "maintenance", "rule_lifecycle", "known_fixes", "accounts", "intake", "repair_actions", "alerts",
           "capability_adapter", "llm_repair", "syntax_triage")
LAUNCHER_KEY = "v2|qualify|1 INSTALLED:NOT_EXECUTABLE"


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def make_app(lib: Path, name: str) -> Path:
    d = lib / name
    ui = d / ".ui-capability"
    (ui / "ui-bridge").mkdir(parents=True)
    (ui / "capability-port").mkdir()
    for f in ("skin.css", "skin.json", "deployment.json", "ui-bridge/proxy.js", "capability-port/port.js"):
        (ui / f).write_text("x\n")
    (ui / "run-ui.sh").write_text("#!/bin/sh\n")
    (d / "README.md").write_text(name + "\n")
    _git(d, "init", "-q"); _git(d, "add", "-A"); _git(d, "commit", "-qm", "init")
    (d / ".atta-intake.json").write_text(json.dumps({"app": name, "status": "READY", "qualification": "web"}))
    return d


def _write_log(path: Path, n: int) -> None:
    for i in range(n):
        append_chained(path, {"event": "E", "i": i})


def _proc_append(path: str, n: int, tag: str) -> None:
    for i in range(n):
        append_chained(Path(path), {"event": "P", "tag": tag, "i": i})


class _Env(unittest.TestCase):
    """A fresh APP_BUILDER_ROOT, fresh ATTa modules and a clean gate environment for every test."""
    ENV = ("APP_BUILDER_CONTROL_GATE", "APP_BUILDER_CONTROL_POLICY", "APP_BUILDER_CONTROL_MAX_EVIDENCE_AGE",
           "ALERT_WEBHOOK_URL", "ANTHROPIC_API_KEY")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._saved = {k: os.environ.get(k) for k in self.ENV + ("APP_BUILDER_ROOT", "APP_BUILDER_SESSION_SECRET")}
        for k in self.ENV:
            os.environ.pop(k, None)
        os.environ["APP_BUILDER_ROOT"] = str(self.root)
        os.environ["APP_BUILDER_SESSION_SECRET"] = "x"
        for m in MODULES:
            sys.modules.pop(m, None)
        with gate._OWN_LOCK:
            gate._OWN_CHANGES.clear()
        self.store = EventStore()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def audit(self, event=None):
        ev = self.store.events(self.store.AUDIT)
        return [e for e in ev if event is None or e.get("event") == event]

    def write_policy(self, rules, version="test-1"):
        p = self.root / "state" / "control" / "repair_policy.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": pol.SCHEMA, "version": version, "rules": rules}))
        return p


# ============================================================================================ store
class HashChainedStore(_Env):
    def test_chain_is_intact_and_counts(self):
        p = self.root / "log.jsonl"
        _write_log(p, 5)
        v = verify_chain(p)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["events"], 5)
        rows = list(read_events(p))
        self.assertEqual([r["seq"] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual(rows[0]["prev"], GENESIS)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_edit_delete_and_reorder_are_detected(self):
        for how in ("edit", "delete", "reorder"):
            p = self.root / f"{how}.jsonl"
            _write_log(p, 4)
            lines = p.read_bytes().split(b"\n")[:-1]
            if how == "edit":
                lines[1] = lines[1].replace(b'"i":1', b'"i":9')
            elif how == "delete":
                del lines[2]
            else:
                lines[1], lines[2] = lines[2], lines[1]
            p.write_bytes(b"\n".join(lines) + b"\n")
            v = verify_chain(p)
            self.assertFalse(v["ok"], how)
            self.assertTrue(v["problems"], how)

    def test_the_end_of_the_log_is_anchored(self):
        """A chain cannot see its own last line edited or lines cut off the end: the .head anchor can."""
        for how in ("edit_last", "truncate", "no_head"):
            p = self.root / f"{how}.jsonl"
            _write_log(p, 4)
            self.assertTrue(verify_chain(p)["ok"])
            lines = p.read_bytes().split(b"\n")[:-1]
            if how == "edit_last":
                lines[-1] = lines[-1].replace(b'"i":3', b'"i":7')
                p.write_bytes(b"\n".join(lines) + b"\n")
            elif how == "truncate":
                p.write_bytes(b"\n".join(lines[:2]) + b"\n")
            else:
                Path(str(p) + ".head").unlink()
            v = verify_chain(p)
            self.assertFalse(v["ok"], how)
            self.assertTrue(any(".head" in x for x in v["problems"]), (how, v["problems"]))

    def test_parallel_threads_and_processes_never_fork_the_chain(self):
        p = self.root / "par.jsonl"
        ts = [threading.Thread(target=_write_log, args=(p, 40)) for _ in range(6)]
        for t in ts: t.start()
        for t in ts: t.join()
        ctx = multiprocessing.get_context("fork")
        ps = [ctx.Process(target=_proc_append, args=(str(p), 40, f"p{i}")) for i in range(4)]
        for x in ps: x.start()
        for x in ps: x.join()
        v = verify_chain(p)
        self.assertTrue(v["ok"], v["problems"][:5])
        self.assertEqual(v["events"], 6 * 40 + 4 * 40)

    def test_torn_final_write_is_reported_and_the_log_keeps_working(self):
        p = self.root / "torn.jsonl"
        _write_log(p, 2)
        with p.open("ab") as f:
            f.write(b'{"event":"half')          # power lost mid-write
        append_chained(p, {"event": "after"})
        rows = list(read_events(p))
        self.assertEqual(rows[-1]["event"], "after")
        v = verify_chain(p)
        self.assertFalse(v["ok"])              # the damage is reported, never hidden
        self.assertTrue(any("not JSON" in x for x in v["problems"]))

    def test_long_values_are_stored_as_hash_and_length(self):
        big = "A" * 50_000
        row = append_chained(self.root / "b.jsonl", {"event": "E", "args": {"content": big}})
        self.assertEqual(row["args"]["content"]["len"], 50_000)
        self.assertNotIn("AAAA", (self.root / "b.jsonl").read_text())


# ============================================================================================ policy
class Policy(_Env):
    def test_no_file_is_empty_and_everything_escalates(self):
        p = pol.load_policy()
        self.assertEqual(p.rules, ())
        self.assertEqual(p.decide(LAUNCHER_KEY, "chmod_launcher").action, "ESCALATE")

    def test_explicit_rule_is_exact(self):
        self.write_policy([{"failure_key": LAUNCHER_KEY, "action": "chmod_launcher", "decision": "AUTO", "why": "mode bit"}])
        p = pol.load_policy()
        self.assertEqual(p.decide(LAUNCHER_KEY, "chmod_launcher").action, "AUTO")
        self.assertEqual(p.decide(LAUNCHER_KEY, "reinstall_app_overlay").action, "ESCALATE")
        self.assertEqual(p.decide("v2|qualify|1 INSTALLED:BAD_JSON", "chmod_launcher").action, "ESCALATE")

    def test_untrustworthy_policies_fail_to_load(self):
        bad = [
            [{"failure_key": LAUNCHER_KEY, "action": "chmod_launcher", "decision": "YES"}],
            [{"failure_key": LAUNCHER_KEY, "action": "rm_rf", "decision": "AUTO"}],
            [{"failure_key": "qualify:1 INSTALLED", "action": "chmod_launcher", "decision": "AUTO"}],
            [{"failure_key": "v9|qualify|X", "action": "chmod_launcher", "decision": "AUTO"}],
            [{"failure_key": "v2|build|UNKNOWN_FAILURE", "action": "requeue_build", "decision": "AUTO"}],
            [{"failure_key": LAUNCHER_KEY, "action": "chmod_launcher", "decision": "AUTO"}] * 2,
            [{"failure_key": LAUNCHER_KEY, "action": "chmod_launcher", "decision": "AUTO", "pattern": ".*"}],
        ]
        for rules in bad:
            self.write_policy(rules)
            with self.assertRaises(pol.PolicyError, msg=json.dumps(rules)):
                pol.load_policy()
        p = self.write_policy([])
        p.write_text("{not json")
        with self.assertRaises(pol.PolicyError):
            pol.load_policy()
        p.write_text(json.dumps({"schema": "OTHER", "version": "1", "rules": []}))
        with self.assertRaises(pol.PolicyError):
            pol.load_policy()

    def test_unknown_failure_may_be_escalated_but_never_executed(self):
        self.write_policy([{"failure_key": "v2|build|UNKNOWN_FAILURE", "action": "requeue_build", "decision": "ESCALATE"}])
        self.assertEqual(pol.load_policy().decide("v2|build|UNKNOWN_FAILURE", "requeue_build").action, "ESCALATE")

    def test_scaffold_proposes_only_repairs_that_ran_and_keeps_human_rules(self):
        existing = pol.RepairPolicy("h1", ({"failure_key": "v2|handoff|UNMAPPED", "action": "lookup_coolify_uuid",
                                             "decision": "AUTO", "why": "human"},))
        events = [
            {"event": "REPAIR_AUTHORIZATION", "failure_key": LAUNCHER_KEY, "fix_name": "chmod_launcher", "remediation_id": "rem-a"},
            {"event": "REPAIR_RETURNED", "remediation_id": "rem-a"},
            {"event": "REPAIR_AUTHORIZATION", "failure_key": "v2|qualify|2 APP_UP:X", "fix_name": "start_proxy", "remediation_id": "rem-b"},
            {"event": "REPAIR_EXCEPTION", "remediation_id": "rem-b"},
            {"event": "REPAIR_AUTHORIZATION", "failure_key": "v2|build|UNKNOWN_FAILURE", "fix_name": "requeue_build", "remediation_id": "rem-c"},
            {"event": "REPAIR_RETURNED", "remediation_id": "rem-c"},
            {"event": "REPAIR_AUTHORIZATION", "failure_key": "v2|handoff|UNMAPPED", "fix_name": "lookup_coolify_uuid", "remediation_id": "rem-d"},
            {"event": "REPAIR_RETURNED", "remediation_id": "rem-d"},
        ]
        p = pol.scaffold(iter(events), "s1", existing)
        m = p.mappings
        self.assertEqual(m[("v2|handoff|UNMAPPED", "lookup_coolify_uuid")], "AUTO")          # human decision kept
        self.assertEqual(m[(LAUNCHER_KEY, "chmod_launcher")], "APPROVAL_REQUIRED")          # proposed, not trusted
        self.assertNotIn(("v2|qualify|2 APP_UP:X", "start_proxy"), m)                       # raised: not proposed
        self.assertNotIn(("v2|build|UNKNOWN_FAILURE", "requeue_build"), m)                  # weak key: never proposed


# ============================================================================================ gate
class _GateEnv(_Env):
    """One app in the library and a NOT_QUALIFIED build whose result carries a complete, current identity."""
    def setUp(self):
        super().setUp()
        self.builds = importlib.import_module("builds")
        from resilience import app_revision, deployment_revision
        self.app_dir = make_app(self.root / "library", "demo")
        (self.app_dir / ".ui-capability" / "run-ui.sh").chmod(0o644)     # the failure: launcher not executable
        (self.root / "state" / "apps").mkdir(parents=True)
        (self.root / "state" / "apps" / "demo.json").write_text(json.dumps(
            {"app": "demo", "ui_dir": str(self.app_dir / ".ui-capability"), "app_dir": str(self.app_dir)}))
        self.dep_rev = deployment_revision(self.root)
        self.app_rev = app_revision(self.app_dir)
        rec = self.builds.create("owner", bundle="b.zip")
        self.result = {"app": "demo", "verdict": "BROKEN AT 1 INSTALLED — NOT_EXECUTABLE", "broken_at": "1 INSTALLED",
                       "code": "NOT_EXECUTABLE", "ts": time.time(),
                       "stages": {"1 INSTALLED": {"status": "FAIL", "code": "NOT_EXECUTABLE", "detail": "run-ui.sh mode"}},
                       "verification": {"fleet_run_id": "fleet-t", "run_id": "run-1-demo", "attempt": 1,
                                        "correlation_id": "c-1", "deployment_revision": self.dep_rev,
                                        "app_revision": self.app_rev, "finished_at": time.time(), "status": "CURRENT"}}
        self.rec = self.builds.update(rec["id"], state="NOT_QUALIFIED", qualification_results=[self.result])
        self.item = {"key": "1 INSTALLED:NOT_EXECUTABLE", "app": "demo", "detail": "run-ui.sh mode"}
        self.calls = []

    def runner(self, name, args):
        self.calls.append((name, args))
        return f"ran {name}"

    def ctx(self, rec=None):
        return {"layer": "qualify", "item": self.item, "rec": rec or self.rec, "tier": 1, "rule": "qualify.launcher_not_executable"}

    def auto_policy(self, decision="AUTO"):
        self.write_policy([{"failure_key": LAUNCHER_KEY, "action": "chmod_launcher", "decision": decision, "why": "t"}])

    def set_result(self, **verification):
        r = json.loads(json.dumps(self.result))
        r["verification"].update(verification)
        self.rec = self.builds.update(self.rec["id"], qualification_results=[r])


class Gate(_GateEnv):
    def test_default_mode_is_observe_and_never_blocks(self):
        self.assertEqual(gate.mode(), "observe")
        out = gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner)   # no policy at all
        self.assertEqual(out, "ran chmod_launcher")
        a = self.audit("REPAIR_AUTHORIZATION")[0]
        self.assertTrue(a["would_block"])
        self.assertEqual(a["failure_key"], LAUNCHER_KEY)
        self.assertEqual(a["identity"]["run_id"], "run-1-demo")
        self.assertEqual([e["event"] for e in self.audit()], ["REPAIR_AUTHORIZATION", "REPAIR_STARTED", "REPAIR_RETURNED"])

    def test_off_mode_runs_exactly_as_before_and_records_nothing(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "off"
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner), "ran chmod_launcher")
        self.assertEqual(self.audit(), [])

    def test_no_context_means_ungated(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.assertEqual(gate.execute(None, "chmod_launcher", {"app": "demo"}, self.runner), "ran chmod_launcher")

    def test_enforce_without_policy_refuses_and_never_calls_the_repair(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        with self.assertRaises(gate.RepairDenied) as cm:
            gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner)
        self.assertIn("ESCALATE", str(cm.exception))
        self.assertEqual(self.calls, [])
        self.assertEqual([e["event"] for e in self.audit()], ["REPAIR_AUTHORIZATION"])

    def test_enforce_with_auto_policy_and_current_evidence_runs(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.auto_policy()
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner), "ran chmod_launcher")
        ev = self.audit()
        self.assertEqual([e["event"] for e in ev], ["REPAIR_AUTHORIZATION", "REPAIR_STARTED", "REPAIR_RETURNED"])
        self.assertTrue(ev[0]["authorized"])
        self.assertEqual(ev[0]["policy_version"], "test-1")
        self.assertEqual(ev[2]["app_revision_after"], self.app_rev)
        self.assertTrue(self.store.verify()["ok"])

    def _denied(self, *must_contain, ctx=None):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        with self.assertRaises(gate.RepairDenied) as cm:
            gate.execute(ctx or self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner)
        for s in must_contain:
            self.assertIn(s, str(cm.exception))
        self.assertEqual(self.calls, [])

    def test_stale_and_future_evidence_is_refused(self):
        self.auto_policy()
        self.set_result(finished_at=time.time() - 7 * 3600)
        self._denied("stale")
        self.set_result(finished_at=time.time() + 3600)
        self._denied("future")
        os.environ["APP_BUILDER_CONTROL_MAX_EVIDENCE_AGE"] = str(8 * 3600)
        self.set_result(finished_at=time.time() - 7 * 3600)
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner), "ran chmod_launcher")

    def test_incomplete_identity_is_refused(self):
        self.auto_policy()
        for field, value, msg in (("run_id", None, "run id"), ("attempt", 0, "attempt"), ("correlation_id", "", "correlation"),
                                  ("app_revision", "missing", "app revision"), ("deployment_revision", "unknown", "deployment revision")):
            self.set_result(**{field: value})
            self._denied(msg)
            self.set_result(**{field: self.result["verification"][field]})
        # A result with no job identity at all (pre-v121.2, or a crash contained before the job had one): its
        # watcher timestamp still dates it, but nothing says whose job it was. Refused on identity.
        r = dict(self.result); r.pop("verification")
        self.rec = self.builds.update(self.rec["id"], qualification_results=[r])
        self._denied("missing run id", "missing correlation id", "app revision missing")
        # And a failure the build record holds no result for at all.
        self.rec = self.builds.update(self.rec["id"], qualification_results=[])
        self._denied("missing run id", "evidence timestamp missing")

    def test_app_changed_since_the_failure_is_refused(self):
        self.auto_policy()
        (self.app_dir / ".ui-capability" / "skin.css").write_text("changed by someone else\n")
        self._denied("the app changed")

    def test_deployment_changed_since_the_failure_is_refused(self):
        self.auto_policy()
        self.set_result(deployment_revision="code:0000000000000000000000ff")
        self._denied("deployment changed")

    def test_record_changed_after_the_healer_read_it_is_refused(self):
        self.auto_policy()
        stale_view = self.rec
        self.set_result(run_id="run-2-demo", correlation_id="c-2")      # a re-qualification landed meanwhile
        self._denied("changed after the self-healer read it", ctx=self.ctx(stale_view))

    def test_a_change_made_by_the_gates_own_earlier_repair_is_accounted_for(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.write_policy([{"failure_key": LAUNCHER_KEY, "action": "write_overlay_file", "decision": "AUTO"},
                           {"failure_key": LAUNCHER_KEY, "action": "chmod_launcher", "decision": "AUTO"}])
        skin = self.app_dir / ".ui-capability" / "skin.css"

        def real_effect(name, args):
            if name == "write_overlay_file":
                skin.write_text("repaired\n")
            return self.runner(name, args)
        gate.execute(self.ctx(), "write_overlay_file", {"app": "demo", "path": "skin.css", "content": "repaired\n"}, real_effect)
        # The overlay (and so the app revision) changed, but by the repair the gate itself recorded: allowed.
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, real_effect), "ran chmod_launcher")
        skin.write_text("changed by someone else\n")
        self.calls.clear()
        self._denied("the app changed")

    def test_approval_is_bound_single_use_and_expires(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.auto_policy("APPROVAL_REQUIRED")
        with self.assertRaises(gate.RepairDenied) as cm:
            gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner)
        rem = self.audit("REPAIR_AUTHORIZATION")[-1]["remediation_id"]
        self.assertIn(rem, str(cm.exception))                          # the refusal names what to approve
        with self.assertRaises(ValueError):
            gate.approve(self.store, "rem-bogus", "sam")
        with self.assertRaises(ValueError):
            gate.approve(self.store, rem, "  ")
        with self.assertRaises(ValueError):
            gate.approve(self.store, rem, "sam", ttl=0)
        with self.assertRaises(ValueError):                              # never asked for: nothing to approve
            gate.approve(self.store, "rem-" + "0" * 20, "sam")
        e = gate.approve(self.store, rem, "sam", note="checked the launcher")
        self.assertEqual((e["covers"]["fix_name"], e["covers"]["failure_key"], e["covers"]["app"]),
                         ("chmod_launcher", LAUNCHER_KEY, "demo"))
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner), "ran chmod_launcher")
        self.calls.clear()
        self._denied("human approval required")                        # consumed: one approval, one repair
        # A different repair (other args) is a different remediation: that approval never covered it.
        gate.approve(self.store, rem, "sam", ttl=1)
        time.sleep(1.1)
        self._denied("human approval required")                        # expired

    def test_untrusted_policy_blocks_in_enforce_but_not_in_observe(self):
        p = self.write_policy([])
        p.write_text("{broken")
        os.environ["APP_BUILDER_CONTROL_GATE"] = "observe"
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner), "ran chmod_launcher")
        self.calls.clear()
        self._denied("policy could not be trusted")

    def test_unrecognised_mode_fails_closed(self):
        self.auto_policy()
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforse"
        self.assertEqual(gate.mode(), "enforce")
        with self.assertRaises(gate.RepairDenied) as cm:
            gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner)
        self.assertIn("not one of", str(cm.exception))

    def test_repair_exception_is_audited_and_re_raised(self):
        self.auto_policy()
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"

        def boom(name, args):
            raise RuntimeError("disk full")
        with self.assertRaises(RuntimeError):
            gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, boom)
        self.assertEqual(self.audit()[-1]["event"], "REPAIR_EXCEPTION")
        self.assertEqual(self.audit()[-1]["exception"], "disk full")

    def test_unwritable_audit_blocks_enforce_but_not_observe(self):
        self.auto_policy()
        bad = EventStore(self.root / "not-a-dir")
        (self.root / "not-a-dir").write_text("a file where the log folder should be")
        os.environ["APP_BUILDER_CONTROL_GATE"] = "observe"
        self.assertEqual(gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner, store=bad), "ran chmod_launcher")
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.calls.clear()
        with self.assertRaises(gate.RepairDenied) as cm:
            gate.execute(self.ctx(), "chmod_launcher", {"app": "demo"}, self.runner, store=bad)
        self.assertIn("could not decide or record", str(cm.exception))
        self.assertEqual(self.calls, [])

    def test_secrets_in_repair_arguments_and_results_are_redacted_in_the_audit(self):
        def leaky(name, args):
            return "wrote DB_PASSWORD=hunter2hunter2 to the recipe"
        gate.execute(self.ctx(), "set_run_recipe", {"app": "demo", "recipe_json": '{"env": {"DB_PASSWORD": "hunter2hunter2"}}'}, leaky)
        text = self.store.path(self.store.AUDIT).read_text()
        self.assertNotIn("hunter2", text)
        self.assertIn("redacted-by-atta", text)
        # The remediation id is computed from the real arguments: redaction never changes what is approved.
        a = self.audit("REPAIR_AUTHORIZATION")[0]
        self.assertEqual(a["remediation_id"], gate.remediation_id("qualify", self.rec["id"], LAUNCHER_KEY, "demo", "set_run_recipe",
                         {"app": "demo", "recipe_json": '{"env": {"DB_PASSWORD": "hunter2hunter2"}}'}))

    def test_a_qualify_failure_with_no_app_is_dated_by_the_build(self):
        item = {"key": "TARGETS:NO_TARGETS", "detail": ""}
        ev = gate.evidence_for("qualify", item, self.rec)
        entered = next(h["at"] for h in reversed(self.rec["history"]) if h["state"] == "NOT_QUALIFIED")
        self.assertEqual(ev["timestamp"], entered)
        self.assertEqual(gate.identity_for("qualify", item, self.rec).kind, "build")

    def test_the_memory_of_own_repairs_is_bounded(self):
        for i in range(gate._OWN_MAX + 50):
            gate._note_own_change("b", f"run-{i}", "app:x")
        self.assertEqual(len(gate._OWN_CHANGES), gate._OWN_MAX)
        self.assertFalse(gate._explained_by_own_repair("b", "run-0", "app:x"))        # oldest dropped
        self.assertTrue(gate._explained_by_own_repair("b", f"run-{gate._OWN_MAX + 49}", "app:x"))

    def test_remediation_id_is_deterministic_and_specific(self):
        a = gate.remediation_id("qualify", "b1", LAUNCHER_KEY, "demo", "chmod_launcher", {"app": "demo"})
        self.assertEqual(a, gate.remediation_id("qualify", "b1", LAUNCHER_KEY, "demo", "chmod_launcher", {"app": "demo"}))
        self.assertNotEqual(a, gate.remediation_id("qualify", "b2", LAUNCHER_KEY, "demo", "chmod_launcher", {"app": "demo"}))
        self.assertNotEqual(a, gate.remediation_id("qualify", "b1", LAUNCHER_KEY, "other", "chmod_launcher", {"app": "other"}))
        self.assertRegex(a, r"^rem-[0-9a-f]{20}$")

    def test_build_layer_evidence_is_the_time_the_build_failed(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        rec = self.builds.update(self.rec["id"], state="FAILED", error="['git', 'clone'] timed out after 600s")
        time.sleep(0.01)
        rec2 = self.builds.update(rec["id"], healing={"build": {"status": "HEALING"}})   # the healer saving progress
        item = {"key": "['git', 'clone'] timed out after #s", "detail": rec["error"]}
        e1 = gate.evidence_for("build", item, rec)
        self.assertEqual(e1, gate.evidence_for("build", item, rec2))       # not new evidence
        self.write_policy([{"failure_key": "v2|build|GIT_TIMEOUT", "action": "requeue_build", "decision": "AUTO"}])
        out = gate.execute({"layer": "build", "item": item, "rec": rec, "tier": 1, "rule": "build.git_timeout"},
                           "requeue_build", {"build_id": rec["id"]}, self.runner)
        self.assertEqual(out, "ran requeue_build")


# ============================================================================================ maintenance, for real
class SelfHealerThroughTheGate(_GateEnv):
    """maintenance.heal() on a real NOT_QUALIFIED build: tier 1's built-in rule (chmod_launcher), tier 2's
    capability adapter and the tier-3 script probe all reach repair_actions through the gate."""
    def setUp(self):
        super().setUp()
        self.m = importlib.import_module("maintenance")
        self.verified = []

        def fake_verify(bid, layer):
            self.verified.append((bid, layer))
            return os.access(self.app_dir / ".ui-capability" / "run-ui.sh", os.X_OK)
        self.m._verify = fake_verify
        self.launcher = self.app_dir / ".ui-capability" / "run-ui.sh"

    def test_observe_heals_exactly_as_v121_3_and_audits_it(self):
        status = self.m.heal(self.rec["id"], "qualify")
        self.assertEqual(status, "HEALED")
        self.assertTrue(os.access(self.launcher, os.X_OK))
        h = self.builds.get(self.rec["id"])["healing"]["qualify"]
        self.assertEqual(h["chain"][0]["rule"], "qualify.launcher_not_executable")
        self.assertEqual(h["chain"][0]["outcome"], "APPLIED")
        a = self.audit("REPAIR_AUTHORIZATION")[0]
        self.assertEqual((a["tier"], a["rule"], a["fix_name"], a["mode"]), (1, "qualify.launcher_not_executable", "chmod_launcher", "observe"))
        self.assertTrue(a["would_block"])        # enforce would have refused it: there is no policy yet

    def test_enforce_without_policy_touches_nothing_and_escalates_to_a_person(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        status = self.m.heal(self.rec["id"], "qualify")
        self.assertEqual(status, "ESCALATED")
        self.assertFalse(os.access(self.launcher, os.X_OK))           # nothing was changed
        self.assertEqual(self.verified, [])                           # nothing applied, nothing to verify
        chain = self.builds.get(self.rec["id"])["healing"]["qualify"]["chain"]
        t1 = chain[0]
        self.assertEqual(t1["outcome"], "FIX_FAILED")
        self.assertTrue(t1["actions"][0]["error"])
        self.assertIn("RepairDenied", t1["actions"][0]["result"])
        self.assertEqual(chain[-1]["tier_name"], "human")
        self.assertEqual(self.audit("REPAIR_STARTED"), [])
        tiers = {e["tier"] for e in self.audit("REPAIR_AUTHORIZATION")}
        self.assertEqual(tiers, {1, 2, 3})                            # every tier asked, none was let through
        self.assertTrue(list((self.root / "state" / "alerts").glob("*.json")))

    def test_enforce_with_an_explicit_rule_heals(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        self.auto_policy()
        self.assertEqual(self.m.heal(self.rec["id"], "qualify"), "HEALED")
        self.assertTrue(os.access(self.launcher, os.X_OK))
        self.assertEqual([e["event"] for e in self.audit()], ["REPAIR_AUTHORIZATION", "REPAIR_STARTED", "REPAIR_RETURNED"])

    def test_run_actions_without_context_is_unchanged(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        ok, done = self.m._run_actions([("chmod_launcher", {"app": "demo"})])
        self.assertTrue(ok, done)
        self.assertTrue(os.access(self.launcher, os.X_OK))


class LLMToolCallsThroughTheGate(_GateEnv):
    """llm_repair.attempt() with a scripted model: a mutating tool call goes through the gate; a read-only one
    does not. In enforce mode the model is told the action was refused and nothing changes."""
    def setUp(self):
        super().setUp()
        self.llm = importlib.import_module("llm_repair")
        calls = [[("read_file", {"path": "state/apps/demo.json"})], [("chmod_launcher", {"app": "demo"})], []]

        class Block:
            def __init__(self, **kw): self.__dict__.update(kw)

        class Messages:
            def __init__(self): self.n = 0
            def create(self, **kw):
                batch = calls[min(self.n, len(calls) - 1)]; self.n += 1
                content = [Block(type="tool_use", id=f"t{self.n}{i}", name=n, input=a) for i, (n, a) in enumerate(batch)]
                content.append(Block(type="text", text="done"))
                return Block(stop_reason="tool_use" if batch else "end_turn", content=content)

        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda: Block(beta=Block(messages=Messages()))
        self._anth = sys.modules.get("anthropic")
        sys.modules["anthropic"] = fake
        self.llm.available = lambda: (True, "")
        self.llm._budget_ok = lambda: True

    def tearDown(self):
        if self._anth is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = self._anth
        super().tearDown()

    def test_enforce_refuses_the_models_mutating_call(self):
        os.environ["APP_BUILDER_CONTROL_GATE"] = "enforce"
        r = self.llm.attempt("qualify", self.item, self.rec, [])
        self.assertFalse(r["applied"])
        self.assertEqual([a["name"] for a in r["actions"]], ["chmod_launcher"])
        self.assertIn("repair gate refused", r["actions"][0]["result"])
        self.assertFalse(os.access(self.app_dir / ".ui-capability" / "run-ui.sh", os.X_OK))
        auth = self.audit("REPAIR_AUTHORIZATION")
        self.assertEqual([(e["fix_name"], e["rule"], e["tier"]) for e in auth], [("chmod_launcher", "llm", 3)])  # read_file ungated

    def test_observe_lets_the_model_repair_and_records_it(self):
        r = self.llm.attempt("qualify", self.item, self.rec, [])
        self.assertTrue(r["applied"])
        self.assertTrue(os.access(self.app_dir / ".ui-capability" / "run-ui.sh", os.X_OK))
        self.assertEqual([e["event"] for e in self.audit()], ["REPAIR_AUTHORIZATION", "REPAIR_STARTED", "REPAIR_RETURNED"])


# ============================================================================================ records, diagnostic, report
class _Fleet(_Env):
    """A real app_runner.qualify_all() fleet run over four apps, with the per-app start+check replaced as in
    test_v121_2_parallel_isolation.py: one passes, one fails at stage 6 with browser evidence, one fails to
    start, one crashes (a contained WORKER/RUNNER exception)."""
    def setUp(self):
        super().setUp()
        self.ar = importlib.import_module("app_runner")
        self.w = importlib.import_module("system_watcher")
        self.ar.START_FREE_MEMORY = 0.0
        self.ar.START_FREE_DISK_GB = 0.0
        self.ar._POOL_POLL = 0.05
        self.ar.sh = lambda cmd, timeout=120, env=None, cwd=None: (0, "")
        self.ar.down = lambda app, prune=None: "stopped"
        for n in ("good", "badui", "nostart", "crashy"):
            make_app(self.ar.LIB, n)
        ar, w = self.ar, self.w

        def fake_qualify(app, keep=None, log=print, run_id=None, deployment_revision_id=None):
            ident = ar._job_identity(app)
            st = {k: w.stage() for k in ("1 INSTALLED", "2 APP_UP", "3 PROXY_UP", "4 SKIN", "5 HOOK", "6 CLEAN")}
            if app == "crashy":
                raise RuntimeError("runner blew up")
            if app == "nostart":
                r = w.fail("nostart", {"1 INSTALLED": w.stage()}, "2 APP_UP", "RUNNER_EXHAUSTED",
                           "COPY plugins/_official ./plugins/_official: not found")
                r["runner"] = {"started": False, "attempts": [{"n": 1, "rule": "build.copy", "outcome": "FAIL"}]}
            elif app == "badui":
                ev = w.EVIDENCE_DIR / "badui" / f"{ident['run_id']}-a{ident['attempt']}"
                ev.mkdir(parents=True)
                (ev / "browser.json").write_text(json.dumps({"app": "badui", "stage": "6 CLEAN", **ident}))
                (ev / "page.html").write_text("<html>502 Bad Gateway</html>")
                st["6 CLEAN"] = w.stage("FAIL", "BROWSER_HTTP", "502 Bad Gateway from the upstream")
                st["6 CLEAN"]["evidence"] = {"dir": str(ev), "browser": str(ev / "browser.json"), **ident}
                r = w.result("badui", st)
                r["runner"] = {"started": True, "profile": "web", "attempts": []}
            else:
                r = w.result(app, st)
                r["runner"] = {"started": True, "profile": "web", "attempts": []}
            ar._write_result(app, r)
            return r
        self.ar.qualify_app = fake_qualify
        self.results = self.ar.qualify_all(["good", "badui", "nostart", "crashy"], log=lambda m: None)
        self.fleet = records.fleet_report()


class RecordsAndDiagnostic(_Fleet):
    def test_the_fleet_is_what_the_runner_reported(self):
        states = {r["app"]: r["state"] for r in self.fleet["apps"]}
        self.assertEqual(states["good"], "QUALIFIED")
        self.assertNotEqual(states["badui"], "QUALIFIED")
        self.assertEqual(states["crashy"], "INCONCLUSIVE")

    def test_export_writes_one_record_per_failed_app_with_top_level_identity(self):
        out = records.export_failures()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["summary"]["CREATED"], 3)
        root = Path(out["records_root"])
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["badui", "crashy", "nostart"])   # QUALIFIED not exported
        (p,) = list((root / "badui").glob("*.json"))
        rec = json.loads(p.read_text())
        v = next(r for r in self.results if r["app"] == "badui")["verification"]
        self.assertEqual((rec["app"], rec["run_id"], rec["attempt"], rec["revision"]),
                         ("badui", v["run_id"], v["attempt"], v["deployment_revision"]))
        self.assertEqual(p.name, f"{v['run_id']}-a{v['attempt']}.json")
        self.assertEqual({Path(e["path"]).name for e in rec["evidence_files"]}, {"browser.json", "page.html"})
        self.assertEqual(rec["stages_reached"], ["1 INSTALLED", "2 APP_UP", "3 PROXY_UP", "4 SKIN", "5 HOOK"])

    def test_export_is_write_once_and_idempotent(self):
        records.export_failures()
        again = records.export_failures()
        self.assertEqual(again["summary"]["SAME"], 3)
        self.assertEqual(again["summary"]["CREATED"], 0)
        (p,) = list((Path(again["records_root"]) / "nostart").glob("*.json"))
        rec = json.loads(p.read_text()); rec["code"] = "EDITED"; p.write_text(json.dumps(rec))
        third = records.export_failures()
        self.assertFalse(third["ok"])
        self.assertEqual(third["summary"]["CONFLICT"], 1)
        self.assertEqual(json.loads(p.read_text())["code"], "EDITED")          # never overwritten

    def test_a_later_run_of_one_app_is_not_this_fleets_record(self):
        res = self.ar.RESULTS / "nostart.json"
        r = json.loads(res.read_text()); r["verification"]["fleet_run_id"] = "fleet-later"; res.write_text(json.dumps(r))
        out = records.export_failures(self.fleet["fleet_run_id"])
        row = next(x for x in out["apps"] if x["app"] == "nostart")
        self.assertEqual(row["outcome"], "SUPERSEDED")

    def test_diagnose_reads_the_exported_tree_read_only_and_correctly(self):
        records.export_failures()
        out_path = self.root / "reports" / "diag.json"
        d = records.diagnose(out_path=out_path)
        self.assertTrue(d["ok"], d)
        self.assertTrue(d["inputs_unchanged"])
        self.assertEqual(d["expected_count"], 3)
        self.assertEqual(d["processed_count"], 3)
        by = {f["app"]: f for f in d["findings"]}
        self.assertEqual(by["nostart"]["classification"], "BUILD_CONTEXT_SOURCE_MISSING")
        self.assertEqual(by["badui"]["classification"], "UPSTREAM_502")      # its own browser.json agrees: no mismatch
        for f in d["findings"]:
            self.assertEqual(f["root_cause"]["value"], "UNKNOWN")
            self.assertIn(f["allowed_action"], rd.ALLOWED_ACTIONS)
            self.assertIsNone(f["remediation_id"])
        self.assertTrue(out_path.is_file())
        with self.assertRaises(ValueError):
            records.diagnose(out_path=Path(d["records_root"]) / "inside.json")

    def test_record_diagnostic_run_reads_the_export_as_documented(self):
        records.export_failures()
        root = records.failures_root() / records.segment(self.fleet["fleet_run_id"])
        out = rd.run(root, ["badui", "crashy", "nostart"])
        self.assertEqual(out["missing_apps"], [])
        self.assertEqual(out["processed_count"], 3)

    def test_ids_used_as_paths_keep_their_case_and_refuse_path_syntax(self):
        self.assertEqual(records.segment("fleet-20260929T061331Z-9b37b3"), "fleet-20260929T061331Z-9b37b3")
        for bad in ("../etc", "a/b", "", None, ".hidden", "x..y", "a b"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                records.segment(bad)
        out = records.export_failures()
        self.assertTrue(out["records_root"].endswith(self.fleet["fleet_run_id"]))

    def test_observations_are_recorded_once(self):
        a = records.record_observations(self.fleet)
        self.assertGreater(a["added"], 0)
        b = records.record_observations(self.fleet)
        self.assertEqual(b["added"], 0)
        self.assertEqual(b["already_recorded"], a["added"])
        stages = {(e["app"], e["stage"]) for e in self.store.events(self.store.OBSERVATIONS)}
        self.assertIn(("badui", "6 CLEAN"), stages)

    def test_control_report_uses_only_records_and_leaves_run_report_alone(self):
        rr = [self.ar.RUNNER / "RUN-REPORT.json", self.ar.RUNNER / "RUN-REPORT.md"]
        before = [p.read_bytes() for p in rr]
        records.export_failures()
        rep = report.build(diagnostic=records.diagnose())
        where = report.write(rep)
        self.assertEqual(before, [p.read_bytes() for p in rr])
        self.assertTrue(where.is_file())
        self.assertTrue((self.ar.RUNNER / "control" / "CONTROL-REPORT.md").is_file())
        by = {a["app"]: a for a in rep["apps"]}
        self.assertEqual(by["badui"]["diagnostic"]["classification"], "UPSTREAM_502")
        self.assertEqual(by["good"]["deployment"]["state"], "NOT_REQUESTED")
        self.assertEqual(rep["execution"]["effective_cap"], 4)
        self.assertEqual(rep["execution"]["configured_cap"], "unset (all apps at once)")
        self.assertTrue(rep["control_logs"]["ok"])
        md = (self.ar.RUNNER / "control" / "CONTROL-REPORT.md").read_text()
        self.assertIn("| badui |", md)
        # A result that is gone is reported as unknown, not reconstructed.
        (self.ar.RESULTS / "good.json").unlink()
        rep2 = report.build()
        self.assertEqual({a["app"]: a for a in rep2["apps"]}["good"]["stages_reached"], "UNKNOWN")


class IdentityCollision(_Env):
    def test_a_colliding_name_never_gets_the_other_apps_record(self):
        ar = importlib.import_module("app_runner"); w = importlib.import_module("system_watcher")
        ar.START_FREE_MEMORY = 0.0; ar.START_FREE_DISK_GB = 0.0; ar._POOL_POLL = 0.05
        ar.sh = lambda cmd, timeout=120, env=None, cwd=None: (0, ""); ar.down = lambda app, prune=None: "stopped"
        make_app(ar.LIB, "my-app")

        def failing(app, keep=None, log=print, run_id=None, deployment_revision_id=None):
            r = w.fail(ar.safe_id(app), {"1 INSTALLED": w.stage()}, "2 APP_UP", "RUNNER_EXHAUSTED", "no way to start it")
            r["runner"] = {"started": False, "attempts": []}
            ar._write_result(app, r)
            return r
        ar.qualify_app = failing
        ar.qualify_all(["my-app", "My App"], log=lambda m: None)
        out = records.export_failures()
        by = {x["app"]: x for x in out["apps"]}
        self.assertEqual(by["my-app"]["outcome"], "CREATED")
        self.assertEqual(by["My App"]["outcome"], "NOT_RUN")
        d = records.diagnose()
        self.assertEqual((d["expected_count"], d["processed_count"], d["missing_apps"]), (1, 1, []))
        rep = {a["app"]: a for a in report.build(diagnostic=d)["apps"]}
        self.assertEqual(rep["My App"]["stages_reached"], "UNKNOWN")
        self.assertIn("same id", rep["My App"]["result_source"])
        self.assertEqual(rep["my-app"]["failure_stage"], "2 APP_UP")


class RecordDiagnosticIdentityRegression(_Env):
    def test_nested_identity_never_overrides_the_records_own(self):
        """v121.3 read identity last-wins over a depth-first walk: a nested invalidated attempt overrode the
        record's own attempt, and the record's own browser evidence then read as EVIDENCE_IDENTITY_MISMATCH."""
        d = self.root / "memos"; d.mkdir()
        p = d / "r.json"
        p.write_text(json.dumps({"app": "memos", "run_id": "run-1", "revision": "code:a", "attempt": 2,
                                 "result": {"verification": {"attempt": 2, "invalidated_attempts": [{"attempt": 1}]}}}))
        b = self.root / "browser.json"
        b.write_text(json.dumps({"app": "memos", "run_id": "run-1", "attempt": 2}))
        self.assertEqual(rd._identity(json.loads(p.read_text()))["attempt"], 2)
        self.assertNotEqual(rd.classify(p, [b])["classification"], "EVIDENCE_IDENTITY_MISMATCH")
        b.write_text(json.dumps({"app": "memos", "run_id": "run-OTHER", "attempt": 2}))
        self.assertEqual(rd.classify(p, [b])["classification"], "EVIDENCE_IDENTITY_MISMATCH")


# ============================================================================================ deployments
class _Handler(http.server.BaseHTTPRequestHandler):
    code = 200
    def do_GET(self):
        self.send_response(self.code); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<html>ok</html>")
    def log_message(self, *a):
        pass


def _serve(code):
    h = type("H", (_Handler,), {"code": code})
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


class Deployments(_Env):
    def setUp(self):
        super().setUp()
        self.builds = importlib.import_module("builds")
        self.ok, self.err = _serve(200), _serve(503)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]
        rec = self.builds.create("o")
        self.builds.update(rec["id"], state="QUALIFIED", coolify={"status": "RETRYING", "attempts": 2, "apps": {
            "up": {"status": "ACCEPTED", "uuid": "u1", "url": f"http://127.0.0.1:{self.ok.server_port},https://alt"},
            "fivehundred": {"status": "ACCEPTED", "uuid": "u2", "url": f"http://127.0.0.1:{self.err.server_port}"},
            "down": {"status": "ACCEPTED", "uuid": "u3", "url": f"http://127.0.0.1:{closed}"},
            "nourl": {"status": "ACCEPTED", "uuid": "u4"},
            "unmapped": {"status": "UNMAPPED", "detail": "no entry"},
            "erroring": {"status": "ERROR", "detail": "HTTP 500: x"},
            "waiting": {"status": "PENDING"}}})
        rec2 = self.builds.create("o")
        self.builds.update(rec2["id"], state="PARTIALLY_QUALIFIED", qualified_apps=["halfway"])

    def tearDown(self):
        self.ok.shutdown(); self.err.shutdown()
        super().tearDown()

    def test_records_alone_never_claim_live(self):
        out = deploy.verify(probe=False)
        by = {r["app"]: r["state"] for r in out["apps"]}
        self.assertEqual(by["up"], "DISPATCHED")
        self.assertEqual(by["nourl"], "DISPATCHED")
        self.assertEqual(by["unmapped"], "BLOCKED")
        self.assertEqual(by["erroring"], "DISPATCH_ERROR")
        self.assertEqual(by["waiting"], "PENDING")
        self.assertEqual(by["halfway"], "NOT_REQUESTED")
        self.assertNotIn("LIVE_HTTP_OK", by.values())

    def test_probe_distinguishes_live_from_broken(self):
        out = deploy.verify(probe=True)
        by = {r["app"]: r for r in out["apps"]}
        self.assertEqual(by["up"]["state"], "LIVE_HTTP_OK")
        self.assertEqual(by["up"]["url"], f"http://127.0.0.1:{self.ok.server_port}")   # first of Coolify's list
        self.assertEqual(by["fivehundred"]["state"], "LIVE_FAILED")
        self.assertEqual(by["down"]["state"], "LIVE_FAILED")
        self.assertEqual(by["nourl"]["state"], "UNKNOWN")
        ev = self.store.events(self.store.RUN_EVENTS)
        self.assertEqual(ev[-1]["event"], "DEPLOYMENTS_VERIFIED")

    def test_not_configured_blocks_every_app(self):
        rec = self.builds.create("o")
        self.builds.update(rec["id"], state="QUALIFIED", coolify={"status": "BLOCKED_NOT_CONFIGURED", "apps": {"x": {"status": "PENDING"}}})
        by = {r["app"]: r for r in deploy.verify()["apps"]}
        self.assertEqual(by["x"]["state"], "BLOCKED")


# ============================================================================================ command line
class CommandLine(_Fleet):
    def run_cli(self, *args):
        env = {**os.environ, "APP_BUILDER_ROOT": str(self.root)}
        return subprocess.run([sys.executable, "-m", "atta_control", *args], cwd=DEP, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_after_run_does_everything_and_is_repeatable(self):
        r = self.run_cli("after-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["export"]["CREATED"], 3)
        self.assertEqual(out["diagnostic"]["processed_count"], 3)
        self.assertTrue(out["control_logs_ok"])
        run_dir = self.ar.RUNNER / "control" / "runs" / self.fleet["fleet_run_id"]
        self.assertTrue((run_dir / "record-diagnostic.json").is_file())
        self.assertTrue((run_dir / "control-report.md").is_file())
        r2 = self.run_cli("after-run")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(json.loads(r2.stdout)["export"]["SAME"], 3)

    def test_status_verify_policy_and_approve(self):
        self.assertEqual(self.run_cli("status").returncode, 0)
        self.assertEqual(self.run_cli("verify-logs").returncode, 0)
        self.assertEqual(self.run_cli("policy", "show").returncode, 0)
        bad = self.run_cli("approve", "nonsense", "--by", "sam")
        self.assertEqual(bad.returncode, 2)
        unseen = self.run_cli("approve", "rem-" + "a" * 20, "--by", "sam")
        self.assertEqual(unseen.returncode, 2)
        self.assertIn("has been asked for", unseen.stderr)
        self.store.append_audit({"event": "REPAIR_AUTHORIZATION", "remediation_id": "rem-" + "a" * 20, "layer": "qualify",
                                 "failure_key": LAUNCHER_KEY, "fix_name": "chmod_launcher", "args": {"app": "x"},
                                 "identity": {"app": "x", "build_id": "b-1"}})
        ok = self.run_cli("approve", "rem-" + "a" * 20, "--by", "sam")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("chmod_launcher", ok.stdout)
        # Break a log: status and verify-logs say so.
        p = self.store.path(self.store.APPROVALS)
        p.write_text(p.read_text().replace('"sam"', '"eve"'))
        self.assertEqual(self.run_cli("verify-logs").returncode, 1)
        self.assertEqual(self.run_cli("status").returncode, 1)

    def test_policy_scaffold_writes_reviewable_rules(self):
        self.store.append_audit({"event": "REPAIR_AUTHORIZATION", "failure_key": LAUNCHER_KEY, "fix_name": "chmod_launcher",
                                 "remediation_id": "rem-x"})
        self.store.append_audit({"event": "REPAIR_RETURNED", "remediation_id": "rem-x"})
        r = self.run_cli("policy", "scaffold", "--write", "--version", "v1")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = pol.load_policy()
        self.assertEqual(p.version, "v1")
        self.assertEqual(p.mappings, {(LAUNCHER_KEY, "chmod_launcher"): "APPROVAL_REQUIRED"})
        self.assertEqual(stat.S_IMODE(pol.policy_path().stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
