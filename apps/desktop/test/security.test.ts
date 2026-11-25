import { describe, expect, it } from "vitest";
import { allowedOrigin, isAllowedNavigation } from "../src/security";

describe("desktop navigation policy", () => {
  it("defaults to the loopback web UI", () => expect(allowedOrigin({})).toBe("http://127.0.0.1:3000"));
  it("refuses plain http to remote hosts and non-http schemes", () => {
    expect(() => allowedOrigin({ ATTA_WEB_URL: "http://example.com" })).toThrow(/https/);
    expect(() => allowedOrigin({ ATTA_WEB_URL: "file:///tmp/x.html" })).toThrow(/http/);
  });
  it("allows only same-origin navigation", () => {
    const o = "http://127.0.0.1:3000";
    expect(isAllowedNavigation("http://127.0.0.1:3000/chat", o)).toBe(true);
    expect(isAllowedNavigation("https://evil.example/", o)).toBe(false);
    expect(isAllowedNavigation("javascript:alert(1)", o)).toBe(false);
  });
});
