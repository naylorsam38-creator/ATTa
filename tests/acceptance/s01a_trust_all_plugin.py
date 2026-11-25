"""TEST-ONLY pytest plugin used by S01A-T48 (never loaded by the application).

It satisfies the new trust control for re-running superseded Stage 01 tests: every package is treated as trusted
and the Stage 01 fixture permission 'test.echo' is added to the catalog. If a superseded test then passes — or
fails only at the removed direct-execution route `reg.execute(` — it is proven that it failed solely because of
the stricter Stage 01A controls (trusted installation, gateway-only execution), not because of a regression."""
import backend.capability_registry.registry as registry_mod
import backend.policy_engine.catalog as catalog_mod
import backend.policy_engine.engine as engine_mod
from backend.capability_registry.trust import TrustStore


class _TrustAll(TrustStore):
    def __init__(self):
        super().__init__({}, [], "<trust-all test plugin>")

    def check(self, manifest, digest):
        return None, []


registry_mod._default_trust = lambda: _TrustAll()
_orig = catalog_mod.load_catalog


def _with_fixture_permission(path):
    c = _orig(path)
    return catalog_mod.Catalog({**c.permissions, "test.echo": "Stage 01 fixture permission"}, c.controller_granted,
                               c.approval_required_risks)


catalog_mod.load_catalog = _with_fixture_permission
engine_mod.load_catalog = _with_fixture_permission
