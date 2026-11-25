# Stage 11 — Search provider interface

The research architecture is provider-independent. Providers are configured **explicitly** in
`configuration/research.yaml`; none is configured by default, so external search reports
`PROVIDER_UNAVAILABLE` (research `BLOCKED`) instead of pretending. Results are never fabricated.

## Configuration

```yaml
providers:
  - {id: searxng-local, kind: searxng, endpoint: "https://search.example.org", max_results: 10, enabled: true}
```

A contract may restrict itself to provider ids (`network_policy.provider_ids`). The endpoint host becomes the only
permitted network destination of the search task; the endpoint is also checked by the planning-layer URL checks.

## Normalised result (every adapter)

| Field | Meaning |
|---|---|
| `query` | the query sent |
| `rank` | 1-based position after URL de-duplication |
| `title` | result title (or null) |
| `url` | http(s) URL only |
| `snippet` | provider text — **not** verified evidence |
| `source` | engine / source name reported by the provider |
| `published_at` | publication metadata as reported (or null) |
| `retrieved_at` | when ATTa retrieved the result list |
| `provider_metadata` | engines, score, category; response-level: number_of_results, unresponsive engines |

The search tool output also carries `provider_kind`, `endpoint_host`, `response_sha256`, `ok`, `error_code`.
Each executed (or failed) query is persisted in `research.queries` with task and execution identifiers.

## Adapters (`capabilities/research_web/providers.py`)

| kind | Status | Notes |
|---|---|---|
| `searxng` | implemented; tested against a deterministic local SearXNG-compatible fixture | keyless, self-hostable; JSON API (`format=json` must be enabled on the instance) |
| `brave` | **BLOCKED_DEPENDENCY** | needs an API key; handlers receive a clean environment and Stage 02 credential protection is not integrated |

Adding an adapter: implement `build_request` / `parse_response` mapping onto the normalised result, add the kind
to the tool's `provider_kind` enum, re-sign the package. No other part of the architecture changes.

## Live provider status

No live provider was configured or tested in this sidecar (no Internet access was used by tests). Live search is
**BLOCKED** until an operator configures a provider and the Stage 11 packages are trusted and numbered.
