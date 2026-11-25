# Environment compatibility matrix (Stage 01B)

One source tree runs everywhere. The installer discovers the machine, selects a profile, resolves configuration,
qualifies the installation in isolated resources and only then activates it. This table states what has actually
been **tested**, not what is merely expected to work.

Legend: **VERIFIED** = executed on that platform with passing evidence · **IMPLEMENTED, UNTESTED** = code exists, not
run on that platform · **BLOCKED** = cannot be qualified without the named hardware/OS · **N/A** = not applicable.

## Platforms

| Platform | Discovery | Profile selection | Install / RUN / HEALTHCHECK | Isolated qualification | Air-gap (network denial) | Worker isolation | Status |
|---|---|---|---|---|---|---|---|
| Linux x86_64 (this build machine: 4 cores, 16 GB, no GPU, cgroup v1, root) | VERIFIED (S01B-T01) | VERIFIED: AUTO → LOCAL_CPU (T02) | VERIFIED: `install.sh` clean install → READY (T39), upgrade from the Stage 01A baseline (T40), `run.sh` refuses unactivated (T34) | VERIFIED: disposable PostgreSQL, own Redis/ports/runtime/bucket (T21–T24) | VERIFIED: kernel network namespace, loopback only (T25, T26, AIR_GAPPED suite) | VERIFIED: cgroups v1 memory+pids, empty netns, unprivileged account, GPU hidden (T42) | **Qualified for LOCAL_CPU** |
| Linux with NVIDIA GPU (LOCAL_NVIDIA) | IMPLEMENTED, UNTESTED on a GPU (selection logic tested with synthetic descriptions, T02/T03) | IMPLEMENTED | same code as above | same code | same code | GPU visibility via `CUDA_VISIBLE_DEVICES` (verified only as "hidden") | Not qualified — no GPU here |
| Linux + NVIDIA H100 80 GB (H100) | IMPLEMENTED (nvidia-smi query, CUDA, vLLM) | IMPLEMENTED; refused here with reasons (T04) | same code | same code | same code | GPU visible only to the intended model worker (`Q-H100-WORKER-GPU`) | **BLOCKED** — no H100; every H100 test recorded BLOCKED (T44) |
| Windows 10/11 x64 | IMPLEMENTED: WMI-free discovery via psutil, removable drives, `D:\` candidates | IMPLEMENTED | `INSTALL.bat` / `RUN.bat` / `HEALTHCHECK.bat` written, never executed | IMPLEMENTED (same runner; needs PostgreSQL binaries) | **BLOCKED**: no OS network namespace; AIR_GAPPED qualification reports BLOCKED, never PASS | IMPLEMENTED, UNTESTED on Windows: Job Object (tree kill, memory limit), restricted LOW-integrity token, workspace ACLs, firewall-bound worker interpreter (T41 BLOCKED here; logic unit-tested on Linux) | **Implemented; qualification BLOCKED** — no Windows host |
| Windows + WSL2 | discovery reports WSL | — | use `install.sh` inside WSL | as Linux (WSL kernel permitting) | as Linux if `unshare -n` works | as Linux | Not tested |
| macOS | partial (no NVIDIA, no namespaces) | LOCAL_CPU | `install.sh` should work | needs PostgreSQL binaries | **BLOCKED** (no network namespaces) | rlimits only | Not tested |

## Profiles

| Profile | Selected by AUTO when | Runtimes (order) | Tool workers | Notes |
|---|---|---|---|---|
| LOCAL_CPU | no usable NVIDIA GPU | llama.cpp, Ollama | 2 workers, 1024 MB, 1 concurrent tool/task | conservative; VERIFIED here |
| LOCAL_NVIDIA | NVIDIA GPU ≥ 4 GB | llama.cpp, Ollama, vLLM | 4 workers, 2048 MB, GPU hidden from tool workers | limits derived from VRAM |
| H100 | NVIDIA H100 ≥ 79 GB **and** CUDA **and** vLLM **and** Linux | vLLM, llama.cpp | 8 workers, 4096 MB, empty network namespace | never "healthy" without the real hardware |
| TEST_ISOLATED | never implicitly | llama.cpp, Ollama | 2 workers | used by every qualification run |
| AIR_GAPPED | never implicitly | local only | empty network namespace | requires network isolation or a genuinely offline host |

Explicit selection (`--profile X`, `ATTA_PROFILE`, `deployment.profile`) is validated against the discovered machine;
an incompatible profile stops the installer in BLOCKED with reasons. Profiles may only **lower** the Stage 01A
controller limits (`max_active_tasks_per_objective` ≤ 4, etc.).

## Model formats

| Format | Runtimes | GPU required | Discovery |
|---|---|---|---|
| GGUF | llama.cpp, Ollama | no | header parsed (name, architecture, context length); arrays skipped |
| safetensors directory (`config.json`) | vLLM | yes | size, architecture, context from `config.json` |
| Ollama manifests/blobs | Ollama | no | manifest layers |

No model is ever downloaded or copied; a missing model is `MODEL_NOT_FOUND`, an unusable one `MODEL_INCOMPATIBLE`, a
missing runtime `RUNTIME_UNAVAILABLE` (exit 11 = BLOCKED from `python -m backend.deployment models --require ID`).
There is **no external inference fallback** in any profile.

## Services

| Service | Mode | Tested here |
|---|---|---|
| PostgreSQL 16 + pgvector | external (local install or Docker) | VERIFIED (production cluster + disposable clusters) |
| Redis 7 | managed (spawned) | VERIFIED |
| Object storage (S3/MinIO) | optional | BLOCKED (B-007) — reported OFFLINE_UNAVAILABLE / unhealthy-optional |
| Temporal | optional (Stage 13) | BLOCKED (B-008) |
| Docker daemon | optional | BLOCKED (B-006): image digests cannot be resolved or bundled |
