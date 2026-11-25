-- Stage 01A: persistent state of the runtime intelligence controller (schema `control`).
-- Append-only tables (objectives, contracts, transitions, proposals, verifications, evidence, checkpoints,
-- drift violations, recovery records) reject UPDATE/DELETE with a trigger, and the runtime role is not granted
-- UPDATE/DELETE on them either. Mutable tables hold only current state; every change is logged append-only.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atta_app') THEN
    RAISE EXCEPTION 'runtime role atta_app missing: run database/bootstrap.sql as a superuser';
  END IF;
END $$;

CREATE SCHEMA control;

CREATE FUNCTION control.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'control.% is append-only: % refused', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TABLE control.tool_identifiers (
    namespace           text NOT NULL DEFAULT 'default',   -- 'default' = production catalogue; tests use their own
    number              text NOT NULL CHECK (number ~ '^(0[0-3][0-9]|040)\.[0-9]{3}$'),
    tool_string         text NOT NULL,
    retired             boolean NOT NULL DEFAULT false,
    first_registered_at timestamptz NOT NULL DEFAULT now(),
    retired_at          timestamptz,
    PRIMARY KEY (namespace, number),
    UNIQUE (namespace, tool_string)
);
CREATE FUNCTION control.tool_identifier_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'tool identifiers are permanent' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NEW.namespace <> OLD.namespace OR NEW.number <> OLD.number OR NEW.tool_string <> OLD.tool_string OR (OLD.retired AND NOT NEW.retired) THEN
    RAISE EXCEPTION 'tool identifier % cannot be reassigned or reactivated', OLD.number USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tool_identifiers_guard BEFORE UPDATE OR DELETE ON control.tool_identifiers
    FOR EACH ROW EXECUTE FUNCTION control.tool_identifier_guard();

CREATE TABLE control.objectives (
    objective_id            text NOT NULL CHECK (objective_id ~ '^obj_[0-9A-HJKMNP-TV-Z]{26}$'),
    version                 integer NOT NULL CHECK (version >= 1),
    tenant_id               text,                                -- populated from Stage 02
    original_instructions   text NOT NULL,
    original_source_sha256  text NOT NULL CHECK (length(original_source_sha256) = 64),
    contract                jsonb NOT NULL,
    contract_hash           text NOT NULL CHECK (length(contract_hash) = 64),
    approval_status         text NOT NULL CHECK (approval_status IN ('draft', 'approved')),
    approved_by             text,
    amendment_reason        text,
    authorised_by           text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, version)
);

CREATE TABLE control.objective_runtime (
    objective_id     text PRIMARY KEY,
    current_version  integer NOT NULL,
    status           text NOT NULL CHECK (status IN ('open', 'integrating', 'completed', 'failed', 'blocked')),
    max_active       integer NOT NULL CHECK (max_active BETWEEN 1 AND 16),
    batch            integer NOT NULL DEFAULT 0,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.task_contracts (
    task_id        text NOT NULL CHECK (task_id ~ '^TASK-[0-9]{4,}$'),
    objective_id   text NOT NULL,
    version        integer NOT NULL CHECK (version >= 1),
    contract       jsonb NOT NULL,
    contract_hash  text NOT NULL CHECK (length(contract_hash) = 64),
    reason         text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, task_id, version)
);

CREATE TABLE control.tasks (
    objective_id     text NOT NULL,
    task_id          text NOT NULL,
    current_version  integer NOT NULL,
    state            text NOT NULL CHECK (state IN ('CREATED', 'VALIDATING', 'PLANNING', 'READY', 'AWAITING_APPROVAL',
                       'QUEUED', 'RUNNING', 'WAITING_FOR_DEPENDENCY', 'RETRYING', 'VERIFYING', 'COMPLETED', 'FAILED',
                       'BLOCKED', 'CANCELLED', 'RECOVERING')),
    attempt_id       text,
    attempt_no       integer NOT NULL DEFAULT 0,
    repair_of        text,
    row_version      integer NOT NULL DEFAULT 0,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, task_id)
);

CREATE TABLE control.task_transitions (
    id             bigserial PRIMARY KEY,
    objective_id   text NOT NULL,
    task_id        text NOT NULL,
    from_state     text,
    to_state       text NOT NULL,
    reason         text NOT NULL,
    controller_id  text NOT NULL,
    attempt_id     text,
    evidence       jsonb NOT NULL DEFAULT '{}'::jsonb,
    at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.active_slots (
    objective_id   text NOT NULL,
    task_id        text NOT NULL,
    controller_id  text NOT NULL,
    reserved_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objective_id, task_id)
);

CREATE TABLE control.proposals (
    proposal_id      text PRIMARY KEY,
    objective_id     text NOT NULL,
    task_id          text NOT NULL,
    attempt_id       text,
    body             jsonb NOT NULL,
    body_hash        text NOT NULL,
    accepted         boolean NOT NULL,
    errors           jsonb NOT NULL DEFAULT '[]'::jsonb,
    source           text NOT NULL,
    at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.executions (
    execution_id     text PRIMARY KEY CHECK (execution_id ~ '^exe_[0-9A-HJKMNP-TV-Z]{26}$'),
    objective_id     text NOT NULL,
    task_id          text NOT NULL,
    attempt_id       text NOT NULL,
    proposal_id      text NOT NULL,
    tool_number      text NOT NULL,
    tool_string      text NOT NULL,
    operation        text NOT NULL,
    request_hash     text NOT NULL,
    arguments        jsonb NOT NULL,
    idempotency      text NOT NULL,
    state            text NOT NULL CHECK (state IN ('reserved', 'running', 'succeeded', 'failed', 'timed_out',
                       'cancelled', 'outcome_unknown')),
    policy           jsonb NOT NULL,
    result           jsonb,
    error            jsonb,
    output_hash      text,
    lease_expires_at timestamptz NOT NULL,
    started_at       timestamptz,
    finished_at      timestamptz,
    UNIQUE (objective_id, task_id, proposal_id)
);

CREATE TABLE control.budgets (
    objective_id  text NOT NULL,
    task_id       text NOT NULL,
    metric        text NOT NULL,
    limit_value   numeric NOT NULL CHECK (limit_value >= 0),
    consumed      numeric NOT NULL DEFAULT 0 CHECK (consumed >= 0),
    reserved      numeric NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    PRIMARY KEY (objective_id, task_id, metric),
    CHECK (consumed + reserved <= limit_value)
);
CREATE FUNCTION control.budget_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.limit_value <> OLD.limit_value THEN
    RAISE EXCEPTION 'budget limits are fixed by the task contract' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.consumed < OLD.consumed THEN
    RAISE EXCEPTION 'consumed budget can never decrease' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER budgets_guard BEFORE UPDATE ON control.budgets FOR EACH ROW EXECUTE FUNCTION control.budget_guard();

CREATE TABLE control.step_failures (
    objective_id text NOT NULL,
    task_id      text NOT NULL,
    step_key     text NOT NULL,
    failures     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (objective_id, task_id, step_key)
);

CREATE TABLE control.resource_locks (
    resource_id   text PRIMARY KEY,
    objective_id  text NOT NULL,
    task_id       text NOT NULL,
    acquired_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.drift_violations (
    id           bigserial PRIMARY KEY,
    objective_id text NOT NULL,
    task_id      text NOT NULL,
    proposal_id  text,
    rule         text NOT NULL,
    detail       text NOT NULL,
    at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.verifications (
    id            bigserial PRIMARY KEY,
    objective_id  text NOT NULL,
    task_id       text NOT NULL,
    attempt_id    text,
    criterion_id  text NOT NULL,
    passed        boolean NOT NULL,
    detail        jsonb NOT NULL,
    evidence_id   text,
    verifier      text NOT NULL,
    at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.evidence (
    evidence_id  text PRIMARY KEY CHECK (evidence_id ~ '^evd_[0-9A-HJKMNP-TV-Z]{26}$'),
    objective_id text NOT NULL,
    task_id      text,
    kind         text NOT NULL,
    sha256       text NOT NULL CHECK (length(sha256) = 64),
    content      jsonb NOT NULL,
    at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.checkpoints (
    checkpoint_id text PRIMARY KEY CHECK (checkpoint_id ~ '^chk_[0-9A-HJKMNP-TV-Z]{26}$'),
    objective_id  text NOT NULL,
    sequence      integer NOT NULL,
    verified      boolean NOT NULL,
    data          jsonb NOT NULL,
    data_hash     text NOT NULL CHECK (length(data_hash) = 64),
    at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (objective_id, sequence)
);

CREATE TABLE control.recovery_records (
    id           bigserial PRIMARY KEY,
    objective_id text NOT NULL,
    action       text NOT NULL,
    detail       jsonb NOT NULL,
    at           timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['objectives', 'task_contracts', 'task_transitions', 'proposals', 'drift_violations',
                           'verifications', 'evidence', 'checkpoints', 'recovery_records'] LOOP
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE ON control.%I FOR EACH ROW '
                   'EXECUTE FUNCTION control.forbid_mutation()', t, t);
  END LOOP;
END $$;

-- The task state machine is enforced by the database too (defence in depth): even SQL issued with the runtime
-- role cannot make an illegal transition, reopen a COMPLETED task or skip verification ordering.
CREATE FUNCTION control.task_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.state <> OLD.state AND (OLD.state, NEW.state) NOT IN (
      ('AWAITING_APPROVAL','BLOCKED'),
      ('AWAITING_APPROVAL','CANCELLED'),
      ('AWAITING_APPROVAL','READY'),
      ('BLOCKED','CANCELLED'),
      ('BLOCKED','PLANNING'),
      ('CANCELLED','QUEUED'),
      ('CREATED','BLOCKED'),
      ('CREATED','CANCELLED'),
      ('CREATED','VALIDATING'),
      ('PLANNING','AWAITING_APPROVAL'),
      ('PLANNING','BLOCKED'),
      ('PLANNING','CANCELLED'),
      ('PLANNING','READY'),
      ('PLANNING','WAITING_FOR_DEPENDENCY'),
      ('QUEUED','CANCELLED'),
      ('QUEUED','RECOVERING'),
      ('QUEUED','RUNNING'),
      ('QUEUED','WAITING_FOR_DEPENDENCY'),
      ('READY','BLOCKED'),
      ('READY','CANCELLED'),
      ('READY','QUEUED'),
      ('READY','WAITING_FOR_DEPENDENCY'),
      ('RECOVERING','BLOCKED'),
      ('RECOVERING','FAILED'),
      ('RECOVERING','QUEUED'),
      ('RECOVERING','RUNNING'),
      ('RECOVERING','VERIFYING'),
      ('RETRYING','BLOCKED'),
      ('RETRYING','CANCELLED'),
      ('RETRYING','FAILED'),
      ('RETRYING','RECOVERING'),
      ('RETRYING','RUNNING'),
      ('RUNNING','BLOCKED'),
      ('RUNNING','CANCELLED'),
      ('RUNNING','FAILED'),
      ('RUNNING','RECOVERING'),
      ('RUNNING','RETRYING'),
      ('RUNNING','VERIFYING'),
      ('VALIDATING','BLOCKED'),
      ('VALIDATING','FAILED'),
      ('VALIDATING','PLANNING'),
      ('VERIFYING','BLOCKED'),
      ('VERIFYING','COMPLETED'),
      ('VERIFYING','FAILED'),
      ('VERIFYING','RECOVERING'),
      ('VERIFYING','RETRYING'),
      ('WAITING_FOR_DEPENDENCY','BLOCKED'),
      ('WAITING_FOR_DEPENDENCY','CANCELLED'),
      ('WAITING_FOR_DEPENDENCY','READY')) THEN
    RAISE EXCEPTION 'illegal task transition % -> %', OLD.state, NEW.state USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.objective_id <> OLD.objective_id OR NEW.task_id <> OLD.task_id THEN
    RAISE EXCEPTION 'task identity is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.current_version < OLD.current_version THEN
    RAISE EXCEPTION 'contract versions only move forward' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tasks_state_guard BEFORE UPDATE ON control.tasks FOR EACH ROW EXECUTE FUNCTION control.task_state_guard();

-- Runtime privileges (data only).
GRANT USAGE ON SCHEMA control TO atta_app;
GRANT SELECT, INSERT ON control.objectives, control.task_contracts, control.task_transitions, control.proposals,
      control.drift_violations, control.verifications, control.evidence, control.checkpoints,
      control.recovery_records TO atta_app;
GRANT SELECT, INSERT, UPDATE ON control.tool_identifiers, control.objective_runtime, control.tasks,
      control.executions, control.budgets, control.step_failures TO atta_app;
GRANT SELECT, INSERT, DELETE ON control.active_slots, control.resource_locks TO atta_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA control TO atta_app;
-- Stage 01 platform tables (RLS-forced, no policies until Stage 02) and the read-only migration ledger.
GRANT USAGE ON SCHEMA platform TO atta_app;
GRANT SELECT, INSERT ON platform.execution_events, platform.verification_evidence TO atta_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.idempotency_keys TO atta_app;
GRANT SELECT ON public.atta_schema_migrations TO atta_app;
