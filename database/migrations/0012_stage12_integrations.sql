-- Stage 12 (sidecar): connectors, MCP servers, skills, plugins and custom providers (schema `integrations`).
-- Merge-safe: own schema, own numbered file (0012), no change to any earlier migration or table.
-- Append-only tables (manifests, versions of skills/plugins, events, health samples, installation events, task pins)
-- reject UPDATE/DELETE with a trigger AND the runtime role is not granted UPDATE/DELETE on them.
-- Every table carries `namespace` ('default' = production; tests use their own, as control.tool_identifiers does).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atta_app') THEN
    RAISE EXCEPTION 'runtime role atta_app missing: run database/bootstrap.sql as a superuser';
  END IF;
END $$;

CREATE SCHEMA integrations;

CREATE FUNCTION integrations.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'integrations.% is append-only: % refused', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TABLE integrations.integrations (
    namespace          text NOT NULL DEFAULT 'default',
    integration_id     text NOT NULL CHECK (integration_id ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    integration_type   text NOT NULL CHECK (integration_type IN ('CONNECTOR','MCP_SERVER','SKILL','PLUGIN','CUSTOM_PROVIDER')),
    name               text NOT NULL,
    provider           text NOT NULL,
    current_version    text,
    state              text NOT NULL CHECK (state IN ('DISCOVERED','VALIDATING','AWAITING_APPROVAL','INSTALLING','REGISTERING',
                         'VERIFYING','INSTALLED','ENABLED','DISABLED','BLOCKED','FAILED','REMOVING','REMOVED')),
    trust_class        text NOT NULL DEFAULT 'UNTRUSTED' CHECK (trust_class IN ('CORE','TRUSTED','RESTRICTED','UNTRUSTED','DISABLED')),
    capability_id      text,
    approved_manifest_digest text,
    approved_package_digest  text,
    approval_binding   text,              -- hash of exactly what the operator approved
    disabled_by_admin  boolean NOT NULL DEFAULT false,
    problems           jsonb NOT NULL DEFAULT '[]'::jsonb,
    installed_at       timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, integration_id)
);

CREATE TABLE integrations.integration_manifests (
    namespace        text NOT NULL DEFAULT 'default',
    manifest_digest  text NOT NULL CHECK (length(manifest_digest) = 64),
    integration_id   text NOT NULL,
    version          text NOT NULL,
    manifest         jsonb NOT NULL,
    source           text NOT NULL,
    package_digest   text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, manifest_digest)
);

CREATE TABLE integrations.integration_versions (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text NOT NULL,
    manifest_digest  text NOT NULL,
    package_digest   text,
    trust_class      text NOT NULL,
    approval_binding text NOT NULL,
    approved_by      text NOT NULL,
    approved_at      timestamptz NOT NULL DEFAULT now(),
    upgrade_report   jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (namespace, integration_id, version, manifest_digest)
);

CREATE TABLE integrations.integration_capabilities (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text NOT NULL,
    logical_id       text NOT NULL,
    registry_capability_id text NOT NULL,
    description      text NOT NULL,
    PRIMARY KEY (namespace, integration_id, version, logical_id)
);

CREATE TABLE integrations.integration_tools (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text NOT NULL,
    tool_name        text NOT NULL,
    tool_string      text NOT NULL,
    tool_number      text NOT NULL CHECK (tool_number ~ '^(0[0-3][0-9]|040)\.[0-9]{3}$'),
    side_effect      text NOT NULL CHECK (side_effect IN ('READ_ONLY','REVERSIBLE_WRITE','EXTERNAL_COMMUNICATION','DESTRUCTIVE',
                       'FINANCIAL','ADMINISTRATIVE','SECURITY_SENSITIVE','OTHER_HIGH_RISK')),
    risk             text NOT NULL CHECK (risk IN ('low','medium','high','critical')),
    schema_hash      text NOT NULL,
    permissions      jsonb NOT NULL,
    status           text NOT NULL CHECK (status IN ('registered','rolled_back','superseded','removed')),
    registered_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, integration_id, version, tool_name)
);

-- Stage 12 automatic numbering (TTT 500-899). Permanent: a number never moves and a tool never changes number.
CREATE TABLE integrations.tool_numbers (
    namespace        text NOT NULL DEFAULT 'default',
    number           text NOT NULL CHECK (number ~ '^(0[0-3][0-9]|040)\.[5-8][0-9]{2}$'),
    tool_string      text NOT NULL,
    integration_id   text NOT NULL,
    assigned_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, number),
    UNIQUE (namespace, tool_string)
);
CREATE TRIGGER tool_numbers_append_only BEFORE UPDATE OR DELETE ON integrations.tool_numbers
    FOR EACH ROW EXECUTE FUNCTION integrations.forbid_mutation();

CREATE TABLE integrations.integration_permissions (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text NOT NULL,
    permission       text NOT NULL CHECK (permission !~ '\*'),
    source           text NOT NULL CHECK (source IN ('requested','approved')),
    PRIMARY KEY (namespace, integration_id, version, permission, source)
);

-- References only: a raw credential can never be stored here (Stage 02 owns the credential store).
CREATE TABLE integrations.integration_credentials_refs (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text NOT NULL,
    name             text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{1,40}$'),
    kind             text NOT NULL,
    ref              text NOT NULL CHECK (ref ~ '^credref://[a-z0-9][a-z0-9_.-]{0,80}/[a-z][a-z0-9_]{1,40}$'),
    required         boolean NOT NULL,
    PRIMARY KEY (namespace, integration_id, version, name)
);

CREATE TABLE integrations.integration_health (
    id               bigserial PRIMARY KEY,
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    status           text NOT NULL CHECK (status IN ('HEALTHY','DEGRADED','UNHEALTHY','STOPPED','BLOCKED',
                       'OFFLINE_UNAVAILABLE','AUTH_REQUIRED')),
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX integration_health_idx ON integrations.integration_health (namespace, integration_id, checked_at DESC);

CREATE TABLE integrations.integration_events (
    event_id         text PRIMARY KEY,
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text,
    event_type       text NOT NULL,
    actor            text NOT NULL,
    details          jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id   text,
    at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX integration_events_idx ON integrations.integration_events (namespace, integration_id, at);

CREATE TABLE integrations.installation_events (
    id               bigserial PRIMARY KEY,
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text,
    from_state       text,
    to_state         text NOT NULL,
    reason           text NOT NULL,
    actor            text NOT NULL,
    at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE integrations.mcp_servers (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    transport        text NOT NULL CHECK (transport IN ('stdio','streamable_http')),
    endpoint         jsonb NOT NULL,
    lifecycle        text NOT NULL CHECK (lifecycle IN ('CONFIGURED','STARTING','HEALTHY','DEGRADED','UNHEALTHY','STOPPED','BLOCKED')),
    protocol_version text,
    server_info      jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_error       text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, integration_id)
);

CREATE TABLE integrations.mcp_capabilities (
    namespace        text NOT NULL DEFAULT 'default',
    integration_id   text NOT NULL,
    version          text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('tool','resource','prompt')),
    name             text NOT NULL,
    schema_hash      text NOT NULL,
    metadata         jsonb NOT NULL,
    accepted         boolean NOT NULL,
    observed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, integration_id, version, kind, name)
);

CREATE TABLE integrations.skills (
    namespace        text NOT NULL DEFAULT 'default',
    skill_id         text NOT NULL,
    integration_id   text NOT NULL,
    current_version  text,
    state            text NOT NULL CHECK (state IN ('ENABLED','DISABLED','REMOVED')),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, skill_id)
);

CREATE TABLE integrations.skill_versions (
    namespace        text NOT NULL DEFAULT 'default',
    skill_id         text NOT NULL,
    version          text NOT NULL,
    content_digest   text NOT NULL CHECK (length(content_digest) = 64),
    content          jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, skill_id, version)
);

CREATE TABLE integrations.plugins (
    namespace        text NOT NULL DEFAULT 'default',
    plugin_id        text NOT NULL,
    current_version  text,
    state            text NOT NULL CHECK (state IN ('ENABLED','DISABLED','REMOVED','INSTALLED','BLOCKED')),
    isolation_required boolean NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, plugin_id)
);

CREATE TABLE integrations.plugin_versions (
    namespace        text NOT NULL DEFAULT 'default',
    plugin_id        text NOT NULL,
    version          text NOT NULL,
    package_digest   text NOT NULL,
    contributes      jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, plugin_id, version, package_digest)
);

-- Exact integration version/package used by each task tool (version pinning, Stage 12 §38).
CREATE TABLE integrations.task_pins (
    namespace        text NOT NULL DEFAULT 'default',
    objective_id     text NOT NULL,
    task_id          text NOT NULL,
    tool_number      text NOT NULL,
    integration_id   text NOT NULL,
    version          text NOT NULL,
    package_digest   text NOT NULL,
    pinned_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, objective_id, task_id, tool_number)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['integration_manifests','integration_versions','integration_events','installation_events',
                           'integration_health','skill_versions','plugin_versions','task_pins','mcp_capabilities',
                           'integration_capabilities','integration_permissions','integration_credentials_refs'] LOOP
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE ON integrations.%I FOR EACH ROW '
                   'EXECUTE FUNCTION integrations.forbid_mutation()', t, t);
  END LOOP;
END $$;

-- integration_tools rows may only change status (registered -> rolled_back/superseded/removed); identity is fixed.
CREATE FUNCTION integrations.integration_tools_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'integration tool records are permanent' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NEW.tool_string <> OLD.tool_string OR NEW.tool_number <> OLD.tool_number OR NEW.schema_hash <> OLD.schema_hash
     OR NEW.risk <> OLD.risk OR NEW.side_effect <> OLD.side_effect OR NEW.permissions <> OLD.permissions THEN
    RAISE EXCEPTION 'integration tool % identity cannot change', OLD.tool_string USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER integration_tools_guard BEFORE UPDATE OR DELETE ON integrations.integration_tools
    FOR EACH ROW EXECUTE FUNCTION integrations.integration_tools_guard();

GRANT USAGE ON SCHEMA integrations TO atta_app;
GRANT SELECT, INSERT ON integrations.integration_manifests, integrations.integration_versions,
    integrations.integration_events, integrations.installation_events, integrations.integration_health,
    integrations.skill_versions, integrations.plugin_versions, integrations.task_pins, integrations.tool_numbers,
    integrations.mcp_capabilities, integrations.integration_capabilities, integrations.integration_permissions,
    integrations.integration_credentials_refs TO atta_app;
GRANT SELECT, INSERT, UPDATE ON integrations.integrations, integrations.integration_tools, integrations.mcp_servers,
    integrations.skills, integrations.plugins TO atta_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA integrations TO atta_app;
