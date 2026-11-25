"""Independent verification (Stage 01A §17). Criteria are fixed in the task contract BEFORE execution; the
executing worker cannot change them. Verifiers read the actual workspace and persisted execution records — never
the worker's claims. A successful tool call is not a completed task."""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from backend.capability_registry import CapabilityRegistry
from backend.capability_runtime import CapabilityRuntime
from backend.contracts import new_id

from .catalogue import ToolCatalogue
from .contracts import TaskContract, TaskCriterion
from .drift import resolve_path
from .store import ControlStore


@dataclass
class CriterionResult:
    criterion_id: str
    passed: bool
    pending_human: bool
    detail: dict


class IndependentVerifier:
    NAME = "independent-verifier/1"

    def __init__(self, store: ControlStore, registry: CapabilityRegistry, runtime: CapabilityRuntime,
                 catalogue: ToolCatalogue, protected_prefixes: list[str]):
        self.store, self.registry, self.runtime, self.catalogue = store, registry, runtime, catalogue
        self.protected = protected_prefixes

    def _path(self, workspace: Path, rel: str) -> Path:
        p, why = resolve_path(rel, workspace, ["."], self.protected)
        if why:
            raise ValueError(why)
        return p

    def _check(self, k: TaskCriterion, c: TaskContract, workspace: Path, executions: list[dict]) -> tuple[bool, bool, dict]:
        prm = k.params
        if k.verifier == "human_review":
            return False, True, {"reason": "semantic correctness needs independent human acceptance (Stage 04 UI)"}
        if k.verifier == "execution_succeeded":
            ok = [e for e in executions if e["state"] == "succeeded" and (not prm.get("tool_id") or
                                                                          e["tool_number"] == prm["tool_id"])]
            return bool(ok), False, {"succeeded_executions": [e["execution_id"] for e in ok]}
        if k.verifier == "tool_verify":
            ok = [e for e in executions if e["state"] == "succeeded" and e["tool_number"] == prm.get("tool_id")]
            if not ok:
                return False, False, {"reason": f"no successful execution of {prm.get('tool_id')}"}
            e = ok[-1]
            t = self.catalogue.get(e["tool_number"])
            reg = self.registry.get(t.spec.capability_id)
            ticket = self.runtime.issue_ticket(new_id("exe"), t.spec.capability_id, "verify")
            handlers = dict(reg.manifest.handler_refs)
            handlers["verify"] = t.spec.verify
            r = self.runtime.run(ticket=ticket, mode="verify", capability_id=t.spec.capability_id,
                                 package_dir=reg.manifest.directory, expected_digest=reg.package_digest,
                                 handlers=handlers, timeout_s=30, payload=e["arguments"], output=e["result"],
                                 workdir=workspace)
            v = r.value
            passed = r.status == "ok" and ((v.get("verified") is True) if isinstance(v, dict) else v is True)
            return passed, False, {"execution_id": e["execution_id"], "verify_status": r.status, "verify_result": v}
        p = self._path(workspace, prm["path"])
        if not p.is_file():
            return False, False, {"path": prm["path"], "reason": "file does not exist"}
        data = p.read_bytes()
        info = {"path": prm["path"], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if k.verifier == "output_exists":
            return len(data) > 0 or prm.get("allow_empty", False), False, info
        if k.verifier == "file_sha256":
            return info["sha256"] == prm["sha256"], False, {**info, "expected": prm["sha256"]}
        if k.verifier == "text_contains":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return False, False, {**info, "reason": "not UTF-8 text"}
            missing = [s for s in prm.get("all", []) if s not in text]
            forbidden = [s for s in prm.get("none", []) if s in text]
            return not missing and not forbidden, False, {**info, "missing": missing, "forbidden": forbidden}
        if k.verifier == "json_schema":
            try:
                doc = json.loads(data)
            except json.JSONDecodeError as exc:
                return False, False, {**info, "reason": f"invalid JSON: {exc.msg}"}
            errs = [e.message for e in Draft202012Validator(prm["schema"]).iter_errors(doc)]
            return not errs, False, {**info, "errors": errs[:5]}
        if k.verifier == "file_format":
            fmt = prm["format"]
            if fmt == "utf8_text":
                try:
                    data.decode("utf-8")
                    return True, False, info
                except UnicodeDecodeError:
                    return False, False, {**info, "reason": "not UTF-8"}
            if fmt == "json":
                try:
                    json.loads(data)
                    return True, False, info
                except json.JSONDecodeError as exc:
                    return False, False, {**info, "reason": exc.msg}
            if fmt in ("zip", "docx"):
                try:
                    with zipfile.ZipFile(p) as z:
                        bad = z.testzip()
                        names = z.namelist()
                except zipfile.BadZipFile as exc:
                    return False, False, {**info, "reason": str(exc)}
                ok = bad is None and (fmt == "zip" or "word/document.xml" in names)
                return ok, False, {**info, "entries": len(names)}
            if fmt == "pdf":
                return data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:], False, info
            return False, False, {**info, "reason": f"unknown format {fmt}"}
        return False, False, {"reason": f"unknown verifier {k.verifier}"}

    def verify(self, c: TaskContract, attempt_id: str | None, workspace: Path,
               criteria: list[TaskCriterion] | None = None, record: bool = True, subject: str | None = None
               ) -> list[CriterionResult]:
        executions = [e for e in self.store.executions(c.parent_objective_id) if
                      e["task_id"] in {c.task_id, *(self._repair_children(c))}]
        out = []
        for k in criteria or c.acceptance_criteria:
            try:
                passed, pending, detail = self._check(k, c, workspace, executions)
            except Exception as exc:  # noqa: BLE001 — a verifier error is a failed verification, never a pass
                passed, pending, detail = False, False, {"error": f"{type(exc).__name__}: {exc}"}
            if record:
                eid = self.store.add_evidence(c.parent_objective_id, subject or c.task_id, "verification",
                                              {"criterion_id": k.id, "verifier": k.verifier, "passed": passed,
                                               "detail": detail})
                self.store.add_verification(c.parent_objective_id, subject or c.task_id, attempt_id, k.id, passed,
                                            detail, eid, self.NAME)
            out.append(CriterionResult(k.id, passed, pending, detail))
        return out

    def _repair_children(self, c: TaskContract) -> set[str]:
        return {t["task_id"] for t in self.store.tasks(c.parent_objective_id) if t["repair_of"] == c.task_id}
