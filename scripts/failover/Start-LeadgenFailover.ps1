<#
.SYNOPSIS
  Explicit-start local-device failover watchdog for LeadGenShortTerm.

.DESCRIPTION
  Creates or reuses the repository virtual environment, installs requirements only when they
  change, and runs the watchdog with EXECUTOR_ROLE=device.

  Commands:
    status  Observe-only health/progress/identity report. Never processes work.
    once    One bounded cycle (thresholds persist in .failover\state.json), then exit.
    run     Loop every FAILOVER_INTERVAL_SECONDS until 'stop' is requested.
    stop    Ask a running watchdog to exit after its current bounded job.

  Processing additionally requires FAILOVER_ENABLED=true in the ignored .env. Acquisition,
  processing, source and Groq gates also come from .env; this script enables none of them.
  It installs no scheduled task or startup entry. Device fallback needs power, connectivity and
  access to the configured PostgreSQL database (its IP allow list).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command status
#>
[CmdletBinding()]
param(
    [ValidateSet('status', 'once', 'run', 'stop')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$Requirements = Join-Path $Root 'services\render_api\requirements.txt'
$Marker = Join-Path $Venv '.leadgen-requirements.sha256'

if (-not (Test-Path (Join-Path $Root '.env'))) {
    throw 'Missing the ignored repository .env; refusing to guess any account or database.'
}

if (-not (Test-Path $Python)) {
    Write-Host 'Creating virtual environment in .venv'
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) { & py -3 -m venv $Venv } else { & python -m venv $Venv }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Python)) { throw 'Virtual environment creation failed.' }
}

$wanted = (Get-FileHash -Path $Requirements -Algorithm SHA256).Hash
$installed = ''
if (Test-Path $Marker) { $installed = (Get-Content -Path $Marker -Raw).Trim() }
if ($installed -ne $wanted) {
    Write-Host 'Installing pinned requirements'
    & $Python -m pip install --disable-pip-version-check --quiet -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Set-Content -Path $Marker -Value $wanted -Encoding ascii
}

$env:EXECUTOR_ROLE = 'device'
Push-Location (Join-Path $Root 'services\render_api')
try {
    & $Python -m app.failover $Command
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $code
