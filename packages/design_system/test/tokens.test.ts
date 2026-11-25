import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { color, contrastRatio } from "../src/index";

describe("design tokens", () => {
  it.each(["light", "dark"] as const)("%s theme text meets WCAG AA contrast", (theme) => {
    expect(contrastRatio(color[theme].text, color[theme].bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(color[theme].text, color[theme].surface)).toBeGreaterThanOrEqual(4.5);
  });

  it("CSS variables match the TypeScript tokens", () => {
    const css = readFileSync(new URL("../src/tokens.css", import.meta.url), "utf8");
    for (const [k, v] of Object.entries(color.light)) expect(css).toContain(`--atta-${k}: ${v}`);
    for (const [k, v] of Object.entries(color.dark)) expect(css).toContain(`--atta-${k}: ${v}`);
  });
});
