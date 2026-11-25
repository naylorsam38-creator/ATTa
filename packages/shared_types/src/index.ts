/**
 * TypeScript mirror of backend/contracts. The single source of truth is Python; contracts.generated.json is exported
 * from it (scripts/export_contracts.py) and test/contracts.test.ts fails if these definitions drift.
 */
export const ERROR_CODES = [
  "validation_failed", "not_found", "unauthenticated", "permission_denied", "conflict", "idempotency_conflict",
  "not_ready", "capability_unavailable", "timeout", "rate_limited", "internal_error",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

export const TASK_STATES = [
  "pending", "queued", "awaiting_approval", "running", "verifying", "succeeded", "failed", "cancelled", "timed_out",
  "blocked",
] as const;
export type TaskState = (typeof TASK_STATES)[number];

export const AVAILABILITY_STATES = [
  "available", "disabled", "invalid", "dependency_missing", "unhealthy", "rejected_duplicate",
] as const;
export type AvailabilityState = (typeof AVAILABILITY_STATES)[number];

export const RISK_CLASSES = ["low", "medium", "high", "critical"] as const;
export type RiskClass = (typeof RISK_CLASSES)[number];

export const HEALTH_STATUSES = ["healthy", "degraded", "unhealthy", "unknown"] as const;
export type HealthStatus = (typeof HEALTH_STATUSES)[number];

export interface ErrorBody {
  code: ErrorCode;
  message: string;
  correlation_id: string | null;
  details: Record<string, unknown> | unknown[] | null;
}
export interface ErrorEnvelope { error: ErrorBody }

export interface HealthReport {
  status: HealthStatus;
  service: string;
  version: string;
  checked_at: string;
}

export interface DependencyHealth {
  name: string;
  essential: boolean;
  status: HealthStatus;
  latency_ms: number | null;
  detail: string | null;
  checked_at: string;
}

export interface ReadinessReport {
  ready: boolean;
  status: HealthStatus;
  environment: string;
  dependencies: DependencyHealth[];
  capabilities: Partial<Record<AvailabilityState, number>>;
  reasons: string[];
  checked_at: string;
}

export interface CapabilityAvailability {
  id: string;
  version: string | null;
  state: AvailabilityState;
  executable: boolean;
  source: string;
  requirement: string | null;
  risk: RiskClass | null;
  problems: string[];
}

export interface Page<T> { items: T[]; next_cursor: string | null; total: number }

export interface VersionInfo { service: string; version: string; api_version: string; stage: string }

export const CORRELATION_HEADER = "X-Correlation-ID";
export const IDEMPOTENCY_HEADER = "Idempotency-Key";
