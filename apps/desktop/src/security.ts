/** Navigation policy for the desktop shell: only the configured ATTa web origin may load inside the window. */
export function allowedOrigin(env: Record<string, string | undefined>): string {
  const raw = env.ATTA_WEB_URL ?? "http://127.0.0.1:3000";
  const url = new URL(raw);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error(`ATTA_WEB_URL must be http(s): ${raw}`);
  if (url.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
    throw new Error("plain http is only allowed for a loopback web UI; use https for remote hosts");
  }
  return url.origin;
}

export function isAllowedNavigation(target: string, origin: string): boolean {
  try {
    return new URL(target).origin === origin;
  } catch {
    return false;
  }
}
