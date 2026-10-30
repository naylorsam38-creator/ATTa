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

## Optional: rent an AWS GPU (paid)

The free local setup stays the default. For a bigger model, the panel's orange
**Rent AWS GPU** button runs `AwsGpu.ps1 -Action Start`:

- Needs the AWS CLI on your PC (`winget install -e --id Amazon.AWSCLI`, then `aws configure`)
  and a GPU quota on your account (Service Quotas > EC2 > "Running On-Demand G and VT instances" >= 4).
- Rents a `g5.xlarge` (24 GB GPU, ~$1/hour) only after you type `YES`, installs Ollama and
  `qwen3-coder:30b`, and opens Claude Code on it through an SSH tunnel (`localhost:11435`).
  Ollama is never exposed to the internet; SSH is allowed from your IP only.
- The server **shuts itself down after 4 hours** and is deleted (no leftover disk charges).
- Red **Stop AWS GPU** ends it immediately; **AWS status / cost** shows what's running and the cost so far.
- Your normal `settings.json` keeps pointing at local Ollama; the AWS run uses a separate `--settings` file.
