"""Run a command and capture verifiable evidence: argv, cwd, UTC start/finish, exit code, output and its hash."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path



def iso_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """Same '<prefix>_<ULID>' shape as backend.contracts.identifiers, without importing worker-editable code."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return prefix + "_" + "".join(alphabet[(value >> (5 * i)) & 31] for i in reversed(range(26)))

def _whoami() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USERNAME", "unknown")


MAX_CAPTURE = 200_000  # characters kept per stream (tail); the hash covers the full output


@dataclass
class CommandEvidence:
    test_id: str
    command: list[str]
    cwd: str
    run_as: str
    started_at: str
    finished_at: str
    duration_s: float
    exit_code: int | None
    expected_exit_code: int
    passed: bool
    timed_out: bool
    stdout: str
    stderr: str
    output_sha256: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def unprivileged_prefix(user: str | None) -> tuple[list[str], dict[str, str]]:
    """Command prefix + environment overrides that run a test as the dedicated unprivileged test identity.
    Only possible when the verifier itself is root on a POSIX host; otherwise ([], {}) — the caller records that."""
    if not user or os.name != "posix" or os.geteuid() != 0:
        return [], {}
    import pwd
    import shutil
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return [], {}
    runuser = shutil.which("runuser")
    if not runuser:
        return [], {}
    return [runuser, "-u", user, "--"], {"HOME": pw.pw_dir, "USER": user, "LOGNAME": user}


def run_command(test_id: str, command: list[str], cwd: Path, expected_exit_code: int = 0, timeout_s: float = 600,
                env: dict[str, str] | None = None, run_as: str | None = None) -> CommandEvidence:
    started, t0 = iso_utc(), time.monotonic()
    timed_out = False
    # Code under test must never learn the verifier's configuration (signing-key path, operator key, users):
    # ATTA_BUILD_* variables are removed from every test environment.
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("ATTA_BUILD_")}
    prefix, overrides = unprivileged_prefix(run_as)
    if run_as and not prefix:
        raise RuntimeError(f"cannot run {test_id} as the unprivileged test identity {run_as!r} on this host")
    command = prefix + command
    base_env.update(overrides)
    try:
        cp = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s,
                            env={**base_env, **(env or {})}, encoding="utf-8", errors="replace")
        code, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, code = True, None
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = ((exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")) \
            + f"\n[build-controller] TIMEOUT after {timeout_s}s"
    except FileNotFoundError as exc:
        code, out, err = None, "", f"[build-controller] cannot execute: {exc}"
    digest = hashlib.sha256(out.encode() + b"\0" + err.encode()).hexdigest()
    return CommandEvidence(test_id=test_id, command=command, cwd=str(cwd), run_as=run_as or _whoami(),
                           started_at=started, finished_at=iso_utc(),
                           duration_s=round(time.monotonic() - t0, 3), exit_code=code,
                           expected_exit_code=expected_exit_code, passed=(code == expected_exit_code and not timed_out),
                           timed_out=timed_out, stdout=out[-MAX_CAPTURE:], stderr=err[-MAX_CAPTURE:],
                           output_sha256=digest)
