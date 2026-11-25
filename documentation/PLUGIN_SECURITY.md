# Plugin security (Stage 12 sidecar)

## Packaging and installation

A plugin (`PLUGIN`, or `CUSTOM_PROVIDER`) may contribute tools (code), UI extension metadata, workflows, schemas and
provider adapters. Installation **never runs plugin code**: it validates structure and integrity, copies exactly the
listed files into staging, verifies the copied bytes equal the approved digest, generates the capability package and
registers it. The first time plugin code runs is the Stage 01A isolated probe in the supervised runtime, after
operator trust. There are no install hooks (unknown fields are rejected). UI extension metadata is inert data with a
closed set of slots.

## Trust classes

| Class | Loading / execution |
|---|---|
| CORE, TRUSTED | Supervised runtime (Stage 01A): separate `python -I` process, stripped environment, rlimits, tree kill, bytes re-verified before execution |
| RESTRICTED | Requires an OS isolation provider (Stage 01B) **and** READ_ONLY tools only; otherwise **BLOCKED** |
| UNTRUSTED | Installable for inspection; never probed, allocated or executed |
| DISABLED | Not installable |

Trust is chosen by the operator inside the signed approval payload and changed only with a new operator signature.
Trust never grants task permission.

## Isolation contract

`backend/integrations/isolation.py` defines `IsolationProvider.report()`. The sidecar ships `NoOsSandbox`, which
reports isolation **unavailable**; RESTRICTED or `ISOLATED_SANDBOX` integrations are therefore BLOCKED (S12-T14
shows BLOCKED without a provider and the contract working with a clearly-labelled test double).
What the supervised runtime does **not** provide, and why TRUSTED means "operator trusts this code": filesystem
confinement, network namespace, separate user, per-tenant separation. A TRUSTED plugin could read files readable by
the ATTa service account (including `configuration/local.env`); keep untrusted code RESTRICTED until Stage 01B.

## What a plugin cannot do

Grant permissions (policy `grant()` always refuses), add itself to a task (pool changes need an operator signature),
change its risk (the package digest and trust gate catch it — S12-T19), raise budgets (trusted config only), approve
itself, re-enable itself after an administrator disable (operator signature), alter another integration (separate
packages, digests and records), change security policy or Stage 01A contracts (protected files, append-only DB),
verify itself (verification uses ATTa-generated `_atta_tools_verify` checking the approved output schema), or
expand permissions on upgrade without a new signature that covers the diff (S12-T15).

## Results

Integration outputs are untrusted tool output: output-schema validated, bounded (`max_result_bytes`, global
ceiling), secret-looking values redacted, instruction-like text flagged and recorded, provenance recorded, and
delivered to the model only under `untrusted` with provenance — never able to change the contract, pool, budget,
trust, policy or approvals (S12-T33, T34).
