"""Typed configuration schema. Validated before any service starts."""
from __future__ import annotations

import os
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiSettings(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=8800, ge=1, le=65535)
    public_bind_allowed: bool = False
    request_timeout_s: float = Field(default=30.0, gt=0)


class DatabaseSettings(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "atta"
    user: str = "atta"
    password: SecretStr | None = None
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    connect_timeout_s: int = Field(default=3, ge=1, le=60)
    require_pgvector: bool = True

    def conninfo(self) -> str:
        parts = {"host": self.host, "port": str(self.port), "dbname": self.name, "user": self.user,
                 "sslmode": self.sslmode, "connect_timeout": str(self.connect_timeout_s)}
        if self.password is not None:
            parts["password"] = self.password.get_secret_value()
        return " ".join(f"{k}='{v.replace(chr(39), chr(92) + chr(39))}'" for k, v in parts.items())


class RedisSettings(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=6380, ge=1, le=65535)
    db: int = Field(default=0, ge=0, le=15)
    password: SecretStr | None = None
    socket_timeout_s: float = 2.0


class ObjectStorageSettings(_Strict):
    endpoint_url: str | None = "http://127.0.0.1:9000"
    bucket: str = "atta-dev"
    region: str = "us-east-1"
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None


class ModelEndpoint(_Strict):
    name: str
    kind: Literal["ollama", "llamacpp", "vllm", "litellm", "openai_compatible", "anthropic", "openai"]
    base_url: str
    enabled: bool = True
    health_path: str | None = None
    api_key_env: str | None = None  # name of the env var holding the key; never the key itself


class ModelSettings(_Strict):
    storage_paths: list[str] = Field(default_factory=list)
    endpoints: list[ModelEndpoint] = Field(default_factory=list)
    default_endpoint: str | None = None
    allow_model_downloads: bool = False


class CapabilitySettings(_Strict):
    directories: list[str] = Field(default_factory=lambda: ["capabilities"])
    healthcheck_timeout_s: float = Field(default=5.0, gt=0, le=120)


class WorkerSettings(_Strict):
    max_concurrency: int = Field(default=4, ge=1, le=256)
    cpu_limit: float = Field(default=2.0, gt=0)
    memory_limit_mb: int = Field(default=2048, ge=128)


class DeadlineSettings(_Strict):
    default_execution_timeout_s: float = Field(default=60.0, gt=0)
    max_execution_timeout_s: float = Field(default=900.0, gt=0)

    @model_validator(mode="after")
    def _order(self):
        if self.default_execution_timeout_s > self.max_execution_timeout_s:
            raise ValueError("default_execution_timeout_s exceeds max_execution_timeout_s")
        return self


class LoggingSettings(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"
    directory: str = "runtime/logs"


class SecuritySettings(_Strict):
    # Stage 01: protected operations are denied until Stage 02 authentication/authorisation exists.
    default_decision: Literal["deny"] = "deny"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:3000", "http://localhost:3000"])
    allow_paid_infrastructure: bool = False


class HealthProbeSpec(_Strict):
    type: Literal["tcp", "http", "redis", "postgres", "s3", "none"]
    host: str | None = None
    port: int | None = None
    url: str | None = None
    timeout_s: float = 2.0


class ServiceSpec(_Strict):
    essential: bool
    mode: Literal["managed", "external", "disabled"]
    description: str = ""
    command: list[str] = Field(default_factory=list)
    requires_binary: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    health: HealthProbeSpec
    startup_timeout_s: float = Field(default=30.0, gt=0)
    max_restarts: int = Field(default=3, ge=0, le=20)
    restart_window_s: float = Field(default=300.0, gt=0)
    restart_backoff_s: float = Field(default=1.0, ge=0)
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _managed_has_command(self):
        if self.mode == "managed" and not self.command:
            raise ValueError("managed service needs a command")
        return self


class RuntimeSettings(_Strict):
    directory: str = "runtime"
    monitor_interval_s: float = Field(default=2.0, gt=0)


class Settings(_Strict):
    environment: Environment = Environment.DEVELOPMENT
    api: ApiSettings = Field(default_factory=ApiSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    object_storage: ObjectStorageSettings = Field(default_factory=ObjectStorageSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    capabilities: CapabilitySettings = Field(default_factory=CapabilitySettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)
    deadlines: DeadlineSettings = Field(default_factory=DeadlineSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    services: dict[str, ServiceSpec] = Field(default_factory=dict)

    @field_validator("services")
    @classmethod
    def _deps_exist(cls, v: dict[str, ServiceSpec]):
        for name, spec in v.items():
            for dep in spec.depends_on:
                if dep not in v:
                    raise ValueError(f"service '{name}' depends on unknown service '{dep}'")
        return v

    @model_validator(mode="after")
    def _security(self):
        if self.api.host not in ("127.0.0.1", "localhost", "::1") and not self.api.public_bind_allowed:
            raise ValueError(f"api.host={self.api.host} would expose unfinished endpoints; set "
                             "api.public_bind_allowed only once Stage 02 authentication is in place")
        if self.environment == Environment.PRODUCTION:
            if self.database.password is None:
                raise ValueError("production requires database.password (ATTA_DATABASE_PASSWORD)")
            if self.database.sslmode in ("disable", "allow", "prefer"):
                raise ValueError("production requires database.sslmode require/verify-*")
        return self

    def warnings(self) -> list[str]:
        out = []
        sysdrive = (os.environ.get("SystemDrive") or "C:").upper()
        for p in self.models.storage_paths:
            if os.name == "nt" and p.upper().startswith(sysdrive):
                out.append(f"model storage path {p} is on the system drive; prefer D: or an external drive")
            if not os.path.isdir(p):
                out.append(f"model storage path {p} is not currently present (removable drive not attached?)")
        return out
