import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseManifestYaml, validateManifest } from "../src/index";

const installed = readFileSync(new URL("../../../capabilities/ops_system_status/capability.yaml", import.meta.url), "utf8");

describe("capability manifest validation (shared JSON Schema)", () => {
  it("accepts the installed ops.system_status manifest", () => {
    expect(validateManifest(parseManifestYaml(installed))).toEqual({ valid: true, errors: [] });
  });

  it("rejects a manifest missing required fields and with a bad id", () => {
    const m = parseManifestYaml(installed) as Record<string, unknown>;
    delete m.handlers;
    m.id = "NoDots";
    const r = validateManifest(m);
    expect(r.valid).toBe(false);
    expect(r.errors.join("\n")).toMatch(/handlers/);
    expect(r.errors.join("\n")).toMatch(/\/id/);
  });

  it("rejects an invalid risk class", () => {
    const m = { ...(parseManifestYaml(installed) as object), risk: "none" };
    expect(validateManifest(m).valid).toBe(false);
  });
});
