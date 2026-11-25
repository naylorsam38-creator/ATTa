"""Child process for S01A-T40: runs one gateway execution of the 'external' fixture tool and is SIGKILLed by the
test while the handler is mid-flight (after the external effect, before it returns). Arguments: a JSON file
written by the test describing the harness location and the task."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s01a_harness import Harness, build  # noqa: E402
from backend.orchestration.contracts import ToolProposal  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text())
fake = Harness(root=Path(spec["root"]), caps=Path(spec["root"]) / "capabilities", controller=None, registry=None,
               store=None, catalogue=None, operator_key=Path(spec["root"]) / "keys" / "operator.pem",
               namespace=spec["namespace"], numbers_file=Path(spec["root"]) / "tool_numbers.yaml", settings=None)
h = build(Path(spec["root"]), reuse=fake, controller_id="crash-worker")
c = h.store.contract(spec["objective_id"], spec["task_id"])
task = h.store.task(spec["objective_id"], spec["task_id"])
ctx = h.controller.gateway.issue_context(spec["objective_id"], spec["task_id"], task["attempt_id"], c.contract_hash,
                                         c.assigned_worker)
print("EXECUTING", flush=True)
h.controller.gateway.execute(ctx, ToolProposal.model_validate(spec["proposal"]))
print("FINISHED", flush=True)
