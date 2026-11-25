# Skill architecture (Stage 12 sidecar)

A skill (`integration_type: SKILL`, `execution_mode: DECLARATIVE`) contains instructions (a file), templates,
schemas, deterministic workflows/scripts, tool references, resource references, validation rules, examples and
metadata. **It contains no executable code and receives no tools.**

## Tool references

`skill.tool_references` lists `CCC.TTT` numbers or `<integration_or_capability_id>:<tool>` strings. At install
they must resolve to numbered tools in the ONE catalogue, else the install fails and rolls back (S12-T11, T51).
Workflow steps may only use referenced tools. Using a skill still goes through the Stage 01A allocator: the skill
can only *propose* tool numbers, which must be among the compatible tools for the task and within the tool limit
(S12-T52: two references, one allocated).

## Versioning and pinning

Every version's full content is stored immutably in `integrations.skill_versions` (same version + different
content is refused). `IntegrationService.script_task_spec()` binds `skill:<id>@<version> → content digest` into the
task contract's `input_checksums`, so the pin is covered by the contract hash. `script_source()` loads exactly the
pinned version and checks its digest; an upgrade changes only what NEW tasks get (S12-T53). Disable, upgrade
(operator-signed) and removal follow the common lifecycle; rollback restores the previous version on failure.

## Reusable scripts (AIRexploit/ATTa requirement)

A workflow with `kind: script` is a parameterised (`parameters` JSON Schema), versioned sequence of tool steps with
`{{param}}` substitution. Scripts are discoverable through the Hub (`IntegrationService.list_scripts()`,
`GET /v1/skills/scripts`) as data. Execution uses `ScriptProposalSource`, a deterministic proposal source labelled
`atta-script` (not a model, not an engine): each step becomes a ToolProposal that the controller validates and runs
exactly like a model proposal — contract, drift guard, policy, approvals, budgets, gateway, independent
verification. Parameters are schema-validated before any proposal exists.
