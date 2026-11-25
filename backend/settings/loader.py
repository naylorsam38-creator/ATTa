"""Configuration loading: base.yaml -> environments/<env>.yaml -> local overrides -> environment variables.

Credentials are never read from tracked YAML: secret fields come only from environment variables
(or configuration/local.env, which is git-ignored)."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import Settings


class ConfigurationError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("invalid configuration:\n  - " + "\n  - ".join(problems))
        self.problems = problems


def find_repo_root(start: Path | None = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "configuration" / "base.yaml").is_file() and (candidate / "backend").is_dir():
            return candidate
    raise RuntimeError("repository root (configuration/base.yaml) not found")


REPO_ROOT = find_repo_root()

# Secret fields: (path in settings, environment variable). Tracked YAML may not set these.
SECRET_FIELDS = {
    ("database", "password"): "ATTA_DATABASE_PASSWORD",
    ("redis", "password"): "ATTA_REDIS_PASSWORD",
    ("object_storage", "access_key_id"): "ATTA_S3_ACCESS_KEY_ID",
    ("object_storage", "secret_access_key"): "ATTA_S3_SECRET_ACCESS_KEY",
}
ENV_PREFIX = "ATTA__"  # ATTA__API__PORT=9000 -> api.port


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigurationError([f"{path}: top level must be a mapping"])
    return data


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _coerce(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def load_settings(environment: str | None = None, root: Path | None = None,
                  env: dict[str, str] | None = None, extra_files: list[Path] | None = None,
                  include_local_env: bool = True) -> Settings:
    root = root or REPO_ROOT
    cfg_dir = root / "configuration"
    environ = dict(parse_env_file(cfg_dir / "local.env")) if include_local_env else {}
    environ.update(os.environ if env is None else env)
    environment = environment or environ.get("ATTA_ENV") or "development"
    problems: list[str] = []

    tracked = [cfg_dir / "base.yaml", cfg_dir / "environments" / f"{environment}.yaml"]
    if not tracked[1].is_file():
        problems.append(f"unknown environment '{environment}' (no {tracked[1].relative_to(root)})")
    data: dict = {}
    for f in tracked:
        layer = _read_yaml(f)
        for (section, key) in SECRET_FIELDS:
            if layer.get(section, {}).get(key) not in (None, ""):
                problems.append(f"{f.relative_to(root)} sets secret {section}.{key}; use {SECRET_FIELDS[(section, key)]}")
        data = _merge(data, layer)
    for f in [cfg_dir / "local.yaml", *(extra_files or [])]:  # untracked operator overrides
        data = _merge(data, _read_yaml(f))
    data["environment"] = environment

    for name, value in environ.items():
        if name.startswith(ENV_PREFIX):
            path = [p.lower() for p in name[len(ENV_PREFIX):].split("__") if p]
            cur = data
            for p in path[:-1]:
                cur = cur.setdefault(p, {})
            if path:
                cur[path[-1]] = _coerce(value)
    for (section, key), var in SECRET_FIELDS.items():
        if environ.get(var):
            data.setdefault(section, {})[key] = environ[var]
    if environ.get("ATTA_MODEL_PATHS"):
        data.setdefault("models", {})["storage_paths"] = [p for p in environ["ATTA_MODEL_PATHS"].split(os.pathsep) if p]

    if problems:
        raise ConfigurationError(problems)
    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError([f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]) from exc
    return settings


def resolve_path(settings_path: str, root: Path | None = None) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(settings_path)))
    return p if p.is_absolute() else (root or REPO_ROOT) / p
