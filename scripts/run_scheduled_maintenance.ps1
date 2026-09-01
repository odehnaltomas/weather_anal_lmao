param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe
)

$ErrorActionPreference = 'Stop'
$Runner = Join-Path $PSScriptRoot 'run_scheduled_job.ps1'
$today = Get-Date
$failed = $false

# Historical climate files change slowly, so collect them on the fifth day of
# each month. Running this decision task daily avoids fragile custom monthly
# Task Scheduler trigger construction.
if ($today.Day -eq 5) {
    & powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $Runner `
        -Job climate-historical -PythonExe $PythonExe
    if ($LASTEXITCODE -ne 0) {
        $failed = $true
    }
}

# Verify archived files weekly. This checks existence, size, and cataloged
# SHA-256 hashes without competing with the frequent radar task.
if ($today.DayOfWeek -eq [System.DayOfWeek]::Sunday) {
    & powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $Runner `
        -Job verify -PythonExe $PythonExe
    if ($LASTEXITCODE -ne 0) {
        $failed = $true
    }
}

if ($failed) {
    exit 1
}
exit 0
