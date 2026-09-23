# 006-app-factory-controller3 — PASS

Run: 2026-09-23T04:11:22.580Z

> App Factory Controller: Python reference implementation (fail-closed incident controller, orchestrator, dependency graph). No web UI, so the proxy/Port checks do not apply; the pipeline runs its unit tests.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | bundled proxy.js, port.js, mover.mjs match canonical |
| 1 | build | PASS | nothing to build |
| 2 | serve | N/A | no web UI |
| 3 | proxy | N/A | no web UI |
| 4 | tests | PASS | 47 tests ran, all OK |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| T1 | Unit tests pass | PASS | 47 tests ran, all OK |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
