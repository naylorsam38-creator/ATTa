# AIRexploit H100 pack, b11

Adds to b10: **the owner's decisions on N1 and N2** (12 Nov 2026, OD-39; verbatim in
governance/OWNER-DECISIONS-N1-N2-2026-11-12.md). Everything else is b10 unchanged, apart from the record files.
Every item stays labelled **built, not proven live**. Items A-E under "Still open" are unchanged.

| Decision | What changed |
|---|---|
| N1 speech models | New `./items.sh w42-compare` (also run by `./items.sh w42` when `VOICE_SAMPLES_TASK` names your samples folder): ONCE at install the main model is briefly unloaded through the controller's own /borrow; Nemotron 3.5 Transcribe, Qwen3-ASR-1.7B and Whisper large-v3 all transcribe your recordings; each one's GPU memory is measured; the lowest word error rate wins (Whisper the fallback). The main model is then reloaded and must be HEALTHY before anything else; if it is not, the step STOPS and reports. Only if the winner does not fit beside the main model is the main model's memory share lowered, to what the winner needs (backed up, reloaded and checked; if the main model cannot run there, the old share goes back and the best model that fits is kept). The report gives the share before/after, the winner's memory, the main model's use and the headroom. Undo: `./items.sh w42-share-undo`. Live check: L-W42C |
| N2 call transport | Kept: WebSocket over the existing HTTPS, no new firewall port (as in b10) |

Changed files (new work): remote/sw42-compare.sh (new), stages/ix-w42-compare.sh and ix-w42-share-undo.sh (new),
stages/ix-itemw42.sh, items.sh (two commands), platform/addons/hub/ext_stt.py 0.2.0, remote/h100-voice-worker.py,
platform/addons/hub/tests/test_stt.py. The H100 controller (remote/h100-controller.py) is NOT changed.
Records: BLOCKED-ON-SAM (N1, N2 DECIDED), DECISIONS OD-39, DEVIATIONS D53, ACCEPTANCE_RECORDS/item-w42.md,
LIVE_ACCEPTANCE_CHECKLIST (L-W42C), INSTALL_RUNBOOK, BUILD_STATE, BUILD_LOG.

## Offline results (session 9, b11)
- Clean copy (fresh unzip, MANIFEST 909 files OK, `bash -n` on every stage and remote script): `run_checks.py` 551 OK
  (80 skipped); coverage PASS 204 rows (unchanged); `run_addon_checks.py` 300 OK (14 skipped: b9's 10 plus the 4 voice-call
  tests, which need pipecat) with real Tika, ClamAV, archive-scanner and code-sandbox containers, SearXNG and Chromium.
- The voice-call image built from the clean copy: its 4 tests OK inside it (read-only, no capabilities).
- The comparison (tests/test_stt.py, 8 OK) uses the real script, hub and worker; SIMULATED: the H100 controller (a stand-in
  with the same admin API), the GPU, docker and the speech models. Real timings and memory figures are live check L-W42C.

## Package
- `AIRexploit-H100-complete-b11.zip` SHA-256 `340b611697fc21050e08586fb2dc4fe9b9843f2cabe9b7518ade2cd2cf2fd791`, MANIFEST 909 files.
  It differs from the clean-copy-validated build (`324b9fc3...`) only in BUILD_STATE.md and BUILD_LOG.md (the results above).
