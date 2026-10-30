# Ollama Model Panel - pick a downloaded model, spin it up on the GPU,
# and launch Claude Code with it. Started from the "Ollama Model Panel" shortcut.

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Remove-TypeData System.Array -ErrorAction SilentlyContinue

$Api       = "http://localhost:11434"
$Settings  = Join-Path $env:USERPROFILE ".claude\settings.json"
$OllamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
$OllamaExe = if (Get-Command ollama -ErrorAction SilentlyContinue) { "ollama" } else { Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
$script:Jobs = @()

function Test-Ollama { try { Invoke-RestMethod "$Api/api/version" -TimeoutSec 2 | Out-Null; $true } catch { $false } }

function Get-DefaultModel {
    try { (Get-Content $Settings -Raw | ConvertFrom-Json).env.ANTHROPIC_MODEL } catch { $null }
}

function Get-Selected {
    if ($list.SelectedItems.Count -eq 0) {
        [Windows.Forms.MessageBox]::Show("Pick a model in the list first.", "Ollama Model Panel") | Out-Null
        return $null
    }
    $list.SelectedItems[0].Tag
}

function Refresh-Models {
    $list.Items.Clear()
    if (-not (Test-Ollama)) { $status.Text = "Ollama is not running."; return }
    $default = Get-DefaultModel
    foreach ($m in (Invoke-RestMethod "$Api/api/tags").models | Sort-Object size) {
        $label = if ($m.name -eq $default) { "$($m.name)  (default)" } else { $m.name }
        $item = New-Object Windows.Forms.ListViewItem $label
        $item.Tag = $m.name
        [void]$item.SubItems.Add(("{0:N1} GB" -f ($m.size / 1GB)))
        [void]$item.SubItems.Add("")
        [void]$list.Items.Add($item)
        if ($m.name -eq $default) { $item.Selected = $true }
    }
    Refresh-Status
}

function Refresh-Status {
    if (-not (Test-Ollama)) { $status.Text = "Ollama is not running - click Refresh to start it."; return }
    $loaded = @((Invoke-RestMethod "$Api/api/ps").models)
    foreach ($item in $list.Items) {
        $hit = $loaded | Where-Object { $_.name -eq $item.Tag }
        $item.SubItems[2].Text = if ($hit) {
            $pct = if ($hit.size) { [math]::Round(100 * $hit.size_vram / $hit.size) } else { 0 }
            "running ($pct% GPU)"
        } else { "" }
    }
    $text = if ($loaded.Count) { "Spun up: " + (($loaded | ForEach-Object { $_.name }) -join ", ") } else { "No model loaded on the GPU." }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $v = (nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits | Select-Object -First 1) -split ",\s*"
        if ($v.Count -eq 2) { $text += "   |   VRAM {0:N1} / {1:N1} GB" -f ($v[0] / 1024), ($v[1] / 1024) }
    }
    $busy = @($script:Jobs | Where-Object { $_.State -eq "Running" }).Count
    if ($busy) { $text += "   |   spinning up..." }
    foreach ($j in @($script:Jobs | Where-Object { $_.State -in "Completed", "Failed" })) {
        if ($j.State -eq "Failed" -or $j.ChildJobs[0].Error.Count) {
            [Windows.Forms.MessageBox]::Show("Could not load the model:`n" + ($j.ChildJobs[0].JobStateInfo.Reason, $j.ChildJobs[0].Error | Out-String), "Ollama Model Panel") | Out-Null
        }
        Remove-Job $j -Force
        $script:Jobs = @($script:Jobs | Where-Object { $_.Id -ne $j.Id })
    }
    $status.Text = $text
}

function Set-KeepAlive($model, $keepAlive) {
    $body = @{ model = $model; prompt = ""; keep_alive = $keepAlive } | ConvertTo-Json
    Invoke-RestMethod -Method Post "$Api/api/generate" -Body $body -ContentType "application/json" -TimeoutSec 900 | Out-Null
}

# ------------------------------------------------------------------ UI
$form = New-Object Windows.Forms.Form
$form.Text = "Ollama Model Panel"
$form.Size = New-Object Drawing.Size 560, 470
$form.StartPosition = "CenterScreen"
$form.Font = New-Object Drawing.Font "Segoe UI", 10
if (Test-Path $OllamaApp) { $form.Icon = [Drawing.Icon]::ExtractAssociatedIcon($OllamaApp) }

$list = New-Object Windows.Forms.ListView
$list.View = "Details"; $list.FullRowSelect = $true; $list.MultiSelect = $false; $list.HideSelection = $false
$list.Location = New-Object Drawing.Point 12, 12
$list.Size = New-Object Drawing.Size 520, 210
[void]$list.Columns.Add("Downloaded model", 260)
[void]$list.Columns.Add("Size", 90)
[void]$list.Columns.Add("GPU", 150)
$form.Controls.Add($list)

$status = New-Object Windows.Forms.Label
$status.Location = New-Object Drawing.Point 12, 228
$status.Size = New-Object Drawing.Size 520, 40
$form.Controls.Add($status)

function Add-Button($text, $x, $y, $w, $onClick) {
    $b = New-Object Windows.Forms.Button
    $b.Text = $text; $b.Location = New-Object Drawing.Point $x, $y; $b.Size = New-Object Drawing.Size $w, 36
    $b.Add_Click($onClick); $form.Controls.Add($b); $b
}

$spin = Add-Button "Spin up on GPU" 12 272 200 {
    $m = Get-Selected; if (-not $m) { return }
    $script:Jobs += Start-Job -ArgumentList $Api, $m -ScriptBlock {
        param($api, $model)
        $body = @{ model = $model; prompt = ""; keep_alive = "30m" } | ConvertTo-Json
        Invoke-RestMethod -Method Post "$api/api/generate" -Body $body -ContentType "application/json" -TimeoutSec 900 | Out-Null
    }
    Refresh-Status
}
$spin.BackColor = [Drawing.Color]::FromArgb(46, 125, 50); $spin.ForeColor = [Drawing.Color]::White
$spin.Font = New-Object Drawing.Font "Segoe UI", 10, ([Drawing.FontStyle]::Bold)

Add-Button "Unload" 220 272 150 {
    $m = Get-Selected; if (-not $m) { return }
    $loaded = @((Invoke-RestMethod "$Api/api/ps").models | ForEach-Object { $_.name })
    if ($loaded -contains $m) { try { Set-KeepAlive $m 0 } catch { } }
    Refresh-Status
} | Out-Null

Add-Button "Refresh" 378 272 154 {
    if (-not (Test-Ollama) -and (Test-Path $OllamaApp)) {
        Start-Process $OllamaApp
        for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep 1 }
    }
    Refresh-Models
} | Out-Null

Add-Button "Launch Claude Code with this model" 12 316 300 {
    $m = Get-Selected; if (-not $m) { return }
    $pick = New-Object Windows.Forms.FolderBrowserDialog
    $pick.Description = "Folder to open Claude Code in"
    $pick.SelectedPath = $env:USERPROFILE
    $dir = if ($pick.ShowDialog() -eq "OK") { $pick.SelectedPath } else { $env:USERPROFILE }
    Start-Process powershell.exe -WorkingDirectory $dir -ArgumentList "-NoExit", "-Command", "claude --model '$m'"
} | Out-Null

Add-Button "Make default" 320 316 212 {
    $m = Get-Selected; if (-not $m) { return }
    $cfg = Get-Content $Settings -Raw | ConvertFrom-Json
    foreach ($k in "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL") {
        $cfg.env | Add-Member -NotePropertyName $k -NotePropertyValue $m -Force
    }
    [IO.File]::WriteAllText($Settings, ($cfg | ConvertTo-Json -Depth 50), (New-Object Text.UTF8Encoding $false))
    Refresh-Models
} | Out-Null

$dl = New-Object Windows.Forms.TextBox
$dl.Location = New-Object Drawing.Point 12, 368
$dl.Size = New-Object Drawing.Size 300, 30
$dl.Text = "qwen3:14b"
$form.Controls.Add($dl)

Add-Button "Download model" 320 362 212 {
    $name = $dl.Text.Trim(); if (-not $name) { return }
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "& '$OllamaExe' pull '$name'; Write-Host 'Done - click Refresh in the panel.'"
} | Out-Null

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 3000
$timer.Add_Tick({ try { Refresh-Status } catch { } })
$timer.Start()

$form.Add_Shown({ try { Refresh-Models } catch { $status.Text = $_.Exception.Message } })
[void]$form.ShowDialog()
Get-Job | Remove-Job -Force
