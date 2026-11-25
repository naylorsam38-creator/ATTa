-- Stage 01 foundation schema: migration bookkeeping, execution events and verification evidence.
-- (pgvector is installed by database/bootstrap.sql; this migration asserts it is present.)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
    RAISE EXCEPTION 'pgvector extension missing: run database/bootstrap.sql as a superuser';
  END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.execution_events (
    id              text PRIMARY KEY CHECK (id ~ '^evt_[0-9A-HJKMNP-TV-Z]{26}$'),
    tenant_id       text,
    type            text NOT NULL,
    subject         text NOT NULL,
    correlation_id  text,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    data            jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS execution_events_subject_idx ON platform.execution_events (subject, occurred_at);
CREATE INDEX IF NOT EXISTS execution_events_correlation_idx ON platform.execution_events (correlation_id);

CREATE TABLE IF NOT EXISTS platform.verification_evidence (
    id              text PRIMARY KEY CHECK (id ~ '^evd_[0-9A-HJKMNP-TV-Z]{26}$'),
    tenant_id       text,
    subject         text NOT NULL,
    command         jsonb NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL,
    exit_code       integer,
    outcome         text NOT NULL CHECK (outcome IN ('passed', 'failed', 'error', 'blocked')),
    output_sha256   text NOT NULL,
    CHECK (finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS platform.idempotency_keys (
    scope           text NOT NULL,
    key             text NOT NULL,
    fingerprint     text NOT NULL,
    status          integer NOT NULL,
    body            jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    PRIMARY KEY (scope, key)
);
