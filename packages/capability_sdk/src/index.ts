/**
 * Capability SDK: the manifest contract for capability authors, validated against the SAME JSON Schema the backend
 * registry uses (backend/capability_registry/manifest.schema.json). Handler resolution and health checks happen in
 * the backend registry; this package gives authors and tooling early, identical schema feedback.
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import type { RiskClass } from "@atta/shared_types";

export interface CapabilityManifest {
  manifest_version: 1;
  id: string;
  version: string;
  description: string;
  requirement?: string;
  handlers: { execute: string; healthcheck: string; verify: string };
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  permissions: string[];
  risk: RiskClass;
  dependencies: { capabilities?: string[]; services?: string[]; python?: string[] };
  timeout_s: number;
  enabled: boolean;
}

const schemaUrl = new URL("../../../backend/capability_registry/manifest.schema.json", import.meta.url);
export const manifestSchema = JSON.parse(readFileSync(schemaUrl, "utf8"));

const ajv = new Ajv2020({ allErrors: true, strict: false });
const validateFn = ajv.compile(manifestSchema);

export interface ValidationResult { valid: boolean; errors: string[] }

export function validateManifest(manifest: unknown): ValidationResult {
  const valid = validateFn(manifest) as boolean;
  const errors = (validateFn.errors ?? []).map((e) => `${e.instancePath || "<root>"}: ${e.message}`);
  for (const key of ["input_schema", "output_schema"] as const) {
    const s = (manifest as Record<string, unknown> | null)?.[key];
    if (s && typeof s === "object") {
      try { ajv.compile(s as object); } catch (err) { errors.push(`${key} is not a valid JSON Schema: ${(err as Error).message}`); }
    }
  }
  return { valid: valid && errors.length === 0, errors };
}

export function parseManifestYaml(text: string): unknown {
  return parse(text);
}
