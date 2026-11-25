"""python -m backend.deployment <command>   — the implementation behind INSTALL / RUN / HEALTHCHECK launchers.

  discover [--json]            read-only machine discovery
  profile [--profile P]        show the profile decision (AUTO or explicit, always validated)
  models                       discovered models (configured roots, removable media, runtime stores)
  config [--profile P]         effective configuration report (secrets redacted)
  plan [--profile P]           deterministic installation plan (changes nothing)
  install [--profile P] [--approve] [--bundle DIR] [--suite S] [--no-start]   resumable installer
  rollback                     restore the previous activation
  run [--no-browser]           start the activated configuration (refuses if the machine/config changed)
  healthcheck                  services, controller, activation and manifest status
  qualify --suite S [--profile P] [--bundle DIR]   isolated qualification (AIR_GAPPED runs in a network namespace)
  deps [--check]               dependency lock / drift report
  audit-offline                zero-hidden-Internet audit
  bundle create|verify|install-offline ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from backend.settings.loader import REPO_ROOT


def ddir() -> Path:
    d = os.environ.get("ATTA_DEPLOYMENT_DIR") or "runtime/deployment"
    p = Path(d)
    return p if p.is_absolute() else REPO_ROOT / p


def _settings(profile=None):
    from backend.settings import load_settings
    env = dict(os.environ)
    if profile and profile.upper() != "AUTO":
        env["ATTA_PROFILE"] = profile
    return load_settings(env=env)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backend.deployment")
    ap.add_argument("command")
    ap.add_argument("sub", nargs="?")
    ap.add_argument("--profile", default="AUTO")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--bundle")
    ap.add_argument("--suite", default=None)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--inside-netns", action="store_true")
    ap.add_argument("--qid")
    ap.add_argument("--out")
    ap.add_argument("--venv")
    ap.add_argument("--models", nargs="*")
    ap.add_argument("--require")
    ap.add_argument("--env", dest="environment")
    a = ap.parse_args(argv)
    from . import deps, discovery, manifest as mf, models as mdl, profiles, resolver
    out = lambda o: print(json.dumps(o, indent=2, default=str))  # noqa: E731
    cmd = a.command

    if cmd == "discover":
        s = _settings()
        out(discovery.discover(s, network_probe=s.deployment.network_probe))
        return 0
    if cmd == "profile":
        s = _settings()
        env = discovery.discover(s, network_probe=s.deployment.network_probe)
        d = profiles.decide(a.profile, env, s.deployment.controller_caps)
        out(asdict(d))
        return 0 if d.compatible else 11
    if cmd == "models":
        s = _settings()
        found = mdl.discover_models(s, discovery.storage_info(), ddir() / "model_index.json")
        if not a.require:
            out([asdict(m) for m in found])
            return 0
        # --require MODEL: deterministic runtime selection for one model; never downloads or substitutes.
        from . import runtimes
        env = discovery.discover(s)
        d = profiles.decide(a.profile if a.profile != "AUTO" else s.deployment.profile, env)
        choice = runtimes.select(d.selected or "LOCAL_CPU", a.require, found, env, s.deployment.runtime_preference)
        out(asdict(choice))
        return 0 if choice.status == "SELECTED" else 11       # 11 = BLOCKED (MODEL_NOT_FOUND / INCOMPATIBLE / ...)
    if cmd == "config":
        r = resolver.resolve(a.environment, profile=None if a.profile == "AUTO" else a.profile)
        print(resolver.report(r))
        return 0 if r.valid else 4
    if cmd in ("plan", "install"):
        from .installer import EXIT, Installer
        inst = Installer(deploy_dir=ddir(), environment=a.environment, profile=a.profile, approve=a.approve,
                         bundle=a.bundle, qualify_suite=a.suite or "INSTALL", start_services=not a.no_start)
        if cmd == "plan":
            if inst.state["state"] in ("NEW", "READY", "BLOCKED", "ROLLED_BACK"):
                inst.state["state"] = "NEW"
                inst.transition("DISCOVER", "plan requested")
                inst.step_discover()
                if inst.state["state"] == "VALIDATE":
                    inst.step_validate()
            if inst.state["state"] != "PLAN":
                print(f"cannot plan: installation state {inst.state['state']} (see install_transitions.jsonl)")
                return EXIT.get(inst.state["state"], 11)
            plan = inst.build_plan()
            (ddir() / "install_plan.json").write_text(json.dumps(plan, indent=2, default=str))
            out(plan)
            return 0
        code = inst.run()
        print(f"installation state: {inst.state['state']} (log: {inst.log_file})")
        if inst.state["state"] == "AWAITING_OPERATOR_ACTION":
            print(f"review {ddir() / 'install_plan.json'} and run INSTALL again with --approve")
        return code
    if cmd == "rollback":
        from .installer import Installer
        inst = Installer(deploy_dir=ddir())
        if inst.state["state"] not in ("FAILED", "ROLLING_BACK"):
            inst.state["state"] = "FAILED"
        inst.rollback("operator requested rollback")
        print(inst.state["state"])
        return 0
    if cmd == "run":
        return run_activated(a)
    if cmd == "healthcheck":
        active = ddir() / "active.json"
        print(f"activation: {json.loads(active.read_text()) if active.is_file() else 'NOT ACTIVATED'}")
        try:
            m = mf.verify(ddir() / "environment_manifest.json", REPO_ROOT / "configuration" / "deployment_ed25519.pub")
            print(f"manifest: {m['environment_id']} profile {m['profile']} integrity OK")
        except mf.ManifestError as exc:
            print(f"manifest: {exc}")
        return subprocess.run([sys.executable, "-m", "backend.service_manager", "healthcheck"], cwd=REPO_ROOT).returncode
    if cmd == "qualify":
        from .qualification import run_suite
        suite = a.suite or "LOCAL"
        root = Path(os.environ.get("ATTA_QUAL_OUT") or (a.out or ddir() / "qualification"))
        try:
            m = mf.verify(ddir() / "environment_manifest.json")
        except mf.ManifestError:
            s = _settings()
            env = discovery.discover(s)
            d = profiles.decide("AUTO", env)
            m = mf.build(env, d, [], s, configuration_source={"qualification": "ad-hoc manifest"})
        data_suites = __import__("yaml").safe_load((REPO_ROOT / "configuration/qualification/suites.yaml").read_text())
        members = data_suites["suites"][suite].get("includes", [suite])
        docs = [run_suite(x, manifest=m, profile=a.profile if a.profile != "AUTO" else (m.get("profile") or "LOCAL_CPU"),
                          out_root=root, bundle=a.bundle, inside_netns=a.inside_netns, qid=a.qid if len(members) == 1 else None)
                for x in members]
        summary = {"suite": suite, "results": [{"suite": d["suite"], "qualification_id": d["qualification_id"],
                                                "status": d["status"], "counts": d["counts"]} for d in docs]}
        if not a.inside_netns:
            out(summary)
        status = {d["status"] for d in docs}
        return 1 if "FAIL" in status else (3 if "BLOCKED" in status else 0)
    if cmd == "deps":
        r = deps.report()
        out(r)
        return 0 if (r["python"]["ok"] and r["node"].get("ok", True) and not r["unbounded"]) or not a.check else 1
    if cmd == "audit-offline":
        from .offline_audit import audit
        r = audit(_settings())
        out(r)
        return 0 if r["core_offline_ok"] else 1
    if cmd == "bundle":
        from . import bundle
        if a.sub == "create":
            m = bundle.create(Path(a.out), models=a.models)
            print(f"bundle written: {len(m['files'])} files")
            return 0
        if a.sub == "verify":
            p = bundle.verify(Path(a.bundle))
            print("\n".join(p) or "BUNDLE OK")
            return 0 if not p else 1
        if a.sub == "install-offline":
            bundle.install_offline(Path(a.bundle), Path(a.venv))
            print("offline install OK")
            return 0
    ap.error(f"unknown command {cmd}")
    return 2


def run_activated(a) -> int:
    """RUN: start only an activated, still-matching configuration. A different machine or configuration needs
    requalification (INSTALL) first — the system never runs an unqualified setup silently."""
    from . import discovery, manifest as mf, profiles, resolver
    from backend.contracts import canonical_hash
    active_p = ddir() / "active.json"
    if not active_p.is_file():
        print("NOT ACTIVATED: run INSTALL first (install, qualify, activate)")
        return 20
    active = json.loads(active_p.read_text())
    try:
        m = mf.verify(ddir() / "environment_manifest.json", REPO_ROOT / "configuration" / "deployment_ed25519.pub")
    except mf.ManifestError as exc:
        print(f"REFUSED: {exc}")
        return 21
    if m["content_sha256"] != active["manifest_sha256"]:
        print("REFUSED: environment manifest differs from the activated one: run INSTALL to requalify")
        return 21
    s = _settings()
    gpu_now = discovery.nvidia_gpus()
    if [g["name"] for g in gpu_now["gpus"]] != [g["name"] for g in m["gpu"]] or \
            profiles.check(active["profile"], {**m, "gpu": gpu_now, "runtimes": {"vllm": {"python_package":
                                                                                         "vllm" in m["available_model_runtimes"]}},
                                               "isolation": m["isolation_capabilities"]}):
        print("REFUSED: hardware changed since activation (GPU set differs or profile no longer compatible): "
              "run INSTALL to requalify")
        return 22
    r = resolver.resolve()
    if canonical_hash(r.effective) != active["effective_sha256"]:
        print("REFUSED: effective configuration changed since qualification: run INSTALL to requalify "
              f"(see {ddir() / 'effective_config.txt'})")
        return 23
    code = subprocess.run([sys.executable, "-m", "backend.service_manager", "start"], cwd=REPO_ROOT,
                          env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}).returncode
    if code == 0:
        print(f"ATTa API: http://{s.api.host}:{s.api.port}/v1/health/ready  (profile {active['profile']})")
        web = (s.security.cors_origins or [None])[0]        # the configured web origin, not a hard-coded port
        if web and not a.no_browser and (os.environ.get("DISPLAY") or os.name == "nt"):
            import webbrowser
            webbrowser.open(web + "/")
    return code


if __name__ == "__main__":
    sys.exit(main())
