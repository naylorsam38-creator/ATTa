"""Shared helpers for the Stage 01B acceptance tests (PROTECTED). Everything drives the real deployment code: real
discovery, real disposable PostgreSQL clusters, real kernel network namespaces, real processes. Synthetic inputs
(GGUF headers, fake hardware descriptions for the pure selection functions) are labelled as test data."""
from __future__ import annotations

import contextlib
import json
import os
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
EVIDENCE = ROOT / "evidence" / "stage_01b" / "acceptance"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def record(test_id: str, data) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{test_id}.json").write_text(json.dumps(data, indent=2, default=str))


def clean_env(**extra: str) -> dict[str, str]:
    """Process environment without inherited ATTa overrides or verifier configuration."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ATTA__", "ATTA_BUILD_", "ATTA_DEPLOYMENT_DIR",
                                                                     "ATTA_PROFILE"))}
    env.update({"ATTA_ENV": "test", "PYTHONPATH": str(ROOT)})
    env.update(extra)
    return env


# ------------------------------------------------------------------------------------------------ GGUF test data
def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def make_gguf(path: Path, name: str = "Tiny Test Llama", arch: str = "llama", ctx: int = 4096,
              padding: int = 4096) -> Path:
    """A syntactically valid GGUF v3 header (TEST DATA: no tensors, not a usable model)."""
    kvs = [(_s("general.architecture"), 8, _s(arch)), (_s("general.name"), 8, _s(name)),
           (_s(f"{arch}.context_length"), 4, struct.pack("<I", ctx)),
           (_s("tokenizer.ggml.tokens"), 9, struct.pack("<IQ", 4, 3) + struct.pack("<III", 1, 2, 3))]
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kvs))
    for k, t, v in kvs:
        body += k + struct.pack("<I", t) + v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + b"\0" * padding)
    return path


# ------------------------------------------------------------------------------------------------ settings / hardware
def settings_with(**env: str):
    from backend.settings import load_settings
    return load_settings("test", env=clean_env(**env))


def synthetic_env(base: dict, *, gpus=(), cuda=None, vllm=False, system=None) -> dict:
    """A copy of a REAL discovery result with the hardware facts replaced (TEST DATA for the pure profile/runtime
    selection functions; never written to a manifest)."""
    e = json.loads(json.dumps(base))
    e["gpu"] = {"present": bool(gpus), "gpus": [{"name": n, "vram_mb": v, "id": i} for i, (n, v) in enumerate(gpus)],
                "driver": "550.00" if gpus else None, "cuda": cuda, "reason": None if gpus else "test: no GPU"}
    e.setdefault("runtimes", {}).setdefault("vllm", {})["python_package"] = vllm
    if system:
        e["operating_system"] = {**e["operating_system"], "system": system}
    return e


# ------------------------------------------------------------------------------------------------ isolation helpers
@contextlib.contextmanager
def disposable_pg(workspace: Path):
    from backend.deployment.isolation import DisposablePostgres
    pg = DisposablePostgres(workspace)
    pg.start()
    try:
        pg.migrate(clean_env())
        yield pg
    finally:
        pg.stop(destroy=True)


def junit(path: Path) -> dict[str, tuple[str, str]]:
    res = {}
    if not path.is_file():
        return res
    for tc in ET.parse(path).getroot().iter("testcase"):
        fail = tc.find("failure")
        if fail is None:
            fail = tc.find("error")
        skip = tc.find("skipped")
        res[tc.get("name")] = ("failed", (fail.get("message") or "") + "\n" + (fail.text or "")) if fail is not None \
            else (("skipped", skip.get("message") or "") if skip is not None else ("passed", ""))
    return res


def run_s01a_isolated(tmp: Path, nodeids: list[str], profile: str, timeout: float = 1800) -> dict:
    """Run existing Stage 01A acceptance tests UNCHANGED against a disposable PostgreSQL cluster, an isolated runtime
    directory and an explicit deployment profile. Returns {test name: (status, text)}."""
    out = tmp / f"s01a-{profile}.xml"
    with disposable_pg(tmp / "pg") as pg:
        env = clean_env(ATTA_PROFILE=profile, ATTA__RUNTIME__DIRECTORY=str(tmp / "runtime"),
                        ATTA__LOGGING__DIRECTORY=str(tmp / "runtime" / "logs"), **pg.env())
        cp = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodeids, f"--junitxml={out}"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        res = junit(out)
        res["__summary__"] = (str(cp.returncode), cp.stdout[-1500:])
        res["__database__"] = ("info", f"disposable cluster 127.0.0.1:{pg.port}")
    return res
