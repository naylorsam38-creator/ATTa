# H100_wired_addons-b6.zip (cumulative: Step 0, Items 0, 1, 2, abl, 3, M, Stage 19 repair; replaces b5)

- **SHA-256:** `1425d71bebd0882a500d9f25dfdd296e104fc520fff36b105a72e12f8b44a469`
- **Built from:** `baseline/H100_wired.zip` (sha256 2f335b281538e3fcf563720687dbae293c915d13044cd160ad3836a40b26f37d)
- **Item versions:** Item 0 `4c3ce563e4c2...`; Item 1 `4a1e1a811a9b...`; Item 2 `a28955ea2d18...`; Item abl
  `463e79eea051...` (all unchanged); Item 3 `80707c6976cd636ed30a035326be12dd2b5d559b9c67fe5f37752905692909b0` (new)
- **Status:** Built, offline-tested, awaiting live verification. Nothing has been run on the server.
- **New in b6:** Item 3 archive scanning (`./items.sh 3`, also part of `./items.sh install` after abl): ZIP/tar/gz
  archives are opened in a new `archive-scanner` container with no network, read-only root and a tmpfs work area;
  every file inside is virus-scanned by Stage 15's ClamAV; only clean files are released with their SHA-256. Live check
  L-I3. Add only: no pack file edited (declared repairs unchanged from b4); Stage 15's clamav service untouched.

## Clean-copy validation (this zip unzipped into an empty folder, session 6)
```
MANIFEST_OK
package integrity OK (every file matches MANIFEST.sha256)
PASS coverage reconciliation: 204 rows, a=65 b=125 c=14, every row decided with evidence
Ran 551 tests in 182.364s
OK (skipped=80)
== run_addon_checks.py (real Tika 4.2.0, real ClamAV 1.5.4 engine with a local EICAR signature, real archive-scanner container)
PASS archive_scanner
PASS document_extraction
PASS file_knowledge
PASS model_invoke
PASS scheduler_watchers
PASS every row has exactly one owner (43 modules)
Ran 183 tests in 289.998s
OK (skipped=10)
```
Real-container emulation of the whole chain on a fresh server (same code, real ClamAV engine): preflight, install0,
install1, install2, installabl, install3, preflight-after, smoke, live0, live1, live2, undo3, undoabl, undo2, undo1,
undo0: all PASS (ACCEPTANCE_RECORDS/item-3.md).

## One-file download
`AIRexploit-H100-complete-b6.zip` (SHA-256 `2fcd6a3b22b7a86da8685db155689bb448d73cb4936e0a155223377c5f068a58`): the same validated pack folder as above (identical after unzip, MANIFEST OK), plus this note and START-HERE.md.
