param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('radar', 'current', 'daily', 'climate-recent', 'climate-historical', 'verify')]
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

function Invoke-Downloader {
    param([string[]]$CliArguments)
    # Windows PowerShell 5.1 turns native stderr into error records. Log the
    # full output and use the process exit code instead of aborting on stderr.
    $ErrorActionPreference = 'Continue'
    # Resumed daily/radar/maintenance tasks can start together. Give the current
    # archive writer time to finish; a timeout still fails for scheduler retry.
    & $PythonExe -m chmi_downloader.cli @CliArguments --lock-timeout 3600 2>&1 |
        ForEach-Object {
            "$_" | Out-File -LiteralPath $LogFile -Append -Encoding utf8 -ErrorAction Stop
        }
    return $LASTEXITCODE
}

try {
    Write-JobLog 'Starting scheduled job.'
    $CurrentExitCode = 0

    if ($Job -eq 'verify') {
        $exitCode = Invoke-Downloader -CliArguments @('verify')
    }
    else {
        if ($Job -eq 'radar') {
            # Reuse the existing 30-minute task for short-retention feeds.
            $CurrentExitCode = Invoke-Downloader -CliArguments @('collect', 'current')
        }
        $exitCode = Invoke-Downloader -CliArguments @('collect', $Job)
    }

    # Still attempt radar after a current-feed failure, but do not let radar's
    # success conceal that failure from Task Scheduler's restart policy.
    if ($CurrentExitCode -ne 0) { $exitCode = $CurrentExitCode }
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
