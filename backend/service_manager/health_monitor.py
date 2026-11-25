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
                 names: list[str] | None = None, probe: Callable[[str], DependencyHealth] | None = None):
        self.settings = settings
        self.interval_s, self.max_age_s = interval_s, max_age_s
        self.names = names if names is not None else [n for n, s in settings.services.items()
                                                      if s.mode != "disabled" and n not in ("api", "controller")]
        self._probe = probe or (lambda n: probe_service(n, settings.services[n].essential,
                                                       settings.services[n].health, settings))
        self._state: dict[str, tuple[DependencyHealth, float]] = {}
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
                prev = self._state.get(n)
                self._state[n] = (h, time.monotonic())
            if prev is None or prev[0].status != h.status:
                changed.add(n)
                if prev is not None:
                    log.warning("service %s %s -> %s", n, prev[0].status.value, h.status.value)
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

    def is_available(self, name: str, max_age_s: float | None = None) -> bool:
        h = self.get(name, max_age_s)
        return h is not None and h.status is HealthStatus.HEALTHY

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
