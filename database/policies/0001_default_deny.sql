-- Row-level security: tenant-scoped tables deny every row unless a policy grants it.
-- Stage 01 defines NO permissive policies, so a non-owner role sees nothing. Stage 02 adds tenant policies
-- keyed on current_setting('atta.tenant_id').
ALTER TABLE platform.execution_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.execution_events FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.verification_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.verification_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.idempotency_keys FORCE ROW LEVEL SECURITY;
