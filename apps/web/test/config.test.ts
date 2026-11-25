import { describe, expect, it } from "vitest";
import { apiBaseUrl } from "../lib/config";

describe("web apiBaseUrl", () => {
  it("defaults to the loopback API", () => expect(apiBaseUrl({})).toBe("http://127.0.0.1:8800"));
  it("strips trailing slashes", () => expect(apiBaseUrl({ ATTA_API_URL: "https://api.example.test/" })).toBe("https://api.example.test"));
  it("rejects non-http schemes and embedded credentials", () => {
    expect(() => apiBaseUrl({ ATTA_API_URL: "file:///etc/passwd" })).toThrow(/http or https/);
    expect(() => apiBaseUrl({ ATTA_API_URL: "http://u:p@host" })).toThrow(/credentials/);
  });
});
