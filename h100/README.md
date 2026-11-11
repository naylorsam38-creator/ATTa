# AIRexploit H100 pack (separate from the ATTa app in the rest of this repository)

- `baseline/H100_wired.zip`: the pack exactly as received (never overwritten).
- `airexploit/`: the pack source being built (start with `airexploit/CLAUDE.md`, then `airexploit/BUILD_STATE.md`).
- `dist/`: the delivered H100 zips with their SHA-256 and release notes.

The repository's .gitignore drops `__pycache__`/`*.pyc`; the original pack contains one such file
(`remote/__pycache__/...`), which is therefore only in the baseline zip and in the delivered zips, not in git.
