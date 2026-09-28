# ============================================================
# ATTa AWS READ-ONLY INVENTORY  (v2)
# Finds the EC2 instance(s) with ~500 GB of attached EBS.
# Read-only: only describe-* / get-caller-identity calls.
# Works in Windows PowerShell 5.1 and PowerShell 7+.
# ============================================================

$ErrorActionPreference = "Continue"
$KnownIPs   = @("13.210.123.184", "34.199.46.201")
$LargeGB    = 400
$ReportDir  = Join-Path $env:USERPROFILE "Desktop\ATTa-AWS-Inventory"
$ReportTxt  = Join-Path $ReportDir "aws_inventory.txt"
$ReportCsv  = Join-Path $ReportDir "aws_inventory.csv"
$script:LastAwsError = ""

function Invoke-AwsJson {
    # Runs an aws CLI command and returns parsed JSON, or $null on failure.
    # Joins output lines first: Windows PowerShell 5.1 can mis-parse
    # multi-line JSON piped line-by-line into ConvertFrom-Json.
    param([string[]]$AwsArgs)
    $raw = & aws @AwsArgs --output json 2>&1
    # Keep stderr (warnings/errors) out of the JSON text.
    $err = @($raw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })
    $out = @($raw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })
    if ($LASTEXITCODE -ne 0) {
        $script:LastAwsError = (($err + $out) | Out-String).Trim()
        return $null
    }
    try { return (($out -join "`n") | ConvertFrom-Json) }
    catch { $script:LastAwsError = "JSON parse failed: $_"; return $null }
}

Write-Host "`n==== ATTa AWS READ-ONLY INVENTORY ====`n" -ForegroundColor Cyan

# [1] Identity - only log in if not already authenticated
Write-Host "[1/6] Checking AWS identity..." -ForegroundColor Yellow
$Identity = Invoke-AwsJson @("sts", "get-caller-identity")
if (-not $Identity) {
    Write-Host "Not logged in - running 'aws login' (needs AWS CLI v2.32+)..." -ForegroundColor Yellow
    aws login
    $Identity = Invoke-AwsJson @("sts", "get-caller-identity")
}
if (-not $Identity) {
    Write-Host "AWS identity check failed:`n$LastAwsError" -ForegroundColor Red
    Write-Host "If 'aws login' is unknown, update the AWS CLI or use 'aws configure' / 'aws sso login'."
    exit 1
}
Write-Host "Account: $($Identity.Account)   Principal: $($Identity.Arn)" -ForegroundColor Green

New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
@(
    "ATTa AWS READ-ONLY INVENTORY",
    "Generated: $(Get-Date -Format o)",
    "Account:   $($Identity.Account)",
    "Principal: $($Identity.Arn)",
    "=============================================="
) | Out-File $ReportTxt -Encoding utf8

# [2] Regions - explicit region so it works with no default region configured
Write-Host "`n[2/6] Getting enabled regions..." -ForegroundColor Yellow
$RegionData = Invoke-AwsJson @("ec2", "describe-regions", "--region", "us-east-1")
if (-not $RegionData) { Write-Host "Could not list regions:`n$LastAwsError" -ForegroundColor Red; exit 1 }
$RegionList = @($RegionData.Regions | ForEach-Object { $_.RegionName } | Sort-Object)
Write-Host "Regions: $($RegionList.Count)" -ForegroundColor Green

# [3] Scan - one call per resource type per region (not one per volume)
Write-Host "`n[3/6] Scanning instances, volumes and Elastic IPs..." -ForegroundColor Yellow
$Results       = New-Object System.Collections.Generic.List[object]
$LooseVolumes  = New-Object System.Collections.Generic.List[object]
$FailedRegions = New-Object System.Collections.Generic.List[string]

foreach ($Region in $RegionList) {
    Write-Host "  $Region ..." -ForegroundColor DarkGray -NoNewline

    $Inst = Invoke-AwsJson @("ec2", "describe-instances", "--region", $Region)
    $Vols = Invoke-AwsJson @("ec2", "describe-volumes",   "--region", $Region)
    $Eips = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $Region)
    if (-not $Inst -or -not $Vols) {
        # Never silently skip: a skipped region could hide the target.
        $FailedRegions.Add("$Region : $LastAwsError")
        Write-Host " FAILED" -ForegroundColor Red
        continue
    }

    $VolById = @{}
    foreach ($v in $Vols.Volumes) { $VolById[$v.VolumeId] = $v }

    $EipByInstance = @{}
    if ($Eips) { foreach ($a in $Eips.Addresses) { if ($a.InstanceId) { $EipByInstance[$a.InstanceId] = $a.PublicIp } } }

    $count = 0
    foreach ($Res in $Vols.Volumes | Where-Object { -not $_.Attachments -or $_.Attachments.Count -eq 0 }) {
        if ([int]$Res.Size -ge $LargeGB) {
            $LooseVolumes.Add([PSCustomObject]@{ Region=$Region; VolumeId=$Res.VolumeId; SizeGB=[int]$Res.Size; State=$Res.State; AZ=$Res.AvailabilityZone })
        }
    }

    foreach ($Reservation in $Inst.Reservations) {
        foreach ($I in $Reservation.Instances) {
            $count++
            $Name = ($I.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value

            $TotalGB = 0; $Detail = @()
            foreach ($M in $I.BlockDeviceMappings) {
                if ($M.Ebs -and $M.Ebs.VolumeId -and $VolById.ContainsKey($M.Ebs.VolumeId)) {
                    $v = $VolById[$M.Ebs.VolumeId]
                    $TotalGB += [int]$v.Size
                    $Detail  += "$($M.DeviceName):$($v.VolumeId)=$($v.Size)GB/$($v.VolumeType)"
                }
            }

            # A stopped instance has no auto-assigned public IP; its Elastic IP still identifies it.
            $ElasticIP = $EipByInstance[$I.InstanceId]
            $AllIPs    = @($I.PublicIpAddress, $ElasticIP) | Where-Object { $_ }

            $Marker = @()
            if ($TotalGB -ge $LargeGB) { $Marker += "LARGE-STORAGE" }
            foreach ($ip in $KnownIPs) { if ($AllIPs -contains $ip) { $Marker += "KNOWN-IP:$ip" } }

            $Results.Add([PSCustomObject]@{
                Region      = $Region
                AZ          = $I.Placement.AvailabilityZone
                Name        = $Name
                InstanceId  = $I.InstanceId
                State       = $I.State.Name
                Type        = $I.InstanceType
                PublicIP    = $I.PublicIpAddress
                ElasticIP   = $ElasticIP
                PublicDNS   = $I.PublicDnsName
                PrivateIP   = $I.PrivateIpAddress
                TotalEBS_GB = $TotalGB
                Volumes     = ($Detail -join ", ")
                KeyName     = $I.KeyName
                ImageId     = $I.ImageId
                Platform    = $I.PlatformDetails
                LaunchTime  = $I.LaunchTime
                Marker      = ($Marker -join " ")
            })
        }
    }
    Write-Host " $count instance(s)" -ForegroundColor DarkGray
}

$Results | Sort-Object TotalEBS_GB -Descending | Export-Csv $ReportCsv -NoTypeInformation -Encoding utf8
$Results | Sort-Object TotalEBS_GB -Descending | Format-List | Out-String -Width 400 | Out-File $ReportTxt -Append -Encoding utf8

# [4] Everything
Write-Host "`n[4/6] ALL INSTANCES ($($Results.Count))" -ForegroundColor Cyan
$Results | Sort-Object TotalEBS_GB -Descending |
    Format-Table Region, Name, InstanceId, State, Type, PublicIP, ElasticIP, TotalEBS_GB, KeyName, Marker -AutoSize |
    Out-String -Width 400 | Write-Host

# [5] Large machines
Write-Host "[5/6] INSTANCES WITH >= $LargeGB GB EBS" -ForegroundColor Green
$Large = @($Results | Where-Object { $_.TotalEBS_GB -ge $LargeGB } | Sort-Object TotalEBS_GB -Descending)
if ($Large.Count -eq 0) { Write-Host "None found in scanned regions." -ForegroundColor Red }
else { $Large | Format-List Region, AZ, Name, InstanceId, State, Type, PublicIP, ElasticIP, PublicDNS, TotalEBS_GB, Volumes, KeyName, ImageId, Platform, LaunchTime | Out-String -Width 400 | Write-Host }

if ($LooseVolumes.Count -gt 0) {
    Write-Host "UNATTACHED VOLUMES >= $LargeGB GB (not on any instance):" -ForegroundColor Yellow
    $LooseVolumes | Format-Table -AutoSize | Out-String -Width 400 | Write-Host
}

# [6] Known IPs
Write-Host "[6/6] KNOWN IP ADDRESSES" -ForegroundColor Cyan
foreach ($IP in $KnownIPs) {
    $Hit = @($Results | Where-Object { $_.PublicIP -eq $IP -or $_.ElasticIP -eq $IP })
    if ($Hit.Count -gt 0) {
        $Hit | Format-List Region, Name, InstanceId, State, Type, PublicIP, ElasticIP, TotalEBS_GB, Volumes, KeyName | Out-String -Width 400 | Write-Host
    }
    elseif ($FailedRegions.Count -gt 0) {
        Write-Host "$IP : not found, BUT some regions failed - result is inconclusive." -ForegroundColor Yellow
    }
    else {
        Write-Host "$IP : not attached to any instance in account $($Identity.Account)." -ForegroundColor Yellow
    }
}

if ($FailedRegions.Count -gt 0) {
    Write-Host "`nREGIONS THAT COULD NOT BE SCANNED (inventory incomplete):" -ForegroundColor Red
    $FailedRegions | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    "`nFAILED REGIONS:`n" + ($FailedRegions -join "`n") | Out-File $ReportTxt -Append -Encoding utf8
}

Write-Host "`n==== INVENTORY FINISHED ====" -ForegroundColor Cyan
Write-Host "Report: $ReportTxt"
Write-Host "CSV:    $ReportCsv"
Write-Host "`nREAD-ONLY: nothing was created, modified, stopped, resized or deleted." -ForegroundColor Green
Write-Host "Paste the [5/6] and [6/6] sections back to Claude." -ForegroundColor Cyan
