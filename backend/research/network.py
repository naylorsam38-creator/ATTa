"""Network-mode integration point for Stage 01B (adaptive deployment / offline qualification) — §9.

Stage 11 does not detect connectivity itself and adds no hidden Internet dependency to the offline core: it asks a
NetworkModeProvider. Until Stage 01B is merged, the mode comes from ATTA_NETWORK_MODE or configuration/research.yaml.
In OFFLINE and AIR_GAPPED modes every external search/fetch is refused with OFFLINE_UNAVAILABLE before any task is
planned; research over already-authorised local/project sources still runs."""
from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from .contracts import NetworkMode


@runtime_checkable
class NetworkModeProvider(Protocol):
    def current_mode(self) -> NetworkMode: ...


class StaticNetworkMode:
    def __init__(self, mode: NetworkMode):
        self.mode = mode

    def current_mode(self) -> NetworkMode:
        return self.mode


class ConfiguredNetworkMode:
    """Environment variable first, then configuration. Stage 01B replaces this with its qualified mode."""

    def __init__(self, configured: NetworkMode):
        self.configured = configured

    def current_mode(self) -> NetworkMode:
        env = os.environ.get("ATTA_NETWORK_MODE", "").strip().upper()
        if env:
            try:
                return NetworkMode(env)
            except ValueError:
                return NetworkMode.AIR_GAPPED   # an unrecognised value fails closed
        return self.configured


def external_allowed(mode: NetworkMode) -> bool:
    return mode is NetworkMode.ONLINE
