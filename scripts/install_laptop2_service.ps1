<#
.SYNOPSIS
  Installs Laptop 2 Always-Hot Supervisor Engine Service on Windows.

.DESCRIPTION
  Installs the Laptop 2 supervisor service to run automatically on Windows login / boot.
  Supports both Windows Scheduled Tasks and User Startup Launcher mechanisms, ensuring
  installation succeeds seamlessly with or without Administrator elevation.

.EXAMPLE
  .\scripts\install_laptop2_service.ps1 -NatsUrl "nats://172.51.154.253:4222"
#>

[CmdletBinding()]
param(
    [string]$NatsUrl = "nats://172.51.154.253:4222",
    [string]$TaskName = "AutoSRE_Laptop2_Service",
    [switch]$StartImmediately = $true
)

# Resolve repo root directory and python executable
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path "$ScriptDir\..").Path
$PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source

if (-not $PythonExe) {
    $PythonExe = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
}

if (-not (Test-Path $PythonExe)) {
    Write-Error "Could not locate python.exe. Please ensure Python is in PATH or installed."
    exit 1
}

$ServiceScript = Join-Path $RepoRoot "scripts\laptop2_engine_service.py"
if (-not (Test-Path $ServiceScript)) {
    Write-Error "Could not locate service script at $ServiceScript"
    exit 1
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  INSTALLING AUTOSRE LAPTOP 2 ALWAYS-HOT SERVICE" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Repo Root     : $RepoRoot"
Write-Host "  Python Exe    : $PythonExe"
Write-Host "  Service Script: $ServiceScript"
Write-Host "  NATS Broker   : $NatsUrl"
Write-Host "============================================================"

$InstalledVia = $null

# Mechanism 1: Try Windows Scheduled Task
try {
    Write-Host "Attempting registration via Windows Task Scheduler..." -ForegroundColor Gray
    
    # Remove existing task if present
    $ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($ExistingTask) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }

    $Action = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument "`"$ServiceScript`" --nats-url $NatsUrl" `
        -WorkingDirectory $RepoRoot

    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Days 0)

    $RegisteredTask = Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "AutoSRE Laptop 2 Always-Hot Execution Node Supervisor Service" `
        -ErrorAction Stop

    if ($RegisteredTask) {
        $InstalledVia = "ScheduledTask"
        Write-Host "Scheduled Task '$TaskName' registered successfully!" -ForegroundColor Green
    }
}
catch {
    Write-Host "Scheduled Task registration requires elevation: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Mechanism 2: Fallback to Windows User Startup Launcher (Silent VBS Launcher)
# Guarantees startup at login for the user without requiring administrator permissions
$StartupFolder = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Startup)
$VbsLauncherPath = Join-Path $StartupFolder "AutoSRE_Laptop2_Service.vbs"
$CmdScriptPath = Join-Path $RepoRoot "scripts\run_laptop2_service.cmd"

# Create batch script wrapper
$CmdContent = @"
@echo off
cd /d "$RepoRoot"
"$PythonExe" "$ServiceScript" --nats-url $NatsUrl
"@
[System.IO.File]::WriteAllText($CmdScriptPath, $CmdContent)

# Create invisible background VBS launcher
$VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "$CmdScriptPath" & chr(34), 0
Set WshShell = Nothing
"@
[System.IO.File]::WriteAllText($VbsLauncherPath, $VbsContent)

Write-Host "User Startup Launcher installed at: $VbsLauncherPath" -ForegroundColor Green
if (-not $InstalledVia) {
    $InstalledVia = "StartupFolder"
}

if ($StartImmediately) {
    Write-Host "Starting Laptop 2 Supervisor Service now..." -ForegroundColor Cyan
    if ($InstalledVia -eq "ScheduledTask") {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    # Also trigger the background launcher if not running
    wscript.exe "$VbsLauncherPath"
    Start-Sleep -Seconds 2
    Write-Host "Supervisor Service is running in background!" -ForegroundColor Green
}

Write-Host "`nInstallation complete. Laptop 2 will now start the supervisor automatically on login/boot." -ForegroundColor Cyan
