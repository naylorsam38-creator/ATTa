# Physical qualification — Windows and H100 (outstanding)

Everything below is **implemented** in this package. It has not been executed on the named hardware because none was
available to this build; the verifier therefore records these tests **BLOCKED (not executed)**, never PASS. Run the
commands on the real machine; the results land in the build-controller ledger / qualification results with evidence
hashes and replace the BLOCKED entries.

## 1. Windows 10/11 x64

### 1.1 Outstanding offline dependencies (not obtainable from the Linux build machine)

`bundle create --target windows` (built here; see the separately supplied `atta-offline-windows` bundle) contains the
Windows wheels for both locks (49 core / 107 full entries, all present), the Windows CPython 3.13 runtime, the npm caches
resolved for win32-x64 (95 + 466 entries) and **PostgreSQL 16 x64** (`@embedded-postgres/windows-x64@16.15.0-beta.19`,
npm-integrity verified). Its `bundle_manifest.json → coverage.system_packages.components` lists these as **BLOCKED**:

| Component | Why it is missing | What to supply (placed under `DIR/<name>/`) |
|---|---|---|
| `pgvector` | pgvector publishes no Windows binaries | pgvector ≥ 0.6 built for PostgreSQL 16 x64: `vector.dll`, `vector.control`, `vector--*.sql` (build: `nmake /F Makefile.win` in an x64 Native Tools prompt with `PGROOT` set) — or run PostgreSQL + pgvector in WSL/Docker Desktop |
| `redis` | no Windows Redis build is reachable from the build machine (memurai.com blocked) | Memurai Developer installer, or a Redis 7 for Windows ZIP/MSI — or Redis in WSL/Docker Desktop (`ATTA__SERVICES__REDIS__MODE=external`) |
| `git` | Git for Windows ships from github.com release assets (blocked here) | Git for Windows 64-bit installer (only needed for build-controller gates and updates) |
| browsers | Playwright CDN blocked here | on a connected Windows machine: `py -m playwright install chromium`, then copy `%LOCALAPPDATA%\ms-playwright\chromium*` into the bundle's `browsers\` |

Rebuild the Windows bundle with them (on any machine with network access):

```
python -m backend.deployment bundle create --target windows --windows-installers D:\atta-win-installers --out D:\atta-offline-windows
python -m backend.deployment bundle verify --bundle D:\atta-offline-windows
python -m backend.deployment bundle coverage --bundle D:\atta-offline-windows
```

### 1.2 Install and qualify on Windows (PowerShell, **Run as Administrator**)

```
INSTALL.bat --offline-bundle D:\atta-offline-windows --install-system --profile LOCAL_CPU          # writes the plan
INSTALL.bat --offline-bundle D:\atta-offline-windows --install-system --profile LOCAL_CPU --approve
#   applies: worker interpreter runtime\worker-python\atta-worker.exe + Windows Firewall rules (admin),
#            secret labels (No-Read-Up/No-Write-Up), qualification, activation
HEALTHCHECK.bat
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\unit\test_windows_isolation.py
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\acceptance\test_s01b_acceptance.py -k T41
.venv\Scripts\python -m backend.deployment qualify --suite LOCAL
.venv\Scripts\python -I -P backend\verification\verify.py verify-stage S01B      # verifier records T41 PASS/FAIL
netsh advfirewall firewall show rule name="ATTa worker: block outbound non-loopback" verbose
```

S01B-T41 certifies: Windows struct layout; Job Object tree kill (detached descendant terminated); restricted token
(only SeChangeNotifyPrivilege left, LOW integrity); a LOW worker **cannot read** nor write a labelled secret while a
medium process can; the worker can start **no process** (`cmd.exe`, renamed interpreter copy denied); workspace ACL
(no inherited entries, LOW label); firewall rules present and bound to `atta-worker.exe`; from the firewall-bound worker
an outbound connection, `cmd.exe` and a renamed interpreter are all denied. AIR_GAPPED qualification stays BLOCKED on
Windows (no OS network namespaces) — the firewall restricts workers, not the whole host.

## 2. NVIDIA H100 80 GB (Linux)

```
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv           # expect an H100 with >= 79 GB
./install.sh --with-ai --profile H100                                          # plan
./install.sh --with-ai --profile H100 --approve                                # INSTALL + H100 suites before activation
.venv/bin/python -m backend.deployment qualify --suite H100 --profile H100
.venv/bin/python -m pytest -q -p no:cacheprovider tests/acceptance/test_s01b_acceptance.py -k "T43 or T44"
ATTA_BUILD_SIGNING_KEY_FILE=/etc/atta-verifier/build_signing_ed25519.pem \
  .venv/bin/python -I -P backend/verification/verify.py verify-stage S01B       # T43 executed instead of BLOCKED
ATTA_BUILD_SIGNING_KEY_FILE=/etc/atta-verifier/build_signing_ed25519.pem \
  .venv/bin/python -I -P backend/verification/verify.py verify-stage S01F       # S01F-T05 executed
```

Certified on the H100: identity/count/VRAM/driver/CUDA (`Q-H100-IDENTITY`), the GPU visible to the model worker only and
to no tool worker (`Q-H100-WORKER-GPU`), vLLM importable with CUDA (`Q-H100-VLLM`). `Q-H100-INFERENCE` (load, real
inference, streaming, cancellation, unload/reload, swap cycles, offline inference) stays BLOCKED until Stage 03
implements inference, then becomes mandatory.

## 3. Verifier independence on the qualifying machine

Run `scripts/protect_verifier.sh` as root first with an operator-chosen `ATTA_BUILD_SIGNING_KEY_PASSPHRASE` (the
build signing key is regenerated or re-encrypted under the operator's own secret — BLOCKERS B-009). Gate tests then run
as the unprivileged `atta-tester` account (only tests marked `privileged` — network namespaces, cgroups, user switching —
run as the verifier), and the ledger records the identity of every test execution.
