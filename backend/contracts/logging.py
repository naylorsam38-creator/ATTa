"""Structured JSON logging. Every record carries UTC time, level, logger, message, correlation id and extra fields.
Values of keys that look like secrets are redacted."""
from __future__ import annotations

import json
import logging
import re
import sys

from .correlation import get_correlation_id
from .timeutil import iso_utc

_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|credential|dsn)", re.I)
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": iso_utc(), "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None) or get_correlation_id(),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and k != "correlation_id"}
        if extra:
            out["fields"] = _redact(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        cid = get_correlation_id() or "-"
        return f"{iso_utc()} {record.levelname:<7} {record.name} [{cid}] {record.getMessage()}"


def configure_logging(level: str = "INFO", fmt: str = "json", file: str | None = None) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    formatter = JsonFormatter() if fmt == "json" else TextFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if file:
        handlers.append(logging.FileHandler(file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(formatter)
        root.addHandler(h)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpx2", "httpcore", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
