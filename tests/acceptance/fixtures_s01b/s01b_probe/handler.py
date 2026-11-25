"""TEST FIXTURE (S01B-T42): reports the isolation actually applied to the handler process. Harmless."""
import os
import socket


def healthcheck():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1.5).close()
        net = "connected"
    except OSError as exc:
        net = f"{type(exc).__name__}: {exc}"
    cg = ""
    try:
        with open("/proc/self/cgroup") as f:
            cg = f.read()
    except OSError:
        pass
    win = {}
    if os.name == "nt":
        import windows_isolation            # the child's own module directory (capability_runtime) is on sys.path
        win["integrity"] = windows_isolation.current_integrity()
        try:                                # a low-integrity worker must not be able to write to the user profile
            with open(os.path.join(os.path.expanduser("~"), "atta-s01b-lowil-probe.txt"), "w") as f:
                f.write("x")
            win["profile_write"] = "ALLOWED"
        except OSError as exc:
            win["profile_write"] = f"denied: {exc}"
        try:
            with open("workspace-write.txt", "w") as f:
                f.write("x")
            win["workspace_write"] = "ok"
        except OSError as exc:
            win["workspace_write"] = f"denied: {exc}"
    return {"healthy": True, "windows": win, "uid": os.getuid() if hasattr(os, "getuid") else None, "pid": os.getpid(),
            "external_network": net, "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "cgroup": cg}


def noop(payload, ctx):
    return {"ok": True}


def verify_any(payload, output):
    return True
