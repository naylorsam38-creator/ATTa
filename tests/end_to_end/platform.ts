import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export const ROOT = resolve(__dirname, "../..");
export const PY = existsSync(resolve(ROOT, ".venv/bin/python")) ? resolve(ROOT, ".venv/bin/python")
  : resolve(ROOT, ".venv/Scripts/python.exe");

export function manager(cmd: "start" | "stop"): string {
  return execFileSync(PY, ["-m", "backend.service_manager", cmd, "--env", "test"], {
    cwd: ROOT, env: { ...process.env, ATTA_ENV: "test" }, encoding: "utf8", timeout: 120_000,
  });
}
