/** Typed client for the ATTa /v1 API, shared by web, desktop and mobile. */
import {
  CORRELATION_HEADER, IDEMPOTENCY_HEADER, type CapabilityAvailability, type ErrorBody, type ErrorCode,
  type HealthReport, type Page, type ReadinessReport, type VersionInfo,
} from "@atta/shared_types";

export class AttaApiError extends Error {
  constructor(public readonly status: number, public readonly code: ErrorCode | "network_error" | "invalid_response",
              message: string, public readonly correlationId: string | null = null,
              public readonly details: unknown = null) {
    super(message);
    this.name = "AttaApiError";
  }
}

export interface ClientOptions {
  baseUrl: string;
  fetch?: typeof fetch;
  timeoutMs?: number;
  correlationId?: () => string;
}

export interface ReadinessResult { httpStatus: number; report: ReadinessReport }

function randomCorrelationId(): string {
  // Correlation ids are not secrets; Web Crypto is used when present (browsers, Node, Electron), with a
  // Math.random fallback for React Native runtimes that do not expose it.
  const bytes = new Uint8Array(12);
  const c = (globalThis as { crypto?: { getRandomValues(a: Uint8Array): Uint8Array } }).crypto;
  if (c) c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return "cor_web_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export class AttaClient {
  private readonly base: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly cid: () => string;

  constructor(opts: ClientOptions) {
    this.base = opts.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = opts.fetch ?? globalThis.fetch.bind(globalThis);
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.cid = opts.correlationId ?? randomCorrelationId;
  }

  private async request<T>(method: string, path: string, init: { body?: unknown; idempotencyKey?: string;
    acceptStatuses?: number[] } = {}): Promise<{ status: number; body: T }> {
    const headers: Record<string, string> = { Accept: "application/json", [CORRELATION_HEADER]: this.cid() };
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    if (init.idempotencyKey) headers[IDEMPOTENCY_HEADER] = init.idempotencyKey;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    let res: Response;
    try {
      res = await this.fetchImpl(this.base + path, {
        method, headers, signal: ctrl.signal,
        ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
      });
    } catch (err) {
      throw new AttaApiError(0, "network_error", `cannot reach ${this.base}: ${(err as Error).message}`);
    } finally {
      clearTimeout(timer);
    }
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      throw new AttaApiError(res.status, "invalid_response", `non-JSON response from ${path}`);
    }
    if (res.ok || init.acceptStatuses?.includes(res.status)) return { status: res.status, body: body as T };
    const e = (body as { error?: ErrorBody }).error;
    if (!e) throw new AttaApiError(res.status, "invalid_response", `HTTP ${res.status} without error envelope`);
    throw new AttaApiError(res.status, e.code, e.message, e.correlation_id, e.details);
  }

  async live(): Promise<HealthReport> {
    return (await this.request<HealthReport>("GET", "/v1/health/live")).body;
  }

  /** Readiness returns 503 with a full report when not ready; that is data, not an exception. */
  async ready(): Promise<ReadinessResult> {
    const r = await this.request<ReadinessReport>("GET", "/v1/health/ready", { acceptStatuses: [503] });
    return { httpStatus: r.status, report: r.body };
  }

  async version(): Promise<VersionInfo> {
    return (await this.request<VersionInfo>("GET", "/v1/system/version")).body;
  }

  async listCapabilities(limit = 50, cursor?: string): Promise<Page<CapabilityAvailability>> {
    const q = new URLSearchParams({ limit: String(limit), ...(cursor ? { cursor } : {}) });
    return (await this.request<Page<CapabilityAvailability>>("GET", `/v1/capabilities?${q}`)).body;
  }

  async executeCapability(id: string, payload: Record<string, unknown>, idempotencyKey?: string): Promise<unknown> {
    return (await this.request("POST", `/v1/capabilities/${encodeURIComponent(id)}/executions`,
      { body: payload, ...(idempotencyKey ? { idempotencyKey } : {}) })).body;
  }
}

export interface ReadinessSummary { label: "Ready" | "Degraded" | "Not ready"; essentialDown: string[]; optionalDown: string[] }

export function summarizeReadiness(report: ReadinessReport): ReadinessSummary {
  const down = report.dependencies.filter((d) => d.status !== "healthy");
  return {
    label: !report.ready ? "Not ready" : down.length ? "Degraded" : "Ready",
    essentialDown: down.filter((d) => d.essential).map((d) => d.name),
    optionalDown: down.filter((d) => !d.essential).map((d) => d.name),
  };
}
