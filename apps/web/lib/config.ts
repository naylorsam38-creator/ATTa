/** Server-side configuration for the web client. The API base URL must be http(s); loopback by default. */
export function apiBaseUrl(env: Record<string, string | undefined> = process.env): string {
  const raw = env.ATTA_API_URL ?? "http://127.0.0.1:8800";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`ATTA_API_URL is not a valid URL: ${raw}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`ATTA_API_URL must use http or https, got ${url.protocol}`);
  }
  if (url.username || url.password) throw new Error("ATTA_API_URL must not embed credentials");
  return url.origin + url.pathname.replace(/\/+$/, "");
}
