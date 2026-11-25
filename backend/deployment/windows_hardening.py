"""Windows host hardening for tool workers (Stage 01B §16): dedicated worker interpreter + Windows Defender Firewall.

Why a dedicated interpreter: Windows Firewall rules bind to a program path. The API, the service manager and the
workers would otherwise share one python.exe (and a venv's python.exe is only a redirector that starts the base
interpreter, so a rule on it would not apply). INSTALL therefore copies the base CPython into
runtime/worker-python/ and renames it atta-worker.exe; the capability runtime starts workers with that executable
(plus the venv's site-packages added explicitly), and the firewall rules below apply to it alone:

  ATTa worker: block outbound non-loopback    all outbound traffic of atta-worker.exe except 127.0.0.0/8 and ::1
  ATTa worker: block inbound                  no inbound connections to atta-worker.exe at all
  ATTa api: loopback only                     inbound to the API port allowed only from 127.0.0.1/::1 (explicit
                                              allowed service port); everything else stays default-deny inbound

Applying rules needs Administrator rights; the installer lists this as an admin action and stops in BLOCKED with the
operator instruction when not elevated. Only executable on Windows; Linux CI verifies the generated commands only
(BLOCKERS B-S01B-01 until S01B-T41 runs on Windows)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RULE_PREFIX = "ATTa"
WORKER_EXE = "atta-worker.exe"
# Everything except loopback, as Windows Firewall address ranges.
NON_LOOPBACK_V4 = "0.0.0.0-126.255.255.255,128.0.0.0-255.255.255.255"
NON_LOOPBACK_V6 = "::-::0,::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"


def worker_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "worker-python"


def worker_exe(runtime_dir: Path) -> Path:
    return worker_dir(runtime_dir) / WORKER_EXE


def provision_worker_interpreter(runtime_dir: Path, base_prefix: str | None = None) -> Path:
    """Copy the base CPython (without its site-packages) and rename python.exe -> atta-worker.exe."""
    src = Path(base_prefix or sys.base_prefix)
    if not (src / "python.exe").is_file():
        raise RuntimeError(f"{src} does not look like a Windows CPython installation (no python.exe)")
    dest = worker_dir(runtime_dir)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("site-packages", "__pycache__", "Scripts", "Tools",
                                                             "Doc", "test", "tests", "idlelib", "tkinter", "turtledemo"))
    exe = dest / "python.exe"
    exe.rename(dest / WORKER_EXE)
    return dest / WORKER_EXE


def firewall_rules(worker: Path, api_port: int) -> list[list[str]]:
    w = str(worker)
    add = ["netsh", "advfirewall", "firewall", "add", "rule"]
    return [
        add + [f"name={RULE_PREFIX} worker: block outbound non-loopback", "dir=out", "action=block", f"program={w}",
               "enable=yes", "profile=any", f"remoteip={NON_LOOPBACK_V4},{NON_LOOPBACK_V6}"],
        add + [f"name={RULE_PREFIX} worker: block inbound", "dir=in", "action=block", f"program={w}", "enable=yes",
               "profile=any"],
        add + [f"name={RULE_PREFIX} api: loopback only", "dir=in", "action=allow", "protocol=TCP",
               f"localport={api_port}", "remoteip=127.0.0.1,::1", "enable=yes", "profile=any"],
    ]


def rule_names() -> list[str]:
    return [f"{RULE_PREFIX} worker: block outbound non-loopback", f"{RULE_PREFIX} worker: block inbound",
            f"{RULE_PREFIX} api: loopback only"]


def remove_commands() -> list[list[str]]:
    return [["netsh", "advfirewall", "firewall", "delete", "rule", f"name={n}"] for n in rule_names()]


def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def apply(runtime_dir: Path, api_port: int) -> dict:
    """Provision the worker interpreter and (re)create the firewall rules. Windows + Administrator only."""
    if os.name != "nt":
        raise RuntimeError("Windows firewall hardening applies to Windows hosts only")
    if not is_admin():
        raise PermissionError("applying firewall rules needs Administrator rights: run INSTALL.bat as Administrator")
    exe = provision_worker_interpreter(runtime_dir)
    for cmd in remove_commands():                       # idempotent: replace any previous ATTa rules
        subprocess.run(cmd, capture_output=True, text=True)
    for cmd in firewall_rules(exe, api_port):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"netsh failed: {r.stdout.strip()} {r.stderr.strip()}")
    return {"worker_interpreter": str(exe), "rules": rule_names(), "verified": verify(exe)}


def verify(worker: Path) -> dict:
    """Read the rules back from Windows Firewall and check they are enabled, blocking and bound to the worker."""
    out = {}
    for name in rule_names():
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}", "verbose"],
                           capture_output=True, text=True)
        text = r.stdout
        ok = r.returncode == 0 and "Enabled:" in text and ("Yes" in text)
        if "worker" in name:
            ok = ok and str(worker).lower() in text.lower() and "Block" in text
        out[name] = ok
    return out


def site_dirs() -> list[str]:
    """The venv's package directories, handed to workers started with the dedicated interpreter."""
    import sysconfig
    return sorted({sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]})


# ------------------------------------------------------------------------------------------------ protected secrets
def secret_paths(root: Path, extra: list[str] | None = None) -> list[Path]:
    """Files a LOW-integrity worker must neither read nor write: local credentials, any key material under
    configuration/, operator-listed paths (deployment.windows_protected_paths)."""
    cfg = root / "configuration"
    out = [cfg / "local.env"] + sorted(p for p in cfg.rglob("*") if p.suffix in (".pem", ".key") and p.is_file())
    for e in extra or []:
        p = Path(e)
        out.append(p if p.is_absolute() else root / p)
    return [p for p in out if p.exists()]


def label_secrets(paths: list[Path]) -> dict:
    """Medium mandatory label with No-Read-Up/No-Write-Up/No-Execute-Up on every protected secret (Windows)."""
    if os.name != "nt":
        raise RuntimeError("mandatory integrity labels exist on Windows only")
    from backend.capability_runtime.windows_isolation import SECRET_LABEL_SDDL, label_secret
    for p in paths:
        label_secret(str(p))
    return {"labelled": [str(p) for p in paths], "label": SECRET_LABEL_SDDL}
