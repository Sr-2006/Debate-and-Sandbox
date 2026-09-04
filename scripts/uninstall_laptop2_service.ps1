<#
.SYNOPSIS
  Uninstalls Laptop 2 Always-Hot Supervisor Engine Service Windows Scheduled Task.
#>

[CmdletBinding()]
param(
    [string]$TaskName = "AutoSRE_Laptop2_Service"
)

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Stopping task '$TaskName'..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "Unregistering task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed successfully." -ForegroundColor Green
} else {
    Write-Host "Task '$TaskName' is not registered." -ForegroundColor Gray
}
