"""Installation state machine (Stage 01B §8/§9). Resumable, recorded, rollback-capable.

DISCOVER -> VALIDATE -> PLAN -> [AWAITING_OPERATOR_ACTION] -> PROVISION -> CONFIGURE -> START_SERVICES -> QUALIFY ->
ACTIVATE -> READY, plus BLOCKED, FAILED, ROLLING_BACK, ROLLED_BACK. Every transition is appended to
install_transitions.jsonl; completed safe steps are not repeated on resume while their inputs are unchanged."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from backend.contracts import canonical_hash, iso_utc, new_id
from backend.settings.loader import REPO_ROOT

from . import deps, discovery, manifest as mf, models as mdl, profiles, resolver

TRANSITIONS = {
    "NEW": {"DISCOVER"},
    "DISCOVER": {"VALIDATE", "BLOCKED", "FAILED"},
    "VALIDATE": {"PLAN", "BLOCKED", "FAILED"},
    "PLAN": {"AWAITING_OPERATOR_ACTION", "PROVISION", "BLOCKED", "FAILED"},
    "AWAITING_OPERATOR_ACTION": {"PROVISION", "BLOCKED", "FAILED", "DISCOVER"},
    "PROVISION": {"CONFIGURE", "FAILED", "ROLLING_BACK"},
    "CONFIGURE": {"START_SERVICES", "FAILED", "ROLLING_BACK"},
    "START_SERVICES": {"QUALIFY", "FAILED", "ROLLING_BACK"},
    "QUALIFY": {"ACTIVATE", "FAILED", "BLOCKED", "ROLLING_BACK"},
    "ACTIVATE": {"READY", "FAILED", "ROLLING_BACK"},
    "READY": {"DISCOVER"},
    "BLOCKED": {"DISCOVER"},
    "FAILED": {"ROLLING_BACK", "DISCOVER"},
    "ROLLING_BACK": {"ROLLED_BACK", "FAILED"},
    "ROLLED_BACK": {"DISCOVER"},
}
EXIT = {"READY": 0, "AWAITING_OPERATOR_ACTION": 10, "BLOCKED": 11, "FAILED": 12, "ROLLED_BACK": 13}


class InstallError(RuntimeError):
    pass


class Installer:
    def __init__(self, *, deploy_dir: Path, environment: str | None = None, profile: str = "AUTO",
                 approve: bool = False, bundle: str | None = None, qualify_suite: str = "INSTALL",
                 start_services: bool = True, env: dict | None = None):
        self.dir = deploy_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.environment, self.profile, self.approve = environment, profile.upper(), approve
        self.bundle, self.qualify_suite, self.start_services = bundle, qualify_suite, start_services
        self.env = dict(os.environ if env is None else env)
        self.env["ATTA_DEPLOYMENT_DIR"] = str(self.dir)
        self.state_file = self.dir / "install_state.json"
        self.log_file = self.dir / "install_transitions.jsonl"
        self.state = self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> dict:
        if self.state_file.is_file():
            return json.loads(self.state_file.read_text())
        return {"run_id": new_id("run"), "state": "NEW", "completed": {}, "created_at": iso_utc()}

    def _save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, default=str))
        os.replace(tmp, self.state_file)

    def transition(self, target: str, reason: str, evidence: dict | None = None) -> None:
        cur = self.state["state"]
        if target not in TRANSITIONS[cur]:
            raise InstallError(f"illegal installation transition {cur} -> {target}")
        rec = {"at": iso_utc(), "run_id": self.state["run_id"], "from": cur, "to": target, "reason": reason,
               "evidence": evidence or {}}
        with self.log_file.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.state["state"] = target
        self._save()

    def _done(self, step: str, inputs: dict, outputs: dict | None = None) -> None:
        self.state["completed"][step] = {"at": iso_utc(), "inputs": canonical_hash(inputs), "outputs": outputs or {}}
        self._save()

    def _is_done(self, step: str, inputs: dict) -> bool:
        c = self.state["completed"].get(step)
        return bool(c) and c["inputs"] == canonical_hash(inputs)

    # ---------------------------------------------------------------- driver
    def run(self) -> int:
        if self.state["state"] in ("READY", "BLOCKED", "ROLLED_BACK", "FAILED", "NEW"):
            if self.state["state"] == "FAILED":
                self.rollback("previous run failed")
            if self.state["state"] in ("READY", "BLOCKED"):
                self.state["run_id"] = new_id("run")    # a new run gets its own rollback snapshot
                self.state["completed"] = {}
            self.transition("DISCOVER", "installation started" if self.state["state"] == "NEW" else "re-run")
        approval_tried = False
        try:
            while True:
                st = self.state["state"]
                if st == "AWAITING_OPERATOR_ACTION" and self.approve and not approval_tried:
                    approval_tried = True          # resume: an approval re-run continues from the recorded plan
                    self.step_awaiting_operator_action()
                    continue
                if st in EXIT:
                    return EXIT[st]
                getattr(self, "step_" + st.lower())()
        except Exception as exc:  # noqa: BLE001 — any unexpected error is FAILED + rollback, never silent
            if self.state["state"] not in ("FAILED", "ROLLING_BACK", "ROLLED_BACK"):
                self.transition("FAILED", f"{type(exc).__name__}: {exc}"[:500])
            if self.state["state"] == "FAILED":
                self.rollback(str(exc)[:300])
            return EXIT[self.state["state"]]

    # ---------------------------------------------------------------- steps
    def step_discover(self) -> None:
        settings = resolver.resolve(self.environment, env=self.env, discovered_dir=self.dir).settings
        if settings is None:
            r = resolver.resolve(self.environment, env=self.env, discovered_dir=self.dir)
            self.transition("BLOCKED", "configuration invalid", {"errors": r.errors})
            return
        env = discovery.discover(settings, network_probe=settings.deployment.network_probe)
        (self.dir / "environment.json").write_text(json.dumps(env, indent=2, default=str))
        requested = self.profile if self.profile != "AUTO" else settings.deployment.profile
        decision = profiles.decide(requested, env, settings.deployment.controller_caps)
        found = mdl.discover_models(settings, env["storage"], self.dir / "model_index.json")
        self.state["decision"] = asdict(decision)
        self.state["models"] = len(found)
        if decision.compatible:
            resolver.write_discovered(self.dir, decision, env)
        body = mf.build(env, decision, [asdict(m) for m in found], settings,
                        configuration_source={"profile_requested": requested, "deployment_dir": str(self.dir)})
        mf.write(body, self.dir)
        self.state["environment_id"] = body["environment_id"]
        self._done("DISCOVER", {"env": canonical_hash({k: env[k] for k in ("operating_system", "cpu", "gpu",
                                                                           "architecture")})})
        if not decision.compatible:
            self.transition("BLOCKED", f"profile {decision.requested} incompatible with this machine",
                            {"reasons": decision.reasons})
            return
        self.transition("VALIDATE", f"discovered; profile {decision.selected}")

    def _resolution(self):
        prof = self.state["decision"]["selected"]
        return resolver.resolve(self.environment, env={**self.env, "ATTA_PROFILE": prof}, discovered_dir=self.dir)

    def step_validate(self) -> None:
        res = self._resolution()
        env = json.loads((self.dir / "environment.json").read_text())
        decision = profiles.ProfileDecision(**self.state["decision"])
        if res.settings is not None:
            resolver.validate_against_environment(res, env, decision)
        (self.dir / "effective_config.json").write_text(json.dumps({"effective": res.effective,
                                                                    "provenance": res.provenance}, indent=2, default=str))
        (self.dir / "effective_config.txt").write_text(resolver.report(res, decision))
        if not res.valid:
            self.transition("BLOCKED", "effective configuration invalid", {"errors": res.errors})
            return
        self.state["effective_sha256"] = canonical_hash(res.effective)
        self._done("VALIDATE", {"effective": self.state["effective_sha256"]})
        self.transition("PLAN", "configuration valid", {"warnings": res.warnings})

    def build_plan(self) -> dict:
        env = json.loads((self.dir / "environment.json").read_text())
        res = self._resolution()
        s = res.settings
        py = deps.python_drift()
        node_ok = (REPO_ROOT / "node_modules").is_dir() and deps.node_drift().get("ok")
        actions = []
        if not py["ok"]:
            actions.append({"action": "install locked Python packages", "admin": False,
                            "network": not self.bundle, "offline_possible": bool(self.bundle)})
        if shutil.which("node") and not node_ok:
            actions.append({"action": "npm ci (locked Node workspaces)", "admin": False,
                            "network": not self.bundle, "offline_possible": bool(self.bundle)})
        if not shutil.which("redis-server") and s.services.get("redis") and s.services["redis"].mode == "managed":
            actions.append({"action": "install redis-server (system package) or switch Redis to external",
                            "admin": True, "network": True, "offline_possible": False, "operator": True})
        if not env["services"].get("postgres", {}).get("healthy"):
            actions.append({"action": "provide PostgreSQL 16 + pgvector and run database/bootstrap.sql as superuser",
                            "admin": True, "network": False, "offline_possible": True, "operator": True})
        actions.append({"action": "apply database migrations (migration role)", "admin": False, "network": False,
                        "offline_possible": True})
        plan = {
            "run_id": self.state["run_id"], "generated_at": iso_utc(),
            "detected_environment": {k: env[k] for k in ("operating_system", "architecture", "cpu", "ram", "gpu")},
            "selected_profile": self.state["decision"]["selected"], "resources": self.state["decision"]["resources"],
            "existing_dependencies": {"python_lock_ok": py["ok"], "node_ok": bool(node_ok),
                                      "services": env["services"], "tools": env["tools"]},
            "missing_dependencies": [a["action"] for a in actions if a["action"].startswith(("install", "npm", "provide"))],
            "configuration_changes": ["runtime/deployment/discovered.yaml", "runtime/deployment/effective_config.json",
                                      "runtime/deployment/active.json (on activation)"],
            "services_to_start": [n for n, sp in s.services.items() if sp.mode == "managed"],
            "ports": env["ports"], "model_locations": [str(r) for r, _ in mdl.candidate_roots(s, env["storage"])],
            "storage_requirements_mb": {"runtime": 512, "workspaces": 1024},
            "actions": actions,
            "admin_actions": [a["action"] for a in actions if a.get("admin")],
            "network_actions": [a["action"] for a in actions if a.get("network")],
            "offline_actions": [a["action"] for a in actions if a.get("offline_possible")],
            "qualification_tests": self.qualify_suite,
            "rollback_actions": ["stop services started by this run", "restore previous active.json, discovered.yaml "
                                 "and effective configuration", "database migrations are additive and are retained"],
        }
        plan["plan_sha256"] = canonical_hash({k: v for k, v in plan.items() if k not in ("generated_at", "run_id")})
        return plan

    def step_plan(self) -> None:
        plan = self.build_plan()
        (self.dir / "install_plan.json").write_text(json.dumps(plan, indent=2, default=str))
        self.state["plan_sha256"] = plan["plan_sha256"]
        self._done("PLAN", {"plan": plan["plan_sha256"]})
        operator_needed = [a for a in plan["actions"] if a.get("operator")]
        if operator_needed:
            self.transition("BLOCKED", "operator must complete prerequisite actions",
                            {"actions": [a["action"] for a in operator_needed]})
            return
        if not self.approve and self.state.get("approved_plan") != plan["plan_sha256"]:
            self.transition("AWAITING_OPERATOR_ACTION", "review install_plan.json, then re-run with --approve",
                            {"plan_sha256": plan["plan_sha256"]})
            return
        self.state["approved_plan"] = plan["plan_sha256"]
        self.transition("PROVISION", "plan approved")

    def step_awaiting_operator_action(self) -> None:
        if not self.approve:
            raise SystemExit(EXIT["AWAITING_OPERATOR_ACTION"])
        plan = self.build_plan()
        if plan["plan_sha256"] != self.state.get("plan_sha256"):
            self.transition("DISCOVER", "the machine or configuration changed since the plan was made: re-planning")
            return
        self.state["approved_plan"] = plan["plan_sha256"]
        self.transition("PROVISION", "operator approved the plan", {"plan_sha256": plan["plan_sha256"]})

    def step_provision(self) -> None:
        inputs = {"plan": self.state["approved_plan"]}
        if not self._is_done("PROVISION", inputs):
            plan = json.loads((self.dir / "install_plan.json").read_text())
            done = []
            for a in plan["actions"]:
                if a["action"] == "npm ci (locked Node workspaces)":
                    args = ["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"]
                    if self.bundle:
                        args += ["--offline", "--logs-max=0", "--cache", str(Path(self.bundle) / "npm-cache")]
                    r = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
                                       env={**self.env, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"})
                    if r.returncode != 0:
                        raise InstallError(f"npm ci failed: {r.stderr[-500:]}")
                    done.append(a["action"])
                elif a["action"] == "install locked Python packages":
                    raise InstallError("Python environment drifted from the lock: re-run the INSTALL launcher, which "
                                       "bootstraps the virtual environment from the lock or the offline bundle")
            self._done("PROVISION", inputs, {"performed": done})
        self.transition("CONFIGURE", "dependencies provisioned")

    def step_configure(self) -> None:
        snap = self.dir / "rollback" / self.state["run_id"]
        if not snap.is_dir():
            snap.mkdir(parents=True)
            for n in ("active.json", "discovered.yaml", "effective_config.json"):
                if (self.dir / n).is_file():
                    shutil.copy2(self.dir / n, snap / n)
            (snap / "taken_at").write_text(iso_utc())
        prof = self.state["decision"]["selected"]
        r = subprocess.run([sys.executable, "-m", "backend.database", "migrate"], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=600, env={**self.env, "ATTA_PROFILE": prof})
        if r.returncode != 0:
            raise InstallError(f"migrations failed: {r.stdout[-300:]} {r.stderr[-500:]}")
        self._done("CONFIGURE", {"effective": self.state["effective_sha256"]}, {"migrate": r.stdout.strip()[-200:]})
        self.transition("START_SERVICES", "configuration applied", {"migrations": r.stdout.strip()[-200:]})

    def _manager(self, cmd: str) -> subprocess.CompletedProcess:
        prof = self.state["decision"]["selected"]
        return subprocess.run([sys.executable, "-m", "backend.service_manager", cmd, "--json"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=300, env={**self.env, "ATTA_PROFILE": prof})

    def step_start_services(self) -> None:
        if self.start_services:
            self.state["services_started_by_run"] = True   # a partial start is still stopped by rollback
            self._save()
            r = self._manager("start")
            if r.returncode not in (0, 3):    # 3 = already running (resume)
                raise InstallError(f"service start failed: {r.stdout[-800:]}")
            self.state["services_started_by_run"] = r.returncode == 0   # 3: it was already running before us
        self.transition("QUALIFY", "services healthy" if self.start_services else "service start skipped")

    def step_qualify(self) -> None:
        from .qualification import run_suite
        doc = mf.verify(self.dir / "environment_manifest.json")
        prof = self.state["decision"]["selected"]
        suites = [self.qualify_suite] + (["H100"] if prof == "H100" else [])   # outstanding H100 tests run on H100
        results = [run_suite(s, manifest=doc, profile=prof, out_root=self.dir / "qualification", bundle=self.bundle)
                   for s in suites if s]
        self.state["qualification"] = [{"suite": r["suite"], "id": r["qualification_id"], "status": r["status"],
                                        "counts": r["counts"]} for r in results]
        self._save()
        if any(r["status"] == "FAIL" for r in results):
            raise InstallError("mandatory qualification failed: " + ", ".join(
                t["test_id"] for r in results for t in r["results"] if t["status"] == "FAIL"))
        if any(r["status"] == "BLOCKED" for r in results):
            self.transition("BLOCKED", "mandatory qualification tests are BLOCKED on this machine",
                            {"blocked": [t["test_id"] for r in results for t in r["results"] if t["status"] == "BLOCKED"]})
            return
        self.transition("ACTIVATE", "qualification passed", {"qualification": self.state["qualification"]})

    def step_activate(self) -> None:
        doc = mf.verify(self.dir / "environment_manifest.json")
        active = {"profile": self.state["decision"]["selected"], "environment_id": doc["environment_id"],
                  "manifest_sha256": doc["content_sha256"], "effective_sha256": self.state["effective_sha256"],
                  "qualification": self.state.get("qualification"), "activated_at": iso_utc(),
                  "run_id": self.state["run_id"]}
        tmp = self.dir / "active.json.tmp"
        tmp.write_text(json.dumps(active, indent=2))
        os.replace(tmp, self.dir / "active.json")
        self.transition("READY", "activated", {"environment_id": doc["environment_id"]})

    # ---------------------------------------------------------------- rollback
    def rollback(self, reason: str) -> None:
        if self.state["state"] == "FAILED":
            self.transition("ROLLING_BACK", reason)
        if self.state.get("services_started_by_run"):
            self._manager("stop")
            self.state["services_started_by_run"] = False
        snap = self.dir / "rollback" / self.state["run_id"]
        restored = []
        for n in ("active.json", "discovered.yaml", "effective_config.json"):
            if (snap / n).is_file():
                shutil.copy2(snap / n, self.dir / n)
                restored.append(n)
            elif snap.is_dir() and (self.dir / n).exists() and n == "active.json":
                (self.dir / n).unlink()          # there was no activation before this run
                restored.append(f"removed {n}")
        self.transition("ROLLED_BACK", "previous activation restored", {"restored": restored,
                                                                        "database": "migrations are additive; retained"})
        self.state["run_id"] = new_id("run")
        self.state["completed"] = {}
        self._save()
