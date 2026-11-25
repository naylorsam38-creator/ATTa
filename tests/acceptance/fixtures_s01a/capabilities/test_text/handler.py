"""TEST FIXTURE handlers: plain file operations on paths the gateway has already validated and resolved."""
import hashlib
import os


def _info(path):
    data = open(path, "rb").read()
    return {"path": os.path.basename(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def write_text(payload, ctx):
    os.makedirs(os.path.dirname(payload["path"]) or ".", exist_ok=True)
    with open(payload["path"], "w", encoding="utf-8") as f:
        f.write(payload["text"])
    return _info(payload["path"])


def read_text(payload, ctx):
    text = open(payload["path"], encoding="utf-8").read()
    return {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def append_text(payload, ctx):
    with open(payload["path"], "a", encoding="utf-8") as f:
        f.write(payload["text"])
    return _info(payload["path"])


def validate_text(payload, ctx):
    text = open(payload["path"], encoding="utf-8").read()
    missing = [s for s in payload["must_contain"] if s not in text]
    return {"valid": not missing, "missing": missing}


def count_words(payload, ctx):
    return {"words": len(open(payload["path"], encoding="utf-8").read().split())}


def fail_always(payload, ctx):
    raise RuntimeError("deliberate test failure")


def healthcheck():
    return {"healthy": True}


def verify_write(payload, output):
    data = open(payload["path"], "rb").read()
    return {"verified": data.decode("utf-8") == payload["text"] and hashlib.sha256(data).hexdigest() == output["sha256"]}


def verify_any(payload, output):
    return {"verified": isinstance(output, dict)}
