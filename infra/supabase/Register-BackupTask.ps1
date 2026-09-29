param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$DeploymentRoot,
    [Parameter(Mandatory=$true)][string]$BackupDirectory,
    [Parameter(Mandatory=$true)][string]$TaskUser,
    [string]$DailyAt = '02:00'
)
$ErrorActionPreference = 'Stop'
foreach ($value in @($PythonExe, $DeploymentRoot, $BackupDirectory)) {
    if ($value.Contains('"') -or $value.Contains("`n") -or $value.Contains("`r")) {
        throw 'Paths must not contain quotes or newlines.'
    }
}
$pythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
$deployPath = (Resolve-Path -LiteralPath $DeploymentRoot).Path
$scriptPath = Join-Path $PSScriptRoot 'Backup.ps1'
if (Get-ScheduledTask -TaskName 'BOM-Supabase-Backup' -ErrorAction SilentlyContinue) {
    throw 'BOM-Supabase-Backup already exists. Review it before changing its owner or schedule.'
}
$arguments = '-NoProfile -NonInteractive -File "{0}" -PythonExe "{1}" -DeploymentRoot "{2}" -BackupDirectory "{3}"' -f $scriptPath, $pythonPath, $deployPath, $BackupDirectory
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$credential = Get-Credential -UserName $TaskUser -Message 'Credentials for the team-managed backup account (not your personal account)'
Register-ScheduledTask -TaskName 'BOM-Supabase-Backup' -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password $credential.GetNetworkCredential().Password -Description 'BOM public-schema backup; check LastTaskResult and copy archives plus manifests off-host.' | Out-Null
Write-Output 'Backup task registered. Run it once and verify a restore before handover.'
