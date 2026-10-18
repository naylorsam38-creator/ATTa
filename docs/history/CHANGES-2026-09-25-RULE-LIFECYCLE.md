# Learned-rule lifecycle (2026-09-25) — v110

Problem: the self-healer learns two kinds of rule (verified LLM fixes in
`state/maintenance/learned_fixes.json`, failure patterns in `state/runner/learned_rules.json`),
checks them FIRST on every failure, and never counted whether they still work. A rule that stopped
holding, or a bad regex that matched everything, ran first on every app forever. Learned entries
were promoted after one requalify pass, against the locked rule that nothing is fixed until a
full-catalogue run proves it. `learned_rules.json` was written non-atomically.

What changed (repair loop itself unchanged: fail → diagnose → known fix → adapter → LLM → human → verify → learn):

- **04-deployment/rule_lifecycle.py (new).** Owns the record on every learned rule: `fired`,
  `held`, `failed`, `streak_failed`, `confidence`, timestamps, `last_failure`. States:
  `candidate` (new; still runs) → `confirmed` (held ≥ PROMOTE_MIN_HELD and a clean full run
  since it was learned) → `degraded` (confidence < DEGRADE_BELOW; still runs, shown; recovers)
  → `retired` (RETIRE_AFTER_FAILED failures in a row: moved to
  `state/maintenance/retired_fixes.json` with its whole record, never runs again). All writes are
  flock + thread-lock + temp-file + rename. Writes `state/checklists/rule_health.md` every full run.
  `python3 rule_lifecycle.py` prints the health table.
- **known_fixes.py.** `learn()` writes the entry as a candidate under the lock. `match()` counts
  `fired` when a learned entry matches. Retired entries are no longer in the file, so the failure
  they covered drops back to the LLM tier.
- **maintenance.py.** After verification: learned tier-1 fixes applied that round are counted
  `held` (layer passed, incl. build layer via `on_build_progress`) or `failed` (still failing,
  with the failure keys as evidence).
- **repair_actions.py.** `add_runner_rule` writes the new rule as a candidate, atomically.
- **app_runner.py.** `diagnose()` counts `fired` on a learned rule and marks the match `learned`.
  `run_app()`: when a learned rule's retry-fix was applied, the NEXT attempt judges it — app up =
  `held`, still down = `failed`. Rules whose fix is `next_part` only classify and are counted
  `fired` only. Checklist text gains the promoted/suspects lines.
- **pipeline.py.** On a full-catalogue run (`only is None`): if the checklist is complete and has
  no regression, candidates that qualify are promoted and the clean-run clock is set; if there IS
  a regression, `problems` gains `REGRESSION SUSPECTS (...)` naming every rule learned or promoted
  since the last clean run. The checklist .md is rewritten after this so it carries the lines.

Knobs (env, all optional): `APP_BUILDER_RULE_PROMOTE_HELD` (2), `APP_BUILDER_RULE_RETIRE_FAILED`
(3), `APP_BUILDER_RULE_DEGRADE_BELOW` (0.5). Also editable at the top of `rule_lifecycle.py`.

Existing v109 learned entries: untouched on disk; the first time one is counted it gets the
record added in place as a candidate. Nothing is deleted, ever — retirement moves.

Unchanged: no new memory store, no second healer, no per-app rules, upstream app source never
touched, hand-written BUILT_IN and RULES are not counted (they are not learned).

Not done: prevention scripts (that is pattern_miner.py's job, delivered separately); no change to
`sh()`'s return shape.

Verified here: every touched module imports; the lifecycle path (learn → fired → held ×2 →
regression run names suspect → clean run promotes → failed ×3 retires → match returns None →
runner rule learned/fired/judged → 40 threads × 25 writes lose no counts) run against the real
files in a scratch APP_BUILDER_ROOT. NOT run here: the qualification pipeline itself — that is
the server's job (`bash run`). First thing to read after the first full run:
`state/checklists/rule_health.md` and the REGRESSION SUSPECTS line, if any, in the build checklist.
