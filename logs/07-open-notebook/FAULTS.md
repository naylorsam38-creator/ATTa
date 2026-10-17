# 7. open-notebook — commit a0f6f3081ae0 (2026-10-04), images lfnovo/open_notebook:v1-latest + surrealdb/surrealdb:v2
Setup used: README Quick Start — repo `docker-compose.yml`, `OPEN_NOTEBOOK_ENCRYPTION_KEY` set to `openssl rand -hex 32` (Step 2), `docker compose up -d`.

Steps: A ok · B ok · C ok · D ok · E ok · F ok (8502 → 307 /notebooks; API /health 200) · G ok (no auth by default — signup/login not applicable; 11 pages, 211 clicks) · H partial (notebook, note, text source, provider credential all created via UI; AI processing/chat cannot run without a real provider key) · I minor fault · J clean (only expected "no model configured" errors)

## App faults
1. **H/I — Chat failure is silent in the UI.** With no default chat model, sending a chat message → `422 POST /api/chat/execute {"detail":"No model configured for default for type=chat. Please go to Manage → Models ..."}`; browser console `Error sending message: AxiosError: Request failed with status code 422`; no error toast/message was visible on the page at +0.7s/+1.5s/+3s/+10s (screenshot chat-error-toast.png) — the typed question just disappears. Points to: frontend chat send handler (`_next/static/chunks/1p2wi2nvw-wdb.js`). Minor.

## Not counted (expected / environment)
- Source "Failed" + log `No model configured for default for type=transformation`, `Failed to embed ... No embedding model configured` — README Step 4 (connect an AI provider) needs a real API key; not available in this test.
- Credential test → `{"success":false,"message":"Connection error - check network/endpoint"}` — made-up key + sandbox TLS interception.
- `WARNING api.routers.config:get_latest_version_cached:90 - Version check failed: [SSL: CERTIFICATE_VERIFY_FAILED]` — sandbox TLS interception.
- 404 on `/signup`, `/register`, `/models` — URLs guessed by the test, not linked by the app.
- `_rsc=... ERR_ABORTED` — Next.js prefetches aborted by test navigation.

Fault count: 1 (minor) · first failing step: H
