# Handoff Bundle — 2026-09-22 — READY FOR AWS LIVE VALIDATION

This is the canonical deployment handoff. There is no second `handoff/handoff` tree and no duplicate extracted `skin/out` copy. The nested UI capability ZIP is the source consumed by the upload pipeline.

## 01-specs/
Builder specifications and watcher acceptance requirements.

## 02-front-door/
Canonical `front-door.html` source. Bootstrap installs this into `/srv/app-builder/front-door.html`.

## 03-ui-skins-capability-package/
`UI_Skin_Capability_OneShot_v2.zip` is the deployment package consumed by the pipeline. Its `out/` tree contains the skin library, capability port, UI bridge, installer, mapping and readiness evidence.

## 04-deployment/
- `bootstrap.sh` installs the gateway, upload pipeline, nginx and persistent six-stage watcher.
- `gateway.py` supports real browser `multipart/form-data` ZIP uploads and validates the ZIP before queueing it.
- `pipeline.py` validates the capability package before touching the app library, preserves local Git changes, rejects mapping failures, and never marks failed uploads as processed.
- `system_watcher.py` performs stages 1–5 plus a real Playwright/Chromium browser stage when deployed. No targets means FAIL, not PASS.
- `upstream_apps.json` contains the 38 catalogue entries; Codex remains explicitly blocked because its repository is unresolved.

## Skin mappings
- Docker Moby / Traefik / Coolify / FRP → `devops_console`
- Grafana / Umami / Supabase → `dashboard`
- Codex is blocked rather than silently assigned a skin until its exact repository is identified.
- Next AI Draw.io → `collaborative_document_editor` with the normalised key fixed.

Agno maps to `developer_tools` so all three newly-added required categories are exercised by real catalogue apps. All 46 skin categories have `skin-002` through `skin-005` CSS and metadata assets. The original 43 categories retain the historical 172 live-browser verification evidence. The three added categories have static deployment preflight evidence and are intentionally subject to the live browser stage on AWS rather than being falsely reported as historically browser-verified.

## Audit evidence
See `04-deployment/AUDIT-RESULTS.md` for the recorded five-pass static audit and real local runtime checks.

## AWS-only proof remaining
DNS, TLS certificate issuance, AWS security-group/network reachability, actual application target URLs/processes, and the external browser login → upload → pipeline → browser verification chain can only be proven on the deployed server. The bundle is designed to fail clearly rather than report a false green state when those live targets are absent.


## Runtime boundary notes

`04-deployment/build.py` is an offline/candidate-builder utility from the broader builder system. It is not part of the AWS upload pipeline and is not copied into the runtime package. The production path is `gateway.py -> pipeline.py -> install_all.py -> run-ui.sh -> proxy.js`.

The public nginx configuration strips `X-Authenticated-Customer-Id` from untrusted client traffic. Customer-specific selection must only be introduced by a trusted authentication layer that overwrites that header; otherwise the proxy uses the default customer/layout.
