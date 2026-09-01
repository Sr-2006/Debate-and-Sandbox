<#
.SYNOPSIS
    Phase 3 evidence sync: pulls Phase 1 telemetry over the Cloudflare tunnel
    and assembles debate_evidence/<incident_id>/ folders for the debate engine.

.DESCRIPTION
    1. GET <HostUrl>/files          -> list of hosted JSON artifacts
    2. Downloads each into frontend_data/ (local cache)
    3. Parses unified_master_dataset.json -> incidents[]
    4. Assembles per-incident evidence folders matching EvidenceLoader's layout
    5. Filters time_series.json to the target service + its dependencies

.PARAMETER HostUrl
    The Phase 1 Cloudflare tunnel URL (e.g. https://xxxx.trycloudflare.com)

.PARAMETER Top
    Only assemble the N hottest incidents (dataset is pre-sorted by priority).

.PARAMETER IncidentId
    Only assemble a specific incident_id (e.g. payment-service_14).

.EXAMPLE
    .\scripts\sync_evidence.ps1 -HostUrl "https://xxxx.trycloudflare.com"
    .\scripts\sync_evidence.ps1 -HostUrl $HOST_URL -Top 5
    .\scripts\sync_evidence.ps1 -HostUrl $HOST_URL -IncidentId "payment-service_14"
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$HostUrl,

    [int]$Top = 0,

    [string]$IncidentId = ""
)

$ErrorActionPreference = "Stop"
$HostUrl = $HostUrl.TrimEnd('/')

$Root        = Split-Path -Parent $PSScriptRoot          # multi-agent-debate/
$CacheDir    = Join-Path $Root "frontend_data"
$EvidenceDir = Join-Path $Root "debate_evidence"

New-Item -ItemType Directory -Force -Path $CacheDir    | Out-Null
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

Write-Host "=== Phase 3 Evidence Sync ===" -ForegroundColor Cyan
Write-Host "Host:     $HostUrl"
Write-Host "Cache:    $CacheDir"
Write-Host "Evidence: $EvidenceDir"
Write-Host ""

# ------------------------------------------------------------------ #
# Step 1: health check
# ------------------------------------------------------------------ #
try {
    $health = Invoke-RestMethod "$HostUrl/health" -TimeoutSec 30
    Write-Host "[1/5] Health check OK: $($health | ConvertTo-Json -Compress -Depth 3)" -ForegroundColor Green
} catch {
    Write-Host "[1/5] Health check FAILED: $_" -ForegroundColor Red
    Write-Host "      Tunnel may still be propagating - wait 60s and retry." -ForegroundColor Yellow
    exit 1
}

# ------------------------------------------------------------------ #
# Step 2: list + download all hosted files
# ------------------------------------------------------------------ #
try {
    $files = @(Invoke-RestMethod "$HostUrl/files" -TimeoutSec 30)
} catch {
    Write-Host "[2/5] Could not list files: $_" -ForegroundColor Red
    exit 1
}

if ($files.Count -eq 0) {
    Write-Host "[2/5] Host returned an empty file list. Has Phase 1 run package_ml_dataset.py?" -ForegroundColor Yellow
    exit 1
}

Write-Host "[2/5] Downloading $($files.Count) file(s)..." -ForegroundColor Cyan
foreach ($f in $files) {
    $out = Join-Path $CacheDir $f
    try {
        Invoke-WebRequest -Uri "$HostUrl/$f" -OutFile $out -TimeoutSec 120
        $size = [math]::Round((Get-Item $out).Length / 1KB, 1)
        Write-Host "      downloaded $f ($size KB)" -ForegroundColor Green
    } catch {
        Write-Host "      FAILED $f : $_" -ForegroundColor Red
    }
}

# The master dataset may be served at /dataset rather than in /files.
$datasetPath = Join-Path $CacheDir "unified_master_dataset.json"
if (-not (Test-Path $datasetPath)) {
    Write-Host "      dataset not in /files - trying /dataset endpoint..." -ForegroundColor Cyan
    try {
        Invoke-WebRequest -Uri "$HostUrl/dataset" -OutFile $datasetPath -TimeoutSec 120
        Write-Host "      downloaded unified_master_dataset.json via /dataset" -ForegroundColor Green
    } catch {
        Write-Host "[2/5] No master dataset available: $_" -ForegroundColor Red
        exit 1
    }
}

# ------------------------------------------------------------------ #
# Step 3: parse the master dataset
# ------------------------------------------------------------------ #
Write-Host "[3/5] Parsing master dataset..." -ForegroundColor Cyan
$dataset = Get-Content $datasetPath -Raw | ConvertFrom-Json
$incidents = @($dataset.incidents)
if ($incidents.Count -eq 0) {
    Write-Host "      No incidents in dataset." -ForegroundColor Yellow
    exit 0
}
$meta = $dataset.metadata
if ($meta) {
    Write-Host "      dataset_version=$($meta.dataset_version) git_sha=$($meta.git_sha) incidents=$($incidents.Count)"
}

# Select which incidents to assemble (dataset is pre-sorted desc by priority).
if ($IncidentId) {
    $selected = @($incidents | Where-Object { $_.incident_event.incident_id -eq $IncidentId })
    if ($selected.Count -eq 0) {
        Write-Host "      incident_id '$IncidentId' not found in dataset." -ForegroundColor Red
        exit 1
    }
} elseif ($Top -gt 0) {
    $selected = @($incidents | Select-Object -First $Top)
} else {
    $selected = $incidents
}

# ------------------------------------------------------------------ #
# Step 4: load time series once (for per-incident filtering)
# ------------------------------------------------------------------ #
$timeSeries = $null
$tsPath = Join-Path $CacheDir "time_series.json"
if (Test-Path $tsPath) {
    try { $timeSeries = Get-Content $tsPath -Raw | ConvertFrom-Json } catch { $timeSeries = $null }
}

function Get-FilteredTimeSeries {
    param($TimeSeries, [string[]]$ServiceNames)
    if ($null -eq $TimeSeries) { return $null }

    # Shape A: object keyed by container/service name.
    if ($TimeSeries -is [System.Management.Automation.PSCustomObject]) {
        $picked = @{}
        foreach ($name in $ServiceNames) {
            $prop = $TimeSeries.PSObject.Properties[$name]
            if ($prop) { $picked[$name] = $prop.Value }
        }
        if ($picked.Count -gt 0) { return $picked }
        return $null
    }

    # Shape B: array of records with a container/service field.
    if ($TimeSeries -is [System.Array]) {
        $rows = @($TimeSeries | Where-Object {
            $rec = $_
            $ServiceNames | Where-Object {
                ("$($rec.container)" -eq $_) -or ("$($rec.service)" -eq $_) -or ("$($rec.name)" -eq $_)
            }
        })
        if ($rows.Count -gt 0) { return $rows }
    }
    return $null
}

# ------------------------------------------------------------------ #
# Step 5: assemble per-incident evidence folders
# ------------------------------------------------------------------ #
Write-Host "[4/5] Assembling evidence folders for $($selected.Count) incident(s)..." -ForegroundColor Cyan
$assembled = 0
$warnings  = @()

foreach ($inc in $selected) {
    $incId = "$($inc.incident_event.incident_id)"
    if (-not ($incId -match '^[a-zA-Z0-9_-]+_\d+$')) {
        $warnings += "$incId : incident_id does not match ^[a-zA-Z0-9_-]+_\d+$"
    }

    $dir = Join-Path $EvidenceDir $incId
    New-Item -ItemType Directory -Force -Path (Join-Path $dir "logs")    | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $dir "metrics") | Out-Null

    # Full 6-block entry (primary source of truth for EvidenceLoader).
    $inc | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $dir "incident_context.json") -Encoding UTF8

    # Standalone block files (gap-fill sources).
    if ($inc.system_context) {
        $inc.system_context | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $dir "system_context.json") -Encoding UTF8
    }
    if ($inc.infrastructure_topology) {
        $inc.infrastructure_topology | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $dir "topology.json") -Encoding UTF8
    }
    if ($null -ne $inc.injected_chaos_context) {
        $inc.injected_chaos_context | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $dir "chaos_context.json") -Encoding UTF8
    }

    # Logs: Drain3 template + up to 5 unmasked samples.
    $telemetry = $inc.telemetry_evidence
    $template = "$($telemetry.log_cluster_template)"
    if (-not $template.Trim()) {
        $warnings += "$incId : log_cluster_template is blank (Phase 1 contract violation)"
    }
    Set-Content (Join-Path $dir "logs\cluster_template.txt") -Value $template -Encoding UTF8

    $samples = @($telemetry.log_samples) | Select-Object -First 5
    $samples | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $dir "logs\samples.json") -Encoding UTF8

    # Metrics: up to 3 snapshot points + filtered time series.
    $snapshot = @($telemetry.metrics_snapshot) | Select-Object -First 3
    $snapshot | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $dir "metrics\snapshot.json") -Encoding UTF8
    if ($snapshot.Count -eq 0) {
        $warnings += "$incId : metrics_snapshot is empty (allowed for fresh clusters)"
    }

    $target = "$($inc.incident_event.target_service)"
    $deps = @()
    if ($inc.infrastructure_topology.downstream_dependencies) {
        $deps = @($inc.infrastructure_topology.downstream_dependencies)
    }
    $filteredTs = Get-FilteredTimeSeries -TimeSeries $timeSeries -ServiceNames (@($target) + $deps)
    if ($null -ne $filteredTs) {
        $filteredTs | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $dir "metrics\time_series.json") -Encoding UTF8
    }

    $assembled++
    Write-Host "      assembled $incId (priority=$($inc.incident_event.priority_score), severity=$($inc.incident_event.severity))" -ForegroundColor Green
}

# ------------------------------------------------------------------ #
# Summary
# ------------------------------------------------------------------ #
Write-Host ""
Write-Host "[5/5] Summary" -ForegroundColor Cyan
Write-Host "      Incidents assembled: $assembled" -ForegroundColor Green
if ($warnings.Count -gt 0) {
    Write-Host "      Warnings:" -ForegroundColor Yellow
    foreach ($w in $warnings) { Write-Host "        - $w" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "Run the debate with:" -ForegroundColor Cyan
Write-Host "  python run_from_evidence.py --dataset frontend_data/unified_master_dataset.json"
Write-Host "  python run_from_evidence.py --folder debate_evidence/<incident_id>"
