"""Append-only, hash-chained event logs for the control layer.

Every event is one JSON line. Each line carries `seq` (1, 2, 3, ...) and `prev` (the SHA-256 of the previous
line's bytes, "GENESIS" for the first), so verify_chain() can show that no line was removed, reordered or
edited after it was written. Appends take an exclusive flock on the log, read the last line's hash, write the
whole line in one write() and fsync: parallel writers (threads or processes) never interleave or fork the chain.

A chain alone cannot show that its LAST line was edited or that lines were cut off the end: nothing after
them carries their hash. So every append also rewrites <log>.head ({"seq", "sha256"} of the newest line) under
the same lock, and verify_chain() checks the log's last line against it. This makes edits, deletions,
reordering and truncation evident unless the anchor is rewritten to match as well; someone able to rewrite
both files at will is out of its reach (copy the heads off the machine for that: the control report prints them).

Nothing here ever rewrites or truncates a log. A torn last line (the machine lost power mid-write) is
reported by verify_chain() and the next append starts a new line after it rather than joining it."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator, Mapping

GENESIS = "GENESIS"
# How far back from the end of a log to look for its last complete line.
_TAIL_WINDOW = 1 << 20
# Strings longer than this in an event are stored as their hash and length, not their text: a repair can
# carry a whole overlay file as an argument, and the audit log is not a copy of the app.
MAX_EVENT_STRING = 2000


def canonical_json_bytes(value: Any) -> bytes:
    """One byte form per value: sorted keys, no whitespace, UTF-8, NaN refused."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bounded(value: Any, limit: int = MAX_EVENT_STRING) -> Any:
    """The value with every long string replaced by {"sha256", "len"}: provable, not copied."""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return {"sha256": sha256_bytes(value.encode("utf-8", "replace")), "len": len(value), "truncated": True}
    if isinstance(value, Mapping):
        return {str(k): bounded(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [bounded(v, limit) for v in value]
    return value


def _last_line(fd: int) -> bytes | None:
    """The last newline-terminated line of the open file, or None if it has none."""
    size = os.lseek(fd, 0, os.SEEK_END)
    if size == 0:
        return None
    start = max(0, size - _TAIL_WINDOW)
    os.lseek(fd, start, os.SEEK_SET)
    buf = b""
    while len(buf) < size - start:
        chunk = os.read(fd, size - start - len(buf))
        if not chunk:
            break
        buf += chunk
    if not buf.endswith(b"\n"):
        # Torn tail: the last complete line is the one before it. The new line starts on its own.
        cut = buf.rfind(b"\n")
        if cut < 0:
            return None
        buf = buf[:cut + 1]
    body = buf[:-1]
    i = body.rfind(b"\n")
    return body[i + 1:] if i >= 0 else (body if start == 0 else None)


def _needs_newline(fd: int) -> bool:
    size = os.lseek(fd, 0, os.SEEK_END)
    if size == 0:
        return False
    os.lseek(fd, size - 1, os.SEEK_SET)
    return os.read(fd, 1) != b"\n"


def append_chained(path: Path, event: Mapping[str, Any]) -> dict[str, Any]:
    """Append one event to the chain at `path` and return it as written (with seq, prev, written_at)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            last = _last_line(fd)
            prev, seq = GENESIS, 1
            if last is not None:
                prev = sha256_bytes(last)
                try:
                    seq = int(json.loads(last).get("seq", 0)) + 1
                except (ValueError, AttributeError, TypeError):
                    seq = 0   # unreadable predecessor: verify_chain() reports it; seq 0 marks the break
            row = {**bounded(dict(event)), "seq": seq, "prev": prev, "written_at": time.time()}
            line = canonical_json_bytes(row) + b"\n"
            if _needs_newline(fd):
                line = b"\n" + line
            written = os.write(fd, line)
            if written != len(line):
                raise OSError(f"short write to {path}: {written} of {len(line)} bytes")
            os.fsync(fd)
            _write_head(path, seq, sha256_bytes(line.lstrip(b"\n")[:-1]))
            return row
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def head_path(path: Path) -> Path:
    return Path(str(path) + ".head")


def _write_head(path: Path, seq: int, digest: str) -> None:
    """Caller holds the log's lock. Whole-then-rename: a reader never sees half an anchor."""
    hp = head_path(path)
    tmp = hp.with_name(hp.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, canonical_json_bytes({"seq": seq, "sha256": digest}) + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, hp)


def read_head(path: Path) -> dict | None:
    try:
        h = json.loads(head_path(path).read_text())
    except (OSError, ValueError):
        return None
    return h if isinstance(h, dict) else None


def read_events(path: Path) -> Iterator[dict[str, Any]]:
    """Every readable event in order. Unreadable lines are skipped here; verify_chain() reports them."""
    path = Path(path)
    if not path.is_file():
        return
    with path.open("rb") as f:
        for raw in f:
            raw = raw.rstrip(b"\n")
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def verify_chain(path: Path) -> dict[str, Any]:
    """Walk the whole log. ok=True only when every line parses, seq counts 1, 2, 3, ... and every prev is the
    hash of the line before it. A missing log is ok with 0 events (nothing was recorded yet)."""
    path = Path(path)
    out: dict[str, Any] = {"path": str(path), "ok": True, "events": 0, "problems": []}
    if not path.is_file():
        return out
    prev, expect = GENESIS, 1
    with path.open("rb") as f:
        data = f.read()
    lines = data.split(b"\n")
    torn = lines[-1] != b""
    for n, raw in enumerate(lines[:-1] if not torn else lines, start=1):
        if not raw:
            out["problems"].append(f"line {n}: empty line")
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            out["problems"].append(f"line {n}: not JSON" + (" (torn final write)" if torn and n == len(lines) else ""))
            prev = sha256_bytes(raw)
            expect += 1
            continue
        if obj.get("prev") != prev:
            out["problems"].append(f"line {n}: prev hash does not match line {n - 1} (edited, removed or reordered)")
        if obj.get("seq") != expect:
            out["problems"].append(f"line {n}: seq {obj.get('seq')} where {expect} was expected")
        prev = sha256_bytes(raw)
        expect = (obj.get("seq") if isinstance(obj.get("seq"), int) and obj.get("seq") > 0 else expect) + 1
        out["events"] += 1
    anchor = read_head(path)
    last_seq = expect - 1
    if anchor is None:
        if out["events"]:
            out["problems"].append(f"{head_path(path).name} missing or unreadable: the end of the log cannot be checked")
    elif anchor.get("sha256") != prev or anchor.get("seq") != last_seq:
        out["problems"].append(f"last line does not match {head_path(path).name} (seq {anchor.get('seq')}): "
                               "the end of the log was edited or cut off")
    out["ok"] = not out["problems"]
    out["head"] = prev if out["events"] else None
    return out


def control_root() -> Path:
    """state/control under the live APP_BUILDER_ROOT, read at call time (tests and tools switch roots)."""
    return Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder")) / "state" / "control"


class EventStore:
    """The control layer's logs, all under one folder (default <APP_BUILDER_ROOT>/state/control):
      repair-audit.jsonl   every gate decision, every repair started / returned / raised
      observations.jsonl   observations the control layer recorded from ATTa results
      run-events.jsonl     exports, deployment verifications, report generations
      approvals.jsonl      human approvals and their consumption (see gate.py)"""
    AUDIT = "repair-audit.jsonl"
    OBSERVATIONS = "observations.jsonl"
    RUN_EVENTS = "run-events.jsonl"
    APPROVALS = "approvals.jsonl"
    LOGS = (AUDIT, OBSERVATIONS, RUN_EVENTS, APPROVALS)

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else control_root()

    def path(self, name: str) -> Path:
        if name not in self.LOGS:
            raise ValueError(f"unknown control log {name!r}")
        return self.root / name

    def append(self, name: str, event: Mapping[str, Any]) -> dict[str, Any]:
        return append_chained(self.path(name), event)

    def append_audit(self, event: Mapping[str, Any]) -> dict[str, Any]:
        return self.append(self.AUDIT, event)

    def append_observation(self, event: Mapping[str, Any]) -> dict[str, Any]:
        return self.append(self.OBSERVATIONS, event)

    def append_run_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        return self.append(self.RUN_EVENTS, event)

    def events(self, name: str) -> list[dict[str, Any]]:
        return list(read_events(self.path(name)))

    def verify(self) -> dict[str, Any]:
        per = {name: verify_chain(self.path(name)) for name in self.LOGS}
        return {"ok": all(v["ok"] for v in per.values()), "logs": per}
