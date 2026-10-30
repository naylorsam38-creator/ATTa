# Claude Code on a local Ollama model (Windows)

1. Download this folder (`setup.ps1` + `ModelPanel.ps1`) to your PC.
2. In PowerShell, in that folder:
   `powershell -ExecutionPolicy Bypass -File .\setup.ps1`
   It backs up `~/.claude/settings.json`, installs/updates Ollama, pulls `qwen3:8b`,
   sets `OLLAMA_CONTEXT_LENGTH=32768`, points Claude Code at Ollama, enables auto-start,
   and adds an **Ollama Model Panel** shortcut (Desktop + Start menu).
   Pausing after each step; any error stops it.
3. Open a new terminal and run `claude`, then `/status` to confirm the model.

**Model Panel:** lists downloaded models; *Spin up on GPU* loads one into VRAM,
*Launch Claude Code with this model* opens Claude Code on it, *Make default* saves it,
*Download model* pulls another (e.g. `qwen3:14b`, `qwen3-coder:30b`).

**Restore original settings:**
`Copy-Item "$env:USERPROFILE\.claude\settings.backup.json" "$env:USERPROFILE\.claude\settings.json" -Force`
