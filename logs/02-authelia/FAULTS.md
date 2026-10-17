# 2. Authelia — commit e8f58adc603d (2026-10-17), image authelia/authelia:latest + traefik:v3.6.13 (examples/compose/local)
Setup used: README "docker compose → Local" bundle: `examples/compose/local/setup.sh` (answers: default domain example.com, user testeralpha, display name "Tester Alpha", made-up password). Script edits /etc/hosts, generates self-signed cert, hashes password, runs `docker compose up -d`.

Steps: A ok · B ok · C ok · D ok (images pulled) · E ok · F ok (portal 200; traefik/secure 302 → portal; public 200) · G ok (no signup by design; bad-password rejected; 1FA → Traefik dashboard; identity-verification one-time code from notification.txt accepted; TOTP registered; 2FA → secure.example.com whoami; password reset via emailed link OK) · H ok · I minor faults · J minor faults

## App faults
1. **I — Uncaught RangeError on Settings → One-Time Password registration with a POSIX browser locale.** `PAGEERROR https://authelia.example.com/settings/two-factor-authentication | Invalid language tag: en-US@posix`. The registration still succeeded server-side but the dialog crashed mid-flow. Triggered because headless Chromium in this sandbox reports `navigator.language = en-US@posix`; the web UI passes it unchecked to `Intl`. With locale `en-US` the flow is clean. Points to: web UI (`web/src` i18n/date formatting with navigator language). Minor.
2. **I — Console error for normal state:** `GET /api/secondfactor/totp → 404` ("Failed to load resource: 404") every time the Settings → Two-Factor page is opened by a user with no OTP registered (server log: `no TOTP configuration for user`). By-design status code that surfaces as a browser console error. Minor.
3. **J — Idle keep-alive connections logged as errors with a stack trace:** `level=error msg="Request timeout occurred while handling request from client." error="read tcp 172.18.0.4:9091->172.18.0.2:41522: i/o timeout" method=GET path=/ ... status_code=408`, stack `internal/server/handlers.go:95 handleError.func2`. Traefik's idle upstream connections produce ERROR log entries. Minor.
4. **C — setup.sh leaves orphaned containers:** `examples/compose/local/setup.sh` runs `sudo docker run authelia/authelia authelia crypto certificate ...` and `... crypto hash generate ...` without `--rm`; two exited containers (`nice_kepler`, `wizardly_galileo`) remain after setup. Minor.

## Environment-caused / expected (NOT counted)
- `level=warning msg="Could not determine the clock offset" ... read udp ...:123: i/o timeout` — sandbox blocks NTP/UDP.
- `401 POST /api/firstfactor` + `Unsuccessful 1FA authentication attempt` — deliberate bad-password test.
- `403 GET /api/configuration`, `403 POST /api/user/info` on /settings while logged out — expected auth responses.
- `the user has already used this code recently` — my test reused a TOTP code in the same window (correct replay protection); fixed in test.

Fault count: 4 (all minor) · first failing step: C (setup.sh orphaned containers); first functional fault at I
