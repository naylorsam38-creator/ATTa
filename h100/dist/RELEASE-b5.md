# H100_wired_addons-b5.zip (cumulative: Step 0, Items 0, 1, 2, abl, M, Stage 19 repair; replaces b4)

- **SHA-256:** `6d7f8cad0296d367f9576d73c49f13cbc2796582113de8022d163608fb114219`
- **Built from:** `baseline/H100_wired.zip` (sha256 2f335b281538e3fcf563720687dbae293c915d13044cd160ad3836a40b26f37d)
- **Item versions:** Item 0 `4c3ce563e4c2...`; Item 1 `4a1e1a811a9b...`; Item 2 `a28955ea2d18...` (all unchanged);
  Item abl `463e79eea05108409c656b8dace8378d7d58a943cfb1aac837fe29d95abf8412` (new); Item M checker in the pack
- **Status:** Built, offline-tested, awaiting live verification. Nothing has been run on the server.
- **New in b5:** Team - abliterated has no unlock timer (OD-17 = 0) as add-on item `abl` (`./items.sh abl`, also part of
  `./items.sh install`); the Unified Master Plan's governing sections in docs/UNIFIED_MASTER_PLAN.md (86 register rows
  verified identical to coverage/coverage.md). Add only: no pack file edited (declared repairs unchanged from b4).

## Clean-copy validation (this zip unzipped into an empty folder, session 5)
```
MANIFEST_OK
package integrity OK (every file matches MANIFEST.sha256)
Iabl   NOT STARTED            ix-itemabl.sh
463e79eea05108409c656b8dace8378d7d58a943cfb1aac837fe29d95abf8412
== coverage reconciliation (Item M) ==
PASS coverage reconciliation: 204 rows, a=65 b=125 c=14, every row decided with evidence
Ran 551 tests in 191.343s
OK (skipped=80)
== run_addon_checks.py (with AIRX_REAL_TIKA_URL: real Tika 4.2.0)
PASS document_extraction
PASS file_knowledge
PASS model_invoke
PASS scheduler_watchers
PASS every row has exactly one owner (42 modules)
Ran 150 tests in 239.366s
OK (skipped=8)
```
Real-container emulation of the whole chain on a fresh server (same code): preflight, install0, install1, install2,
installabl, preflight-after, smoke, live0, live1, live2, undoabl, undo2, undo1, undo0: all PASS (ACCEPTANCE_RECORDS/item-abl.md).
