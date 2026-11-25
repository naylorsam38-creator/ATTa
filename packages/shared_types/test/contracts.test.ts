import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { AVAILABILITY_STATES, CONTROLLER_TASK_STATES, ERROR_CODES, HEALTH_STATUSES, RISK_CLASSES, TASK_STATES } from "../src/index";

const generated = JSON.parse(readFileSync(new URL("../contracts.generated.json", import.meta.url), "utf8"));

describe("shared_types mirror backend/contracts exactly", () => {
  it.each([
    ["ErrorCode", ERROR_CODES], ["TaskState", TASK_STATES], ["AvailabilityState", AVAILABILITY_STATES],
    ["RiskClass", RISK_CLASSES], ["HealthStatus", HEALTH_STATUSES],
  ])("%s", (name, values) => {
    expect([...values].sort()).toEqual([...generated.enums[name as string]].sort());
  });

  it("controller task states match backend/orchestration/states.py", () => {
    expect([...CONTROLLER_TASK_STATES].sort()).toEqual(Object.keys(generated.controller_task_transitions).sort());
  });

  it("ReadinessReport fields match the backend schema", () => {
    const props = Object.keys(generated.schemas.ReadinessReport.properties).sort();
    expect(props).toEqual(["capabilities", "checked_at", "dependencies", "environment", "ready", "reasons", "status"]);
  });
});
