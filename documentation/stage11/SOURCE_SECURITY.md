# Stage 11 — Source retrieval security

## Layers

1. **Planning layer** (`backend/research/urls.py`, API process): before a task contract is written, every URL is
   structurally checked (`precheck_url`) and scoped (`check_scope`: allowed/excluded domains, excluded sources).
   Refused URLs are recorded (`source.refused` events) and never reach a worker.
2. **Contract layer** (Stage 01A): a retrieval task's `permitted_network_destinations` is exactly the URL's host;
   the drift guard rejects any proposal whose `url_arguments` point elsewhere (`network_not_permitted`).
3. **Policy layer** (Stage 01A): tool execution only as the attested controller, LOW risk, permissions
   `network.web.read` / `research.search.query` granted by operator configuration.
4. **Retrieval layer** (`capabilities/research_web/netguard.py`, isolated handler process, clean environment,
   rlimits, killed with its whole process tree on timeout/cancel) — the authoritative enforcement.

## Retrieval layer checks (every hop)

| Threat | Control |
|---|---|
| Unsupported protocols (`file:`, `ftp:`, `gopher:`, `data:`, `javascript:` …) | scheme allow-list `http`, `https`; re-checked on every redirect target |
| Credentials / userinfo tricks (`http://a@169.254.169.254/`) | any userinfo or `@` in the authority is refused |
| localhost / internal names | blocked names (`localhost`, `metadata.google.internal`, …), blocked suffixes (`.local`, `.internal`, `.localhost`, `.lan`, `.corp`, `.arpa`, `.onion` …), single-label hosts |
| Numeric/hex host encodings (`2130706433`, `0x7f.1`, `017700000001`, `127.1`) | refused structurally; and resolved addresses are checked anyway |
| Private-network / loopback / link-local / CGNAT / multicast / reserved / unspecified | `classify_ip` on EVERY resolved address; IPv4-mapped, 6to4, NAT64 and Teredo embeddings are unwrapped |
| Cloud metadata endpoints | 169.254.169.254, 169.254.170.2, 100.100.100.200, 192.0.0.192, fd00:ec2::254 named explicitly (also non-global) |
| DNS rebinding | all answers must be public; the connection goes to the **validated IP** (pinned socket, TLS SNI/verification still use the hostname) |
| Unsafe redirects | manual redirects, ≤ 5; target re-validated; https→http downgrade refused; cross-host redirect refused and returned as `redirect_target` for a separately authorised fetch |
| Non-standard ports | only 80/443 |
| Oversized responses | declared Content-Length, streamed byte cap (5 MiB), bounded gzip/deflate decompression (10 MiB) — `SOURCE_TOO_LARGE` |
| Slow / hanging servers | per-socket timeouts + total deadline (30 s) — `SOURCE_UNAVAILABLE` (timeout); the Stage 01A supervisor also kills at the tool timeout |
| Unexpected content | content-type allow-list; PDF stored but extraction `BLOCKED_DEPENDENCY` (no approved PDF parser in the locked dependencies) |

The policy lives in `capabilities/research_web/retrieval_policy.py`, inside the operator-signed package digest,
loaded from the integrity-verified in-memory snapshot. Arguments (worker-controlled) can never relax it.
`loopback_ports_allowed_for_tests` is empty in production; tests trust a modified copy with a throw-away key.

## Untrusted web content

* Retrieved bytes are DATA: stored content-addressed, parsed in the isolated process, never executed.
* Hidden elements (`hidden`, `aria-hidden`, `display:none`) are excluded from extracted text and counted.
* Content reaches workers only under `untrusted` in the Stage 01A context packet, with provenance.
* A page cannot change system instructions, task contracts, tool pools, budgets, acceptance criteria, approvals,
  or the research objective: those are stored in PostgreSQL, hash-sealed, versioned only through operator-
  authorised calls, and the gateway refuses proposals targeting `contract:`, `budget:`, `pool:` … resources.
* Tested: `test_prompt_injection_cannot_alter_contract_tools_or_budgets`,
  `test_compromised_worker_obeying_injected_content_is_contained`.

## Read-only scope

No form submission, POST requests, purchases, messages, account changes, browser or desktop control. Requests are
GET only with no cookies or credentials. Interactive pages are reported as a Stage 15 browser dependency.

## Known limits

* Handlers share the host network namespace (BLOCKERS B-014): isolation of the network stack itself needs the
  sandbox stage. The checks above are application-level.
* Provider API keys are not available to handlers until Stage 02 credential protection (BLOCKED for `brave`).
