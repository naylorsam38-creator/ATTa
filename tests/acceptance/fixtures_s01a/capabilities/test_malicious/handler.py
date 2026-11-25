"""TEST FIXTURE: hostile at import. If this module is ever imported, a marker file proves it."""
import os
import tempfile

_marker = os.environ.get("ATTA_S01A_MALICIOUS_MARKER") or os.path.join(tempfile.gettempdir(), "atta_s01a_malicious_import.marker")
with open(_marker, "a") as _f:
    _f.write(f"imported by pid {os.getpid()}\n")


def noop(payload, ctx):
    return {}


def healthcheck():
    return True


def verify(payload, output):
    return True
