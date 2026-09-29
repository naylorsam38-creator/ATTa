# ATTa v121 collect-all-adaptive -> the 500 GB target, from the Windows laptop.
#
# One command (PowerShell):
#   $f="$env:TEMP\atta-deploy.ps1"; iwr -UseBasicParsing https://raw.githubusercontent.com/naylorsam38-creator/ATTa/claude/move-atta-to-500gb-ec2-fkvkit/tools/deploy-from-laptop.ps1 -OutFile $f; powershell -ExecutionPolicy Bypass -File $f -Email you@yourdomain.com
#
# Finds ATTa-v121-collect-all-adaptive.zip on this laptop, checks it is the exact bundle, uploads it
# with the server-side script and the shared kit fix, and starts the deploy ON the server as a
# systemd job (it keeps going if this window or the SSH connection closes). Streams its progress.
# -Email is only used if Coolify has to be installed (its admin account needs a real address).
param(
    [string]$Email = "",
    [string]$Key = "$env:USERPROFILE\Downloads\airexploit-key.pem",
    [string]$Server = "ubuntu@16.26.56.70",
    [string]$Zip = "",
    [string]$Domain = "airexploit.com,www.airexploit.com",
    [string]$Branch = "claude/move-atta-to-500gb-ec2-fkvkit",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$BundleSha = "ccca34de3988653d4af13367dd86b5ae0297c16faffa5f784a2fbf858b31c601"
$Raw = "https://raw.githubusercontent.com/naylorsam38-creator/ATTa/$Branch/tools"
function Say($m) { Write-Host "[laptop] $m" -ForegroundColor Cyan }

if (-not $Zip) {
    $roots = @("$env:USERPROFILE\Downloads", "$env:USERPROFILE\Desktop", "$env:USERPROFILE\Documents") | Where-Object { $_ -and (Test-Path $_) }
    $Zip = Get-ChildItem -Path $roots -Filter "*ATTa-v121-collect-all-adaptive*.zip" -Recurse -Depth 3 -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Zip -or -not (Test-Path $Zip)) { throw "ATTa-v121-collect-all-adaptive.zip not found in Downloads/Desktop/Documents. Run again with -Zip <full path>" }
$h = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLower()
if ($h -ne $BundleSha) { throw "$Zip has checksum $h - not the expected ATTa-v121-collect-all-adaptive.zip" }
Say "bundle: $Zip (checksum OK)"
if (-not (Test-Path $Key)) { throw "SSH key not found: $Key (run again with -Key <path>)" }

$tmp = Join-Path $env:TEMP "atta-deploy"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Invoke-WebRequest -UseBasicParsing "$Raw/atta-deploy-target.sh" -OutFile "$tmp\atta-deploy-target.sh"
Invoke-WebRequest -UseBasicParsing "$Raw/patches/v121-kit-registry-mirror-domains.patch" -OutFile "$tmp\v121-kit.patch"
Say "downloaded the server-side script and the shared kit fix"

$o = @("-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=15", "-i", $Key)
& ssh @o $Server "echo connected to `$(hostname)"
if ($LASTEXITCODE -ne 0) { throw "SSH to $Server failed (key, username or port 22). Nothing was changed." }
Say "uploading (about 25 MB)..."
& scp @o $Zip "$tmp\atta-deploy-target.sh" "$tmp\v121-kit.patch" "${Server}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw "upload to $Server failed. Nothing was changed." }

$zipName = Split-Path $Zip -Leaf
$flags = "--zip '/tmp/$zipName' --patch /tmp/v121-kit.patch --domain '$Domain'"
if ($Email) { $flags += " --admin-email '$Email'" }
if ($DryRun) { $flags += " --dry-run" }
$remote = @"
set -u
L=/var/log/atta-deploy-target.log
n=`$(sudo sh -c "wc -l < `$L" 2>/dev/null || echo 0)
if sudo systemctl is-active --quiet atta-deploy; then echo '[server] a deploy is already running - following it'; else
  sudo systemctl reset-failed atta-deploy >/dev/null 2>&1 || true
  sudo systemd-run --unit=atta-deploy --collect --quiet bash /tmp/atta-deploy-target.sh $flags || { echo '[server] could not start the deploy job'; exit 1; }
fi
sleep 2
sudo tail -n +`$((n+1)) -F `$L 2>/dev/null & T=`$!
while sudo systemctl is-active --quiet atta-deploy; do sleep 5; done
sleep 3; kill `$T 2>/dev/null
"@
Say "starting the deploy on the server (it continues even if this window closes)"
& ssh @o $Server ($remote -replace "`r", "")
Say "finished. To follow a running deploy again:  ssh -i `"$Key`" $Server `"sudo tail -f /var/log/atta-deploy-target.log`""
