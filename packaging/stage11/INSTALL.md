# ATTa Stage 11 additions — web search and deep research (CAP-11, CAP-12)

Drop-in package for an ATTa repository at the Stage 01A baseline (`21104c4`) or later (also after Stage 01B /
Stage 04 have been merged). It contains only what Stage 11 adds or changes. **Final merge approval is pending; do
not install into the authoritative repository before it is granted.**

| Path | What |
|---|---|
| `install.py` | transactional installer (Python 3.8+, standard library only, Windows / Linux / macOS, needs Git) |
| `install.bat`, `install.ps1`, `install.sh` | Windows cmd, PowerShell and POSIX wrappers for `install.py` |
| `overlay/` | the new files at their repository paths (`ADDED_FILES.txt`) |
| `base/`, `ours/` | baseline and Stage 11 versions of the 6 shared documents Stage 11 edits (`MODIFIED_FILES.txt`) |
| `patches/` | the same edits as a unified diff; `stage11-full.mbox` = the Stage 11 commits (`git am` onto `21104c4`) |
| `compat/check_compat.py` | compatibility check against the actual Stage 01B and Stage 04 packages |
| `PACKAGE.json` | metadata: baseline, sidecar commit, file lists, protected paths, merge rules |
| `SHA256SUMS` | SHA-256 of every other file; verified by the installer before anything else |

No existing runtime source file and no protected file is changed. No new Python or Node dependency.

## 1. Verify the download
Compare the zip's SHA-256 with the value given in the hand-off. The installer then verifies every file against
`SHA256SUMS` and refuses (exit 7) on any mismatch, missing or extra file.

## 2. Dry run (always first)
```
python install.py  <ATTa root> --check          # Linux / macOS (or: sh install.sh <ATTa root> --check)
install.bat        C:\path\to\ATTa --check       # Windows cmd   (uses the py launcher, else python)
.\install.ps1      C:\path\to\ATTa --check       # Windows PowerShell
```
The dry run performs every check **and** rehearses the complete installation in a temporary `git worktree` of the
target's HEAD. It exits non-zero if anything would fail. Nothing in the target is changed.

## 3. Install
Same command without `--check`. Requirements enforced (the installer refuses otherwise, exit 2):
* the target is the **top level** of its own Git repository — not a sub-directory, not a submodule / linked
  worktree, not nested inside another repository, and no nested repository on any path that would be written;
* the working tree is **clean** (no modified, staged or untracked files) and no merge/rebase is in progress;
* the Stage 01A runtime is present (and the baseline commit is in HEAD's history when that object exists).

Transaction: the installer plans everything (an existing file that differs is a conflict, exit 3), rehearses,
backs up the shared documents and writes a journal in `.git/atta-stage11-install/`, applies, then verifies every
written file, that `git status` shows exactly the planned paths, and that protected configuration is unchanged.
Any error, Ctrl-C or failed verification **restores the original state** and confirms the tree is clean again
(exit 5). If the process is killed hard, the next run refuses and
`python install.py <ATTa root> --rollback-interrupted` restores the original state.

Shared documents are merged, never overwritten: `BLOCKERS.md`, `DECISIONS.md`, `IMPLEMENTATION_PROGRESS.md`,
`CHANGE_MANIFEST.md` keep every stage's sections (append-only logs; union merge when both stages appended at the
same place); `documentation/requirements/requirement_register.yaml` is 3-way merged and any conflict stops the
installation; `MASTER_IMPLEMENTATION_REGISTER.md` is regenerated from the merged YAML
(`scripts/requirement_register.py render`) using the target's `.venv` Python (needs PyYAML; otherwise a conflict
there stops the installation).

Exit codes: 0 ok / already installed · 2 refused · 3 conflicts · 4 rehearsal failed · 5 failed and rolled back ·
6 rollback incomplete · 7 package integrity failure.

After a successful install, review and commit: `git add -A && git commit -m "Stage 11 additions"`.

## 4. Database and tests
```
ATTA_ENV=<env> python -m backend.database migrate      # adds schema `research` (0011_stage11_research.sql)
python scripts/requirement_register.py check
ATTA_ENV=test ATTA_REQUIRE_DB=1 python -m pytest -q tests/research
ATTA_ENV=test python -m pytest -q tests/unit tests/security tests/integration tests/acceptance
python -m pytest -q tests/test_*.py
```
`0011` depends only on 0001/0002. Rename it only **before** it is first applied anywhere (the ledger is keyed by name).

## 5. Compatibility with the actual Stage 01B and Stage 04 packages
```
python compat/check_compat.py --baseline <Stage 01A tree or zip> \
    --stage 01B=<01B package> --stage 04=<04 package> [--run-tests] --out compat-report.json
```
Builds the combined tree in a temporary repository, checks overlaps (paths, migrations, `research` schema,
capability ids, tool numbers, permissions, identifier prefixes, `/v1/research` routes, configuration), runs this
installer on it, and with `--run-tests` runs Stage 11, Stage 01/01A, legacy and the other stages' tests, separating
new failures from the documented pre-existing ones. Verdict COMPATIBLE (exit 0) / INCOMPATIBLE (exit 1).

## 6. Controlled integration (each step separately approved)

**Stage 02 — permission registration (operator; protected files):**
```
python -m backend.research.operator_config check
python -m backend.research.operator_config render --out <review dir>   # merged copies + SHA-256; never in place
```
Review, copy `tool_numbers.yaml` and `permissions.yaml` into `configuration/`, sign the packages
(`python -m backend.capability_registry trust capabilities/research_web --key <key> --operator NAME`, same for
`capabilities/research_evidence`), re-seal. API operations, roles, approvals and tenant inputs for Stage 02:
`documentation/stage11/integration/stage02_research_permissions.yaml`. Until then the packages stay `untrusted`.

**Stage 03 — model integration:** pass a `ResearchHub` (`backend/research/hub.py`) to
`ResearchService(hub=...)` / `build_research_service(controller_service, hub=...)`; optionally a model-backed
`worker_factory`. Hub output is validated (bounded decomposition, labelled claims bound only to stored evidence,
synthesis accepted only on verified citations). Without a hub these features stay BLOCKED.

**Search provider:** copy the `providers:` block from
`documentation/stage11/integration/research.providers.example.yaml` into `configuration/research.yaml`, then
`python -m backend.research.provider_check`. Live check (after the Stage 02 operator steps): create and approve a
QUICK_SEARCH through `ResearchService` and record the result; live checks always go through the controller.

**Stage 04 — API wiring:** one call after the app is created:
```python
from backend.research.wiring import wire_research_api
wire_research_api(app)      # mounts /v1/research*, service resolved lazily from app.state.controller_service
```
Routes stay policy-DENIED until Stage 02 grants the `research.*` operations.

Full details: `STAGE_11_MERGE_MANIFEST.md`, `STAGE_11_VERIFICATION.md`, `documentation/stage11/`.
