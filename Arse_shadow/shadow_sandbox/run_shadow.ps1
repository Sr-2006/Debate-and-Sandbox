<#
.SYNOPSIS
    Root wrapper script for Shadow Sandbox lifecycle management.
    Spins up the isolated environment and executes the pipeline orchestrator.
#>
param (
    [Parameter(Position=0, Mandatory=$false)]
    [ValidateSet("batch", "watch")]
    [string]$Mode = "watch"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[WRAPPER] 1/2: Spinning up shadow sandbox infrastructure..." -ForegroundColor Cyan
$TargetScript = Join-Path $ScriptDir "clone\run_shadow.ps1"
& $TargetScript -Action "up"

Write-Host "[WRAPPER] Waiting 5 seconds for containers to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

Write-Host "[WRAPPER] 2/2: Starting Pipeline Orchestrator in '$Mode' mode..." -ForegroundColor Cyan
# Run the Python orchestrator
python -m shadow_sandbox.run_pipeline shadow_sandbox/sample_inputs --mode $Mode