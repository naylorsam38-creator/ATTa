# MCP architecture (Stage 12 sidecar)

## Components

| File | Role |
|---|---|
| `backend/integrations/mcp/wire.py` | JSON-RPC 2.0 MCP client, **standard library only**: `StdioConnection`, `HttpConnection` (Streamable HTTP: POST, JSON or SSE responses, `Mcp-Session-Id`, `MCP-Protocol-Version`, redirects refused, no proxy), `McpSession` (initialize/negotiation, tools/resources/prompts listing with bounded pagination, tools/call, ping, timeouts, cancellation via `notifications/cancelled`) |
| `backend/integrations/mcp/client.py` | host-side sessions from an APPROVED manifest; network policy applied before any HTTP connection |
| `backend/integrations/mcp/validation.py` | `check_server()` — validates server metadata against the approved manifest before anything registers |
| `backend/integrations/bridges/mcp_bridge.py` | copied into the generated package; performs exactly one `tools/call` inside the Stage 01A supervised runtime |

The same `wire.py` bytes are used host-side and (as `_atta_mcp_wire.py`) inside the generated package: one protocol
implementation, two supervised contexts.

## Supported protocol

Protocol versions `2025-06-18` (preferred), `2025-03-26`, `2024-11-05`. Transports: stdio (newline-delimited JSON)
and Streamable HTTP. The ATTa client advertises **no client capabilities** (no roots, sampling or elicitation):
a server can never obtain model output, files or secrets from ATTa; server-initiated requests receive
`-32601 method not supported`. Server `instructions` are **never** forwarded to a model or stored
(`instructions_ignored` is recorded).

## Lifecycle

`CONFIGURED → STARTING → HEALTHY | DEGRADED | UNHEALTHY | STOPPED | BLOCKED` in `integrations.mcp_servers`,
recomputed on every health request (live `initialize` + `ping` + `tools/list` inside the supervised runtime):

* HEALTHY — negotiated, every approved tool offered, nothing unexpected;
* DEGRADED — server offers tools that were never approved (ignored, never registered);
* UNHEALTHY — cannot start/negotiate/ping now;
* STOPPED — not enabled, or network unavailable by policy;
* BLOCKED — malicious metadata, trust or integrity failure.

## Trust boundary

Server metadata and results are untrusted. Install verification (after operator approval, before INSTALLED):

* every declared tool must be offered with an `inputSchema` **canonically equal** to the approved one;
* undeclared tools are recorded with `accepted=false` and never registered (adding a tool needs a new approval);
* descriptions/titles of tools, resources and prompts containing instruction-like text → server **BLOCKED**;
* annotations can only make a tool riskier (a `destructiveHint` on a READ_ONLY tool is rejected), never safer;
* invalid/remote-`$ref`/oversized schemas are rejected.

A server cannot change system instructions, grant permissions, change the task objective, increase budgets,
approve actions, add tools after approval or request secrets: none of those operations exists on the client side.

## Execution

A task's MCP tool call is an ordinary gateway execution of the generated capability tool: contract, pool, drift
guard (arguments validated against the approved input schema **before** the server sees them), policy, approvals,
budgets, supervised runtime, output schema (MCP envelope `{content[], structured_content, is_error}` whose
`structured_content` must match the approved output schema), Stage 12 result filter, audit. Binary content is passed
only as type/length/SHA-256. A tool-level `isError` becomes an explicit failed execution — no retry, no fallback.

Local processes: argv only (`shell=False`), own session, minimal environment, killed as a process group on close,
timeout or cancellation; inside the gateway path the Stage 01A supervisor additionally kills every descendant.
Network: Streamable HTTP only to hosts declared by the manifest and permitted by the connectivity policy
(AIR_GAPPED → loopback only; RESTRICTED → operator allowlist).

## Tests

S12-T40 negotiation/discovery/execution, T41 schema drift + malicious metadata, T42 host-side timeout/cancellation/
no shell strings, T43 gateway timeout and cancellation with process cleanup, T44 failure isolation and no fallback,
T45 Streamable HTTP, session/protocol headers, AIR_GAPPED loopback, remote host OFFLINE_UNAVAILABLE, dynamic health.
