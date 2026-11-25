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
    user: str = "atta_app"              # runtime role: data access only (D-024)
    password: SecretStr | None = None
    migration_user: str = "atta"        # schema owner, used only by `python -m backend.database migrate`
    migration_password: SecretStr | None = None
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    connect_timeout_s: int = Field(default=3, ge=1, le=60)
    require_pgvector: bool = True

    def conninfo(self, *, migration: bool = False, read_only: bool = False) -> str:
        user = self.migration_user if migration else self.user
        secret = self.migration_password if migration else self.password
        parts = {"host": self.host, "port": str(self.port), "dbname": self.name, "user": user,
                 "sslmode": self.sslmode, "connect_timeout": str(self.connect_timeout_s),
                 "application_name": "atta-migrate" if migration else "atta-runtime"}
        if read_only:
            parts["options"] = "-c default_transaction_read_only=on"
        if secret is not None:
            parts["password"] = secret.get_secret_value()
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
    # Operator-signed list of trusted capability packages (id, version, package digest, approved permissions).
    trust_store: str = "configuration/capability_trust.json"
    trust_public_key: str = "configuration/capability_trust_ed25519.pub"
    tool_numbers: str = "configuration/tool_numbers.yaml"
    catalogue_namespace: str = "default"
    service_health_max_age_s: float = Field(default=5.0, gt=0, le=300)
    execution_lease_s: float = Field(default=900.0, gt=0)


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
    # Anything the policy engine cannot positively authorise is denied.
    default_decision: Literal["deny"] = "deny"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:3000", "http://localhost:3000"])
    allow_paid_infrastructure: bool = False
    permissions_file: str = "configuration/permissions.yaml"


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


class DeploymentSettings(_Strict):
    """Stage 01B adaptive deployment. `profile` AUTO = deterministic discovery; explicit choices are still validated."""
    profile: Literal["AUTO", "LOCAL_CPU", "LOCAL_NVIDIA", "H100", "TEST_ISOLATED", "AIR_GAPPED"] = "AUTO"
    model_scan_depth: int = Field(default=3, ge=1, le=8)
    scan_removable_media: bool = True
    windows_candidate_roots: list[str] = Field(default_factory=lambda: ["D:\\models", "D:\\"])
    network_probe: str | None = None            # host:port for the optional external-network probe; None = never
    runtime_preference: list[str] = Field(default_factory=lambda: ["llamacpp", "ollama", "vllm"])
    model_context_tokens: int = Field(default=8192, ge=512)
    # Controller limits may be LOWERED for a profile (never raised above configuration/controller.yaml).
    controller_caps: dict[str, float] = Field(default_factory=dict)
    gpu_visible_to_workers: bool = False
    worker_network: Literal["host", "none"] = "host"   # "none": tool workers get their own empty network namespace
    worker_user: str | None = None                      # unprivileged OS account for tool workers (when run as root)
    health_recovery_confirmations: int = Field(default=2, ge=1, le=10)
    # Windows: restricted worker identity (privileges removed, LOW integrity), workspace ACLs, Job Object limits and
    # the dedicated firewall-bound worker interpreter (runtime/worker-python/atta-worker.exe) when INSTALL created it.
    windows_worker_isolation: bool = True


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
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)
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
            raise ValueError(f"api.host={self.api.host} exposes the API beyond this machine; set "
                             "api.public_bind_allowed explicitly (and terminate TLS in front of it)")
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
