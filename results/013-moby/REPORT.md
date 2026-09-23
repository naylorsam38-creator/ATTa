# 013-moby — PASS

Run: 2026-09-23T06:17:19.392Z

> Moby is the Docker Engine source: a daemon and CLI with no browser UI, so the proxy/Port checks do not apply. The pipeline builds the engine binaries from the received source and runs a unit-test package as the check.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | no bundle shipped; canonical files used |
| 1 | build | PASS | 2 step(s), 1s |
| 2 | serve | N/A | no web UI |
| 3 | proxy | N/A | no web UI |
| 4 | tests | PASS | go test: 5 package(s) ok, 0 failed |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| T1 | Unit tests pass | PASS | go test: 5 package(s) ok, 0 failed |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
