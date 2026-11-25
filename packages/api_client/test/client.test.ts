import { describe, expect, it } from "vitest";
import { AttaApiError, AttaClient, summarizeReadiness } from "../src/index";
import type { ReadinessReport } from "@atta/shared_types";

function fakeFetch(status: number, body: unknown, seen: Request[] = []): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    seen.push(new Request(input, init));
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

const report: ReadinessReport = {
  ready: false, status: "unhealthy", environment: "test", reasons: ["essential dependency 'redis' is unhealthy"],
  capabilities: { available: 1 }, checked_at: "2026-11-25T00:00:00Z",
  dependencies: [
    { name: "postgres", essential: true, status: "healthy", latency_ms: 1, detail: null, checked_at: "" },
    { name: "redis", essential: true, status: "unhealthy", latency_ms: 1, detail: "refused", checked_at: "" },
    { name: "temporal", essential: false, status: "unhealthy", latency_ms: 1, detail: "refused", checked_at: "" },
  ],
};

describe("AttaClient", () => {
  it("treats a 503 readiness response as data and sends a correlation id", async () => {
    const seen: Request[] = [];
    const c = new AttaClient({ baseUrl: "http://api/", fetch: fakeFetch(503, report, seen), correlationId: () => "cor_test_1234" });
    const r = await c.ready();
    expect(r.httpStatus).toBe(503);
    expect(r.report.ready).toBe(false);
    expect(seen[0]!.url).toBe("http://api/v1/health/ready");
    expect(seen[0]!.headers.get("X-Correlation-ID")).toBe("cor_test_1234");
  });

  it("maps the standard error envelope to AttaApiError", async () => {
    const env = { error: { code: "permission_denied", message: "denied", correlation_id: "cor_x", details: null } };
    const c = new AttaClient({ baseUrl: "http://api", fetch: fakeFetch(403, env) });
    await expect(c.listCapabilities()).rejects.toMatchObject({ status: 403, code: "permission_denied", correlationId: "cor_x" });
  });

  it("reports network failures distinctly", async () => {
    const c = new AttaClient({ baseUrl: "http://api", fetch: (async () => { throw new Error("ECONNREFUSED"); }) as typeof fetch });
    const err = await c.live().catch((e) => e);
    expect(err).toBeInstanceOf(AttaApiError);
    expect(err.code).toBe("network_error");
  });
});

describe("summarizeReadiness", () => {
  it("separates essential from optional failures", () => {
    expect(summarizeReadiness(report)).toEqual({ label: "Not ready", essentialDown: ["redis"], optionalDown: ["temporal"] });
    const degraded = { ...report, ready: true, dependencies: [report.dependencies[0]!, report.dependencies[2]!] };
    expect(summarizeReadiness(degraded).label).toBe("Degraded");
  });
});
