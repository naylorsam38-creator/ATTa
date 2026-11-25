# Installed capabilities

Every directory here that contains a `capability.yaml` is discovered automatically by the central capability
registry (`backend/capability_registry`) at service-manager start and API start. Nothing else needs editing —
START.bat / start.sh never change when a capability is added.

Manifest contract: `backend/capability_registry/manifest.schema.json` (documented in
`documentation/CAPABILITY_MANIFEST.md`). Create a new one with:

    python scripts/new_capability.py my_area.my_capability --requirement CAP-40

Invalid, duplicate, unhealthy or dependency-missing capabilities are reported and are never executable.
Execution always passes the central policy engine; at Stage 01 every execution is DENIED.
