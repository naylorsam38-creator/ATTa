"""Deterministic build controller.

It manages implementation tasks independently of whoever (human or LLM) writes the code:

  * Tasks and their acceptance tests live in configuration/build/stages/<STAGE>.yaml. Those files, the roadmap and
    tests/acceptance/** are PROTECTED: their SHA-256 digests are sealed in configuration/build/protected.lock.json.
    Any change makes the controller refuse to run until an operator re-seals (HMAC-signed when the operator key is
    configured outside the repository).
  * Dependency ordering: a task runs only after every dependency has PASSED; otherwise it is BLOCKED.
  * File authorisation: every file changed since the task/stage base commit (tracked, deleted or untracked) must
    match the task's permitted paths, and must never match the stage's protected paths.
  * Tests are executed for real; command, timestamps, exit code and output are written as evidence files.
  * Results are appended to a SHA-256 hash-chained ledger. Task status is derived only from recorded executions —
    there is no command that marks a task or a failed test as passed. A failed test blocks progression.
  * Stage gates: a stage passes only when every task passed; stage N+1 cannot start before stage N's gate passed.
  * Recovery checkpoints: recorded at the Stage 01 baseline and after every fourth stage (S04, S08, ...).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from backend.contracts import iso_utc, new_id

from .runner import CommandEvidence, run_command

LEDGER_GENESIS = "0" * 64
EVIDENCE_DIR = "evidence/build_controller"
ALWAYS_PERMITTED = ("evidence/**",)  # the controller's own output


class ControllerError(Exception):
    pass


class IntegrityError(ControllerError):
    pass


# ---------------------------------------------------------------------------------------------- path rules
def glob_to_regex(pattern: str) -> re.Pattern:
    i, out = 0, ["^"]
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


# ---------------------------------------------------------------------------------------------- model
@dataclass
class AcceptanceTest:
    id: str
    description: str
    command: list[str]
    expected_exit_code: int = 0
    expected_result: str = ""
    timeout_s: float = 600


@dataclass
class Task:
    id: str
    objective: str
    depends_on: list[str]
    permitted_paths: list[str]
    required_changes: list[str]
    acceptance_tests: list[AcceptanceTest]
    rollback: str
    verification_evidence: str = ""


@dataclass
class Stage:
    id: str
    number: int
    title: str
    base_commit: str | None
    protected_paths: list[str]
    tasks: list[Task]
    source: Path

    def task(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise ControllerError(f"unknown task {task_id}")

    def order(self) -> list[Task]:
        ids = {t.id for t in self.tasks}
        for t in self.tasks:
            for d in t.depends_on:
                if d not in ids:
                    raise ControllerError(f"{t.id} depends on unknown task {d}")
        done: list[str] = []
        pending = list(self.tasks)
        while pending:
            progressed = False
            for t in list(pending):
                if all(d in done for d in t.depends_on):
                    done.append(t.id)
                    pending.remove(t)
                    progressed = True
            if not progressed:
                raise ControllerError("task dependency cycle among " + ", ".join(t.id for t in pending))
        return [self.task(i) for i in done]


@dataclass
class TaskOutcome:
    task_id: str
    status: str  # PASSED | FAILED | BLOCKED
    reasons: list[str] = field(default_factory=list)
    tests: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------- controller
class BuildController:
    def __init__(self, root: Path, python: str = sys.executable):
        self.root = root.resolve()
        self.python = python
        self.build_dir = self.root / "configuration" / "build"
        self.lock_file = self.build_dir / "protected.lock.json"
        self.evidence_dir = self.root / EVIDENCE_DIR
        self.ledger_file = self.evidence_dir / "ledger.jsonl"
        self.checkpoints_file = self.evidence_dir / "checkpoints.json"
        self.roadmap = self._load_yaml(self.build_dir / "roadmap.yaml")

    # ------------------------------------------------------------------ loading
    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if not path.is_file():
            raise ControllerError(f"missing {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ControllerError(f"{path} must be a mapping")
        return data

    def stage_ids(self) -> list[str]:
        return [s["id"] for s in self.roadmap["stages"]]

    def load_stage(self, stage_id: str) -> Stage:
        path = self.build_dir / "stages" / f"{stage_id}.yaml"
        d = self._load_yaml(path)
        if d.get("stage") != stage_id:
            raise ControllerError(f"{path} declares stage {d.get('stage')!r}, expected {stage_id}")
        tasks = []
        for t in d.get("tasks", []):
            missing = [k for k in ("id", "objective", "depends_on", "permitted_paths", "required_changes",
                                   "acceptance_tests", "rollback") if k not in t]
            if missing:
                raise ControllerError(f"task {t.get('id', '?')} lacks {missing}")
            if not t["acceptance_tests"]:
                raise ControllerError(f"task {t['id']} has no acceptance tests")
            tests = [AcceptanceTest(id=a["id"], description=a.get("description", ""), command=list(a["command"]),
                                    expected_exit_code=int(a.get("expected_exit_code", 0)),
                                    expected_result=a.get("expected_result", ""),
                                    timeout_s=float(a.get("timeout_s", 600))) for a in t["acceptance_tests"]]
            tasks.append(Task(id=t["id"], objective=t["objective"], depends_on=list(t["depends_on"]),
                              permitted_paths=list(t["permitted_paths"]), required_changes=list(t["required_changes"]),
                              acceptance_tests=tests, rollback=t["rollback"],
                              verification_evidence=t.get("verification_evidence", "")))
        number = int(re.sub(r"\D", "", stage_id))
        return Stage(id=stage_id, number=number, title=d.get("title", ""), base_commit=d.get("base_commit"),
                     protected_paths=list(d.get("protected_paths", [])), tasks=tasks, source=path)

    # ------------------------------------------------------------------ protected criteria
    def protected_files(self) -> list[Path]:
        pats = self.roadmap.get("protected_criteria", [])
        files = []
        for p in sorted(self._git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()):
            if matches_any(p, pats) and p != str(self.lock_file.relative_to(self.root)).replace(os.sep, "/"):
                files.append(self.root / p)
        return files

    def _digests(self) -> dict[str, str]:
        return {str(f.relative_to(self.root)).replace(os.sep, "/"): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in self.protected_files() if f.is_file()}

    @staticmethod
    def _operator_key() -> bytes | None:
        path = os.environ.get("ATTA_BUILD_OPERATOR_KEY_FILE")
        if not path:
            return None
        p = Path(path).expanduser()
        return p.read_bytes().strip() if p.is_file() else None

    def _sign(self, payload: dict) -> str | None:
        key = self._operator_key()
        if key is None:
            return None
        return hmac.new(key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()

    def seal(self, operator: str) -> dict:
        key_path = os.environ.get("ATTA_BUILD_OPERATOR_KEY_FILE")
        if key_path and self.root in Path(key_path).expanduser().resolve().parents:
            raise ControllerError("the operator key must live outside the repository")
        payload = {"sealed_at": iso_utc(), "sealed_by": operator, "files": self._digests()}
        sig = self._sign(payload)
        lock = {**payload, "hmac_sha256": sig, "signed": sig is not None}
        self.lock_file.write_text(json.dumps(lock, indent=2) + "\n")
        return lock

    def check_integrity(self) -> list[str]:
        """Return problems with the protected acceptance criteria (empty list = intact)."""
        if not self.lock_file.is_file():
            return ["protected.lock.json missing: acceptance criteria are not sealed"]
        lock = json.loads(self.lock_file.read_text())
        problems = []
        payload = {k: lock[k] for k in ("sealed_at", "sealed_by", "files")}
        key = self._operator_key()
        if lock.get("signed"):
            if key is None:
                problems.append("lock is HMAC-signed but ATTA_BUILD_OPERATOR_KEY_FILE is not configured to verify it")
            elif not hmac.compare_digest(self._sign(payload) or "", lock.get("hmac_sha256") or ""):
                problems.append("lock signature invalid: protected.lock.json was altered")
        elif key is not None:
            problems.append("an operator key is configured but the lock is unsigned: re-seal as operator")
        current = self._digests()
        for path, digest in lock["files"].items():
            if path not in current:
                problems.append(f"protected file removed: {path}")
            elif current[path] != digest:
                problems.append(f"protected file modified: {path}")
        for path in current:
            if path not in lock["files"]:
                problems.append(f"unsealed file added to protected criteria: {path}")
        return problems

    # ------------------------------------------------------------------ git
    def _git(self, *args: str) -> str:
        cp = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)
        if cp.returncode != 0:
            raise ControllerError(f"git {' '.join(args)} failed: {cp.stderr.strip()}")
        return cp.stdout

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def changed_files(self, base: str) -> list[tuple[str, str]]:
        """(status, path) for every change between base and the working tree, including untracked files."""
        out = []
        for line in self._git("diff", "--name-status", "--no-renames", base).splitlines():
            status, path = line.split("\t", 1)
            out.append((status, path))
        for path in self._git("ls-files", "--others", "--exclude-standard").splitlines():
            out.append(("A?", path))
        return sorted(set(out), key=lambda x: x[1])

    def is_clean(self) -> bool:
        dirty = [line for line in self._git("status", "--porcelain").splitlines()
                 if not matches_any(line[3:].strip('"'), ALWAYS_PERMITTED)]
        return not dirty

    def file_violations(self, base: str, permitted: list[str], protected: list[str]) -> list[str]:
        problems = []
        for status, path in self.changed_files(base):
            if matches_any(path, ALWAYS_PERMITTED):
                continue
            if matches_any(path, protected):
                problems.append(f"protected path changed ({status}): {path}")
            elif not matches_any(path, permitted):
                problems.append(f"unauthorised change ({status}): {path}")
        return problems

    # ------------------------------------------------------------------ ledger
    def _ledger_key(self) -> bytes | None:
        return self._operator_key()

    def ledger(self) -> list[dict]:
        if not self.ledger_file.is_file():
            return []
        return [json.loads(line) for line in self.ledger_file.read_text().splitlines() if line.strip()]

    def verify_ledger(self) -> list[str]:
        problems, prev = [], LEDGER_GENESIS
        for i, rec in enumerate(self.ledger()):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("prev") != prev:
                problems.append(f"ledger record {i} breaks the chain")
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != rec.get("hash"):
                problems.append(f"ledger record {i} hash mismatch (edited)")
            ev = rec.get("evidence_file")
            if ev:
                p = self.root / ev
                if not p.is_file():
                    problems.append(f"ledger record {i}: evidence file missing: {ev}")
                elif hashlib.sha256(p.read_bytes()).hexdigest() != rec.get("evidence_sha256"):
                    problems.append(f"ledger record {i}: evidence file altered: {ev}")
            prev = rec.get("hash")
        return problems

    def _append(self, record: dict) -> dict:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        entries = self.ledger()
        rec = {"seq": len(entries), "prev": entries[-1]["hash"] if entries else LEDGER_GENESIS,
               "recorded_at": iso_utc(), **record}
        rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
        with self.ledger_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def task_status(self, task_id: str) -> str:
        status = "NOT_STARTED"
        for rec in self.ledger():
            if rec.get("task") == task_id and rec["type"] in ("task_result", "task_begin"):
                status = rec["status"]
        return status

    def task_base(self, task_id: str) -> str | None:
        base = None
        for rec in self.ledger():
            if rec.get("task") == task_id and rec["type"] == "task_begin":
                base = rec["base_commit"]
        return base

    def stage_gate_passed(self, stage_id: str) -> bool:
        return any(r["type"] == "stage_gate" and r["stage"] == stage_id and r["status"] == "PASSED"
                   for r in self.ledger())

    # ------------------------------------------------------------------ preconditions
    def _preflight(self, stage: Stage) -> list[str]:
        problems = [f"integrity: {p}" for p in self.check_integrity()]
        problems += [f"ledger: {p}" for p in self.verify_ledger()]
        ids = self.stage_ids()
        if stage.id not in ids:
            problems.append(f"{stage.id} is not in the roadmap")
        else:
            idx = ids.index(stage.id)
            if idx > 0 and not self.stage_gate_passed(ids[idx - 1]):
                problems.append(f"previous stage {ids[idx - 1]} has not passed its gate")
        return problems

    def begin_task(self, stage_id: str, task_id: str) -> dict:
        stage = self.load_stage(stage_id)
        task = stage.task(task_id)
        problems = self._preflight(stage)
        for d in task.depends_on:
            if self.task_status(d) != "PASSED":
                problems.append(f"dependency {d} is {self.task_status(d)}")
        if problems:
            raise ControllerError("cannot begin " + task_id + ":\n  - " + "\n  - ".join(problems))
        return self._append({"type": "task_begin", "stage": stage_id, "task": task_id, "status": "IN_PROGRESS",
                             "base_commit": self.head()})

    # ------------------------------------------------------------------ execution
    def _render(self, cmd: list[str]) -> list[str]:
        return [c.replace("{python}", self.python).replace("{repo_root}", str(self.root)) for c in cmd]

    def _run_tests(self, stage: Stage, task: Task, run_id: str) -> tuple[bool, list[dict]]:
        results, ok = [], True
        run_dir = self.evidence_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        for test in task.acceptance_tests:
            ev: CommandEvidence = run_command(test.id, self._render(test.command), self.root,
                                              test.expected_exit_code, test.timeout_s,
                                              env={"ATTA_BUILD_CONTROLLER_RUN": run_id})
            ev_path = run_dir / f"{test.id}.json"
            ev_path.write_text(ev.to_json())
            rel = str(ev_path.relative_to(self.root)).replace(os.sep, "/")
            self._append({"type": "test_result", "stage": stage.id, "task": task.id, "test": test.id,
                          "run": run_id, "exit_code": ev.exit_code, "expected_exit_code": test.expected_exit_code,
                          "passed": ev.passed, "started_at": ev.started_at, "finished_at": ev.finished_at,
                          "evidence_file": rel, "evidence_sha256": hashlib.sha256(ev_path.read_bytes()).hexdigest()})
            results.append({"test": test.id, "passed": ev.passed, "exit_code": ev.exit_code,
                            "duration_s": ev.duration_s, "evidence": rel})
            ok &= ev.passed
        return ok, results

    def verify_task(self, stage_id: str, task_id: str, base: str | None = None, run_id: str | None = None,
                    check_files: bool = True) -> TaskOutcome:
        stage = self.load_stage(stage_id)
        task = stage.task(task_id)
        run_id = run_id or new_id("run")
        reasons = self._preflight(stage)
        if reasons:
            raise ControllerError("refusing to verify:\n  - " + "\n  - ".join(reasons))
        for d in task.depends_on:
            st = self.task_status(d)
            if st != "PASSED":
                reasons.append(f"dependency {d} is {st}")
        if reasons:
            out = TaskOutcome(task_id, "BLOCKED", reasons)
        else:
            if check_files:
                b = base or self.task_base(task_id) or stage.base_commit
                if not b:
                    raise ControllerError(f"no base commit for {task_id}: run 'begin' first or pass --base")
                reasons += self.file_violations(b, task.permitted_paths, stage.protected_paths)
            ok, tests = self._run_tests(stage, task, run_id)
            if not ok:
                reasons += [f"acceptance test {t['test']} failed (exit {t['exit_code']})" for t in tests if not t["passed"]]
            out = TaskOutcome(task_id, "PASSED" if ok and not reasons else "FAILED", reasons, tests)
        self._append({"type": "task_result", "stage": stage_id, "task": task_id, "run": run_id, "status": out.status,
                      "reasons": out.reasons, "head": self.head()})
        return out

    def verify_stage(self, stage_id: str, base: str | None = None) -> dict:
        stage = self.load_stage(stage_id)
        problems = self._preflight(stage)
        if problems:
            raise ControllerError("refusing to verify stage:\n  - " + "\n  - ".join(problems))
        base = base or stage.base_commit
        if not base:
            raise ControllerError("stage has no base_commit; pass --base")
        run_id = new_id("run")
        permitted = sorted({p for t in stage.tasks for p in t.permitted_paths})
        file_problems = self.file_violations(base, permitted, stage.protected_paths)
        outcomes: list[TaskOutcome] = []
        statuses: dict[str, str] = {}
        for task in stage.order():
            blocked = [d for d in task.depends_on if statuses.get(d) != "PASSED"]
            if blocked:
                o = TaskOutcome(task.id, "BLOCKED", [f"dependency {d} is {statuses.get(d)}" for d in blocked])
                self._append({"type": "task_result", "stage": stage_id, "task": task.id, "run": run_id,
                              "status": "BLOCKED", "reasons": o.reasons, "head": self.head()})
            else:
                ok, tests = self._run_tests(stage, task, run_id)
                reasons = [f"acceptance test {t['test']} failed (exit {t['exit_code']})" for t in tests if not t["passed"]]
                o = TaskOutcome(task.id, "PASSED" if ok else "FAILED", reasons, tests)
                self._append({"type": "task_result", "stage": stage_id, "task": task.id, "run": run_id,
                              "status": o.status, "reasons": o.reasons, "head": self.head()})
            statuses[task.id] = o.status
            outcomes.append(o)
        passed = all(o.status == "PASSED" for o in outcomes) and not file_problems
        gate = self._append({"type": "stage_gate", "stage": stage_id, "run": run_id,
                             "status": "PASSED" if passed else "FAILED", "head": self.head(), "base": base,
                             "file_problems": file_problems,
                             "tasks": {o.task_id: o.status for o in outcomes}})
        checkpoint = None
        if passed and self._is_checkpoint_stage(stage):
            checkpoint = self.create_checkpoint(stage, run_id)
        return {"stage": stage_id, "run": run_id, "passed": passed, "file_problems": file_problems,
                "tasks": [o.__dict__ for o in outcomes], "gate_record": gate["hash"], "checkpoint": checkpoint}

    # ------------------------------------------------------------------ checkpoints
    def _is_checkpoint_stage(self, stage: Stage) -> bool:
        every = int(self.roadmap.get("checkpoint_every", 4))
        return stage.number == 1 or stage.number % every == 0

    def checkpoints(self) -> list[dict]:
        if not self.checkpoints_file.is_file():
            return []
        return json.loads(self.checkpoints_file.read_text())

    def create_checkpoint(self, stage: Stage, run_id: str) -> dict:
        if not self.is_clean():
            cp = {"stage": stage.id, "status": "DEFERRED", "reason": "working tree has uncommitted changes; "
                  "commit and re-run verify-stage so the checkpoint names exactly the verified code"}
            self._append({"type": "checkpoint", **cp, "run": run_id})
            return cp
        migrations = sorted(p.name for p in (self.root / "database" / "migrations").glob("*.sql"))
        cp = {"id": f"checkpoint-{stage.id}-{run_id[-8:]}", "stage": stage.id, "status": "VERIFIED",
              "commit": self.head(), "created_at": iso_utc(), "run": run_id, "migrations": migrations,
              "lock_sha256": hashlib.sha256(self.lock_file.read_bytes()).hexdigest()}
        items = self.checkpoints() + [cp]
        self.checkpoints_file.write_text(json.dumps(items, indent=2) + "\n")
        self._append({"type": "checkpoint", **cp})
        return cp

    def plan(self, stage_id: str) -> list[dict]:
        stage = self.load_stage(stage_id)
        return [{"task": t.id, "status": self.task_status(t.id), "depends_on": t.depends_on,
                 "objective": t.objective, "tests": [a.id for a in t.acceptance_tests]} for t in stage.order()]


def recover(controller: BuildController, checkpoint_id: str | None, applied_migrations: list[str] | None,
            yes: bool = False) -> dict:
    """Restore the last verified compatible checkpoint onto a new branch. Current work is preserved in a stash."""
    cps = [c for c in controller.checkpoints() if c.get("status") == "VERIFIED"]
    if checkpoint_id:
        cps = [c for c in cps if c["id"] == checkpoint_id]
    if not cps:
        raise ControllerError("no verified checkpoint available")
    target = None
    for cp in reversed(cps):
        incompatible = sorted(set(applied_migrations or []) - set(cp["migrations"]))
        if not incompatible:
            target = cp
            break
    if target is None:
        raise ControllerError("no checkpoint is compatible with the database: it has migrations newer than every "
                              "checkpoint. Restore a database backup first.")
    plan = {"checkpoint": target, "steps": ["stash uncommitted work (including untracked files)",
                                            f"create branch recovery/{target['stage']}-<timestamp> at {target['commit']}",
                                            "switch to it"]}
    if not yes:
        return {"dry_run": True, **plan}
    g = controller._git
    stamp = iso_utc().replace(":", "").replace("-", "").replace(".", "")[:15]
    if g("status", "--porcelain").strip():
        g("stash", "push", "--include-untracked", "-m", f"atta-recovery-{stamp}")
    branch = f"recovery/{target['stage']}-{stamp}"
    g("switch", "-c", branch, target["commit"])
    return {"dry_run": False, "branch": branch, **plan}
