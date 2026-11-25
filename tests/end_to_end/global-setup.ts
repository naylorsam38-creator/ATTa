import { manager } from "./platform";

export default async function globalSetup(): Promise<void> {
  const out = manager("start");
  if (!/READY/.test(out)) throw new Error(`platform did not become ready:\n${out}`);
}
