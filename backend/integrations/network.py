"""Connectivity / network policy for integrations (Stage 12 §16, §33). Stage 01B will provide the deployment
connectivity contract; until then the mode comes from configuration/integrations.yaml (or ATTA_INTEGRATIONS__CONNECTIVITY).

AIR_GAPPED: loopback only — network integrations report OFFLINE_UNAVAILABLE, local ones keep working; there is no
hidden download, telemetry, update check or fallback to another provider. RESTRICTED: only operator-allowed hosts."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Callable

from .types import ConnectivityMode

LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class NetworkPolicy:
    """`mode` is the configured fallback; `provider` (optional) returns the LIVE mode on every check, so a change of
    deployment profile / qualified offline state takes effect without restarting integrations."""
    mode: ConnectivityMode
    allowed_hosts: frozenset[str] = frozenset()
    provider: Callable[[], ConnectivityMode] | None = None

    def current(self) -> ConnectivityMode:
        if self.provider is None:
            return self.mode
        try:
            return self.provider()
        except Exception:  # noqa: BLE001 — an unreadable connectivity source fails closed
            return ConnectivityMode.AIR_GAPPED

    def host_allowed(self, host: str, declared: tuple[str, ...]) -> tuple[bool, str]:
        if host not in declared:
            return False, f"host '{host}' is not declared in the integration's network_requirements"
        if is_loopback(host):
            return True, "loopback"
        mode = self.current()
        if mode in (ConnectivityMode.AIR_GAPPED, ConnectivityMode.OFFLINE):
            return False, f"OFFLINE_UNAVAILABLE: deployment is {mode.value}"
        if mode is ConnectivityMode.RESTRICTED and host not in self.allowed_hosts:
            return False, f"host '{host}' is not in the operator's allowed_network_hosts (RESTRICTED)"
        return True, "allowed"

    def integration_offline(self, declared_hosts: tuple[str, ...]) -> list[str]:
        """Reasons this integration cannot reach what it needs right now (empty = reachable as far as policy goes)."""
        out = []
        for h in declared_hosts:
            ok, why = self.host_allowed(h, declared_hosts)
            if not ok:
                out.append(f"{h}: {why}")
        return out
