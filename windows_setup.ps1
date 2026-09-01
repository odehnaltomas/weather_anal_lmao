$ErrorActionPreference = 'Stop'
$Installer = Join-Path $PSScriptRoot 'scripts\install_scheduled_tasks.ps1'

# Keep this repository-root entry point for users following the README. The
# implementation lives under scripts so all scheduling helpers stay together.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer
exit $LASTEXITCODE
