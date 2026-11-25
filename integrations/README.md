# Integrations (Stage 12)

`integrations/available/<package>/integration.yaml` — operator-supplied integration packages (connectors, MCP
servers, skills, plugins, custom providers). Discovery only READS manifests here; it never imports or runs code.
Installation copies an approved package into the managed install root (`runtime/integrations/`) and registers it
through the existing capability registry. See INTEGRATION_MANIFEST_SPEC.md and INTEGRATION_ARCHITECTURE.md.

This directory ships empty: test fixtures live only in `tests/integrations/fixtures/` and are never used as
production fallbacks.
