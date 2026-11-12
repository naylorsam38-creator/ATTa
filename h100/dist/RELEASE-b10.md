# AIRexploit H100 pack, b10

Adds to b9: **the owner's decisions of 12 Nov 2026** (OD-38; verbatim in governance/OWNER-DECISIONS-b9-2026-11-12.md),
built to match. Same rules: wired up, not replaced; add-only; offline build; local models only. Every item is labelled
**built, not proven live** until the owner's live check (LIVE_ACCEPTANCE_CHECKLIST.md rows L-W*).
governance/BLOCKED-ON-SAM.md marks D1-D7 DECIDED. The items under "Still open" (A-E) are left exactly as they were.

| Decision | Item | What changed |
|---|---|---|
| D1 GPU price | w36 | GPU_USD_PER_HOUR=4.00 shipped as a placeholder (GPU_USD_PER_HOUR_SOURCE=placeholder), one setting; /cost/live says so |
| D2 Models and menu | w41 (new), w4 | Only team-normal and team-abl in every catalogue and the chat-bar picker, both visible; leftovers in LiteLLM and Open WebUI removed after a backup (`./items.sh w41-menu-undo` puts them back); w4 no longer adds "AIRexploit Auto". The abliterated password lock and no-timer unlock are untouched |
| D3 Sandbox allowlist | w9e | `./items.sh install` now runs the Coders' egress allowlist FIRST (PyPI, GitHub, npm, Python docs) |
| D4 Voice | w42 (new), w43 (new) | Speech to text on the shared H100 GPU on demand: GPU memory recorded before install, a worker that connects out (no new GPU inbound rule), a model started only if it fits next to the main model; the pick tests Nemotron 3.5 Transcribe and Qwen3-ASR-1.7B on your audio and keeps the lower word error rate, Whisper large-v3 the fallback. A two-way voice call (Pipecat 1.8.0): STT -> the Manager's own model via LiteLLM -> Kokoro, barge-in, tools through the Manager (start_task, task_status, research), at https://<domain>/airx/voice/ |
| D5 Web access | w8 | Both Managers (normal and abliterated) research; Coders stay on the allowlist |
| D6 Repair mode | w8 | Removed (no /hub/mode, no repair guard) |
| D7 Credentials | w45 (new), w44 (new), w9, w32, w29 | Credential board: every service empty until you sign in; nothing sends without a real credential; fake tokens only in tests/FAKE_TEST_CREDENTIALS.env (blanked and alerted if found in the real secrets file); "proven live" only after a real success. Google built up to the sign-in (read; send only after approval). GitHub: Manager-only push, two-month token |

Install: `./items.sh install` (now w9e first, then the other 29 add-on items in order, then the smoke test), or one at a time,
e.g. `./items.sh w41`, `./items.sh w42` (the H100 must be running for its second half), `./items.sh w43`.
New owner steps: `OWUI_ADMIN_KEY=...` before w41; the speech-model pick (`POST /voice/stt/pick`); Google sign-in and
`bash remote/sw44-google-token.sh`. See INSTALL_RUNBOOK.md.

Found while building (not decided; defaults used): governance/BLOCKED-ON-SAM.md N1 (on the planned figures only Whisper fits
next to the main model; testing Qwen3-ASR or Nemotron needs the main model's share lowered or the model unloaded during the
pick) and N2 (the call uses WebSocket over HTTPS, not WebRTC). DEVIATIONS D52-D54.

## Offline results (session 9)
(filled in below after the full run)
