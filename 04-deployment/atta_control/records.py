"""ATTa's real run records, read into the control layer's contract, and exported for the record diagnostic.

Sources (all written by the existing runner; nothing here writes to them):
  <ROOT>/state/runner/results/<app>.json          each app's latest result (app_runner._write_result)
  <ROOT>/state/runner/RUN-REPORT.json             the latest fleet report (resilience.report)
  <ROOT>/state/runner/runs/<fleet>/report.json    every fleet report, kept
  <ROOT>/state/runner/evidence/<app>/<run>-a<n>/  stage-6 browser evidence (pruned by the watcher over time)

results/<app>.json is overwritten by every run of that app, and the watcher prunes evidence folders. The
export below is what keeps a fleet run's failures after that: one record per failed app per fleet run,

  <ROOT>/state/runner/failures/<fleet run id>/<app>/<run id>-a<attempt>.json

written once (never overwritten), carrying the job identity at top level (app, run_id, revision, attempt:
the fields record_diagnostic.py requires), the source result's hash, the SHA-256 of every evidence file as it
was at export time, and the full result. That folder is exactly what record_diagnostic.run() reads."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from resilience.states import handoff_state

from .models import STAGES, AppIdentity, EvidenceRef, Observation
from .store import EventStore, file_sha256, fingerprint

RECORD_SCHEMA = "ATTA_FAILURE_RECORD.v1"
MAX_SUMMARY = 600


def root() -> Path:
    return Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))


def runner_dir(base: Path | None = None) -> Path:
    return (base or root()) / "state" / "runner"


def safe_id(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "app"


def segment(value: str) -> str:
    """A fleet or run id as one folder/file name: case kept (fleet ids read fleet-20260929T061331Z-...), and
    anything that is not a plain id refused rather than rewritten into something that might collide."""
    import re
    v = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", v) or ".." in v:
        raise ValueError(f"{value!r} is not a usable run id")
    return v


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- reading
def fleet_report(fleet_run_id: str | None = None, base: Path | None = None) -> dict | None:
    """The fleet report for fleet_run_id (runs/<id>/report.json), or the latest (RUN-REPORT.json)."""
    rd = runner_dir(base)
    if fleet_run_id:
        rep = _read_json(rd / "runs" / fleet_run_id / "report.json")
    else:
        rep = _read_json(rd / "RUN-REPORT.json")
    return rep if isinstance(rep, dict) else None


def load_results(base: Path | None = None) -> dict[str, tuple[Path, dict]]:
    """safe_id(app) -> (path, result) for every readable results/<app>.json. results/late/ is not read here:
    a late result is by definition not the recorded one."""
    out: dict[str, tuple[Path, dict]] = {}
    d = runner_dir(base) / "results"
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        r = _read_json(p)
        if isinstance(r, dict):
            out[safe_id(r.get("app") or p.stem)] = (p, r)
    return out


def late_results(fleet: dict | None, base: Path | None = None) -> list[str]:
    """results/late/<app>.<run id>.json files whose run belongs to this fleet run."""
    d = runner_dir(base) / "results" / "late"
    if not d.is_dir() or not fleet:
        return []
    runs = {a.get("run_id") for a in fleet.get("apps") or [] if a.get("run_id")}
    out = []
    for p in sorted(d.glob("*.json")):
        r = _read_json(p) or {}
        if (r.get("verification") or {}).get("fleet_run_id") == fleet.get("fleet_run_id") or \
                any(p.name.endswith(f".{run}.json") for run in runs):
            out.append(p.name)
    return out


def belongs_to_fleet(result: dict, fleet: dict) -> tuple[bool, str]:
    """Is this result the one the fleet run produced for its app? By the fleet id the job stamped on it; a
    result with no fleet id (a crash contained before the job had an identity) by its timestamp falling
    inside the fleet's run window. Anything else was written by another run and is not this fleet's."""
    v = result.get("verification") or {}
    fid = v.get("fleet_run_id")
    if fid:
        return (fid == fleet.get("fleet_run_id"), "fleet id" if fid == fleet.get("fleet_run_id") else f"written by fleet {fid}")
    ts, t0, t1 = result.get("ts"), fleet.get("started_at"), fleet.get("finished_at")
    if all(isinstance(x, (int, float)) for x in (ts, t0, t1)) and t0 <= ts <= t1 + 5:
        return True, "no fleet id; written inside this fleet's run window"
    return False, "no fleet id and not written inside this fleet's run window"


def evidence_refs(result: dict) -> list[EvidenceRef]:
    """Stage-6 evidence files that still exist, hashed now."""
    ev = (((result.get("stages") or {}).get("6 CLEAN") or {}).get("evidence") or {})
    d = ev.get("dir")
    out: list[EvidenceRef] = []
    if not d or not Path(d).is_dir():
        return out
    for p in sorted(Path(d).iterdir()):
        if p.is_file():
            try:
                out.append(EvidenceRef(path=str(p), sha256=file_sha256(p), source="system_watcher",
                                       stage="6 CLEAN", captured_at=p.stat().st_mtime))
            except OSError:
                continue
    return out


def observations(result: dict, build_id: str | None = None) -> list[Observation]:
    """One Observation per stage the result records, in stage order. Nothing is added for a stage the
    result does not mention."""
    ident = AppIdentity.from_result(result, build_id=build_id)
    stages = result.get("stages") or {}
    refs = tuple(evidence_refs(result))
    ts = (result.get("verification") or {}).get("finished_at") or result.get("ts")
    out = []
    for name in list(STAGES) + sorted(k for k in stages if k not in STAGES):
        st = stages.get(name)
        if not isinstance(st, dict):
            continue
        summary = " ".join(str(x) for x in (st.get("code"), st.get("detail")) if x)[:MAX_SUMMARY]
        out.append(Observation(identity=ident, stage=name, result=str(st.get("status") or "UNKNOWN"),
                               code=st.get("code"), summary=summary, observed_at=ts, source="system_watcher",
                               evidence=refs if name == "6 CLEAN" else (),
                               details={"verdict": result.get("verdict"), "handoff_state": handoff_state(result)}))
    return out


def stages_reached(result: dict) -> list[str]:
    stages = result.get("stages") or {}
    return [s for s in STAGES if (stages.get(s) or {}).get("status") == "OK"]


# ---------------------------------------------------------------- recording observations
def record_observations(fleet: dict, store: EventStore | None = None, base: Path | None = None) -> dict:
    """Append this fleet's per-stage observations to observations.jsonl, once per (run id, attempt, stage):
    running it twice records nothing new."""
    store = store or EventStore()
    seen = {(e.get("run_id"), e.get("attempt"), e.get("stage"), e.get("app"))
            for e in store.events(store.OBSERVATIONS)}
    added = skipped = 0
    for k, (path, r) in load_results(base).items():
        ok, _ = belongs_to_fleet(r, fleet)
        if not ok:
            continue
        for o in observations(r):
            key = (o.identity.run_id, o.identity.attempt, o.stage, o.identity.app)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            store.append_observation({"event": "STAGE_OBSERVED", "fleet_run_id": fleet.get("fleet_run_id"),
                                      "app": o.identity.app, "run_id": o.identity.run_id, "attempt": o.identity.attempt,
                                      "stage": o.stage, "observation": o.to_dict(), "source_result": str(path)})
            added += 1
    return {"added": added, "already_recorded": skipped}


# ---------------------------------------------------------------- the failures tree
def failures_root(base: Path | None = None) -> Path:
    return runner_dir(base) / "failures"


def failure_record(result: dict, source: Path, fleet: dict) -> dict:
    v = result.get("verification") or {}
    refs = evidence_refs(result)
    return {
        "schema": RECORD_SCHEMA,
        # identity at top level: record_diagnostic requires app, run_id, revision, attempt
        "app": result.get("app"),
        "run_id": v.get("run_id"),
        "revision": v.get("deployment_revision"),
        "attempt": v.get("attempt"),
        "deployment_revision": v.get("deployment_revision"),
        "app_revision": v.get("app_revision"),
        "correlation_id": v.get("correlation_id"),
        "fleet_run_id": fleet.get("fleet_run_id"),
        "status": handoff_state(result),
        "stage": result.get("broken_at"),
        "code": result.get("code"),
        "stages_reached": stages_reached(result),
        "exported_at": time.time(),
        "source_result": str(source),
        "source_result_sha256": file_sha256(source),
        "evidence_files": [{"path": e.path, "sha256": e.sha256} for e in refs],
        "result": result,
    }


def _record_name(rec: dict) -> str:
    try:
        run = segment(rec.get("run_id"))
    except ValueError:
        run = "no-run-id"   # a contained crash before the job had an identity: named as such, never guessed
    return f"{run}-a{rec.get('attempt') if isinstance(rec.get('attempt'), int) else 'x'}.json"


def _write_once(dest: Path, rec: dict) -> str:
    """CREATED, SAME (an identical record is already there), or CONFLICT (a different one is: kept as it is).
    Created with O_EXCL: two exporters racing can never both write the same record."""
    body = json.dumps(rec, indent=2, sort_keys=True, default=str) + "\n"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError:
        old = _read_json(dest)
        if isinstance(old, dict) and _stable(old) == _stable(rec):
            return "SAME"
        return "CONFLICT"
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    return "CREATED"


def _stable(rec: dict) -> str:
    """A record's identity-bearing content: everything except when it was exported and the evidence hashes
    (evidence the watcher pruned since the first export would otherwise read as a different record)."""
    return fingerprint({k: v for k, v in rec.items() if k not in ("exported_at", "evidence_files")})


def export_failures(fleet_run_id: str | None = None, base: Path | None = None,
                    store: EventStore | None = None, out_root: Path | None = None) -> dict:
    """Write this fleet run's failure records (every app whose outcome is not QUALIFIED). Idempotent:
    re-running creates nothing new. Returns what was done, app by app, and never raises for one bad app."""
    fleet = fleet_report(fleet_run_id, base)
    if not fleet or not fleet.get("fleet_run_id"):
        return {"ok": False, "error": f"no fleet report for {fleet_run_id or 'the latest run'} under {runner_dir(base)}"}
    fid = fleet["fleet_run_id"]
    dest_root = (out_root or failures_root(base)) / segment(fid)
    results = load_results(base)
    rows: list[dict] = []
    seen: set[str] = set()
    for row in fleet.get("apps") or []:
        if row.get("state") == "QUALIFIED" or not row.get("app"):
            continue
        app = safe_id(row["app"])
        entry = {"app": app, "state": row.get("state")}
        got = results.get(app)
        if row.get("code") == "IDENTITY_COLLISION" or app in seen:
            # Two names, one id: only the first was run. Its result must never be filed under the other name.
            entry.update(app=row["app"], outcome="NOT_RUN", detail="identity collision: this name was not run")
            rows.append(entry)
            continue
        seen.add(app)
        if got is None:
            entry.update(outcome="NO_RESULT", detail="the fleet report lists this app but results/ has no result for it")
        else:
            path, r = got
            ok, why = belongs_to_fleet(r, fleet)
            if not ok:
                entry.update(outcome="SUPERSEDED", detail=f"results/{path.name} is not this fleet's result ({why})")
            else:
                try:
                    rec = failure_record(r, path, fleet)
                    dest = dest_root / app / _record_name(rec)
                    entry.update(outcome=_write_once(dest, rec), path=str(dest), matched_by=why)
                except Exception as e:
                    entry.update(outcome="ERROR", detail=f"{type(e).__name__}: {e}")
        rows.append(entry)
    summary = {s: sum(1 for x in rows if x["outcome"] == s)
               for s in ("CREATED", "SAME", "CONFLICT", "SUPERSEDED", "NO_RESULT", "NOT_RUN", "ERROR")}
    out = {"ok": summary["CONFLICT"] == 0 and summary["ERROR"] == 0, "fleet_run_id": fid,
           "records_root": str(dest_root), "apps": rows, "summary": summary}
    try:
        (store or EventStore()).append_run_event({"event": "FAILURES_EXPORTED", "fleet_run_id": fid,
                                                  "records_root": str(dest_root), "summary": summary})
    except Exception as e:
        out["audit_error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------- the record diagnostic, read-only
def diagnose(fleet_run_id: str | None = None, base: Path | None = None, store: EventStore | None = None,
             out_path: Path | None = None) -> dict:
    """record_diagnostic.classify() on every exported failure record of the fleet run, with the stage-6
    browser.json of the same job as related evidence (so a mismatched identity is caught). Proves it is
    read-only: every input file's hash before and after must match, or the result says so."""
    import record_diagnostic as rd
    fleet = fleet_report(fleet_run_id, base)
    if not fleet or not fleet.get("fleet_run_id"):
        return {"ok": False, "error": f"no fleet report for {fleet_run_id or 'the latest run'}"}
    rec_root = failures_root(base) / segment(fleet["fleet_run_id"])
    expected = list(dict.fromkeys(safe_id(a["app"]) for a in fleet.get("apps") or []
                                  if a.get("app") and a.get("state") != "QUALIFIED" and a.get("code") != "IDENTITY_COLLISION"))
    inputs = sorted(rec_root.glob("*/*.json")) if rec_root.is_dir() else []
    related_by_record: dict[Path, list[Path]] = {}
    for p in inputs:
        rec = _read_json(p) or {}
        b = next((Path(e["path"]) for e in rec.get("evidence_files") or []
                  if isinstance(e, dict) and str(e.get("path", "")).endswith("browser.json") and Path(e["path"]).is_file()), None)
        related_by_record[p] = [b] if b else []
    all_inputs = inputs + [q for v in related_by_record.values() for q in v]
    before = rd.audit_input_fingerprints(all_inputs)
    findings, missing = [], []
    for app in expected:
        cands = sorted((rec_root / app).glob("*.json")) if (rec_root / app).is_dir() else []
        if not cands:
            missing.append(app)
            continue
        if len(cands) > 1:
            findings.append({"app": app, "classification": "APP_IDENTITY_DUPLICATE", "allowed_action": "NO_ACTION",
                             "root_cause": {"value": "UNKNOWN", "basis": "Multiple candidate records exist."},
                             "evidence": [], "remediation_id": None, "policy_version": rd.POLICY_VERSION})
            continue
        findings.append(rd.classify(cands[0], related_by_record.get(cands[0], [])))
    unchanged = rd.fingerprints_unchanged(before)
    out = {"schema_version": rd.POLICY_VERSION, "read_only": True, "inputs_unchanged": unchanged,
           "fleet_run_id": fleet["fleet_run_id"], "records_root": str(rec_root),
           "expected_count": len(expected), "processed_count": len(findings), "missing_apps": missing,
           "findings": findings, "ok": unchanged and not missing}
    if out_path is not None:
        out_path = Path(out_path).resolve()
        if out_path.is_relative_to(rec_root.resolve()):
            raise ValueError("refusing to write diagnostic output inside the input evidence tree")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_bytes(json.dumps(out, indent=2, sort_keys=True, default=str).encode() + b"\n")
        os.replace(tmp, out_path)
        out["output_path"] = str(out_path)
    try:
        (store or EventStore()).append_run_event({"event": "RECORD_DIAGNOSTIC_RUN", "fleet_run_id": fleet["fleet_run_id"],
                                                  "processed": len(findings), "missing": missing,
                                                  "inputs_unchanged": unchanged,
                                                  "findings_sha256": fingerprint(findings)})
    except Exception as e:
        out["audit_error"] = f"{type(e).__name__}: {e}"
    return out


def iter_exported(fleet_run_id: str, base: Path | None = None) -> Iterable[tuple[Path, dict]]:
    d = failures_root(base) / segment(fleet_run_id)
    for p in sorted(d.glob("*/*.json")) if d.is_dir() else []:
        r = _read_json(p)
        if isinstance(r, dict):
            yield p, r


