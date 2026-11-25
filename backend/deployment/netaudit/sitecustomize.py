"""Qualification network audit (Stage 01B §15). Loaded only in qualification processes (via PYTHONPATH) when
ATTA_NET_AUDIT_LOG is set. Records every attempted non-loopback connection/DNS lookup by Python code. In AIR_GAPPED
mode the kernel network namespace denies the connection; this hook records that it was attempted."""
import json
import os
import sys

_LOG = os.environ.get("ATTA_NET_AUDIT_LOG")
_LOCAL = ("127.", "::1", "localhost", "0.0.0.0")


def _record(kind, target):
    try:
        with open(_LOG, "a") as f:
            f.write(json.dumps({"pid": os.getpid(), "argv0": os.path.basename(sys.argv[0]) if sys.argv else "",
                                "kind": kind, "target": str(target)[:200]}) + "\n")
    except OSError:
        pass


def _hook(event, args):
    if event == "socket.connect":
        addr = args[1]
        if isinstance(addr, tuple) and addr and isinstance(addr[0], str) and not addr[0].startswith(_LOCAL):
            _record("connect", f"{addr[0]}:{addr[1] if len(addr) > 1 else ''}")
    elif event == "socket.getaddrinfo":
        host = args[0]
        if isinstance(host, (str, bytes)):
            h = host.decode() if isinstance(host, bytes) else host
            if h and not h.startswith(_LOCAL) and h not in ("localhost",):
                _record("dns", h)


if _LOG:
    sys.addaudithook(_hook)
