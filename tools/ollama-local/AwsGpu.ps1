# Rents an AWS GPU server running Ollama, reached over an SSH tunnel, for Claude Code.
# This is the PAID option - the free local setup is untouched.
#   powershell -ExecutionPolicy Bypass -File .\AwsGpu.ps1 -Action Start
#   powershell -ExecutionPolicy Bypass -File .\AwsGpu.ps1 -Action Stop
#   powershell -ExecutionPolicy Bypass -File .\AwsGpu.ps1 -Action Status
# Uses the AWS account configured on this PC with `aws configure`.

param(
    [ValidateSet("Start", "Stop", "Status")] [string]$Action = "Status",
    [string]$Model = "qwen3-coder:30b",
    [string]$InstanceType = "g5.xlarge",
    [int]$MaxHours = 4,
    [switch]$Spot,
    [string]$ProjectDir = $env:USERPROFILE
)

$ErrorActionPreference = "Stop"
Remove-TypeData System.Array -ErrorAction SilentlyContinue

$Name      = "ollama-gpu"
$LocalPort = 11435
$StateDir  = Join-Path $env:LOCALAPPDATA "OllamaPanel\aws"
$Pem       = Join-Path $StateDir "$Name.pem"
$StateFile = Join-Path $StateDir "state.json"
$SshExe    = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
# Approximate us-east-1 on-demand USD/hour (spot is usually 50-70% less)
$Rates = @{ "g5.xlarge" = 1.01; "g5.2xlarge" = 1.21; "g6.xlarge" = 0.81; "g6e.xlarge" = 1.86 }

New-Item -ItemType Directory -Force $StateDir | Out-Null

function Fail($msg) { Write-Host "FAILED: $msg" -ForegroundColor Red; exit 1 }

# Runs the aws CLI; Windows PowerShell treats native stderr as an error under "Stop".
function Aws {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $out = & aws @args 2>&1
    $code = $LASTEXITCODE; $ErrorActionPreference = $old
    $text = (($out | ForEach-Object { "$_" }) -join "`n").Trim()
    if ($code -ne 0) { throw "aws $($args[0]) $($args[1]): $text" }
    $text
}

function Write-Ascii($path, $text) { [IO.File]::WriteAllText($path, ($text -replace "`r", ""), [Text.Encoding]::ASCII) }

function Get-Instances {
    $ids = Aws ec2 describe-instances --filters "Name=tag:Name,Values=$Name" "Name=instance-state-name,Values=pending,running" `
        --query "Reservations[].Instances[].InstanceId" --output text
    @($ids -split "\s+" | Where-Object { $_ })
}

function Stop-Tunnel {
    if (Test-Path $StateFile) {
        $s = Get-Content $StateFile -Raw | ConvertFrom-Json
        if ($s.sshPid) { Stop-Process -Id $s.sshPid -Force -ErrorAction SilentlyContinue }
    }
}

function Test-Remote {
    try { $t = Invoke-RestMethod "http://localhost:$LocalPort/api/tags" -TimeoutSec 5; return @($t.models | ForEach-Object { $_.name }) }
    catch { return $null }
}

# ---------------------------------------------------------------- prerequisites
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Fail "AWS CLI not installed. Run:  winget install -e --id Amazon.AWSCLI   then open a new window and run:  aws configure"
}
try { $account = Aws sts get-caller-identity --query Account --output text }
catch { Fail "AWS CLI has no working credentials. Run:  aws configure`n$($_.Exception.Message)" }
$region = try { Aws configure get region } catch { "" }
if (-not $region) { $region = "us-east-1"; $env:AWS_DEFAULT_REGION = $region }
Write-Host "AWS account $account, region $region"

# ---------------------------------------------------------------- Status
if ($Action -eq "Status") {
    $ids = Get-Instances
    if (-not $ids) { Write-Host "No AWS GPU rented right now - you are not being charged for one." -ForegroundColor Green; exit 0 }
    foreach ($id in $ids) {
        $info = Aws ec2 describe-instances --instance-ids $id --query "Reservations[0].Instances[0].[InstanceType,LaunchTime,PublicIpAddress]" --output text
        $type, $launch, $ip = $info -split "\s+"
        $hours = ((Get-Date).ToUniversalTime() - ([datetime]$launch).ToUniversalTime()).TotalHours
        $rate = $Rates[$type]
        $cost = if ($rate) { " (about `${0:N2} so far)" -f ($hours * $rate) } else { "" }
        Write-Host ("RUNNING {0} {1} at {2}, up {3:N1} h{4}" -f $id, $type, $ip, $hours, $cost) -ForegroundColor Yellow
    }
    $models = Test-Remote
    if ($models) { Write-Host "Tunnel OK on localhost:$LocalPort, models: $($models -join ', ')" } else { Write-Host "Tunnel not connected." }
    exit 0
}

# ---------------------------------------------------------------- Stop
if ($Action -eq "Stop") {
    Stop-Tunnel
    $ids = Get-Instances
    if ($ids) {
        Aws ec2 terminate-instances --instance-ids @ids --output text | Out-Null
        Write-Host "Terminated $($ids -join ', '). Billing stops within a minute." -ForegroundColor Green
    } else {
        Write-Host "Nothing was running." -ForegroundColor Green
    }
    Remove-Item $StateFile -ErrorAction SilentlyContinue
    exit 0
}

# ---------------------------------------------------------------- Start
if (-not (Test-Path $SshExe)) { Fail "OpenSSH client missing. Settings > System > Optional features > Add 'OpenSSH Client'." }

$id = (Get-Instances | Select-Object -First 1)
if (-not $id) {
    $rate = $Rates[$InstanceType]
    $price = if ($rate) { "about `$$rate/hour" } else { "see aws.amazon.com/ec2/pricing" }
    if ($Spot) { $price += " on-demand, less as spot" }
    Write-Host ""
    Write-Host "This RENTS a $InstanceType GPU server in $region on AWS account $account - $price." -ForegroundColor Yellow
    Write-Host "It shuts itself down after $MaxHours hours. Use 'Stop AWS GPU' when you're done." -ForegroundColor Yellow
    if ((Read-Host "Type YES to rent it") -ne "YES") { Write-Host "Cancelled - nothing rented."; exit 0 }

    # Key pair (recreated if the local .pem is missing)
    $haveKey = $true
    try { Aws ec2 describe-key-pairs --key-names $Name | Out-Null } catch { $haveKey = $false }
    if ($haveKey -and -not (Test-Path $Pem)) { Aws ec2 delete-key-pair --key-name $Name | Out-Null; $haveKey = $false }
    if (-not $haveKey) {
        Write-Ascii $Pem ((Aws ec2 create-key-pair --key-name $Name --query KeyMaterial --output text) + "`n")
        icacls $Pem /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
    }

    # Security group: SSH from this PC's public IP only. Ollama itself is never exposed.
    $vpc = Aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text
    if ($vpc -eq "None") { Fail "No default VPC in $region. Create one in the VPC console (Actions > Create default VPC)." }
    $sg = Aws ec2 describe-security-groups --filters "Name=group-name,Values=$Name-sg" "Name=vpc-id,Values=$vpc" --query "SecurityGroups[0].GroupId" --output text
    if ($sg -eq "None") {
        $sg = Aws ec2 create-security-group --group-name "$Name-sg" --description "SSH-only access for Ollama GPU" --vpc-id $vpc --query GroupId --output text
    }
    $myIp = (Invoke-RestMethod "https://checkip.amazonaws.com").Trim()
    try { Aws ec2 authorize-security-group-ingress --group-id $sg --protocol tcp --port 22 --cidr "$myIp/32" | Out-Null } catch { }

    # GPU AMI with NVIDIA drivers preinstalled
    $ami = Aws ssm get-parameter --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id --query Parameter.Value --output text

    $userData = Join-Path $StateDir "user-data.sh"
    Write-Ascii $userData @"
#!/bin/bash
shutdown -h +$($MaxHours * 60)
curl -fsSL https://ollama.com/install.sh | sh
mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_CONTEXT_LENGTH=32768"\nEnvironment="OLLAMA_KEEP_ALIVE=-1"\n' > /etc/systemd/system/ollama.service.d/override.conf
systemctl daemon-reload
systemctl restart ollama
sleep 5
ollama pull $Model
"@
    $bdm = Join-Path $StateDir "disk.json"
    Write-Ascii $bdm '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]'

    $runArgs = @("ec2", "run-instances", "--image-id", $ami, "--instance-type", $InstanceType, "--key-name", $Name,
        "--security-group-ids", $sg, "--user-data", "file://$userData", "--block-device-mappings", "file://$bdm",
        "--instance-initiated-shutdown-behavior", "terminate",
        "--tag-specifications", "ResourceType=instance,Tags=[{Key=Name,Value=$Name}]",
        "--query", "Instances[0].InstanceId", "--output", "text")
    if ($Spot) { $runArgs += "--instance-market-options", "MarketType=spot,SpotOptions={SpotInstanceType=one-time}" }
    try { $id = Aws @runArgs }
    catch {
        $m = $_.Exception.Message
        if ($m -match "VcpuLimitExceeded|MaxSpotInstanceCountExceeded") {
            Fail ("Your AWS account isn't allowed GPU servers yet. In the AWS console open Service Quotas > Amazon EC2 > " +
                  "'Running On-Demand G and VT instances' (or 'All G and VT Spot Instance Requests' for -Spot) and request 4 or more vCPUs. Approval can take a day.")
        }
        Fail $m
    }
    Write-Host "Rented $id. Waiting for it to boot..."
}
else {
    Write-Host "Reusing already-running $id"
}

Aws ec2 wait instance-running --instance-ids $id | Out-Null
$ip = Aws ec2 describe-instances --instance-ids $id --query "Reservations[0].Instances[0].PublicIpAddress" --output text

# SSH tunnel localhost:11435 -> server's Ollama on 127.0.0.1:11434
Stop-Tunnel
$known = Join-Path $StateDir "known_hosts_$id"
$sshArgs = "-i `"$Pem`" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=`"$known`" -o ConnectTimeout=5"
Write-Host "Waiting for SSH on $ip..."
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
    $p = Start-Process $SshExe -ArgumentList "$sshArgs -o BatchMode=yes ubuntu@$ip exit" -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -eq 0) { $ok = $true } else { Start-Sleep 5 }
}
if (-not $ok) { Fail "Could not SSH to $ip. Run Stop to avoid charges, then try again." }
$tunnel = Start-Process $SshExe -WindowStyle Hidden -PassThru -ArgumentList `
    "$sshArgs -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L ${LocalPort}:127.0.0.1:11434 ubuntu@$ip"
@{ instanceId = $id; ip = $ip; sshPid = $tunnel.Id; model = $Model } | ConvertTo-Json | Set-Content $StateFile

Write-Host "Installing Ollama and downloading $Model on the server (first time ~5-10 min)..."
$deadline = (Get-Date).AddMinutes(30)
while ((Get-Date) -lt $deadline) {
    $models = Test-Remote
    if ($models -contains $Model) { break }
    Write-Host -NoNewline "."; Start-Sleep 10
}
Write-Host ""
if (-not ((Test-Remote) -contains $Model)) { Fail "$Model never appeared on the server. Run Stop to avoid charges." }

# Claude Code settings override for this launch only (your normal settings.json still points at local Ollama)
$cloudSettings = Join-Path $StateDir "claude-aws-settings.json"
$envVars = [ordered]@{ ANTHROPIC_BASE_URL = "http://localhost:$LocalPort" }
foreach ($k in "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
               "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL") { $envVars[$k] = $Model }
@{ env = $envVars } | ConvertTo-Json | Set-Content $cloudSettings

Write-Host "AWS GPU ready. Starting Claude Code on $Model. REMEMBER: click 'Stop AWS GPU' when done." -ForegroundColor Green
Set-Location $ProjectDir
claude --settings $cloudSettings --model $Model
