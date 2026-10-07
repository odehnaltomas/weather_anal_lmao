param(
    [switch]$RunRadarNow
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot 'run_scheduled_job.ps1'
$MaintenanceRunner = Join-Path $PSScriptRoot 'run_scheduled_maintenance.ps1'
$HiddenRunner = Join-Path $PSScriptRoot 'run_hidden.vbs'

# Resolve the actual interpreter once during installation. Scheduled tasks
# should not depend on the WindowsApps "py" launcher or the user's PATH.
$PythonExe = (& py -3 -c 'import sys; print(sys.executable)').Trim()
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python interpreter was not found: $PythonExe"
}
$StorageRoot = (& $PythonExe -c 'from chmi_downloader.config import Config; print(Config().storage_root())').Trim()

$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$WScriptExe = (Get-Command wscript.exe -ErrorAction Stop).Source
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

function Quote-TaskArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

function New-JobAction {
    param(
        [string]$Script,
        [string]$Job
    )
    $arguments = @(
        '//B'
        '//NoLogo'
        (Quote-TaskArgument $HiddenRunner)
        (Quote-TaskArgument $PowerShellExe)
        (Quote-TaskArgument $Script)
        (Quote-TaskArgument $PythonExe)
    )
    if ($Job) {
        $arguments += Quote-TaskArgument $Job
    }
    return New-ScheduledTaskAction `
        -Execute $WScriptExe `
        -Argument ($arguments -join ' ') `
        -WorkingDirectory $ProjectRoot
}

# IgnoreNew complements the application's collector.lock. If a slow download
# is still running, Task Scheduler will not start a competing process.
# IgnoreNew applies within one task; the Python lock coordinates different
# tasks. Restart settings recover failures after that bounded wait or a network
# outage without waiting for the next day's regular trigger.
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 6 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

$RadarAction = New-JobAction -Script $Runner -Job 'radar'
$RadarTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$RecentAction = New-JobAction -Script $Runner -Job 'daily'
$RecentTrigger = New-ScheduledTaskTrigger -Daily -At '03:20'

$MaintenanceAction = New-JobAction -Script $MaintenanceRunner -Job ''
$MaintenanceTrigger = New-ScheduledTaskTrigger -Daily -At '04:30'

$Tasks = @(
    @{
        Name = 'CHMI-Weather-Downloader-Radar'
        Description = 'Archive all missing CHMI radar composites every 30 minutes.'
        Action = $RadarAction
        Trigger = $RadarTrigger
    },
    @{
        Name = 'CHMI-Weather-Downloader-Daily'
        Description = 'Collect recent CHMI climate data once per day.'
        Action = $RecentAction
        Trigger = $RecentTrigger
    },
    @{
        Name = 'CHMI-Weather-Downloader-Maintenance'
        Description = 'Run monthly historical collection and weekly archive verification.'
        Action = $MaintenanceAction
        Trigger = $MaintenanceTrigger
    }
)

foreach ($task in $Tasks) {
    Register-ScheduledTask `
        -TaskName $task.Name `
        -Description $task.Description `
        -Action $task.Action `
        -Trigger $task.Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Force | Out-Null
    Write-Host ("Installed task: " + $task.Name)
}

Write-Host ("Python: " + $PythonExe)
Write-Host ("Logs: " + (Join-Path $StorageRoot 'logs\scheduled-JOB-YYYY-MM.log'))
Write-Host 'Tasks run for the current user while that user is logged on.'

if ($RunRadarNow) {
    Start-ScheduledTask -TaskName 'CHMI-Weather-Downloader-Radar'
    Write-Host 'Started the radar task.'
}
