-- Stage 11: web search and deep research (schema `research`).
-- Depends only on 0001_foundation and 0002_runtime_controller (runtime role atta_app). Numbered 0011 (= Stage 11)
-- so that parallel stage sidecars (01B, 04, ...) adding 0003+ cannot collide; it references no other stage's tables.
-- Conventions copied from 0002: append-only tables refuse UPDATE/DELETE by trigger and are granted SELECT/INSERT only;
-- mutable tables hold current state only and every change is logged append-only in research.events;
-- budgets are fixed at creation and consumption can never decrease (trigger); tenant_id columns exist for Stage 02 RLS.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atta_app') THEN
    RAISE EXCEPTION 'runtime role atta_app missing: run database/bootstrap.sql as a superuser';
  END IF;
END $$;

CREATE SCHEMA research;

CREATE FUNCTION research.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'research.% is append-only: % refused', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

-- research_runs: current state of one research run (mutable; changes logged in research.events)
CREATE TABLE research.runs (
    research_id          text PRIMARY KEY CHECK (research_id ~ '^rsr_[0-9A-HJKMNP-TV-Z]{26}$'),
    tenant_id            text,                                   -- Stage 02
    principal_id         text,                                   -- Stage 02 (authenticated principal)
    objective_id         text,                                   -- Stage 01A objective executing the research
    parent_task_id       text,
    mode                 text NOT NULL CHECK (mode IN ('QUICK_SEARCH', 'STANDARD_RESEARCH', 'DEEP_RESEARCH',
                           'SOURCE_COMPARISON', 'FACT_VERIFICATION')),
    question             text NOT NULL,
    status               text NOT NULL CHECK (status IN ('CREATED', 'RUNNING', 'PAUSED', 'CANCELLING', 'CANCELLED',
                           'COMPLETED', 'COMPLETED_WITH_LIMITATIONS', 'FAILED', 'BLOCKED')),
    verification_status  text NOT NULL DEFAULT 'NOT_VERIFIED' CHECK (verification_status IN ('NOT_VERIFIED', 'VERIFIED',
                           'VERIFICATION_FAILED')),
    error                jsonb,
    current_version      integer NOT NULL CHECK (current_version >= 1),
    phase                text NOT NULL DEFAULT 'created',
    batch                integer NOT NULL DEFAULT 0,
    cancel_requested     boolean NOT NULL DEFAULT false,
    started_at           timestamptz,
    deadline_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION research.run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.research_id <> OLD.research_id OR NEW.mode <> OLD.mode OR NEW.question <> OLD.question THEN
    RAISE EXCEPTION 'research identity, mode and question are immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.current_version < OLD.current_version THEN
    RAISE EXCEPTION 'research contract versions only move forward' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status IN ('CANCELLED', 'COMPLETED', 'COMPLETED_WITH_LIMITATIONS', 'FAILED') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'research % is final (%): status cannot change', OLD.research_id, OLD.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.cancel_requested AND NOT NEW.cancel_requested THEN
    RAISE EXCEPTION 'a cancellation request cannot be withdrawn' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.cancel_requested AND NEW.status IN ('COMPLETED', 'COMPLETED_WITH_LIMITATIONS') THEN
    RAISE EXCEPTION 'cancelled research can never be reported as completed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER runs_guard BEFORE UPDATE ON research.runs FOR EACH ROW EXECUTE FUNCTION research.run_guard();

-- Versioned research contracts (append-only)
CREATE TABLE research.contract_versions (
    research_id    text NOT NULL REFERENCES research.runs (research_id),
    version        integer NOT NULL CHECK (version >= 1),
    contract       jsonb NOT NULL,
    contract_hash  text NOT NULL CHECK (length(contract_hash) = 64),
    reason         text NOT NULL,
    authorised_by  text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (research_id, version)
);

-- Research budgets (queries, sources, fetches, depth, network requests, tokens, retries, ...)
CREATE TABLE research.budgets (
    research_id  text NOT NULL REFERENCES research.runs (research_id),
    metric       text NOT NULL,
    limit_value  numeric NOT NULL CHECK (limit_value >= 0),
    consumed     numeric NOT NULL DEFAULT 0 CHECK (consumed >= 0),
    PRIMARY KEY (research_id, metric),
    CHECK (consumed <= limit_value)
);
CREATE FUNCTION research.budget_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.limit_value > OLD.limit_value THEN
    RAISE EXCEPTION 'research budget limits can never be raised' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.consumed < OLD.consumed THEN
    RAISE EXCEPTION 'consumed research budget can never decrease' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER budgets_guard BEFORE UPDATE ON research.budgets FOR EACH ROW EXECUTE FUNCTION research.budget_guard();

-- research_queries (append-only: one row per executed or refused query)
CREATE TABLE research.queries (
    query_id           text PRIMARY KEY CHECK (query_id ~ '^rqy_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id        text NOT NULL REFERENCES research.runs (research_id),
    subquestion_index  integer,
    query_text         text NOT NULL,
    provider_id        text,
    provider_kind      text,
    objective_id       text,
    task_id            text,
    execution_id       text,
    status             text NOT NULL CHECK (status IN ('executed', 'failed', 'refused')),
    error_code         text,
    result_count       integer NOT NULL DEFAULT 0,
    response_sha256    text,
    results            jsonb NOT NULL DEFAULT '[]'::jsonb,
    executed_at        timestamptz NOT NULL DEFAULT now()
);

-- source_snapshots / evidence store: exact extracted text of the retrieved bytes, content-addressed (append-only)
CREATE TABLE research.snapshots (
    snapshot_id     text PRIMARY KEY CHECK (snapshot_id ~ '^snp_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id     text NOT NULL REFERENCES research.runs (research_id),
    content_sha256  text NOT NULL CHECK (length(content_sha256) = 64),
    text_sha256     text NOT NULL CHECK (length(text_sha256) = 64),
    content_type    text NOT NULL,
    bytes           bigint NOT NULL CHECK (bytes >= 0),
    text            text NOT NULL,
    blocks          jsonb NOT NULL,
    links           jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_path        text,
    stored_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (research_id, content_sha256)
);

-- research_sources: canonical source records (append-only; duplicates reference the record they duplicate)
CREATE TABLE research.sources (
    source_id           text PRIMARY KEY CHECK (source_id ~ '^src_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id         text NOT NULL REFERENCES research.runs (research_id),
    kind                text NOT NULL CHECK (kind IN ('web', 'local', 'project')),
    original_url        text,
    canonical_url       text,
    final_url           text,
    domain              text,
    title               text,
    publisher           text,
    author              text,
    published_at        text,                -- as stated by the source (never invented); NULL when not stated
    modified_at         text,
    retrieved_at        timestamptz,
    content_type        text,
    content_hash        text,
    text_hash           text,
    extraction_status   text NOT NULL CHECK (extraction_status IN ('extracted', 'failed', 'unsupported', 'not_retrieved')),
    error_code          text,
    quality             jsonb NOT NULL DEFAULT '{}'::jsonb,
    snapshot_id         text REFERENCES research.snapshots (snapshot_id),
    citation_metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata_provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    duplicate_of        text REFERENCES research.sources (source_id),
    duplicate_relation  text CHECK (duplicate_relation IN ('same_url', 'identical_content', 'near_duplicate', 'syndicated')),
    independence_group  text,
    depth               integer NOT NULL DEFAULT 0 CHECK (depth >= 0),
    discovered_by       text,                -- query_id or parent source_id
    objective_id        text,
    task_id             text,
    execution_id        text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sources_research_idx ON research.sources (research_id, canonical_url);

-- evidence spans inside stored snapshots (append-only)
CREATE TABLE research.evidence (
    evidence_id        text PRIMARY KEY CHECK (evidence_id ~ '^rev_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id        text NOT NULL REFERENCES research.runs (research_id),
    source_id          text NOT NULL REFERENCES research.sources (source_id),
    snapshot_id        text NOT NULL REFERENCES research.snapshots (snapshot_id),
    span_start         integer NOT NULL CHECK (span_start >= 0),
    span_end           integer NOT NULL,
    quote              text NOT NULL,
    quote_sha256       text NOT NULL CHECK (length(quote_sha256) = 64),
    text_sha256        text NOT NULL CHECK (length(text_sha256) = 64),
    purpose            text NOT NULL CHECK (purpose IN ('candidate', 'support', 'contradiction')),
    subquestion_index  integer,
    task_id            text,
    execution_id       text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (span_end > span_start)
);

-- research_claims (current state; only support/contradiction/verification status may change)
CREATE TABLE research.claims (
    claim_id              text PRIMARY KEY CHECK (claim_id ~ '^clm_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id           text NOT NULL REFERENCES research.runs (research_id),
    claim_text            text NOT NULL,
    material              boolean NOT NULL,
    subquestion_index     integer,
    structured            jsonb NOT NULL DEFAULT '{}'::jsonb,
    support_status        text NOT NULL CHECK (support_status IN ('supported', 'partially_supported', 'unsupported',
                            'contested')),
    contradiction_status  text NOT NULL CHECK (contradiction_status IN ('none', 'contradicted', 'unresolved')),
    confidence            jsonb NOT NULL DEFAULT '{}'::jsonb,
    verification_status   text NOT NULL DEFAULT 'NOT_VERIFIED' CHECK (verification_status IN ('NOT_VERIFIED',
                            'VERIFIED', 'VERIFICATION_FAILED')),
    builder               text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION research.claim_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'claims are never deleted' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NEW.claim_id <> OLD.claim_id OR NEW.research_id <> OLD.research_id OR NEW.claim_text <> OLD.claim_text
     OR NEW.structured <> OLD.structured OR NEW.builder <> OLD.builder OR NEW.material <> OLD.material THEN
    RAISE EXCEPTION 'claim content is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER claims_guard BEFORE UPDATE OR DELETE ON research.claims FOR EACH ROW EXECUTE FUNCTION research.claim_guard();

-- claim_sources: which source/evidence supports or contradicts which claim (append-only)
CREATE TABLE research.claim_sources (
    claim_id     text NOT NULL REFERENCES research.claims (claim_id),
    source_id    text NOT NULL REFERENCES research.sources (source_id),
    evidence_id  text NOT NULL REFERENCES research.evidence (evidence_id),
    relation     text NOT NULL CHECK (relation IN ('supports', 'contradicts')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, evidence_id, relation)
);

-- research_citations: minted by the service only; bound to a stored source + evidence span
CREATE TABLE research.citations (
    citation_id    text PRIMARY KEY CHECK (citation_id ~ '^cit_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id    text NOT NULL REFERENCES research.runs (research_id),
    claim_id       text NOT NULL REFERENCES research.claims (claim_id),
    source_id      text NOT NULL REFERENCES research.sources (source_id),
    evidence_id    text NOT NULL REFERENCES research.evidence (evidence_id),
    span_start     integer NOT NULL,
    span_end       integer NOT NULL,
    quote_sha256   text NOT NULL CHECK (length(quote_sha256) = 64),
    text_sha256    text NOT NULL CHECK (length(text_sha256) = 64),
    locator        text NOT NULL,
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified       boolean,
    verification   jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION research.citation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'citations are never deleted' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NEW.citation_id <> OLD.citation_id OR NEW.claim_id <> OLD.claim_id OR NEW.source_id <> OLD.source_id
     OR NEW.evidence_id <> OLD.evidence_id OR NEW.span_start <> OLD.span_start OR NEW.span_end <> OLD.span_end
     OR NEW.quote_sha256 <> OLD.quote_sha256 OR NEW.text_sha256 <> OLD.text_sha256 THEN
    RAISE EXCEPTION 'citation binding is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER citations_guard BEFORE UPDATE OR DELETE ON research.citations FOR EACH ROW
    EXECUTE FUNCTION research.citation_guard();

-- research_contradictions (append-only): disagreement is recorded, never hidden
CREATE TABLE research.contradictions (
    contradiction_id          text PRIMARY KEY CHECK (contradiction_id ~ '^cdn_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id               text NOT NULL REFERENCES research.runs (research_id),
    claim_id                  text NOT NULL REFERENCES research.claims (claim_id),
    other_claim_id            text REFERENCES research.claims (claim_id),
    supporting_source_ids     jsonb NOT NULL DEFAULT '[]'::jsonb,
    contradicting_source_ids  jsonb NOT NULL DEFAULT '[]'::jsonb,
    date_differences          jsonb NOT NULL DEFAULT '{}'::jsonb,
    scope_differences         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                    text NOT NULL CHECK (status IN ('unresolved', 'possibly_scope_or_time')),
    detail                    text NOT NULL,
    detected_by               text NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now()
);

-- research_checkpoints (append-only, hash-sealed)
CREATE TABLE research.checkpoints (
    checkpoint_id  text PRIMARY KEY CHECK (checkpoint_id ~ '^rck_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id    text NOT NULL REFERENCES research.runs (research_id),
    sequence       integer NOT NULL,
    data           jsonb NOT NULL,
    data_hash      text NOT NULL CHECK (length(data_hash) = 64),
    at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (research_id, sequence)
);

-- research_events: the audit trail objective -> contract -> query -> provider -> source -> evidence -> claim ->
-- citation -> verification -> result (append-only)
CREATE TABLE research.events (
    event_id      text PRIMARY KEY CHECK (event_id ~ '^ret_[0-9A-HJKMNP-TV-Z]{26}$'),
    research_id   text NOT NULL REFERENCES research.runs (research_id),
    kind          text NOT NULL,
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    objective_id  text,
    task_id       text,
    execution_id  text,
    query_id      text,
    source_id     text,
    claim_id      text,
    citation_id   text,
    at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_research_idx ON research.events (research_id, at);

-- final results (append-only, hash-sealed; the latest row is the current result)
CREATE TABLE research.results (
    research_id  text NOT NULL REFERENCES research.runs (research_id),
    sequence     integer NOT NULL,
    result       jsonb NOT NULL,
    result_hash  text NOT NULL CHECK (length(result_hash) = 64),
    at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (research_id, sequence)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['contract_versions', 'queries', 'snapshots', 'sources', 'evidence', 'claim_sources',
                           'contradictions', 'checkpoints', 'events', 'results'] LOOP
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE ON research.%I FOR EACH ROW '
                   'EXECUTE FUNCTION research.forbid_mutation()', t, t);
  END LOOP;
END $$;

-- Runtime privileges (data only).
GRANT USAGE ON SCHEMA research TO atta_app;
GRANT SELECT, INSERT ON research.contract_versions, research.queries, research.snapshots, research.sources,
      research.evidence, research.claim_sources, research.contradictions, research.checkpoints, research.events,
      research.results TO atta_app;
GRANT SELECT, INSERT, UPDATE ON research.runs, research.budgets, research.claims, research.citations TO atta_app;
