# backend/orchestration

AI orchestration (LangGraph graphs) and durable workflows (Temporal). Status: **BLOCKED until Stage 03** —
by design. Dependencies are already selected and locked (`langgraph`, `temporalio`, `litellm` in `uv.lock`,
extra `ai`). There is exactly one intelligence engine: it will live here and reach models only through
`backend/model_gateway` and capabilities only through `backend/capability_registry` (policy-gated).
