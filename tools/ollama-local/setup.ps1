# Sets Claude Code up to run on a free local Ollama model (Windows).
# Run from PowerShell in this folder:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Each step pauses so you can read the output; any error stops the script.

param(
    [string]$Model = "qwen3:8b",
    [int]$ContextLength = 32768
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 otherwise writes arrays as {"value":[...],"Count":n} in ConvertTo-Json
Remove-TypeData System.Array -ErrorAction SilentlyContinue
$MinOllama   = [version]"0.14.0"   # first version with the Anthropic Messages API
$ClaudeDir   = Join-Path $env:USERPROFILE ".claude"
$Settings    = Join-Path $ClaudeDir "settings.json"
$Backup      = Join-Path $ClaudeDir "settings.backup.json"
$OllamaDir   = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$OllamaExe   = Join-Path $OllamaDir "ollama.exe"
$OllamaApp   = Join-Path $OllamaDir "ollama app.exe"
$PanelDir    = Join-Path $env:LOCALAPPDATA "OllamaPanel"

function Step($n, $title) { Write-Host "`n===== STEP $n - $title =====" -ForegroundColor Cyan }
function Done { Read-Host "Press Enter to continue (Ctrl+C to stop)" | Out-Null }
function Fail($msg) { Write-Host "FAILED: $msg" -ForegroundColor Red; Read-Host "Press Enter to exit" | Out-Null; exit 1 }

function Get-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if (Test-Path $OllamaExe) { return $OllamaExe }
    return $null
}

function Wait-Ollama([int]$seconds = 60) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try { Invoke-RestMethod "http://localhost:11434/api/version" -TimeoutSec 2 | Out-Null; return $true } catch { Start-Sleep 1 }
    }
    return $false
}

function Start-Ollama {
    if (Test-Path $OllamaApp) { Start-Process $OllamaApp } else { Start-Process (Get-Ollama) -ArgumentList "serve" -WindowStyle Hidden }
}

try {
    # ---------------------------------------------------------------- 1
    Step 1 "Back up settings"
    New-Item -ItemType Directory -Force $ClaudeDir | Out-Null
    if (Test-Path $Settings) {
        Copy-Item $Settings $Backup -Force
        Write-Host "Backed up to $Backup" -ForegroundColor Green
    } else {
        Write-Host "No settings.json yet - a new one will be created (nothing to back up)."
    }
    Done

    # ---------------------------------------------------------------- 2
    Step 2 "Hardware"
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        nvidia-smi --query-gpu=name,memory.total --format=csv
    } else {
        # AdapterRAM is capped at 4 GB by Windows; the registry value is accurate.
        Get-ItemProperty "HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*" -ErrorAction SilentlyContinue |
            Where-Object { $_.DriverDesc } |
            ForEach-Object { "{0}  {1} GB" -f $_.DriverDesc, [math]::Round(($_."HardwareInformation.qwMemorySize") / 1GB, 1) }
    }
    "System RAM: {0} GB" -f [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free } |
        ForEach-Object { "Drive {0}: {1} GB free" -f $_.Name, [math]::Round($_.Free / 1GB, 1) }
    Done

    # ---------------------------------------------------------------- 3
    Step 3 "Install / update Ollama"
    $exe = Get-Ollama
    $current = $null
    if ($exe) {
        $out = (& $exe --version 2>&1) -join " "
        if ($out -match '(\d+\.\d+\.\d+)') { $current = [version]$Matches[1] }
        Write-Host "Found Ollama $current"
    }
    if (-not $current -or $current -lt $MinOllama) {
        Write-Host "Installing latest Ollama with winget..."
        Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
        if ($current) { winget upgrade -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements }
        else          { winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements }
        $exe = Get-Ollama
        if (-not $exe) { Fail "Ollama did not install. Get it from https://ollama.com/download/windows and re-run." }
        & $exe --version
    }
    if (-not (Wait-Ollama 3)) { Start-Ollama }
    if (-not (Wait-Ollama 60)) { Fail "Ollama is not answering on http://localhost:11434" }
    Write-Host "Ollama is running on http://localhost:11434" -ForegroundColor Green
    Done

    # ---------------------------------------------------------------- 4
    Step 4 "Pull $Model"
    Write-Host "Using $Model (the small model: fast, ~5 GB, fits 8 GB GPUs). Bigger models can be added from the panel."
    & $exe pull $Model
    if ($LASTEXITCODE -ne 0) { Fail "ollama pull $Model failed" }
    Write-Host "Test prompt:"
    & $exe run $Model "Reply with one word: OK"
    if ($LASTEXITCODE -ne 0) { Fail "Test prompt failed" }
    Done

    # ---------------------------------------------------------------- 5
    Step 5 "Context length = $ContextLength"
    [Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "$ContextLength", "User")
    $env:OLLAMA_CONTEXT_LENGTH = "$ContextLength"
    Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    Start-Ollama
    if (-not (Wait-Ollama 60)) { Fail "Ollama did not come back after restart" }
    Write-Host "Ollama restarted with OLLAMA_CONTEXT_LENGTH=$ContextLength" -ForegroundColor Green
    Done

    # ---------------------------------------------------------------- 6
    Step 6 "Point Claude Code at Ollama"
    $cfg = if (Test-Path $Settings) { Get-Content $Settings -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    if (-not $cfg.env) { $cfg | Add-Member -NotePropertyName env -NotePropertyValue ([pscustomobject]@{}) -Force }

    $remove = "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK",
              "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
              "ANTHROPIC_BEDROCK_BASE_URL", "CLAUDE_CODE_SKIP_BEDROCK_AUTH"
    foreach ($k in $remove) { $cfg.env.PSObject.Properties.Remove($k) }
    foreach ($k in "awsAuthRefresh", "awsCredentialExport") { $cfg.PSObject.Properties.Remove($k) }

    $set = [ordered]@{
        ANTHROPIC_BASE_URL                       = "http://localhost:11434"
        ANTHROPIC_AUTH_TOKEN                     = "ollama"
        ANTHROPIC_API_KEY                        = ""
        ANTHROPIC_MODEL                          = $Model
        ANTHROPIC_SMALL_FAST_MODEL               = $Model
        ANTHROPIC_DEFAULT_HAIKU_MODEL            = $Model
        ANTHROPIC_DEFAULT_SONNET_MODEL           = $Model
        ANTHROPIC_DEFAULT_OPUS_MODEL             = $Model
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
    }
    foreach ($k in $set.Keys) { $cfg.env | Add-Member -NotePropertyName $k -NotePropertyValue $set[$k] -Force }

    $json = $cfg | ConvertTo-Json -Depth 50
    [IO.File]::WriteAllText($Settings, $json, (New-Object Text.UTF8Encoding $false))
    Write-Host "Final $Settings :" -ForegroundColor Green
    Get-Content $Settings
    Done

    # ---------------------------------------------------------------- 7
    Step 7 "Auto-start + Model Panel shortcut"
    $startup = [Environment]::GetFolderPath("Startup")
    $shell = New-Object -ComObject WScript.Shell
    if (Test-Path $OllamaApp) {
        $lnk = Join-Path $startup "Ollama.lnk"
        if (-not (Test-Path $lnk)) {
            $s = $shell.CreateShortcut($lnk); $s.TargetPath = $OllamaApp; $s.Save()
        }
        Write-Host "Ollama starts with Windows ($lnk)" -ForegroundColor Green
    } else {
        Write-Host "Could not find '$OllamaApp' - add Ollama to Startup manually." -ForegroundColor Yellow
    }

    $panelSrc = Join-Path $PSScriptRoot "ModelPanel.ps1"
    if (Test-Path $panelSrc) {
        New-Item -ItemType Directory -Force $PanelDir | Out-Null
        Copy-Item $panelSrc $PanelDir -Force
        Unblock-File (Join-Path $PanelDir "ModelPanel.ps1")
        $targets = @(
            (Join-Path ([Environment]::GetFolderPath("Desktop")) "Ollama Model Panel.lnk"),
            (Join-Path ([Environment]::GetFolderPath("Programs")) "Ollama Model Panel.lnk")
        )
        foreach ($t in $targets) {
            $s = $shell.CreateShortcut($t)
            $s.TargetPath = "powershell.exe"
            $s.Arguments  = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PanelDir\ModelPanel.ps1`""
            if (Test-Path $OllamaApp) { $s.IconLocation = "$OllamaApp,0" }
            $s.Save()
        }
        Write-Host "Model Panel shortcut added to Desktop and Start menu (right-click it > Pin to taskbar)." -ForegroundColor Green
    } else {
        Write-Host "ModelPanel.ps1 not found next to setup.ps1 - skipping panel." -ForegroundColor Yellow
    }

    Write-Host "`nAll done. Close this window, open a NEW terminal and run: claude" -ForegroundColor Cyan
    Write-Host "To go back:  Copy-Item `"$Backup`" `"$Settings`" -Force"
    Read-Host "Press Enter to exit" | Out-Null
}
catch {
    Fail $_.Exception.Message
}
