param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('radar', 'daily', 'climate-recent', 'climate-historical', 'verify')]
    [string]$Job,

    [Parameter(Mandatory = $true)]
    [string]$PythonExe
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# The scheduled task starts with a minimal environment. Always select the
# repository explicitly so Python imports this checkout, not another install.
Set-Location -LiteralPath $ProjectRoot
$StorageRoot = (& $PythonExe -c 'from chmi_downloader.config import Config; print(Config().storage_root())').Trim()
$LogDirectory = Join-Path $StorageRoot 'logs'
# Each job owns its log file. Separate files prevent simultaneous radar and
# climate runs from competing for one Out-File handle on Windows.
$LogFile = Join-Path $LogDirectory ("scheduled-{0}-{1:yyyy-MM}.log" -f $Job, (Get-Date))
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null

function Write-JobLog {
    param([string]$Message)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "[$timestamp] [$Job] $Message" |
        Out-File -LiteralPath $LogFile -Append -Encoding utf8
}

try {
    Write-JobLog 'Starting scheduled job.'

    if ($Job -eq 'verify') {
        & $PythonExe -m chmi_downloader.cli verify 2>&1 |
            ForEach-Object {
                "$_" | Out-File -LiteralPath $LogFile -Append -Encoding utf8
            }
    }
    else {
        & $PythonExe -m chmi_downloader.cli collect $Job 2>&1 |
            ForEach-Object {
                "$_" | Out-File -LiteralPath $LogFile -Append -Encoding utf8
            }
    }

    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Downloader exited with code $exitCode."
    }
    Write-JobLog 'Scheduled job completed successfully.'
    exit 0
}
catch {
    Write-JobLog ("FAILED: " + $_.Exception.Message)
    exit 1
}
