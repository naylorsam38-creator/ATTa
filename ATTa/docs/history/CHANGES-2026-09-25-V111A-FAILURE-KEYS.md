# v111a — FailureKey, normaliser, pending barrier (2026-09-25)

First of the v111 bundles. Built on v110 + supersede-to-retired. Repair loop unchanged:
fail → diagnose → known fix → adapter → LLM → human → verify → learn.

## Problem

1. A learned fix was looked up by `(layer, key)`, and for the build layer `key` was 160 chars of an
   error message with numbers stripped. Two unrelated errors that start the same way shared a key, so
   a fix learned on one replayed on the other. A WEAK key was as good as a strong one.
2. A freshly learned rule ran on the very next failure, before any full-catalogue run had seen it.
3. `learned.<layer>.<seconds>` ids: two fixes learned in the same second on one layer collided and
   their counts merged onto one record (found while testing this bundle).

## What changed — five files, nothing else

- **04-deployment/failure_keys.py (new).** The one key: `v2|<layer>|<strong-key>`. The normaliser
  keeps keys the system itself produces from code (stage codes, Coolify statuses, the named
  build-failure families in `BUILD_FAMILIES`) and turns everything else into `UNKNOWN_FAILURE`.
  `for_rule()` reads a v110-shaped entry (layer+key, no `failure_key`) as the same fact — no
  rewrite, no migration code. `refused()` says why a rule can never match: unknown key version,
  or keyed on `UNKNOWN_FAILURE`. `KNOWN_VERSIONS = (2,)`; when v3 exists, v3 handling is written then.
- **known_fixes.py.** `learn()`: computes the FailureKey; returns `None` and stores nothing when
  the key is `UNKNOWN_FAILURE` (the LLM still repaired that build, nothing is remembered from it);
  supersede is now by FailureKey; new entries carry `failure_key` and start `pending`; ids are
  millisecond-based and bumped until unused in the live and retired stores. `match()`: one lookup
  by FailureKey; skips `pending` rules and refused rules; `UNKNOWN_FAILURE` never matches a learned
  fix; built-in rules unchanged and still match on key+detail. Result carries `failure_key`.
- **rule_lifecycle.py.** New first state `pending`. `new_fields()` writes it, so runner rules from
  `add_runner_rule` start pending too (no change to repair_actions.py). `_ensure()` keeps v110
  entries as `candidate` (they were already running) and gives them `activated_at = learned_at`.
  `after_full_run()`: any COMPLETE full run activates pending rules learned before `run_started`
  (→ candidate, `activated_at` set); those never ran in that run so they are not suspects.
  Promotion needs `activated_at < run_started`, not `learned_at`. Suspects are rules ACTIVATED or
  promoted since the last clean run. Health table gains a Key column, a PENDING count, an
  "Activated this run" line, and shows REFUSED rules with the reason.
- **app_runner.py.** `diagnose()` ignores pending runner rules. `learned_rules()` reads through the
  locked loader.
- **maintenance.py.** `_succeeded()` records what `learn()` actually did: `learned` is False and
  `learned_rule` is None when the key was UNKNOWN_FAILURE (repaired, not remembered).

## Locking, end to end

Every write to the four stores (`learned_fixes.json`, `learned_rules.json`, `retired_fixes.json`,
`rule_runs.json`) goes through `rule_lifecycle.save()`/`_save_obj()` (temp file + rename) inside
`_Locked()`. Every READ now goes through `rule_lifecycle.load()`, also under the lock — `known_fixes.learned()`
and `app_runner.learned_rules()` call it. `_Locked` is re-entrant across the flock (depth counter), so a
locked writer calling a locked reader cannot deadlock on its own lock. Verified: 3-deep re-entry unwinds;
a reader thread blocks behind a writer; 4 separate processes × 50 records on one rule = 200 counted.

## The pending barrier has no back door

`pipeline.qualify(only=...)` (single-app rerun) never calls `after_full_run` — only `only is None`
does. `requalify()` (healing verification) calls `qualify()` with no `only`, i.e. a full run, but
learning happens in `_succeeded` AFTER that verification, so the rule it produces waits for the
next full run. Verified by reading the code; nothing was changed to make it true.

## What was run here (real modules, real files, scratch APP_BUILDER_ROOT)

27 checks, all pass: normaliser strong/UNKNOWN per layer; unknown version and bad shape refused;
UNKNOWN never learned; learn → pending with generalised args; pending not matched (built-in fires),
not counted; run started before learn does not activate; incomplete run does not activate; complete
run activates and the activated rule is not a suspect; activated rule matched by FailureKey with
args filled and `fired` counted; clean run promotes; suspects = activated-since-last-clean only;
ids unique in one second; supersede by FailureKey keeps the old record in retired; v110-shaped
entry matched via derived key; v9 key refused and shown REFUSED in `rule_health.md`; runner rule
pending → not consulted → activated → fires; 40 threads recording at once lose nothing.

NOT run here: the qualification pipeline against the catalogue. That is the server's run.

## Untouched, on purpose

flock locking · action-based rules · no app names in rules · own-failure retirement · archive-not-delete ·
pattern_miner.py (still not placed) · repair budgets and journal (v111b/c) · replay harness (v111d,
waits for real JSONL history from the box).

## Read after the first server run

`state/checklists/rule_health.md`: any REFUSED row, any PENDING row, and the "Activated this run" line.
A pending rule that never activates across two complete full runs is a bug — report it.
