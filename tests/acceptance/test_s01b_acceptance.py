"""Stage 01B acceptance tests S01B-T01 … S01B-T45 (PROTECTED): adaptive deployment, isolated offline qualification
and Stage 01 closure. Real discovery, real disposable PostgreSQL clusters, real network namespaces, real processes.
Where a pure selection function is exercised with a hardware description that this machine does not have, the
description is a modified copy of the REAL discovery result and is labelled synthetic in the evidence; it is never
written to a manifest and never counts as hardware qualification (S01B-T43 needs real H100 hardware)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from s01b_common import (PY, ROOT, clean_env, disposable_pg, evidence_digest, junit, make_gguf, record,
                         run_s01a_isolated, settings_with, source_tree, synthetic_env)

from backend.deployment import (deps, discovery, isolation, manifest as mf, models as mdl, profiles, resolver,
                                runtimes)

S01A_A = "tests/acceptance/test_s01a_acceptance.py"
S01A_B = "tests/acceptance/test_s01a_acceptance_b.py"
FIX = Path(__file__).parent / "fixtures_s01b"
LINUX = sys.platform.startswith("linux")


@pytest.fixture(scope="module")
def real_env():
    """One real, read-only discovery of this machine shared by the module (discovery takes several seconds)."""
    return discovery.discover(settings_with())


def _tree_digest(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(d)).encode() + p.read_bytes())
    return h.hexdigest()


def _installer(tmp: Path, extra_env: dict, **kw):
    from backend.deployment.installer import Installer
    env = clean_env(**extra_env)
    return Installer(deploy_dir=tmp, environment="test", env=env, **kw)


def _transitions(d: Path) -> list[dict]:
    return [json.loads(x) for x in (d / "install_transitions.jsonl").read_text().splitlines()]


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:   # a zombie is not running
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------ T01-T06
def test_S01B_T01_environment_discovery_on_supported_os(real_env):
    before = _tree_digest(ROOT / "configuration")
    env = discovery.discover(settings_with())
    assert _tree_digest(ROOT / "configuration") == before, "discovery must be read-only"
    for key in ("operating_system", "architecture", "host_id", "cpu", "ram", "storage", "gpu", "docker", "tools",
                "services", "runtimes", "ports", "network_interfaces", "external_network", "isolation"):
        assert key in env, key
    import platform
    assert env["operating_system"]["system"] == platform.system()
    assert env["cpu"]["logical_cores"] >= 1 and env["ram"]["total_mb"] > 0 and env["storage"]
    assert socket.gethostname() not in json.dumps(env), "raw host name must not be recorded"
    assert len(env["host_id"]) >= 12
    assert set(env["gpu"]) >= {"present", "gpus"}
    assert env["tools"]["python"]
    record("S01B-T01", {k: env[k] for k in ("operating_system", "architecture", "cpu", "ram", "gpu", "isolation")})


def test_S01B_T02_deterministic_profile_selection(real_env):
    d1, d2 = profiles.decide("AUTO", real_env), profiles.decide("AUTO", real_env)
    assert asdict(d1) == asdict(d2)
    expected_real = "H100" if not profiles.check("H100", real_env) else \
        ("LOCAL_NVIDIA" if not profiles.check("LOCAL_NVIDIA", real_env) else "LOCAL_CPU")
    assert d1.selected == expected_real and d1.compatible
    cases = {  # synthetic hardware descriptions (copies of the real discovery with hardware facts replaced)
        "no_gpu": (synthetic_env(real_env), "LOCAL_CPU"),
        "rtx_8gb": (synthetic_env(real_env, gpus=[("NVIDIA GeForce RTX 4060", 8188)], cuda="12.4"), "LOCAL_NVIDIA"),
        "a100_80gb": (synthetic_env(real_env, gpus=[("NVIDIA A100-SXM4-80GB", 81920)], cuda="12.4", vllm=True,
                                    system="Linux"), "LOCAL_NVIDIA"),
        "h100_80gb": (synthetic_env(real_env, gpus=[("NVIDIA H100 80GB HBM3", 81559)], cuda="12.4", vllm=True,
                                    system="Linux"), "H100"),
    }
    got = {}
    for name, (env, want) in cases.items():
        picks = {profiles.decide("AUTO", env).selected for _ in range(3)}
        assert picks == {want}, (name, picks)
        got[name] = want
    assert all(profiles.decide("AUTO", e).selected not in ("AIR_GAPPED", "TEST_ISOLATED") for e, _ in cases.values())
    record("S01B-T02", {"real_machine": asdict(d1), "synthetic_cases": got})


def test_S01B_T03_explicit_profile_override_validation(real_env, tmp_path):
    ok = profiles.decide("TEST_ISOLATED", real_env)
    assert ok.compatible and ok.selected == "TEST_ISOLATED"
    bogus = profiles.decide("TURBO", real_env)
    assert not bogus.compatible and "unknown profile" in bogus.reasons[0]
    gpu_less = synthetic_env(real_env)
    nv = profiles.decide("LOCAL_NVIDIA", gpu_less)
    assert not nv.compatible and nv.selected is None and "NVIDIA GPU" in nv.reasons[0]
    # explicit override through configuration is validated, not trusted
    res = resolver.resolve("test", env=clean_env(ATTA_PROFILE="TURBO"))
    assert not res.valid
    res_ok = resolver.resolve("test", env=clean_env(ATTA_PROFILE="LOCAL_CPU"))
    assert res_ok.valid and res_ok.settings.deployment.profile == "LOCAL_CPU"
    record("S01B-T03", {"test_isolated": asdict(ok), "unknown": asdict(bogus), "nvidia_without_gpu": asdict(nv),
                        "config_errors": res.errors})


def test_S01B_T04_reject_incompatible_h100_profile(real_env, tmp_path):
    here = profiles.decide("H100", real_env)
    if not profiles.check("H100", real_env):
        pytest.skip("this machine IS H100-compatible; rejection is tested on other hardware")
    assert not here.compatible and here.selected is None and here.reasons
    for name, env in {
        "a100_80gb": synthetic_env(real_env, gpus=[("NVIDIA A100-SXM4-80GB", 81920)], cuda="12.4", vllm=True, system="Linux"),
        "h100_without_cuda": synthetic_env(real_env, gpus=[("NVIDIA H100 80GB HBM3", 81559)], cuda=None, vllm=True,
                                           system="Linux"),
        "h100_without_vllm": synthetic_env(real_env, gpus=[("NVIDIA H100 80GB HBM3", 81559)], cuda="12.4", system="Linux"),
        "h100_pcie_40gb": synthetic_env(real_env, gpus=[("NVIDIA H100 PCIe", 40960)], cuda="12.4", vllm=True, system="Linux"),
        "h100_on_windows": synthetic_env(real_env, gpus=[("NVIDIA H100 80GB HBM3", 81559)], cuda="12.4", vllm=True,
                                         system="Windows"),
    }.items():
        assert not profiles.decide("H100", env).compatible, name
    # the installer refuses the H100 profile and never reports it healthy
    inst = _installer(tmp_path / "deploy", {}, profile="H100")
    code = inst.run()
    assert code == 11 and inst.state["state"] == "BLOCKED"
    assert not (tmp_path / "deploy" / "active.json").exists()
    m = json.loads((tmp_path / "deploy" / "environment_manifest.json").read_text())
    assert m["profile"] is None and m["profile_requested"] == "H100" and not m["profile_compatible"]
    record("S01B-T04", {"reasons": here.reasons, "installer_exit": code, "transitions": _transitions(tmp_path / "deploy")})


def _manifest(real_env, s, tmp: Path) -> Path:
    d = profiles.decide("AUTO", real_env)
    body = mf.build(real_env, d, [], s, configuration_source={"test": "S01B"})
    return mf.write(body, tmp)


def test_S01B_T05_environment_manifest_persistence(real_env, tmp_path):
    s = settings_with()
    p = _manifest(real_env, s, tmp_path)
    doc = mf.verify(p)
    required = ("environment_id", "profile", "operating_system", "architecture", "cpu", "ram", "storage", "gpu",
                "gpu_vram", "nvidia_driver", "cuda_status", "available_model_runtimes", "available_models",
                "required_services", "available_services", "unavailable_services", "ports", "isolation_capabilities",
                "offline_capability", "configuration_source", "discovery_timestamp", "qualification_status",
                "content_sha256")
    assert all(k in doc for k in required), [k for k in required if k not in doc]
    hist = tmp_path / "manifests" / f"{doc['environment_id']}.json"
    assert hist.is_file() and json.loads(hist.read_text())["content_sha256"] == doc["content_sha256"]
    again = mf.verify(p)
    assert again == doc
    record("S01B-T05", {"environment_id": doc["environment_id"], "content_sha256": doc["content_sha256"]})


def test_S01B_T06_manifest_integrity_validation(real_env, tmp_path, monkeypatch):
    from backend.capability_registry.trust import generate_keypair
    s = settings_with()
    p = _manifest(real_env, s, tmp_path / "plain")
    doc = json.loads(p.read_text())
    doc["profile"] = "H100"
    p.write_text(json.dumps(doc))
    with pytest.raises(mf.ManifestError, match="modified"):
        mf.verify(p)
    keys = tmp_path / "keys"
    keys.mkdir()
    generate_keypair(keys / "deploy.pem", keys / "deploy.pub")
    monkeypatch.setenv("ATTA_DEPLOY_SIGNING_KEY_FILE", str(keys / "deploy.pem"))
    sp = _manifest(real_env, s, tmp_path / "signed")
    assert json.loads(sp.read_text())["signature_ed25519"]
    mf.verify(sp, keys / "deploy.pub")
    other = tmp_path / "other"
    other.mkdir()
    generate_keypair(other / "o.pem", other / "o.pub")
    with pytest.raises(mf.ManifestError, match="signature invalid"):
        mf.verify(sp, other / "o.pub")
    monkeypatch.delenv("ATTA_DEPLOY_SIGNING_KEY_FILE")
    unsigned = _manifest(real_env, s, tmp_path / "unsigned")
    with pytest.raises(mf.ManifestError, match="not signed"):
        mf.verify(unsigned, keys / "deploy.pub")
    assert "PRIVATE KEY" not in sp.read_text()
    record("S01B-T06", {"tamper": "detected", "signature": "verified/invalid/missing detected"})


# ------------------------------------------------------------------------------------------------ T07-T11 models/runtimes
def test_S01B_T07_model_discovery_from_configured_path(tmp_path):
    store = tmp_path / "models"
    make_gguf(store / "llama" / "tiny-q4.gguf")
    hf = store / "hf" / "tiny-hf"
    hf.mkdir(parents=True)
    (hf / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"], "max_position_embeddings": 8192}))
    (hf / "model.safetensors").write_bytes(b"\0" * 2048)
    s = settings_with(ATTA__MODELS__STORAGE_PATHS=json.dumps([str(store)]))
    found = mdl.discover_models(s, [], tmp_path / "index.json")
    by = {m.model_id: m for m in found}
    g = by["tiny-q4"]
    assert g.format == "gguf" and g.display_name == "Tiny Test Llama" and g.context_length == 4096
    assert g.architecture == "llama" and g.status == "AVAILABLE" and set(g.compatible_runtimes) == {"llamacpp", "ollama"}
    assert g.fingerprint.startswith("sha256-partial:") and g.size_bytes == (store / "llama" / "tiny-q4.gguf").stat().st_size
    h = by["tiny-hf"]
    assert h.format == "safetensors" and h.compatible_runtimes == ["vllm"] and h.context_length == 8192
    # incremental: an unchanged file is taken from the index; a changed one is re-read
    idx = json.loads((tmp_path / "index.json").read_text())
    assert str(store / "llama" / "tiny-q4.gguf") in idx
    make_gguf(store / "llama" / "tiny-q4.gguf", name="Renamed Tiny", padding=5000)
    again = {m.model_id: m for m in mdl.discover_models(s, [], tmp_path / "index.json")}
    assert again["tiny-q4"].display_name == "Renamed Tiny"
    record("S01B-T07", {m.model_id: asdict(m) for m in found})


def test_S01B_T08_removable_model_path_discovery(tmp_path):
    usb = tmp_path / "usb-stick"
    make_gguf(usb / "models" / "portable.gguf", name="Portable Model")
    s = settings_with(ATTA__MODELS__STORAGE_PATHS="[]")
    storage = [{"mountpoint": str(usb), "removable": True, "fstype": "vfat"}]   # as storage_info() reports removables
    found = mdl.discover_models(s, storage, None)
    m = mdl.find(found, "portable")
    assert m is not None and m.removable and m.display_name == "Portable Model"
    off = settings_with(ATTA__MODELS__STORAGE_PATHS="[]", ATTA__DEPLOYMENT__SCAN_REMOVABLE_MEDIA="false")
    assert mdl.find(mdl.discover_models(off, storage, None), "portable") is None
    roots = [str(r) for r, rem in mdl.candidate_roots(s, storage) if rem]
    assert roots == [str(usb)]
    record("S01B-T08", {"removable_roots": roots, "model": asdict(m)})


def test_S01B_T09_no_unnecessary_model_copying(tmp_path):
    store = tmp_path / "models"
    f = make_gguf(store / "big.gguf", padding=8 << 20)
    st = f.stat()
    deploy = tmp_path / "deploy"
    s = settings_with(ATTA__MODELS__STORAGE_PATHS=json.dumps([str(store)]))
    found = mdl.discover_models(s, [], deploy / "model_index.json")
    assert mdl.find(found, "big").path == str(f)
    assert sorted(p.name for p in store.iterdir()) == ["big.gguf"]
    st2 = f.stat()
    assert (st2.st_ino, st2.st_size, st2.st_mtime_ns) == (st.st_ino, st.st_size, st.st_mtime_ns)
    copied = sum(p.stat().st_size for p in deploy.rglob("*") if p.is_file())
    assert copied < 64 * 1024, f"{copied} bytes written by discovery: models must be referenced, not copied"
    assert s.models.allow_model_downloads is False
    record("S01B-T09", {"model_bytes": st.st_size, "bytes_written_by_discovery": copied})


def test_S01B_T10_missing_model_returns_blocked(real_env, tmp_path):
    store = tmp_path / "models"
    (store).mkdir()
    (store / "broken.gguf").write_bytes(b"NOTGGUF" + b"\0" * 64)
    hf = store / "hf-only"
    hf.mkdir()
    (hf / "config.json").write_text("{}")
    (hf / "model.safetensors").write_bytes(b"\0" * 64)
    s = settings_with(ATTA__MODELS__STORAGE_PATHS=json.dumps([str(store)]))
    found = mdl.discover_models(s, [], None)
    cpu = synthetic_env(real_env)
    assert runtimes.select("LOCAL_CPU", "llama-3-70b", found, cpu, ["llamacpp"]).status == "MODEL_NOT_FOUND"
    assert runtimes.select("LOCAL_CPU", "broken", found, cpu, ["llamacpp"]).status == "MODEL_INCOMPATIBLE"
    assert runtimes.select("LOCAL_CPU", "hf-only", found, cpu, ["vllm", "llamacpp"]).status == "MODEL_INCOMPATIBLE"
    deploy = tmp_path / "deploy"
    cp = subprocess.run([PY, "-m", "backend.deployment", "models", "--require", "no-such-model-xyz"], cwd=ROOT,
                        capture_output=True, text=True, timeout=300,
                        env=clean_env(ATTA_DEPLOYMENT_DIR=str(deploy),
                                      ATTA__MODELS__STORAGE_PATHS=json.dumps([str(store)])))
    out = json.loads(cp.stdout)
    assert cp.returncode == 11 and out["status"] == "MODEL_NOT_FOUND" and out["runtime"] is None
    assert "nothing will be downloaded" in out["reason"]
    record("S01B-T10", {"cli": out, "exit": cp.returncode})


def test_S01B_T11_runtime_discovery(real_env, tmp_path, monkeypatch):
    rt = real_env["runtimes"]
    assert set(rt) >= {"llamacpp", "ollama", "vllm", "endpoints"}
    assert rt["vllm"]["python_package"] == (__import__("importlib.util").util.find_spec("vllm") is not None)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    exe = fake_bin / "llama-server"
    exe.write_text("#!/bin/sh\necho 'version: 9999 (test fixture)'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    found = discovery.runtimes(settings_with())
    assert found["llamacpp"]["binary"] == str(exe)
    env = {**real_env, "runtimes": found, "gpu": {"present": False, "gpus": []}}
    assert "llamacpp" in runtimes.available_runtimes(env)
    store = tmp_path / "m"
    make_gguf(store / "tiny.gguf")
    models = mdl.discover_models(settings_with(ATTA__MODELS__STORAGE_PATHS=json.dumps([str(store)])), [], None)
    ch = runtimes.select("LOCAL_CPU", "tiny", models, env, ["vllm", "llamacpp", "ollama"])
    assert ch.status == "SELECTED" and ch.runtime == "llamacpp"
    none = runtimes.select("LOCAL_CPU", "tiny", models, {**env, "runtimes": {"endpoints": {}}}, ["llamacpp"])
    assert none.status == "RUNTIME_UNAVAILABLE" and "no external fallback" in none.reason
    record("S01B-T11", {"real": {k: rt[k] for k in ("llamacpp", "ollama", "vllm")}, "selected": asdict(ch)})


# ------------------------------------------------------------------------------------------------ T12-T17 config/install
def test_S01B_T12_port_conflict_detection(real_env, tmp_path):
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        sk.listen()
        port = sk.getsockname()[1]
        s = settings_with(ATTA__REDIS__PORT=str(port))
        ports = discovery.ports(s)
        assert ports["redis"]["port"] == port and ports["redis"]["in_use"]
        res = resolver.resolve("test", env=clean_env(ATTA__REDIS__PORT=str(port)))
        resolver.validate_against_environment(res, {**real_env, "ports": ports}, profiles.decide("LOCAL_CPU", real_env))
        assert any(str(port) in w and "redis" in w for w in res.warnings), res.warnings
        # the service manager refuses to start a managed service on an occupied port
        env = clean_env(ATTA__REDIS__PORT=str(port), ATTA__API__PORT=str(isolation.free_port()),
                        ATTA__RUNTIME__DIRECTORY=str(tmp_path / "rt"), ATTA__LOGGING__DIRECTORY=str(tmp_path / "rt/logs"))
        cp = subprocess.run([PY, "-m", "backend.service_manager", "start", "--json"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=180)
        subprocess.run([PY, "-m", "backend.service_manager", "stop"], cwd=ROOT, env=env, capture_output=True, timeout=180)
    assert cp.returncode != 0 and ("in use" in cp.stdout.lower() or "port" in cp.stdout.lower()), cp.stdout[-1500:]
    record("S01B-T12", {"port": port, "warnings": res.warnings, "manager_exit": cp.returncode})


def test_S01B_T13_configuration_precedence(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "configuration", root / "configuration",
                    ignore=shutil.ignore_patterns("local.env", "local.yaml", "operator.yaml"))
    (root / "configuration" / "local.env").write_text("")
    disc = root / "runtime" / "deployment"
    disc.mkdir(parents=True)
    key = ("workers", "memory_limit_mb")
    (disc / "discovered.yaml").write_text(yaml.safe_dump({"workers": {"memory_limit_mb": 111, "max_concurrency": 3}}))
    env = clean_env(ATTA_PROFILE="LOCAL_CPU", ATTA_DATABASE_PASSWORD="t13-not-a-real-secret")
    r1 = resolver.resolve("test", root=root, env=env, discovered_dir=disc)
    # discovered (111) is overridden by the LOCAL_CPU profile (1024); max_concurrency: profile 2 over discovered 3
    assert r1.valid and r1.settings.workers.memory_limit_mb == 1024 and r1.provenance["workers.memory_limit_mb"] == "profile:LOCAL_CPU"
    (root / "configuration" / "local.yaml").write_text(yaml.safe_dump({"workers": {"memory_limit_mb": 333}}))
    r2 = resolver.resolve("test", root=root, env=env, discovered_dir=disc)
    assert r2.settings.workers.memory_limit_mb == 333 and r2.provenance["workers.memory_limit_mb"] == "local"
    r3 = resolver.resolve("test", root=root, env={**env, "ATTA__WORKERS__MEMORY_LIMIT_MB": "444"}, discovered_dir=disc)
    assert r3.settings.workers.memory_limit_mb == 444 and r3.provenance["workers.memory_limit_mb"] == "local-environment"
    (root / "configuration" / "operator.yaml").write_text(yaml.safe_dump({"workers": {"memory_limit_mb": 555}}))
    r4 = resolver.resolve("test", root=root, env={**env, "ATTA__WORKERS__MEMORY_LIMIT_MB": "444"}, discovered_dir=disc)
    assert r4.settings.workers.memory_limit_mb == 555 and r4.provenance["workers.memory_limit_mb"] == "operator"
    (disc / "discovered.yaml").write_text(yaml.safe_dump({"deployment": {"model_context_tokens": 2048}}))
    (root / "configuration" / "local.yaml").unlink()
    r5 = resolver.resolve("test", root=root, env=clean_env(ATTA_DATABASE_PASSWORD="t13-not-a-real-secret"),
                          discovered_dir=disc)
    assert r5.provenance.get("deployment.model_context_tokens") == "discovered"
    (root / "configuration" / "operator.yaml").write_text(yaml.safe_dump({"workers": {"memory_limit_mb": -5}}))
    bad = resolver.resolve("test", root=root, env=env, discovered_dir=disc)
    assert not bad.valid and bad.errors
    report = resolver.report(r4, profiles.ProfileDecision("LOCAL_CPU", "LOCAL_CPU", True, [], {}))
    assert "workers.memory_limit_mb = 555    [operator]" in report
    record("S01B-T13", {"chain": [1024, 333, 444, 555], "provenance": [r.provenance["workers.memory_limit_mb"]
                                                                       for r in (r1, r2, r3, r4)], "invalid": bad.errors})


def test_S01B_T14_secret_exclusion_from_generated_reports(real_env, tmp_path):
    secret = "S01B-unique-" + hashlib.sha256(os.urandom(16)).hexdigest()[:20]
    env = clean_env(ATTA_DATABASE_PASSWORD=secret, ATTA_REDIS_PASSWORD=secret + "r")
    res = resolver.resolve("test", env=env)
    assert res.valid and res.settings.database.password.get_secret_value() == secret
    text = resolver.report(res) + json.dumps(res.effective, default=str) + json.dumps(res.provenance)
    assert secret not in text and "secret:ATTA_DATABASE_PASSWORD" in text
    body = mf.build(real_env, profiles.decide("AUTO", real_env), [], res.settings,
                    configuration_source={"password": secret, "nested": {"api_token": secret}})
    assert secret not in json.dumps(body)
    deploy = tmp_path / "deploy"
    inst = _installer(deploy, {"ATTA_DATABASE_PASSWORD": secret}, profile="LOCAL_CPU")
    inst.run()                                   # stops at AWAITING_OPERATOR_ACTION: plan + reports written
    leaked = [p.name for p in deploy.rglob("*") if p.is_file() and secret in p.read_text(errors="replace")]
    assert leaked == [], leaked
    record("S01B-T14", {"files_checked": sorted(p.name for p in deploy.rglob("*") if p.is_file()), "leaks": leaked})


def test_S01B_T15_installation_plan_generation(tmp_path):
    deploy = tmp_path / "deploy"
    inst = _installer(deploy, {}, profile="LOCAL_CPU", start_services=False)
    code = inst.run()
    assert code == 10 and inst.state["state"] == "AWAITING_OPERATOR_ACTION"
    plan = json.loads((deploy / "install_plan.json").read_text())
    for k in ("detected_environment", "selected_profile", "existing_dependencies", "missing_dependencies",
              "configuration_changes", "services_to_start", "ports", "model_locations", "storage_requirements_mb",
              "actions", "admin_actions", "network_actions", "offline_actions", "qualification_tests",
              "rollback_actions", "plan_sha256"):
        assert k in plan, k
    assert plan["selected_profile"] == "LOCAL_CPU"
    assert not (deploy / "active.json").exists() and not (deploy / "rollback").exists(), "no changes before approval"
    states = [t["to"] for t in _transitions(deploy)]
    assert states == ["DISCOVER", "VALIDATE", "PLAN", "AWAITING_OPERATOR_ACTION"]
    record("S01B-T15", {"plan": {k: plan[k] for k in ("selected_profile", "actions", "plan_sha256")}})


def test_S01B_T16_resumable_installation(tmp_path):
    deploy = tmp_path / "deploy"
    with disposable_pg(tmp_path / "pg") as pg:
        first = _installer(deploy, pg.env(), profile="LOCAL_CPU", start_services=False, qualify_suite="")
        assert first.run() == 10
        run_id = first.state["run_id"]
        # a NEW process (fresh Installer object) resumes from the persisted state; approval continues the plan
        second = _installer(deploy, pg.env(), profile="LOCAL_CPU", start_services=False, qualify_suite="", approve=True)
        assert second.state["state"] == "AWAITING_OPERATOR_ACTION"
        assert second.run() == 0 and second.state["state"] == "READY"
        tr = _transitions(deploy)
        assert [t["to"] for t in tr].count("DISCOVER") == 1, "approval must resume, not restart discovery"
        assert {t["run_id"] for t in tr} == {run_id}
        assert [t["to"] for t in tr][-6:] == ["PROVISION", "CONFIGURE", "START_SERVICES", "QUALIFY", "ACTIVATE", "READY"]
        # completed steps are recorded with their input hashes so an interrupted run skips them
        assert {"DISCOVER", "VALIDATE", "PLAN", "PROVISION", "CONFIGURE"} <= set(second.state["completed"])
        # simulate an interruption after CONFIGURE (state persisted, process gone) and resume
        st = json.loads((deploy / "install_state.json").read_text())
        st["state"] = "START_SERVICES"
        (deploy / "install_state.json").write_text(json.dumps(st))
        third = _installer(deploy, pg.env(), profile="LOCAL_CPU", start_services=False, qualify_suite="")
        assert third.run() == 0
        tail = [t["to"] for t in _transitions(deploy)][-3:]
        assert tail == ["QUALIFY", "ACTIVATE", "READY"]
    record("S01B-T16", {"transitions": _transitions(deploy)})


def test_S01B_T17_rollback_after_controlled_failure(tmp_path):
    deploy = tmp_path / "deploy"
    rt = tmp_path / "rt"
    with disposable_pg(tmp_path / "pg") as pg:
        base = {**pg.env(), "ATTA__RUNTIME__DIRECTORY": str(rt), "ATTA__LOGGING__DIRECTORY": str(rt / "logs"),
                "ATTA__REDIS__PORT": str(isolation.free_port())}
        ok = _installer(deploy, {**base, "ATTA__API__PORT": str(isolation.free_port())}, profile="LOCAL_CPU",
                        start_services=False, qualify_suite="", approve=True)
        assert ok.run() == 0
        good = (deploy / "active.json").read_text()
        with socket.socket() as blocker:            # controlled failure: the API port is taken by another process
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            bad_env = {**base, "ATTA__API__PORT": str(blocker.getsockname()[1])}
            failing = _installer(deploy, bad_env, profile="LOCAL_CPU", start_services=True, qualify_suite="",
                                 approve=True)
            code = failing.run()
        tr = _transitions(deploy)
        assert code == 13 and failing.state["state"] == "ROLLED_BACK", tr[-3:]
        assert [t["to"] for t in tr][-3:] == ["FAILED", "ROLLING_BACK", "ROLLED_BACK"]
        assert (deploy / "active.json").read_text() == good, "previous activation must be restored"
        # services started by the failed run were stopped: nothing listens on the run's Redis port any more
        assert not discovery.tcp_open("127.0.0.1", int(base["ATTA__REDIS__PORT"])), "redis left running after rollback"
        pidf = rt / "state" / "manager.pid"
        assert not pidf.is_file() or not _pid_alive(int(pidf.read_text().strip() or 0)), "service manager left running"
    record("S01B-T17", {"transitions": tr[-5:]})


# ------------------------------------------------------------------------------------------------ T18-T20 deps/offline
def test_S01B_T18_dependency_lock_validation(tmp_path):
    drift = deps.python_drift()
    assert drift["ok"], drift
    lock = (ROOT / "configuration/locks/backend-core.lock.txt").read_text()
    import re
    m = re.search(r"^(fastapi)==([\w.]+)", lock, re.M)
    assert m
    bad = tmp_path / "lock.txt"
    bad.write_text(lock.replace(f"fastapi=={m.group(2)}", "fastapi==0.0.1"))
    d2 = deps.python_drift(bad)
    assert not d2["ok"] and any("fastapi" in x for x in json.dumps(d2).split('"'))
    assert deps.unbounded_specs() == []
    cov = deps.import_coverage()          # every third-party import of the source tree is pinned in a lock
    assert cov["ok"], cov["missing"]
    assert cov["modules"]["fastapi"]["lock"] == "core" and cov["modules"]["playwright"]["lock"] == "full"
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "uses_unlocked.py").write_text("import totally_unlocked_pkg_xyz\n")
    orig = deps.SOURCE_DIRS
    deps.SOURCE_DIRS = orig + [str(planted)]
    try:
        assert "totally_unlocked_pkg_xyz" in deps.import_coverage()["missing"], "an unlocked import must be detected"
    finally:
        deps.SOURCE_DIRS = orig
    for f in ("system-dependencies.yaml", "runtime-compatibility.yaml"):
        assert yaml.safe_load((ROOT / "configuration/locks" / f).read_text())
    cp = subprocess.run([PY, "-m", "backend.deployment", "deps", "--check"], cwd=ROOT, env=clean_env(),
                        capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stdout[-2000:]
    record("S01B-T18", {"drift_detected": d2, "node": deps.node_drift()})


def test_S01B_T19_offline_dependency_manifest_validation(tmp_path):
    from backend.deployment import bundle
    b = tmp_path / "bundle"
    # Full-coverage components are exercised by the AIR_GAPPED Q-OFFLINE-INSTALL step (bundled CPython, both npm
    # projects, browsers); here: every Python lock, system packages, image handling, manifest integrity.
    bundle.create(b, include_node=False, python_runtime=False, browsers=False)
    assert bundle.verify(b) == []
    m = json.loads((b / "bundle_manifest.json").read_text())
    cov = m["coverage"]
    assert cov["python_wheels"]["backend-core.lock.txt"]["packages"] > 0
    assert cov["python_wheels"]["backend-full.lock.txt"]["packages"] >= cov["python_wheels"]["backend-core.lock.txt"]["packages"]
    from backend.deployment.deps import python_lock
    names = {w.name.split("-")[0].lower().replace("_", "-") for w in (b / "wheels").glob("*.whl")}
    full = set(python_lock(ROOT / "configuration/locks/backend-full.lock.txt"))
    assert {n.replace("_", "-") for n in full} <= {n.replace(".", "-") for n in names} | \
        {n for n in full if n.replace("-", "_") in {x.replace("-", "_") for x in names}}, "every locked package has a wheel"
    if sys.platform.startswith("linux") and shutil.which("apt-get"):
        assert cov["system_packages"]["status"] == "INCLUDED" and (b / "system/debs/Packages").is_file()
        assert cov["system_packages"]["debs"] == cov["system_packages"]["closure"] > 0
    assert cov["container_images"]["status"] in ("INCLUDED", "BLOCKED")
    if cov["container_images"]["status"] == "BLOCKED":
        assert "Docker" in cov["container_images"]["reason"] and len(cov["container_images"]["images"]) >= 4
    assert m["includes_models"] is False and not (b / "models").exists()
    assert all(len(v["sha256"]) == 64 for v in m["files"].values())
    wheel = next(p for p in (b / "wheels").iterdir())
    data = bytearray(wheel.read_bytes())
    data[-1] ^= 0xFF
    wheel.write_bytes(bytes(data))
    probs = bundle.verify(b)
    assert any(wheel.name in p for p in probs), probs
    data[-1] ^= 0xFF
    wheel.write_bytes(bytes(data))
    (b / "assets" / "extra.txt").write_text("unlisted")
    assert any("extra.txt" in p for p in bundle.verify(b))
    (b / "assets" / "extra.txt").unlink()
    (b / "assets" / "k.pem").write_text("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    assert any("private key" in p.lower() for p in bundle.verify(b))
    (b / "assets" / "k.pem").unlink()
    # Node coverage check detects a cache that lacks locked packages
    empty = tmp_path / "empty-cache"
    empty.mkdir()
    nc = bundle.node_coverage(ROOT / "package-lock.json", empty)
    assert nc["missing_count"] > 0 and nc["cached"] == 0
    record("S01B-T19", {"files": len(m["files"]), "coverage": cov})


def test_S01B_T20_core_frontend_contains_no_mandatory_external_assets():
    from backend.deployment.offline_audit import audit
    a = audit(settings_with())
    assert a["core_offline_ok"], a
    assert not [f for f in a["findings"] if "external asset" in f]
    nc = (ROOT / "apps/web/next.config.ts").read_text()
    assert "next/font/google" not in nc
    for d in ("apps/web/app", "apps/web/lib", "packages/design_system/src"):
        for p in (ROOT / d).rglob("*.ts*"):
            assert "next/font/google" not in p.read_text(errors="replace"), p
    for launcher in ("run.sh", "RUN.bat"):
        assert "NEXT_TELEMETRY_DISABLED" in (ROOT / launcher).read_text()
    record("S01B-T20", a)


# ------------------------------------------------------------------------------------------------ T21-T26 isolation
def test_S01B_T21_isolated_test_database(tmp_path):
    import psycopg
    prod = settings_with()
    with disposable_pg(tmp_path / "pg") as pg:
        assert pg.port != prod.database.port
        assert (pg.data / "PG_VERSION").is_file()
        data_dir = pg.data
        env = pg.env()
        with psycopg.connect(host="127.0.0.1", port=pg.port, dbname="atta", user="atta_app",
                             password=env["ATTA_DATABASE_PASSWORD"]) as c:
            ver = c.execute("SELECT count(*) FROM public.atta_schema_migrations").fetchone()[0]
            assert ver >= 1, "migrations applied in the disposable cluster"
            assert c.execute("SELECT current_user").fetchone()[0] == "atta_app"
            assert c.execute("SELECT inet_server_port()").fetchone()[0] == pg.port
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("CREATE TABLE control.s01b_probe(x int)")
        assert env["ATTA_DATABASE_PASSWORD"] != (prod.database.password.get_secret_value() if prod.database.password else None)
    assert not data_dir.exists(), "disposable cluster destroyed"
    record("S01B-T21", {"port": pg.port, "production_port": prod.database.port, "migrations": ver})


def test_S01B_T22_isolated_redis_state(tmp_path):
    import redis as redislib
    from backend.deployment.qualification import load_suites  # noqa: F401  (suite definitions load)
    prod = settings_with()
    port = isolation.free_port()
    assert port != prod.redis.port
    env = clean_env(ATTA__REDIS__PORT=str(port), ATTA__API__PORT=str(isolation.free_port()),
                    ATTA__RUNTIME__DIRECTORY=str(tmp_path / "rt"), ATTA__LOGGING__DIRECTORY=str(tmp_path / "rt/logs"))
    s = settings_with(**{k: v for k, v in env.items() if k.startswith("ATTA__")})
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "",
                             "--dir", str(tmp_path)], stdout=subprocess.DEVNULL)
    try:
        r = None
        for _ in range(50):
            try:
                r = redislib.Redis(host="127.0.0.1", port=port, socket_timeout=1)
                r.ping()
                break
            except redislib.ConnectionError:
                time.sleep(0.1)
        key = "s01b:isolation:" + os.urandom(4).hex()
        r.set(key, "qualification")
        assert s.redis.port == port
        try:
            prod_r = redislib.Redis(host=prod.redis.host, port=prod.redis.port, socket_timeout=1,
                                    password=prod.redis.password.get_secret_value() if prod.redis.password else None)
            assert prod_r.get(key) is None, "qualification state must not appear in the production Redis"
            prod_checked = True
        except redislib.ConnectionError:
            prod_checked = "production redis not running (nothing to leak into)"
    finally:
        proc.kill()
        proc.wait(5)
    # the qualification runner gives every run its own Redis port, runtime directory and bucket
    src = (ROOT / "backend/deployment/qualification.py").read_text()
    for var in ("ATTA__REDIS__PORT", "ATTA__RUNTIME__DIRECTORY", "ATTA__OBJECT_STORAGE__BUCKET", "ATTA__API__PORT"):
        assert var in src
    record("S01B-T22", {"isolated_port": port, "production_port": prod.redis.port, "production_checked": prod_checked})


def test_S01B_T23_isolated_filesystem_workspace(tmp_path):
    from backend.deployment.qualification import run_suite
    s = settings_with()
    prod_runtime = Path(s.runtime.directory)
    m = {"environment_id": "env_s01b_t23", "isolation_capabilities": {}, "gpu": [], "available_model_runtimes": []}
    out = tmp_path / "qual"
    r = run_suite("SMOKE", manifest=m, profile="TEST_ISOLATED", out_root=out)
    assert r["status"] == "PASS", json.dumps(r, indent=1)[:3000]
    ws = out / r["qualification_id"]
    assert (ws / "results.json").is_file()
    assert r["isolation"]["workspace"] == str(ws)
    for t in r["results"]:
        assert t["evidence_sha256"] and (ws / t["evidence"]).is_file() if t["evidence"] else True
    assert not str(ws).startswith(str((ROOT / prod_runtime).resolve()))
    # verifier independence: non-privileged qualification tests never run as root when a test identity exists
    import pwd
    try:
        pwd.getpwnam("atta-tester")
        tester = True
    except KeyError:
        tester = False
    for t in r["results"]:
        if not t["privileged"] and (tester or os.geteuid() != 0):
            assert t["run_as"] != "root", t
    record("S01B-T23", {"workspace": str(ws), "isolation": r["isolation"], "counts": r["counts"]})


def test_S01B_T24_production_state_remains_unchanged_by_qualification(tmp_path):
    from backend.deployment.qualification import run_suite
    from backend.settings import load_settings
    prod = load_settings()                       # the operator's real environment (not 'test')
    before = isolation.production_fingerprint(prod)
    m = {"environment_id": "env_s01b_t24", "isolation_capabilities": {}, "gpu": [], "available_model_runtimes": []}
    r = run_suite("SMOKE", manifest=m, profile="TEST_ISOLATED", out_root=tmp_path / "qual")
    assert r["status"] == "PASS"
    after = isolation.production_fingerprint(prod)
    assert before["files"] == after["files"]
    assert before["database"] == after["database"], "production database contents changed during qualification"
    assert before["sha256"] == after["sha256"]
    record("S01B-T24", {"fingerprint": before["sha256"], "database_tables": len(before["database"] or {})})


def _need_netns():
    if not isolation.netns_available():
        pytest.fail("no kernel network namespace available: air-gap cannot be enforced here (record BLOCKED, never PASS)")


@pytest.mark.privileged
def test_S01B_T25_external_network_denial_detection(tmp_path):
    _need_netns()
    from backend.deployment.qualification import run_suite
    m = {"environment_id": "env_s01b_t25", "isolation_capabilities": {"network_namespace": True}, "gpu": [],
         "available_model_runtimes": []}
    r = run_suite("SMOKE_AIRGAP", manifest=m, profile="TEST_ISOLATED", out_root=tmp_path / "qual")
    by = {t["test_id"]: t for t in r["results"]}
    assert by["Q-NETWORK-DENIED"]["status"] == "PASS", by["Q-NETWORK-DENIED"]
    assert r["network"] == "none" and "network namespace" in r["isolation"]["network"]
    # an attempted external connection inside the namespace fails AND is recorded by the audit hook
    log = tmp_path / "audit.jsonl"
    code = ("import socket\ntry:\n    socket.create_connection(('1.1.1.1', 443), timeout=2)\n    print('CONNECTED')\n"
            "except OSError as e:\n    print('DENIED', e.errno)\n")
    env = clean_env(ATTA_NET_AUDIT_LOG=str(log),
                    PYTHONPATH=str(ROOT / "backend/deployment/netaudit") + os.pathsep + str(ROOT))
    cp = subprocess.run(isolation.in_new_netns([PY, "-c", code]), cwd=ROOT, env=env, capture_output=True, text=True,
                        timeout=60)
    assert "DENIED" in cp.stdout and "CONNECTED" not in cp.stdout, cp.stdout + cp.stderr
    attempts = [json.loads(x) for x in log.read_text().splitlines()]
    assert any("1.1.1.1" in json.dumps(a) for a in attempts), attempts
    # loopback still works inside the namespace
    lo = subprocess.run(isolation.in_new_netns([PY, "-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));"
                                                      "s.listen();c=socket.create_connection(s.getsockname());print('LO')"]),
                        cwd=ROOT, env=clean_env(), capture_output=True, text=True, timeout=60)
    assert "LO" in lo.stdout, lo.stderr
    record("S01B-T25", {"suite": r["counts"], "audit": attempts, "inside": cp.stdout.strip()})


@pytest.mark.privileged
def test_S01B_T26_offline_core_startup(tmp_path):
    _need_netns()
    os.chmod(tmp_path, 0o755)
    out = tmp_path / "result.json"
    cp = subprocess.run(isolation.in_new_netns([PY, str(Path(__file__).parent / "s01b_offline_core.py"), str(tmp_path),
                                                str(out)]), cwd=ROOT, env=clean_env(), capture_output=True, text=True,
                        timeout=900)
    assert out.is_file(), cp.stdout[-3000:] + cp.stderr[-3000:]
    res = json.loads(out.read_text())
    assert res["external_reachable"] == [], res
    assert res["start_exit"] == 0, res
    assert res["ready_status"] == 200 and res["ready"]["ready"] is True, res
    assert res["live_status"] == 200
    assert res["controller_status"] == 200, res
    assert res["stop_exit"] == 0
    record("S01B-T26", res)


# ------------------------------------------------------------------------------------------------ T27-T33 runtime
def _redis(port: int, d: Path) -> subprocess.Popen:
    p = subprocess.Popen(["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--dir", str(d)],
                         stdout=subprocess.DEVNULL)
    return p


def test_S01B_T27_dynamic_dependency_failure_propagation(tmp_path):
    from s01a_harness import build
    from backend.service_manager.health_monitor import HealthMonitor
    from backend.service_manager.manager import ServiceRuntime
    port = isolation.free_port()
    proc = _redis(port, tmp_path)
    try:
        s = settings_with(ATTA__REDIS__PORT=str(port))
        mon = HealthMonitor(s, interval_s=60, max_age_s=0.0, names=["redis"], recovery_confirmations=2)
        for _ in range(50):
            mon.refresh()
            if mon.state("redis") == "HEALTHY":
                break
            time.sleep(0.1)
        assert mon.state("redis") == "HEALTHY"
        h = build(tmp_path, service_available=lambda n: mon.is_available(n, max_age_s=0.0))
        changes = []
        mon.on_change(lambda ch: (changes.append(sorted(ch)), h.registry.refresh_dependencies(),
                                  h.catalogue.refresh_availability()))
        assert h.registry.get("test.needs_redis").executable and h.catalogue.get("040.903").available
        proc.kill()
        proc.wait(5)
        mon.refresh()
        assert mon.state("redis") == "UNHEALTHY" and ["redis"] in changes
        assert h.registry.get("test.needs_redis").state.value == "dependency_missing"
        assert not h.catalogue.get("040.903").available
        # chained propagation inside the registry: a capability depending on an unhealthy capability is blocked
        assert h.registry.get("test.chained").state.value == "dependency_missing"
    finally:
        proc.kill()
    assert ServiceRuntime.HEALTH_STATES["unhealthy"] == "UNHEALTHY" and ServiceRuntime.HEALTH_STATES["unavailable"] == "BLOCKED"
    record("S01B-T27", {"changes": changes})


def test_S01B_T28_service_recovery_revalidation(tmp_path):
    from s01a_harness import build
    from backend.service_manager.health_monitor import HealthMonitor
    port = isolation.free_port()
    proc = _redis(port, tmp_path)
    try:
        s = settings_with(ATTA__REDIS__PORT=str(port))
        mon = HealthMonitor(s, interval_s=60, max_age_s=3600, names=["redis"], recovery_confirmations=3)
        for _ in range(50):
            mon.refresh()
            if mon.state("redis") == "HEALTHY":
                break
            time.sleep(0.1)
        h = build(tmp_path, service_available=lambda n: mon.is_available(n))
        mon.on_change(lambda ch: (h.registry.refresh_dependencies(), h.catalogue.refresh_availability()))
        proc.kill()
        proc.wait(5)
        mon.refresh()
        assert mon.state("redis") == "UNHEALTHY"
        proc = _redis(port, tmp_path)
        seen = []
        for _ in range(100):
            mon.refresh()
            if mon.snapshot()[0].status.value == "healthy":
                break
            time.sleep(0.1)
        seen.append(mon.state("redis"))
        assert seen[-1] == "DEGRADED", "first healthy probe after a failure only starts revalidation"
        assert not h.registry.get("test.needs_redis").executable
        mon.refresh()
        seen.append(mon.state("redis"))
        assert seen[-1] == "DEGRADED" and not h.registry.get("test.needs_redis").executable
        mon.refresh()
        seen.append(mon.state("redis"))
        assert seen[-1] == "HEALTHY" and h.registry.get("test.needs_redis").executable
    finally:
        proc.kill()
    record("S01B-T28", {"states_after_recovery": seen, "confirmations_required": 3})


def _ok(res: dict, names: list[str]) -> None:
    bad = {n: res.get(n, ("missing", ""))[0] for n in names if res.get(n, ("missing",))[0] != "passed"}
    assert not bad, (bad, {n: res[n][1][-1500:] for n in bad if n in res}, res.get("__summary__"))


def test_S01B_T29_stage01a_controller_works_under_local_cpu_profile(tmp_path):
    names = ["test_S01A_T06_generate_and_validate_task_contract", "test_S01A_T09_allocate_only_compatible_tools",
             "test_S01A_T53_complete_multi_task_objective"]
    res = run_s01a_isolated(tmp_path, [f"{S01A_A}::{names[0]}", f"{S01A_A}::{names[1]}", f"{S01A_B}::{names[2]}"],
                            "LOCAL_CPU")
    _ok(res, names)
    caps = profiles.resources("LOCAL_CPU", {"ram": {"total_mb": 16000}, "cpu": {"logical_cores": 4}},
                              {"max_active_tasks_per_objective": 4})
    assert caps["controller_caps"]["max_active_tasks_per_objective"] <= 4
    record("S01B-T29", {n: res[n][0] for n in names} | {"database": res["__database__"][1]})


def test_S01B_T30_focus4_works_in_isolated_qualification(tmp_path):
    names = ["test_S01A_T13_four_active_task_limit", "test_S01A_T14_concurrent_controllers_cannot_exceed_limit"]
    res = run_s01a_isolated(tmp_path, [f"{S01A_A}::{n}" for n in names], "TEST_ISOLATED")
    _ok(res, names)
    record("S01B-T30", {n: res[n][0] for n in names} | {"database": res["__database__"][1]})


def test_S01B_T31_drift_guard_works_in_isolated_qualification(tmp_path):
    names = ["test_S01A_T10_reject_tools_outside_pool", "test_S01A_T19_reject_unauthorised_resource_ids",
             "test_S01A_T20_reject_unauthorised_filesystem_paths",
             "test_S01A_T21_reject_unauthorised_network_destinations",
             "test_S01A_T22_reject_attempts_to_change_acceptance_criteria"]
    res = run_s01a_isolated(tmp_path, [f"{S01A_A}::{n}" for n in names], "TEST_ISOLATED")
    _ok(res, names)
    record("S01B-T31", {n: res[n][0] for n in names})


def test_S01B_T32_cancellation_leaves_no_subsequent_local_side_effects(tmp_path):
    names = ["test_S01A_T29_process_termination_after_cancellation", "test_S01A_T30_no_local_side_effects_after_timeout"]
    res = run_s01a_isolated(tmp_path, [f"{S01A_B}::{n}" for n in names], "TEST_ISOLATED")
    _ok(res, names)
    record("S01B-T32", {n: res[n][0] for n in names})


def test_S01B_T33_restart_restores_verified_state(tmp_path):
    names = ["test_S01A_T38_persist_and_restore_verified_checkpoint", "test_S01A_T39_consumed_budgets_survive_restart",
             "test_S01A_T04_identifiers_preserved_after_restart"]
    res = run_s01a_isolated(tmp_path, [f"{S01A_B}::{names[0]}", f"{S01A_B}::{names[1]}", f"{S01A_A}::{names[2]}"],
                            "TEST_ISOLATED")
    _ok(res, names)
    record("S01B-T33", {n: res[n][0] for n in names})


# ------------------------------------------------------------------------------------------------ T34-T35 launchers
LAUNCHERS = ["install.sh", "run.sh", "healthcheck.sh", "INSTALL.bat", "RUN.bat", "HEALTHCHECK.bat"]


def _logic_lines(name: str) -> list[str]:
    """Executable launcher lines: comments and operator help text (echo) excluded."""
    out = []
    for ln in (ROOT / name).read_text().splitlines():
        t = ln.strip()
        if not t or t.startswith(("#", "REM ", "rem ", "::", "@echo")) or t.lower().startswith("echo"):
            continue
        out.append(t)
    return out


def test_S01B_T34_thin_launcher_test(tmp_path):
    import re
    for name in LAUNCHERS:
        text = "\n".join(_logic_lines(name))
        assert "backend.deployment" in text, f"{name} must delegate to the deployment service"
        assert not re.search(r"/home/|C:\\\\Users|/Users/|\b8800\b|\b8810\b|\b6380\b|\b5432\b", text), name
    for sh in ("run.sh", "healthcheck.sh", "install.sh"):
        assert subprocess.run(["bash", "-n", str(ROOT / sh)]).returncode == 0
    for name in ("run.sh", "healthcheck.sh", "RUN.bat", "HEALTHCHECK.bat"):
        logic = _logic_lines(name)
        assert len(logic) <= 25, f"{name} is not thin ({len(logic)} logic lines)"
    # RUN refuses an unactivated deployment (no silent unqualified start)
    cp = subprocess.run(["bash", str(ROOT / "run.sh"), "--no-browser"], cwd=ROOT, capture_output=True, text=True,
                        timeout=300, env=clean_env(ATTA_DEPLOYMENT_DIR=str(tmp_path / "deploy")))
    assert cp.returncode == 20 and "NOT ACTIVATED" in cp.stdout, cp.stdout + cp.stderr
    record("S01B-T34", {"launchers": LAUNCHERS, "run_unactivated_exit": cp.returncode})


def test_S01B_T35_new_capability_discovery_requires_no_launcher_modification(tmp_path):
    from backend.capability_registry import CapabilityRegistry, TrustStore
    from backend.capability_registry.trust import generate_keypair, trust_package
    from backend.capability_runtime import CapabilityRuntime
    from backend.policy_engine import PolicyEngine
    digests = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in LAUNCHERS + ["START.bat", "start.sh"]
               if (ROOT / n).is_file()}
    for n in digests:
        assert not [ln for ln in _logic_lines(n) if "capabilit" in ln.lower()], f"{n} wires capabilities by hand"
    caps = tmp_path / "caps"
    shutil.copytree(Path(__file__).parent / "fixtures_s01a/capabilities/test_text", caps / "brand_new_capability",
                    ignore=shutil.ignore_patterns("__pycache__"))
    keys = tmp_path / "k"
    keys.mkdir()
    generate_keypair(keys / "t.pem", keys / "t.pub")
    trust_package(caps / "brand_new_capability", tmp_path / "trust.json", keys / "t.pem", "test-operator")
    reg = CapabilityRegistry([caps], policy=PolicyEngine(), trust=TrustStore.load(tmp_path / "trust.json", keys / "t.pub"),
                             runtime=CapabilityRuntime(), service_available=lambda n: True, healthcheck_timeout_s=10)
    reg.discover()
    assert reg.get("test.text").executable
    assert digests == {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in digests}
    record("S01B-T35", {"discovered": "test.text", "launchers_unchanged": sorted(digests)})


# ------------------------------------------------------------------------------------------------ T36-T40 regression
@pytest.mark.qualification_wrapper
def test_S01B_T36_legacy_atta_regression_suite(tmp_path):
    before, src = evidence_digest(), source_tree(tmp_path)
    cp = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         f"{S01A_B}::test_S01A_T49_existing_atta_application_builder_regression"],
                        cwd=src, capture_output=True, text=True, timeout=2400, env=clean_env(PYTHONPATH=str(src)))
    assert cp.returncode == 0, cp.stdout[-3000:]
    assert evidence_digest() == before, "recorded Stage 01/01A evidence must not change"
    record("S01B-T36", {"stdout_tail": cp.stdout[-400:]})


@pytest.mark.qualification_wrapper
def test_S01B_T37_stage01_regression_suite(tmp_path):
    # S01A-T48 runs the unchanged Stage 01 suite and proves that the superseded tests fail only because of the
    # stricter Stage 01A controls (recorded in STAGE_01A_VERIFICATION.md); nothing is rewritten to pass.
    before, src = evidence_digest(), source_tree(tmp_path)
    cp = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         f"{S01A_B}::test_S01A_T48_existing_stage01_acceptance_tests"],
                        cwd=src, capture_output=True, text=True, timeout=2400, env=clean_env(PYTHONPATH=str(src)))
    assert cp.returncode == 0, cp.stdout[-3000:]
    assert evidence_digest() == before, "recorded Stage 01/01A evidence must not change"
    record("S01B-T37", {"stdout_tail": cp.stdout[-400:]})


@pytest.mark.qualification_wrapper
def test_S01B_T38_stage01a_regression_suite(tmp_path):
    out = tmp_path / "s01a.xml"
    before, src = evidence_digest(), source_tree(tmp_path)
    # T48/T49 are S01B-T37/T36; T51 checks the REAL repository's OS protection and keys, which S01B-T42 does
    # directly (a source copy is not the protected tree).
    cp = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", S01A_A, S01A_B,
                         "-k", "not T48 and not T49 and not T51", f"--junitxml={out}"],
                        cwd=src, capture_output=True, text=True, timeout=3600, env=clean_env(PYTHONPATH=str(src)))
    assert evidence_digest() == before, "recorded Stage 01/01A evidence must not change"
    res = junit(out)
    failed = {n: r[1][-800:] for n, r in res.items() if r[0] != "passed"}
    assert cp.returncode == 0 and not failed and len(res) >= 50, (len(res), failed, cp.stdout[-2000:])
    record("S01B-T38", {"tests": len(res), "all_passed": True})


def _clone(dest: Path, rev: str | None = None) -> Path:
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dest)], check=True, timeout=600)
    if rev:
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", rev], check=True, timeout=120)
    return dest


def test_S01B_T39_clean_installation_qualification(tmp_path):
    repo = _clone(tmp_path / "clean")
    deploy = tmp_path / "deploy"
    with disposable_pg(tmp_path / "pg") as pg:
        env = clean_env(ATTA_DEPLOYMENT_DIR=str(deploy), **pg.env())
        env.pop("PYTHONPATH", None)
        env.pop("VIRTUAL_ENV", None)
        cp = subprocess.run(["bash", "install.sh", "--no-node", "--profile", "LOCAL_CPU", "--approve", "--no-start",
                             "--suite", "SMOKE"], cwd=repo, env=env, capture_output=True, text=True, timeout=2400)
        text = cp.stdout + cp.stderr
        assert cp.returncode == 0, text[-4000:]
        assert "INSTALL COMPLETE" in text
        st = json.loads((deploy / "install_state.json").read_text())
        assert st["state"] == "READY", st
        active = json.loads((deploy / "active.json").read_text())
        assert active["profile"] == "LOCAL_CPU" and active["qualification"][0]["status"] == "PASS"
        assert (repo / ".venv").is_dir()
    record("S01B-T39", {"active": active, "tail": text[-1200:]})


def test_S01B_T40_upgrade_from_current_baseline_qualification(tmp_path):
    baseline = yaml.safe_load((ROOT / "configuration/build/stages/S01B.yaml").read_text())["base_commit"]
    repo = _clone(tmp_path / "upgrade", baseline)
    deploy = tmp_path / "deploy"
    with disposable_pg(tmp_path / "pg") as pg:   # migrated by the CURRENT code below only after the baseline ran
        env = clean_env(**pg.env())
        env["PYTHONPATH"] = str(repo)
        old = subprocess.run([PY, "-m", "backend.database", "migrate"], cwd=repo, env=env, capture_output=True,
                             text=True, timeout=600)
        assert old.returncode == 0, old.stdout + old.stderr
        import psycopg
        with psycopg.connect(host="127.0.0.1", port=pg.port, dbname="atta", user="atta",
                             password=pg.env()["ATTA_DATABASE_MIGRATION_PASSWORD"]) as c:
            old_migrations = c.execute("SELECT name, checksum FROM public.atta_schema_migrations ORDER BY name").fetchall()
        # upgrade the same checkout to the current revision and run the new installer against the old state
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", str(ROOT), head], check=True, timeout=300)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", head], check=True, timeout=120)
        env2 = clean_env(ATTA_DEPLOYMENT_DIR=str(deploy), **pg.env())
        env2["PYTHONPATH"] = str(repo)
        cp = subprocess.run([PY, "-m", "backend.deployment", "install", "--profile", "LOCAL_CPU", "--approve",
                             "--no-start", "--suite", "SMOKE", "--env", "test"], cwd=repo, env=env2,
                            capture_output=True, text=True, timeout=1800)
        assert cp.returncode == 0, cp.stdout[-3000:] + cp.stderr[-3000:]
        with psycopg.connect(host="127.0.0.1", port=pg.port, dbname="atta", user="atta",
                             password=pg.env()["ATTA_DATABASE_MIGRATION_PASSWORD"]) as c:
            new_migrations = c.execute("SELECT name, checksum FROM public.atta_schema_migrations ORDER BY name").fetchall()
    assert new_migrations[:len(old_migrations)] == old_migrations, "baseline migration history must be preserved"
    assert json.loads((deploy / "install_state.json").read_text())["state"] == "READY"
    record("S01B-T40", {"baseline": baseline, "head": head, "old_migrations": len(old_migrations),
                        "new_migrations": len(new_migrations)})


# ------------------------------------------------------------------------------------------------ T41-T45 platform
def _probe_run(pkg: str, tmp: Path, timeout_s: float, prepare=None, **rt_kw):
    """Run a fixture package's healthcheck in the real isolated runtime; returns (result, workdir)."""
    from backend.capability_registry.package import package_digest
    from backend.capability_runtime import CapabilityRuntime
    rt = CapabilityRuntime(**rt_kw)
    d = FIX / pkg
    import tempfile
    # the handler may run as an unprivileged worker account, which cannot enter pytest's private (0700) tmp_path
    work = Path(tempfile.mkdtemp(prefix="atta-s01b-work-"))
    os.chmod(work, 0o777)
    if prepare is not None:
        prepare(work)
    t = rt.issue_ticket("exe_s01b_" + os.urandom(4).hex(), "s01b." + pkg, "healthcheck")
    r = rt.run(ticket=t, mode="healthcheck", capability_id="s01b." + pkg, package_dir=d, expected_digest=package_digest(d),
               handlers={"execute": "handler:noop", "healthcheck": "handler:healthcheck", "verify": "handler:verify_any"},
               timeout_s=timeout_s, workdir=work)
    return r, work


@pytest.mark.qualification_wrapper
def test_S01B_T41_windows_specific_tests():
    """Windows only (the build controller records it BLOCKED elsewhere). Certifies every Windows worker control:
    struct layout, Job Object tree termination + memory limit, restricted token (privileges removed, LOW integrity),
    workspace ACL + integrity label, and — when run as Administrator — the firewall rules of the worker interpreter."""
    if os.name != "nt":
        pytest.fail("Windows-specific test executed on a non-Windows host: the verifier must record it BLOCKED")
    import ctypes
    import tempfile
    from backend.capability_runtime import windows_isolation as wi
    from backend.deployment import windows_hardening as wh
    sizes = {k: ctypes.sizeof(v) for k, v in wi._structs().items()}
    assert sizes == {"BASIC": 64, "IOC": 48, "EXT": 144, "LUID": 8, "LUID_AND_ATTRIBUTES": 12,
                     "SID_AND_ATTRIBUTES": 16, "TOKEN_MANDATORY_LABEL": 16}, sizes
    tmp = Path(tempfile.mkdtemp())
    r, work = _probe_run("s01b_spawn", tmp, 3.0, windows_allow_child_processes=True)
    assert r.status == "timeout"
    pid = int((work / "descendant.pid").read_text())
    time.sleep(1)
    assert not _pid_alive(pid), "the Job Object must terminate the detached descendant"
    p, pwork = _probe_run("s01b_probe", tmp / "probe", 20.0, isolation_required=True)
    assert p.status == "ok", (p.status, p.error, p.stderr_tail)
    w = p.value["windows"]
    assert w["integrity"] == "low", w
    assert w["profile_write"].startswith("denied"), w
    assert w["workspace_write"] == "ok", w
    assert p.isolation["windows"]["privileges_kept"] == ["SeChangeNotifyPrivilege"]
    acl = subprocess.run(["icacls", str(pwork)], capture_output=True, text=True).stdout
    assert "Mandatory Label\\Low Mandatory Level" in acl and "(I)" not in acl, acl
    # Protected secrets: a LOW-integrity worker can neither READ nor write a file carrying the secret label, while
    # this medium-integrity test process still can (positive control).
    secret = tmp / "test-secret.env"
    secret.write_text("ATTA_TEST_SECRET=s01b-t41-not-a-real-secret\n")
    wh.label_secrets([secret])
    assert "s01b-t41" in secret.read_text()
    targets = lambda d: (d / "probe_targets.json").write_text(json.dumps({"secret": str(secret)}))  # noqa: E731
    e, _ = _probe_run("s01b_winprobe", tmp / "escape", 30.0, prepare=targets, isolation_required=True)
    assert e.status == "ok", (e.status, e.error, e.stderr_tail)
    esc = e.value["probe"]
    assert esc["secret_read"].startswith("denied") and "PermissionError" in esc["secret_read"], esc
    assert esc["secret_write"].startswith("denied"), esc
    # No child processes: the worker cannot start another executable or a renamed copy of its interpreter
    assert esc["spawn_cmd"].startswith("denied"), esc
    assert esc["spawn_renamed_interpreter"].startswith("denied"), esc
    assert p.isolation["windows"]["child_processes"].startswith("denied"), p.isolation
    fw = {}
    if wh.is_admin():
        exe = wh.provision_worker_interpreter(tmp / "rt")
        for cmd in wh.remove_commands():
            subprocess.run(cmd, capture_output=True)
        for cmd in wh.firewall_rules(exe, isolation.free_port()):
            assert subprocess.run(cmd, capture_output=True).returncode == 0
        fw = wh.verify(exe)
        assert all(fw.values()), fw
        # the real worker path: the runtime started with the firewall-bound interpreter, every escape attempted
        fr, _ = _probe_run("s01b_winprobe", tmp / "fw", 30.0, prepare=targets, python=str(exe),
                           site_dirs=wh.site_dirs(), isolation_required=True)
        for cmd in wh.remove_commands():
            subprocess.run(cmd, capture_output=True)
        assert fr.status == "ok", (fr.status, fr.error, fr.stderr_tail)
        fesc = fr.value["probe"]
        assert fesc["connect_external"].startswith("denied"), fesc          # firewall
        assert fesc["spawn_cmd"].startswith("denied") and fesc["spawn_renamed_interpreter"].startswith("denied"), fesc
        assert fesc["secret_read"].startswith("denied"), fesc
        fw["worker_escape_attempts"] = fesc
    else:
        pytest.fail("run S01B-T41 as Administrator: the firewall part of Windows isolation must be certified too")
    record("S01B-T41", {"descendant": pid, "windows": w, "privileges": p.isolation["windows"], "escape": esc,
                        "firewall": fw})


@pytest.mark.privileged
@pytest.mark.skipif(not LINUX, reason="Linux-specific")
def test_S01B_T42_linux_specific_tests(tmp_path):
    caps = discovery.isolation_capabilities()
    worker = None
    import pwd
    for u in ("atta-builder", "nobody"):
        try:
            pwd.getpwnam(u)
            worker = u
            break
        except KeyError:
            continue
    can_isolate = os.geteuid() == 0 and caps.get("network_namespace")
    if not can_isolate:
        pytest.fail("Linux isolation unavailable (needs privileges for network namespaces): record as BLOCKED")
    r, _ = _probe_run("s01b_probe", tmp_path, 20.0, network="none", worker_user=worker, memory_limit_mb=256,
                      isolation_required=True)
    assert r.status == "ok", (r.status, r.error, r.stderr_tail)
    v = r.value
    assert v["uid"] == pwd.getpwnam(worker).pw_uid and v["uid"] != 0
    assert v["external_network"] != "connected" and ("Errno" in v["external_network"] or "Error" in v["external_network"])
    assert v["cuda_visible_devices"] == ""
    assert r.isolation.get("network") == "private empty namespace", r.isolation
    if caps.get("cgroup_v1_memory") or caps.get("cgroup_v2"):
        assert "atta" in v["cgroup"], v["cgroup"]
    r2, work = _probe_run("s01b_spawn", tmp_path / "spawn", 3.0)
    assert r2.status == "timeout" and not r2.survivors
    pid = int((work / "descendant.pid").read_text())
    time.sleep(0.5)
    assert not _pid_alive(pid), "detached descendant must be killed with the tree"
    # Independent verifier keys: unreadable by the worker account, encrypted at rest (a root test process reading the
    # file gets ciphertext only), and no verifier secret is present in this test's environment.
    key = Path(os.environ.get("ATTA_BUILD_SIGNING_KEY_FILE", "/etc/atta-verifier/build_signing_ed25519.pem"))
    keys = {}
    if key.exists():
        for account in (worker, "atta-tester"):          # implementation worker AND the gate's test identity
            rd = subprocess.run(["runuser", "-u", account, "--", PY, "-I", "-c", f"open({str(key)!r},'rb').read()"],
                                capture_output=True, text=True, timeout=60)
            assert rd.returncode != 0 and "PermissionError" in rd.stderr, (account, rd.stderr)
        from cryptography.hazmat.primitives import serialization
        raw = key.read_bytes()
        assert b"ENCRYPTED PRIVATE KEY" in raw, "verifier signing key must be encrypted at rest"
        with pytest.raises(TypeError):
            serialization.load_pem_private_key(raw, password=None)
        keys = {"worker_read": "PermissionError", "at_rest": "encrypted", "usable_without_passphrase": False}
    else:
        pytest.fail(f"verifier signing key {key} not found: the build controller protection is not installed")
    # ATTA_BUILD_CONTROLLER_RUN (the run id) is the only verifier variable a gate passes on, and it is not secret
    leaked = [k for k in os.environ if k.startswith("ATTA_BUILD_") and k != "ATTA_BUILD_CONTROLLER_RUN"]
    assert not leaked, f"verifier configuration/secrets leaked into a test process: {leaked}"
    denied = {}
    for t in ("backend/verification/build_controller.py", "configuration/build/roadmap.yaml",
              "configuration/build/protected.lock.json", "tests/acceptance/test_s01b_acceptance.py",
              "evidence/build_controller/ledger.jsonl", "configuration/qualification/suites.yaml"):
        w = subprocess.run(["runuser", "-u", worker, "--", PY, "-I", "-c", f"open({str(ROOT / t)!r}, 'a')"],
                           capture_output=True, text=True, timeout=60)
        denied[t] = w.returncode != 0 and "PermissionError" in w.stderr
    assert all(denied.values()), denied
    record("S01B-T42", {"probe": {k: v[k] for k in ("uid", "external_network", "cuda_visible_devices")},
                        "isolation": r.isolation, "descendant_killed": pid, "verifier_key": keys,
                        "protected_files_denied_to_worker": denied})


@pytest.mark.qualification_wrapper
def test_S01B_T43_h100_hardware_tests_pass_only_on_actual_hardware(tmp_path):
    """H100 only (the build controller records it BLOCKED elsewhere). Runs the real H100 qualification suite."""
    g = discovery.nvidia_gpus()
    h100 = [x for x in g["gpus"] if "H100" in x["name"].upper() and x["vram_mb"] >= 79 * 1024]
    if not h100:
        pytest.fail("no H100 present: this test must be recorded BLOCKED by the verifier, never PASS")
    cp = subprocess.run([PY, "-m", "backend.deployment", "qualify", "--suite", "H100", "--profile", "H100"], cwd=ROOT,
                        env=clean_env(ATTA_DEPLOYMENT_DIR=str(tmp_path / "deploy")), capture_output=True, text=True,
                        timeout=7200)
    res = json.loads(next((tmp_path / "deploy" / "qualification").glob("*/results.json")).read_text())
    by = {t["test_id"]: t["status"] for t in res["results"]}
    for tid in ("Q-H100-IDENTITY", "Q-H100-WORKER-GPU", "Q-H100-VLLM"):
        assert by[tid] == "PASS", by
    record("S01B-T43", {"gpus": h100, "results": by, "exit": cp.returncode})


def test_S01B_T44_unavailable_h100_tests_remain_blocked_elsewhere(real_env, tmp_path):
    if not profiles.check("H100", real_env):
        pytest.skip("this machine has H100 hardware: covered by S01B-T43")
    from backend.deployment.qualification import run_suite
    m = mf.build(real_env, profiles.decide("AUTO", real_env), [], settings_with(), configuration_source={})
    r = run_suite("H100", manifest=m, profile="H100", out_root=tmp_path / "qual")
    statuses = {t["test_id"]: t["status"] for t in r["results"]}
    assert set(statuses.values()) == {"BLOCKED"}, statuses
    assert r["status"] == "BLOCKED" and r["counts"]["PASS"] == 0
    for t in r["results"]:
        assert t["actual"] == "not executed" and t["exit_code"] is None and t["reason"]
    cp = subprocess.run([PY, "-m", "backend.deployment", "qualify", "--suite", "H100"], cwd=ROOT,
                        env=clean_env(ATTA_DEPLOYMENT_DIR=str(tmp_path / "deploy")), capture_output=True, text=True,
                        timeout=900)
    assert cp.returncode == 3, cp.stdout[-2000:]
    # the build controller itself records H100/Windows-only acceptance tests BLOCKED here, never PASSED
    sys.path.insert(0, str(ROOT / "backend" / "verification"))
    import build_controller as bc
    assert bc.condition_met("gpu:h100")[0] is False
    record("S01B-T44", {"suite": statuses, "cli_exit": cp.returncode})


def test_S01B_T45_complete_requirement_to_evidence_traceability():
    reqs = yaml.safe_load((ROOT / "documentation/requirements/stage_01b_requirements.yaml").read_text())
    stage = yaml.safe_load((ROOT / "configuration/build/stages/S01B.yaml").read_text())
    staged = {a["id"] for t in stage["tasks"] for a in t["acceptance_tests"]}
    src = Path(__file__).read_text()
    defined = {f"S01B-T{n:02d}" for n in range(1, 46) if f"def test_S01B_T{n:02d}_" in src}
    assert defined == {f"S01B-T{n:02d}" for n in range(1, 46)}
    assert defined <= staged, defined - staged
    covered = set()
    problems = []
    for r in reqs["requirements"]:
        if not r.get("tests"):
            problems.append(f"{r['id']} has no test")
        for t in r.get("tests", []):
            if t not in defined:
                problems.append(f"{r['id']} references unknown test {t}")
            covered.add(t)
        for f in r.get("implemented_in", []):
            if not (ROOT / f).exists():
                problems.append(f"{r['id']} implementation file missing: {f}")
        if r.get("status") not in ("VERIFIED", "PARTIAL", "BLOCKED"):
            problems.append(f"{r['id']} has invalid status {r.get('status')}")
    assert not problems, problems
    assert defined <= covered | {"S01B-T45"}, defined - covered
    for doc in ("STAGE_01B_VERIFICATION.md", "STAGE_01_FINAL_QUALIFICATION.md", "ENVIRONMENT_COMPATIBILITY_MATRIX.md",
                "OFFLINE_DEPLOYMENT.md", "H100_DEPLOYMENT.md", "LOCAL_DEPLOYMENT.md", "ISOLATION_MODEL.md"):
        assert (ROOT / doc).is_file(), doc
    record("S01B-T45", {"requirements": len(reqs["requirements"]), "tests": sorted(defined)})
