"""Current service health (correction 3): background probing plus on-demand refresh with a maximum age.

Replaces startup-only snapshots. Consumers (capability registry, readiness, the execution gateway) ask
is_available(name); a cached result older than max_age_s is re-probed synchronously. When any service changes
state the on_change callbacks run, so dependent capabilities are disabled or restored automatically."""
from __future__ import annotations

import threading
import time
from typing import Callable

from backend.contracts import DependencyHealth, HealthStatus, get_logger
from backend.settings import Settings

from .probes import probe_service

log = get_logger("atta.health")


class HealthMonitor:
    def __init__(self, settings: Settings, *, interval_s: float = 5.0, max_age_s: float = 5.0,
                 names: list[str] | None = None, probe: Callable[[str], DependencyHealth] | None = None,
                 recovery_confirmations: int = 2):
        self.settings = settings
        self.interval_s, self.max_age_s = interval_s, max_age_s
        self.names = names if names is not None else [n for n, s in settings.services.items()
                                                      if s.mode != "disabled" and n not in ("api", "controller")]
        self._probe = probe or (lambda n: probe_service(n, settings.services[n].essential,
                                                       settings.services[n].health, settings))
        self._state: dict[str, tuple[DependencyHealth, float]] = {}
        # Revalidation (Stage 01B §19): after a failure a service counts as available only after this many
        # consecutive healthy probes; until then its state is DEGRADED.
        self.recovery_confirmations = recovery_confirmations
        self._streak: dict[str, int] = {}
        self._failed_before: set[str] = set()
        self._callbacks: list[Callable[[set[str]], None]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def on_change(self, cb: Callable[[set[str]], None]) -> None:
        self._callbacks.append(cb)

    def refresh(self, names: list[str] | None = None) -> dict[str, DependencyHealth]:
        changed: set[str] = set()
        out = {}
        for n in names or self.names:
            if n not in self.settings.services:
                continue
            h = self._probe(n)
            with self._lock:
                before = self._available_locked(n)
                prev = self._state.get(n)
                self._state[n] = (h, time.monotonic())
                if h.status is HealthStatus.HEALTHY:
                    self._streak[n] = self._streak.get(n, 0) + 1
                else:
                    self._streak[n] = 0
                    self._failed_before.add(n)
                after = self._available_locked(n)
            if prev is None or before != after:
                changed.add(n)
                if prev is not None:
                    log.warning("service %s availability %s -> %s (%s)", n, before, after, self.state(n))
            out[n] = h
        if changed:
            for cb in self._callbacks:
                try:
                    cb(changed)
                except Exception:  # noqa: BLE001
                    log.exception("health change callback failed")
        return out

    def get(self, name: str, max_age_s: float | None = None) -> DependencyHealth | None:
        age = self.max_age_s if max_age_s is None else max_age_s
        with self._lock:
            cur = self._state.get(name)
        if cur is None or time.monotonic() - cur[1] > age:
            return self.refresh([name]).get(name)
        return cur[0]

    def _available_locked(self, name: str) -> bool:
        cur = self._state.get(name)
        if cur is None or cur[0].status is not HealthStatus.HEALTHY:
            return False
        return name not in self._failed_before or self._streak.get(name, 0) >= self.recovery_confirmations

    def is_available(self, name: str, max_age_s: float | None = None) -> bool:
        self.get(name, max_age_s)
        with self._lock:
            return self._available_locked(name)

    def state(self, name: str) -> str:
        """STARTING | HEALTHY | DEGRADED (recovering, awaiting revalidation) | UNHEALTHY."""
        with self._lock:
            cur = self._state.get(name)
            if cur is None:
                return "STARTING"
            if cur[0].status is not HealthStatus.HEALTHY:
                return "UNHEALTHY"
            return "HEALTHY" if self._available_locked(name) else "DEGRADED"

    def snapshot(self) -> list[DependencyHealth]:
        with self._lock:
            return [h for h, _ in self._state.values()]

    def start(self) -> None:
        if self._thread:
            return
        self.refresh()

        def loop():
            while not self._stop.wait(self.interval_s):
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001
                    log.exception("health refresh failed")
        self._thread = threading.Thread(target=loop, daemon=True, name="health-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
