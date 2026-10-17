# 3. Mixpost (Lite) — commit df57648b8663 (2026-03-16), image inovector/mixpost:latest + mysql/mysql-server:8.0 + redis:latest
Repo has no Dockerfile/compose; README → docs.mixpost.app/lite → "Install Mixpost Lite with Docker". Used the docs' **"Without SSL Support"** docker-compose.yml (port 9000) and the docs' `.env` verbatim (APP_KEY generated, APP_DOMAIN=localhost:9000, made-up DB password). `docker compose up -d`.

Steps: A ok · B ok (docs) · C fault · D ok · E ok · F partial (`/` → 302 to https) · G FAIL (login impossible) · H NOT REACHED - blocked by step G · I faults · J faults

## App faults
1. **C/F/G — Documented "Without SSL Support" setup is unusable over HTTP.** The docs' `.env` hard-codes `APP_URL=https://${APP_DOMAIN}` ("no modification required"), but this compose variant serves plain HTTP on :9000 with no TLS. Result: `GET http://localhost:9000/` → `302 https://localhost:9000/mixpost` → browser `net::ERR_SSL_PROTOCOL_ERROR`; the login form's action is `https://localhost:9000/mixpost/login`, so login with the documented default credentials (admin@example.com / changeme) fails: `POST https://localhost:9000/mixpost/login net::ERR_SSL_PROTOCOL_ERROR`. Favicon also emitted as https. Points to: docs.mixpost.app/lite/installation/docker (step 4 `.env`: `APP_URL=https://${APP_DOMAIN}`) vs. the "Without SSL Support" compose.
2. **G/I/J — Every unknown URL under /mixpost returns HTTP 500 instead of 404.** `GET /mixpost/forgot-password` → 500, `/mixpost/register` → 500, `/mixpost/this-does-not-exist` → 500 (outside the prefix `/nope` → 404). Log: `production.ERROR: Uncaught RuntimeException: Session store not set on request. in vendor/laravel/framework/src/Illuminate/Http/Request.php:612`; nginx: `FastCGI sent in stderr: "PHP message: PHP Fatal error: Uncaught RuntimeException: Session store not set on request."` (63 occurrences).
   Points to: `src/Http/Middleware/HandleInertiaRequests.php:56` (`$request->session()->get('success')` in the shared `flash` prop) called from `src/Exceptions/MixpostExceptionHandler.php:55` (`renderInertiaException` → `Inertia\Response->toResponse()`) for a request that never went through the session middleware.

## Not reached
- G (authenticated pages, all buttons/links), H (create post / accounts / media forms): NOT REACHED - blocked by step G (login POST goes to https on an http-only port).
- No signup feature in Mixpost Lite (single admin account) — signup not applicable.

Fault count: 2 · first failing step: C (documented config) → G
