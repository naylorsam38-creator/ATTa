# Syntax triage (2026-09-24)

Problem: a one-character slip in an app's overlay (a trailing comma, a missing bracket) was
reported exactly like a real breakage, and the self-healer had no safe way to fix it.

What changed:

- **04-deployment/syntax_triage.py (new).** Before healing a qualify-layer failure, the app's
  `.ui-capability` overlay is syntax-checked by file type (Python, JSON, YAML, JavaScript via
  `node --check`, shell via `bash -n`). Errors are attached to the failure as
  `SYNTAX_ERROR <file>: line N: ...`. A clean scan records `syntax_errors: []`, meaning syntax
  was ruled out and the failure is real. File types with no checker are skipped and logged to
  `state/maintenance/unchecked_extensions.json`; add a checker with one line in `CHECKERS`.
- **repair_actions.py.** Two new actions: `syntax_check` (read-only) and `patch_overlay_file`
  (replace one exact snippet, re-check syntax, put the original back if it still fails).
  Actions can be marked `learnable=False`; file-specific edits are never replayed on other builds.
- **known_fixes.py.** Rule `qualify.syntax_json`: a JSON syntax error found by triage is fixed
  mechanically, no AI.
- **maintenance.py.** Runs triage before tier 1; skips learning content-specific fixes.
- **llm_repair.py.** Prompt tells Claude to fix listed syntax errors with the smallest patch,
  and to look for a real cause when syntax was ruled out.

Unchanged: upstream app source in `library/` is never scanned or edited (governing spec).
The LLM tier still needs `ANTHROPIC_API_KEY` and the `anthropic` package on the server.
