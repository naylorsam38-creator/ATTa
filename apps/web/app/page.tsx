import { AttaApiError, AttaClient, summarizeReadiness } from "@atta/api_client";
import type { ReadinessReport } from "@atta/shared_types";
import { apiBaseUrl } from "../lib/config";

export const dynamic = "force-dynamic";

async function load(): Promise<{ report: ReadinessReport | null; error: string | null; base: string }> {
  const base = apiBaseUrl();
  try {
    const { report } = await new AttaClient({ baseUrl: base, timeoutMs: 5000 }).ready();
    return { report, error: null, base };
  } catch (err) {
    const msg = err instanceof AttaApiError ? `${err.code}: ${err.message}` : String(err);
    return { report: null, error: msg, base };
  }
}

export default async function StatusPage() {
  const { report, error, base } = await load();
  const summary = report ? summarizeReadiness(report) : null;
  return (
    <main>
      <h1>ATTa</h1>
      <p className="muted">Stage 01 foundation — platform status from {base}/v1/health/ready</p>
      <section className="card" aria-live="polite">
        {error && <p className="unhealthy" data-testid="status">Backend unreachable — {error}</p>}
        {summary && report && (
          <>
            <p>
              Status:{" "}
              <span data-testid="status" className={`pill ${report.status}`}>{summary.label}</span>{" "}
              <span className="muted">({report.environment})</span>
            </p>
            {report.reasons.map((r) => <p key={r} className="unhealthy">{r}</p>)}
            <table>
              <thead><tr><th>Dependency</th><th>Essential</th><th>Status</th><th>Detail</th></tr></thead>
              <tbody>
                {report.dependencies.map((d) => (
                  <tr key={d.name} data-testid={`dep-${d.name}`}>
                    <td>{d.name}</td><td>{d.essential ? "yes" : "no"}</td>
                    <td className={d.status}>{d.status}</td><td className="detail">{d.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">
              Capabilities: {Object.entries(report.capabilities).map(([k, v]) => `${v} ${k}`).join(", ") || "none"}
            </p>
          </>
        )}
      </section>
    </main>
  );
}
