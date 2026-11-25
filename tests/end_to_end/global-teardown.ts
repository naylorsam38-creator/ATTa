import { manager } from "./platform";

export default async function globalTeardown(): Promise<void> {
  manager("stop");
}
