param(
    [ValidateSet('start', 'status', 'link', 'stop', 'logs')]
    [string]$Action = 'status',
    [string]$DeploymentRoot = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
# Keep the existing shortcut, now controlling Docker instead of Windows processes.
$pilotAction = if ($Action -eq 'stop') { 'tunnel-stop' } else { $Action }
& (Join-Path $DeploymentRoot 'Pilot.ps1') -Action $pilotAction -DeploymentRoot $DeploymentRoot
