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
    return {"healthy": True, "uid": os.getuid() if hasattr(os, "getuid") else None, "pid": os.getpid(),
            "external_network": net, "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "cgroup": cg}


def noop(payload, ctx):
    return {"ok": True}


def verify_any(payload, output):
    return True
