<#
.SYNOPSIS
    Install, inspect, or remove the Tower of Babel power-aware auto-compaction task on Windows.
.DESCRIPTION
    Runs `compact_cli.py tick` silently via pythonw.exe:
      - Every 10 minutes while plugged into AC power
      - Throttled to once every 30 minutes while running on battery
#>
param(
    [int]$AcMinutes = 10,
    [int]$BatteryMinutes = 30,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$TaskName = "TowerOfBabelAutoCompact"
$BridgeRoot = $PSScriptRoot

if ($Status) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "Task '$TaskName' is NOT installed."
        exit 0
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task      : $($t.TaskName) ($($t.State))"
    Write-Host "Last Run  : $($info.LastRunTime) (Result=$($info.LastTaskResult))"
    Write-Host "Next Run  : $($info.NextRunTime)"
    $statePath = Join-Path $env:USERPROFILE ".tower-of-babel\compact_state.json"
    if (Test-Path $statePath) {
        Write-Host "State     :" (Get-Content $statePath -Raw)
    }
    exit 0
}

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "Task '$TaskName' was not present."
    }
    exit 0
}

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $pythonw) {
    $fallback = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\pythonw.exe"
    if (Test-Path $fallback) { $pythonw = $fallback }
}
if (-not $pythonw) {
    throw "Could not find pythonw.exe. Please install Python 3.10+ on PATH."
}

$args = "-m tower_bridge.compact_cli tick --ac-minutes $AcMinutes --battery-minutes $BatteryMinutes"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $args -WorkingDirectory $BridgeRoot

$triggerInterval = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $AcMinutes)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($triggerInterval, $triggerLogon) `
    -Settings $settings `
    -Principal $principal `
    -Description "Tower of Babel silent power-aware DeepSeek 1M auto-compaction (AC: ${AcMinutes}m, Battery: ${BatteryMinutes}m)" `
    -Force | Out-Null

Write-Host "Installed '$TaskName' (AC=${AcMinutes}m / Battery=${BatteryMinutes}m) using $pythonw"
