#!/usr/bin/env python3
"""Export backend contract enumerations + JSON Schemas to packages/shared_types/contracts.generated.json so the
TypeScript packages are checked against the single Python definition (vitest + tests/unit)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.contracts import (AvailabilityState, ErrorCode, EventType, HealthStatus, RiskClass, TaskState)  # noqa
from backend.contracts.capability import CapabilityAvailability  # noqa
from backend.contracts.errors import ErrorEnvelope  # noqa
from backend.contracts.health import HealthReport, ReadinessReport  # noqa
from backend.contracts.tasks import ALLOWED_TRANSITIONS  # noqa
from backend.orchestration.states import TRANSITIONS as CONTROLLER_TRANSITIONS  # noqa

OUT = ROOT / "packages" / "shared_types" / "contracts.generated.json"


def build() -> dict:
    enums = {e.__name__: [m.value for m in e] for e in (ErrorCode, TaskState, AvailabilityState, RiskClass,
                                                         HealthStatus, EventType)}
    return {
        "generated_by": "scripts/export_contracts.py",
        "api_version": "v1",
        "enums": enums,
        "task_transitions": {k.value: sorted(v.value for v in vs) for k, vs in ALLOWED_TRANSITIONS.items()},
        "controller_task_transitions": {k: sorted(v) for k, v in CONTROLLER_TRANSITIONS.items()},
        "schemas": {m.__name__: m.model_json_schema() for m in (ErrorEnvelope, HealthReport, ReadinessReport,
                                                                 CapabilityAvailability)},
    }


def main() -> int:
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        ok = OUT.is_file() and OUT.read_text() == text
        print("contracts in sync" if ok else "contracts.generated.json is stale: run scripts/export_contracts.py")
        return 0 if ok else 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
