"""Service manager ordering and validation without starting processes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.service_manager import ServiceManager  # noqa: E402
from backend.settings import ConfigurationError, load_settings  # noqa: E402


def test_dependencies_start_before_dependents():
    order = ServiceManager(load_settings("test")).startup_order()
    assert order.index("postgres") < order.index("api") and order.index("redis") < order.index("api")


def test_unknown_service_dependency_rejected():
    with pytest.raises(ConfigurationError, match="unknown service"):
        load_settings("test", env={"ATTA__SERVICES__API__DEPENDS_ON": "[postgres, nosuch]"})


def test_dependency_cycle_rejected():
    s = load_settings("test", env={"ATTA__SERVICES__REDIS__DEPENDS_ON": "[api]"})
    problems = ServiceManager(s).validate_dependencies()
    assert any("cycle" in p for p in problems)
