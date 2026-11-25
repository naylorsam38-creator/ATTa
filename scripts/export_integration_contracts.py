"""Export the Stage 12 integration API contracts (JSON Schema) for Stage 04 / packages/shared_types.

    .venv/bin/python scripts/export_integration_contracts.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.integrations import service as s  # noqa: E402

MODELS = [s.IntegrationSummary, s.IntegrationDetail, s.ToolInfo, s.InstallRequest, s.ValidateRequest,
          s.AuthorizedRequest, s.DisableRequest, s.OperatorAuthorizationIn]
out = {m.__name__: m.model_json_schema() for m in MODELS}
out["_note"] = {"stage": "S12 sidecar", "routes": "backend/integrations/api.py", "default": "DENY until Stage 02"}
p = ROOT / "packages" / "shared_types" / "integrations.contracts.generated.json"
p.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
print(p)
