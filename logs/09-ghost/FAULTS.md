# 9. Ghost — commit d4952f1dec90 (2026-10-17), image ghcr.io/tryghost/ghost:nightly (built from repo `Dockerfile.production --target selfhost`) + mysql:8.0
README documents only Ghost-CLI (no Docker). Repo Docker setup used: `docker/selfhost/README.md` "Nightly" `docker run` command verbatim (url=http://localhost:2368, database__* → MySQL host `mysql`, root/example, db ghost) with a mysql:8.0 container named `mysql` on a shared network as the command requires. (Local build of the target requires a host-side `pnpm nx run @tryghost/admin:build` first, so the published nightly of that target was used.)
Secondary check: Docker Official Image `ghost:6-alpine` (docs.ghost.org) with the same env → identical failure.

Steps: A ok · B ok · C ok · D ok (pull) · E **FAIL** · F–J NOT REACHED - blocked by step E

## App faults
1. **E — Server cannot start on a host without IPv6: bind address hard-coded to `::`.** Container exits (code 2) ~10 s after start:
   `ERROR (Code: -97)` / `"There was an error starting your server."` / `InternalServerError: (Code: -97)` / `Ghost has shut down`. errno -97 = EAFNOSUPPORT: this sandbox kernel has no IPv6 (`/proc/net/if_inet6` absent; `node -e "net.createServer().listen(2368,'::')"` in the same image → `EAFNOSUPPORT -97`). The image config has no IPv4 fallback.
   Points to: `docker/selfhost/config.production.json:5` `"host": "::"` (same value in the Docker Official Image's `/var/lib/ghost/config.production.json`). Environment-triggered, but a hard failure with no fallback.
2. **E/J — Startup error hides the cause.** Any listen error other than EADDRINUSE is reported only as `(Code: <errno>)` — no error code name, address or port — with help text "Please use the error code above to search for a solution."
   Points to: `ghost/core/server/ghost-server.ts:114-121` (`messages.otherError` with only `error.errno`).

- F. HTTP — NOT REACHED - blocked by step E (`curl http://localhost:2368/` → no response for 240 s)
- G. Playwright pages/signup/login — NOT REACHED - blocked by step E
- H. Forms — NOT REACHED - blocked by step E
- I. Browser console — NOT REACHED - blocked by step E
- J. Container logs — captured (startup failure above); runtime NOT REACHED - blocked by step E

Fault count: 2 · first failing step: E
