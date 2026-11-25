# PowerShell wrapper: runs the transactional installer. Usage: .\install.ps1 C:\path\to\ATTa [--check]
$ErrorActionPreference = "Stop"
$installer = Join-Path $PSScriptRoot "install.py"
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 $installer @args }
elseif (Get-Command python -ErrorAction SilentlyContinue) { & python $installer @args }
else { Write-Error "Python 3.8+ is required"; exit 2 }
exit $LASTEXITCODE
