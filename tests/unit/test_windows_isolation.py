"""Platform-independent checks of the Windows isolation code (Stage 01B §16). These do NOT certify Windows behaviour —
S01B-T41 does that on a Windows host — they verify the generated firewall rules, ACL commands, Job Object limits and
the worker-interpreter provisioning logic."""
from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest

from backend.capability_runtime import windows_isolation as wi
from backend.deployment import windows_hardening as wh


def test_job_limits_kill_tree_limit_memory_and_never_allow_breakaway():
    flags, mem = wi.job_limit_flags(256)
    assert flags & wi.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert flags & wi.JOB_OBJECT_LIMIT_PROCESS_MEMORY and mem == 256 * 1024 * 1024
    assert flags & wi.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    assert not flags & 0x800 and not flags & 0x1000          # BREAKAWAY_OK / SILENT_BREAKAWAY_OK never set
    assert wi.job_limit_flags(None) == (wi.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
                                        wi.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION, 0)


def test_workspace_acl_removes_inheritance_grants_minimum_and_labels_low_integrity():
    cmds = wi.workspace_acl_commands(r"C:\atta\ws", "S-1-5-21-1-2-3-1001")
    assert cmds[0] == ["icacls", r"C:\atta\ws", "/inheritance:r"]
    grant = cmds[1]
    assert "/grant:r" in grant and "*S-1-5-18:(OI)(CI)F" in grant and "*S-1-5-21-1-2-3-1001:(OI)(CI)M" in grant
    assert not any("S-1-1-0" in c or "Everyone" in c or "S-1-5-32-545" in c for c in grant), "no broad grants"
    assert cmds[2] == ["icacls", r"C:\atta\ws", "/setintegritylevel", "(OI)(CI)low"]
    assert wi.LOW_INTEGRITY_SID == "S-1-16-4096" and wi.INTEGRITY_RIDS[0x1000] == "low"
    assert wi.KEEP_PRIVILEGES == {"SeChangeNotifyPrivilege"}


def _ranges(spec: str):
    out = []
    for part in spec.split(","):
        lo, hi = part.split("-")
        out.append((ipaddress.ip_address(lo), ipaddress.ip_address(hi)))
    return out


def test_firewall_rules_block_all_but_loopback_for_the_worker_only():
    worker = Path(r"C:\ATTa\runtime\worker-python\atta-worker.exe")
    rules = wh.firewall_rules(worker, 8800)
    out_rule, in_rule, api_rule = rules
    assert all(r[:5] == ["netsh", "advfirewall", "firewall", "add", "rule"] for r in rules)
    assert "dir=out" in out_rule and "action=block" in out_rule and f"program={worker}" in out_rule
    remote = next(x for x in out_rule if x.startswith("remoteip=")).split("=", 1)[1]
    ranges = _ranges(remote)
    probes = {"8.8.8.8": True, "1.1.1.1": True, "10.0.0.5": True, "192.168.1.10": True, "2606:4700::1111": True,
              "127.0.0.1": False, "127.255.0.9": False, "::1": False}
    for ip, blocked in probes.items():
        a = ipaddress.ip_address(ip)
        hit = any(lo.version == a.version and lo <= a <= hi for lo, hi in ranges)
        assert hit == blocked, (ip, hit)
    assert "dir=in" in in_rule and "action=block" in in_rule and f"program={worker}" in in_rule
    assert "action=allow" in api_rule and "localport=8800" in api_rule and "remoteip=127.0.0.1,::1" in api_rule
    assert not any(x.startswith("program=") for x in api_rule)
    assert [c[-1] for c in wh.remove_commands()] == [f"name={n}" for n in wh.rule_names()]


def test_worker_interpreter_is_a_renamed_copy_without_site_packages(tmp_path):
    base = tmp_path / "Python313"
    (base / "Lib" / "site-packages" / "evil").mkdir(parents=True)
    (base / "Lib" / "os.py").write_text("# stdlib")
    (base / "DLLs").mkdir()
    (base / "DLLs" / "_socket.pyd").write_bytes(b"\0")
    (base / "python313.dll").write_bytes(b"\0")
    (base / "python.exe").write_bytes(b"MZ")
    (base / "Scripts").mkdir()
    exe = wh.provision_worker_interpreter(tmp_path / "runtime", str(base))
    assert exe == tmp_path / "runtime" / "worker-python" / "atta-worker.exe" and exe.read_bytes() == b"MZ"
    root = exe.parent
    assert not (root / "python.exe").exists() and (root / "python313.dll").is_file()
    assert (root / "Lib" / "os.py").is_file() and not (root / "Lib" / "site-packages").exists()
    assert not (root / "Scripts").exists()
    with pytest.raises(RuntimeError):
        wh.provision_worker_interpreter(tmp_path / "rt2", str(tmp_path / "Lib"))   # not a CPython tree


def test_windows_only_operations_refuse_elsewhere(tmp_path):
    import os
    if os.name == "nt":
        pytest.skip("covered by S01B-T41 on Windows")
    with pytest.raises(RuntimeError):
        wh.apply(tmp_path, 8800)


def test_secret_label_denies_read_and_write_up_for_low_integrity():
    # Medium mandatory label with No-Read-Up, No-Write-Up, No-Execute-Up, inherited by objects and containers
    assert wi.SECRET_LABEL_SDDL == "S:(ML;OICI;NRNWNX;;;ME)"
    assert wi.LABEL_SECURITY_INFORMATION == 0x10 and wi.SE_FILE_OBJECT == 1


def test_workers_get_a_no_child_process_job_by_default():
    assert wi.JOB_OBJECT_LIMIT_ACTIVE_PROCESS == 0x8
    import inspect
    assert inspect.signature(wi.restrict_current_process).parameters["allow_child_processes"].default is False
    from backend.capability_runtime.runner import CapabilityRuntime
    assert CapabilityRuntime().windows_allow_child_processes is False
    from backend.settings.schema import DeploymentSettings
    assert DeploymentSettings().windows_worker_child_processes is False


def test_secret_paths_cover_local_credentials_and_key_material(tmp_path):
    cfg = tmp_path / "configuration"
    (cfg / "sub").mkdir(parents=True)
    (cfg / "local.env").write_text("ATTA_DATABASE_PASSWORD=x")
    (cfg / "sub" / "op.pem").write_text("k")
    (cfg / "x.key").write_text("k")
    (cfg / "base.yaml").write_text("a: 1")
    (tmp_path / "extra.txt").write_text("s")
    got = {p.relative_to(tmp_path).as_posix() for p in wh.secret_paths(tmp_path, ["extra.txt", "missing.txt"])}
    assert got == {"configuration/local.env", "configuration/sub/op.pem", "configuration/x.key", "extra.txt"}
    import os
    if os.name != "nt":
        with pytest.raises(RuntimeError):
            wh.label_secrets([tmp_path / "extra.txt"])
