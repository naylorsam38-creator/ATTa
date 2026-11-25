# H100 deployment

> **Status: NOT QUALIFIED.** No H100 was available to this build. Every H100 test was recorded **BLOCKED (not
> executed)** — none was simulated or reported as PASS (S01B-T44, Stage 01 FINAL gate S01F-T05). The first run on a
> real H100 must execute the suite below before anyone may call the H100 profile qualified.

## Requirements checked by the profile

| Check | Source |
|---|---|
| NVIDIA H100 with ≥ 79 GB VRAM | `nvidia-smi --query-gpu=name,memory.total,driver_version` (UUIDs are not recorded) |
| CUDA reported by the driver | `nvidia-smi` header |
| vLLM importable | Python package discovery |
| Linux | discovery |

If any check fails, `--profile H100` stops in BLOCKED with the reasons; AUTO falls back to LOCAL_NVIDIA/LOCAL_CPU.

## Install on an H100 server

```
./install.sh --with-ai --profile H100            # review runtime/deployment/install_plan.json
./install.sh --with-ai --profile H100 --approve  # qualifies with INSTALL + H100 suites before activation
```

The profile (`configuration/profiles/h100.yaml`): vLLM first, 32k context, 8 tool workers × 4 GB, tool workers in an
empty network namespace, **GPU hidden from tool workers** (`CUDA_VISIBLE_DEVICES=""`); only the model runtime sees
the GPU. Stage 01A budgets remain authoritative; the profile cannot raise them. Model workers do not get host
privileges because the host has an H100.

## H100 qualification suite (`python -m backend.deployment qualify --suite H100 --profile H100`)

| Test | What it proves | Status now |
|---|---|---|
| Q-H100-IDENTITY | H100 identity, count, VRAM, driver, CUDA | BLOCKED (no H100) |
| Q-H100-WORKER-GPU | CUDA driver API sees the GPU with the model worker's `CUDA_VISIBLE_DEVICES` and sees none with the tool worker's | BLOCKED |
| Q-H100-VLLM | vLLM importable with CUDA | BLOCKED |
| Q-H100-INFERENCE | model load, real inference, streaming, cancellation, unload/reload, identity after reload, swap cycles, Hub routing, offline inference | BLOCKED until Stage 03 implements inference, then mandatory |

S01B-T43 executes this suite and must PASS on a real H100; the build controller records it BLOCKED elsewhere.
Results land in `runtime/deployment/qualification/<id>/results.json` with evidence hashes.
