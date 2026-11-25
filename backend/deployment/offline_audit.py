"""Zero-hidden-Internet audit (Stage 01B §13): finds core dependencies on external CDNs, remote fonts/scripts/CSS,
telemetry, external inference, remote registries or update checks. Features that inherently need the Internet are
reported OFFLINE_UNAVAILABLE instead of breaking the rest."""
from __future__ import annotations

import re
from pathlib import Path

from backend.settings import Settings
from backend.settings.loader import REPO_ROOT

URL = re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//(?!localhost|127\.0\.0\.1)[^"']+|@import\s+url\(\s*["']?https?://|url\(\s*["']?https?://(?!localhost|127\.0\.0\.1)|fonts\.googleapis\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com""", re.I)
FRONTEND = ["apps/web/app", "apps/web/lib", "packages/design_system/src", "packages/api_client/src", "apps/desktop/src"]


def audit(settings: Settings) -> dict:
    findings, offline_unavailable = [], []
    for d in FRONTEND:
        for p in (REPO_ROOT / d).rglob("*"):
            if p.suffix in (".ts", ".tsx", ".css", ".html", ".js") and p.is_file():
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if URL.search(line):
                        findings.append(f"{p.relative_to(REPO_ROOT)}:{i}: external asset reference")
    nc = (REPO_ROOT / "apps/web/next.config.ts").read_text()
    if "telemetry" in nc.lower() and "false" not in nc.lower():
        findings.append("next.config enables telemetry")
    for launcher in ("run.sh", "RUN.bat", "start.sh", "START.bat"):
        p = REPO_ROOT / launcher
        if p.is_file() and launcher.lower().startswith("run") and "NEXT_TELEMETRY_DISABLED" not in p.read_text():
            findings.append(f"{launcher} does not disable Next.js telemetry")
    if settings.models.allow_model_downloads:
        findings.append("models.allow_model_downloads is true (silent downloads possible)")
    if settings.deployment.network_probe:
        offline_unavailable.append(f"external network probe {settings.deployment.network_probe} (skipped offline)")
    for e in settings.models.endpoints:
        if e.enabled and e.kind in ("anthropic", "openai"):
            offline_unavailable.append(f"model endpoint {e.name} ({e.kind}): OFFLINE_UNAVAILABLE, never a fallback")
    ep = settings.object_storage.endpoint_url or ""
    if ep and not re.match(r"https?://(127\.0\.0\.1|localhost)", ep):
        offline_unavailable.append(f"object storage {ep}: OFFLINE_UNAVAILABLE")
    if any(s.mode == "external" and s.health.type == "http" and s.health.url and
           not re.search(r"//(127\.0\.0\.1|localhost|\{api)", s.health.url) for s in settings.services.values()):
        findings.append("an external HTTP service is configured as a dependency")
    return {"core_offline_ok": not findings, "findings": findings, "offline_unavailable": offline_unavailable}
