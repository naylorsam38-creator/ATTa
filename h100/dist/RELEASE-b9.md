# AIRexploit H100 pack, b9

Adds to b8: **the 17 disconnected modules wired up** (owner brief 2026-11-12, OD-37). Each is its own add-on step after
19.0d, like 19.0d: the hub is backed up first, the item proves itself inside the hub, a failure rolls back, and each has
an undo (`./items.sh undo w<n>`). Add-only: no module, stage, `config/decisions.env` or coverage row was changed.

Install: `./items.sh install` (now installs all 23 add-on items in order, then the smoke test), or one at a time:
`./items.sh w4 | w2 | w36 | w16 | w10 | w9 | w7 | w8 | w11 | w24 | w25 | w32 | w30 | w18 | w1 | w29 | w39`.
Separate owner-run steps: `./items.sh w9-egress` (agent server sandbox allowlist), `bash remote/sw32-vapid.sh` (phone push
keys), `bash remote/sw-cost-aws.sh` (AWS cost figure), `bash remote/sw39-journeys.sh` (release browser journeys).

| Item | Module | What it does now |
|---|---|---|
| w4 | 4 Model Gateway | the hub applies model_gateway's choice and calls model_invoke with it; Open WebUI "AIRexploit Auto" |
| w2 | 2 Memory | chat memory: /project, /remember, /recall, message log, per-project scope |
| w36 | 36 Cost | model and GPU spend into cost_budget; daily cap enforced (402); 80% monthly alert (repaired in b9: alert shape) |
| w16 | 16 Files | uploads through ClamAV, archives through Item 3, a Files page, signed download links, zip make/open |
| w10 | 10 Execution | code_sandbox only in a no-network container; run_shell; produced files to Files |
| w9 | 9 Coding | GitHub tools for Coders (no push); Manager-only push after approval; sandbox egress allowlist step |
| w7 | 7 Agent teams | task -> module-job links; the agent's workspace collected into Files |
| w8 | 8 Research | Stage 13 SearXNG; research task with verified citations and freshness; repair mode = no web |
| w11 | 11 Evaluation | standard cases per team model side by side; tested capabilities; weekly run; regression alert |
| w24 | 24 Voice | Kokoro + an STT server through an adapter; speech -> model -> speech; translation; video transcripts |
| w25 | 25 Meetings | browser audio (mic or tab) -> live notes -> local-model summary -> artifact |
| w32 | 32 Notifications | one queue; Telegram, webhook, web push, WhatsApp, Slack (READY until credentials) |
| w30 | 30 Jobs | Jobs screen: stop, retry, emergency stop, files and log per task |
| w18 | 18 Artifacts | artifact pane (sandboxed), versions, restore, download, approved publish, office files as artifacts |
| w1 | 1 Workspace | a Features column in Open WebUI's sidebar (Jobs, Files, Artifacts, Approvals, ...), with lock state |
| w29 | 29 Social | posts only after your approval and unlock; Facebook / Telegram / WhatsApp bridge |
| w39 | 39 Release | HTTP checks + Playwright browser journeys; go/no-go on required ones; known-good baseline |

Status of every item: **built, not proven live**. The live steps are LIVE_ACCEPTANCE_CHECKLIST.md rows L-W*.
Owner decisions waiting: governance/BLOCKED-ON-SAM.md (12).

Offline results (session 8): see the end of this file.

## Offline results (session 8)
- Working pack and an independent clean copy (fresh unzip): `run_checks.py` 551 OK (80 skipped); coverage PASS 204 rows
  (unchanged); `run_addon_checks.py` 277 OK (10 skipped, same as b8) with real Tika, ClamAV, archive-scanner and
  code-sandbox containers, SearXNG and Chromium; MANIFEST 868 files OK; every add-on script passes `bash -n`.
- Sandbox install of each item + `./items.sh install` of all 23 items + smoke test: OK.
- Package: every original file present; only the 4 declared repairs differ; original stages, run.sh and libs byte-identical.

**AIRexploit-H100-complete-b9.zip sha256 `1fd5f4c8e63b08e68cf04410d3ba05fcda940d08d2ed49deba915bc645ef8204`**
(it differs from the clean-copy-validated build `463558cf...` only in BUILD_LOG.md, which records that validation, and MANIFEST.sha256).
