param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$DeploymentRoot,
    [Parameter(Mandatory=$true)][string]$BackupDirectory
)
$ErrorActionPreference = 'Stop'
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
$target = Join-Path $BackupDirectory "bom-public-$stamp.dump"
& $PythonExe (Join-Path $PSScriptRoot 'manage.py') --root $DeploymentRoot backup $target
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# No automatic deletion: retain older known-good backups until a restore is tested.
exit 0
