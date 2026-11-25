"""Stage 12 — compatibility adapters for Worker A's Stage 01B, Worker 3's Stage 11 and Worker 2's Stage 04.
Uses the other stages' REAL modules when they are present in the tree (trial trees); otherwise the duck-typed
interface they publish, clearly labelled."""
from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from stage12_harness import ROOT, build12, record, stage01b_runtime_present

from backend.integrations.adapters import ConnectivitySources, RuntimeIsolation
from backend.integrations.types import ConnectivityMode as CM

pytestmark = pytest.mark.requires_services


def test_S12_T70_connectivity_most_restrictive_signal_wins_live():
    env = {}
    src = ConnectivitySources(CM.ONLINE, settings=None, provider=None, environ=env)
    pol = src.policy([])
    assert pol.current() is CM.ONLINE and pol.host_allowed("api.example.com", ("api.example.com",))[0]
    env["ATTA_NETWORK_MODE"] = "OFFLINE"                 # Stage 11's variable, read live
    assert pol.current() is CM.OFFLINE and not pol.host_allowed("api.example.com", ("api.example.com",))[0]
    assert pol.host_allowed("127.0.0.1", ("127.0.0.1",))[0], "loopback stays available offline"
    env["ATTA_NETWORK_MODE"] = "bogus"
    assert pol.current() is CM.AIR_GAPPED, "unknown values fail closed"
    env.clear()
    if importlib.util.find_spec("backend.research") is not None:    # Worker 3's real provider
        from backend.research.contracts import NetworkMode
        from backend.research.network import StaticNetworkMode
        prov, which = StaticNetworkMode(NetworkMode.AIR_GAPPED), "stage11-real"
    else:
        prov, which = SimpleNamespace(current_mode=lambda: "AIR_GAPPED"), "stage11-interface (module not in tree)"
    assert ConnectivitySources(CM.ONLINE, provider=prov, environ=env).current_mode() is CM.AIR_GAPPED
    from backend.settings.schema import Settings
    if "deployment" in Settings.model_fields:                      # Worker A's real settings model
        from backend.settings import load_settings
        s = load_settings("test", env={"ATTA_PROFILE": "air_gapped", "ATTA_DATABASE_PASSWORD": "x",
                                       "ATTA_DATABASE_MIGRATION_PASSWORD": "x", "ATTA_S3_ACCESS_KEY_ID": "x",
                                       "ATTA_S3_SECRET_ACCESS_KEY": "x"}, include_local_env=False)
        s01b = "stage01b-real"
    else:
        s, s01b = SimpleNamespace(deployment=SimpleNamespace(profile="AIR_GAPPED")), "stage01b-interface"
    assert ConnectivitySources(CM.ONLINE, settings=s, environ=env).current_mode() is CM.AIR_GAPPED
    record("S12-T70", {"stage11": which, "stage01b": s01b})


def test_S12_T71_isolation_adapter_reports_the_real_runtime_honestly():
    from backend.capability_runtime import CapabilityRuntime
    rep = RuntimeIsolation(CapabilityRuntime()).report()
    assert rep.available is False
    if stage01b_runtime_present():
        on = RuntimeIsolation(CapabilityRuntime(network="none", isolation_required=True)).report()
        assert on.available and any("network namespace" in g for g in on.guarantees)
        assert "not required" in rep.reason
        mode = "stage01b-real"
    else:
        assert "not present" in rep.reason
        mode = "stage01a-baseline: isolation honestly UNAVAILABLE"
    record("S12-T71", {"mode": mode, "default_report": rep.__dict__})


def test_S12_T72_api_mount_is_idempotent_for_stage04(tmp_path):
    from fastapi import FastAPI
    from backend.integrations.api import mount_integrations
    app = FastAPI()
    assert mount_integrations(app, lambda op: (lambda: None)) is True
    assert mount_integrations(app, lambda op: (lambda: None)) is False, "Stage 04 can mount again safely"
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert len(paths) == len(set(paths))
    stage04 = (ROOT / "backend" / "api" / "routes_conversations.py").exists() or \
        (ROOT / "apps" / "web" / "app" / "integrations").exists()
    record("S12-T72", {"routes": len(paths), "stage04_present": stage04,
                       "note": "Stage 04 code is not published; contracts: packages/shared_types/"
                               "integrations.contracts.generated.json"})


def test_S12_T73_attach_uses_the_controllers_own_runtime_for_isolation(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    assert isinstance(H.mgr.isolation, RuntimeIsolation) and H.mgr.isolation.runtime is H.h.controller.runtime
    assert H.mgr.registry is H.h.controller.registry and H.mgr.catalogue is H.h.controller.catalogue
    assert H.h.controller.gateway.guards and H.h.controller.gateway.result_filters
    record("S12-T73", {"one_registry": True, "one_catalogue": True, "one_gateway": True})
