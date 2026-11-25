/** Design tokens shared by web (CSS variables), desktop (same web UI) and mobile (React Native style values). */
export const color = {
  light: { bg: "#faf9f5", surface: "#ffffff", text: "#1f1e1d", muted: "#6b6a68", border: "#e5e2da",
           accent: "#c96442", ok: "#2e7d4f", warn: "#a86b00", danger: "#b3261e" },
  dark: { bg: "#1f1e1d", surface: "#2a2927", text: "#f5f4ef", muted: "#a8a69f", border: "#3d3b38",
          accent: "#d97757", ok: "#5fbf86", warn: "#e0a940", danger: "#f2766b" },
} as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
export const radius = { sm: 6, md: 10, lg: 16 } as const;
export const font = {
  family: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  size: { sm: 13, md: 15, lg: 18, xl: 24 },
} as const;

export type Theme = keyof typeof color;

export function statusColor(theme: Theme, status: "healthy" | "degraded" | "unhealthy" | "unknown"): string {
  const c = color[theme];
  return { healthy: c.ok, degraded: c.warn, unhealthy: c.danger, unknown: c.muted }[status];
}

/** Relative luminance contrast ratio (WCAG 2.x) — used by tests to keep text legible in both themes. */
export function contrastRatio(a: string, b: string): number {
  const lum = (hex: string) => {
    const n = parseInt(hex.slice(1), 16);
    const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * ch[0]! + 0.7152 * ch[1]! + 0.0722 * ch[2]!;
  };
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p) as [number, number];
  return (x + 0.05) / (y + 0.05);
}
