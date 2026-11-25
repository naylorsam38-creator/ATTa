"""TEST FIXTURE: reports what a handler process can see of the parent's secrets (expected: nothing)."""
import os


def probe(payload, context):
    keys = sorted(os.environ)
    leaked = [k for k in keys if any(s in k.upper() for s in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "ATTA_"))]
    leaked += [f"value:{k}" for k, v in os.environ.items() if "s11-canary" in v]
    return {"keys": keys, "leaked": leaked}


def healthcheck():
    return {"healthy": True}


def verify(payload, output):
    return {"verified": True, "problems": []}
