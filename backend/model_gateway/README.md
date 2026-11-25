# backend/model_gateway

Single entry point for model access. Stage 01: endpoint inventory + real health probes (`endpoint_health`).
Stage 03 adds LiteLLM routing (locked in `uv.lock`, extra `ai`). Model files live wherever
`models.storage_paths` / `ATTA_MODEL_PATHS` points (D:\models, external USB drives, Linux mounts) — never
required on the Windows system drive, and never downloaded without `models.allow_model_downloads: true`.
