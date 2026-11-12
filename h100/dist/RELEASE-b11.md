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
(filled in below after the full run)
