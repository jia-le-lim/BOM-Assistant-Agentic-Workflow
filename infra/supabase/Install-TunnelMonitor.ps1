param(
    [string]$DeploymentRoot = 'C:\ProgramData\BOM-Supabase',
    [string]$PythonExe = 'C:\Program Files\Python313\pythonw.exe',
    [string]$Recipient = '',
    [ValidateSet('outlook', 'smtp')][string]$Delivery = 'outlook',
    [string]$Sender = '',
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$taskName = 'BOM-Cloudflare-Monitor'
$deployPath = (Resolve-Path -LiteralPath $DeploymentRoot).Path
$pythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
foreach ($value in @($deployPath, $pythonPath)) {
    if ($value.Contains('"') -or $value.Contains("`n") -or $value.Contains("`r")) {
        throw 'Paths must not contain quotes or newlines.'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $deployPath 'runtime/pilot/compose.yml'))) {
    throw 'Prepare the existing pilot deployment before installing its monitor.'
}
$scriptPath = Join-Path $deployPath 'tunnel_monitor.py'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Actions.Count -ne 1 -or $existing.Actions[0].Arguments -notlike ('*"' + $scriptPath + '"*')) {
        throw 'An unrelated scheduled task has this name; refusing to replace it.'
    }
    $oldProcesses = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"' + $scriptPath + '"') } |
        ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
    Stop-ScheduledTask -TaskName $taskName
    foreach ($oldProcess in $oldProcesses) {
        if (-not $oldProcess.WaitForExit(15000)) {
            throw 'The previous monitor has not exited; retry the update after it stops.'
        }
    }
}
foreach ($name in @('tunnel_monitor.py', 'tunnel_monitor.html', 'Send-TunnelEmail.ps1', 'tunnel-monitor-email.example.json', 'Pilot.ps1', 'Install-TunnelMonitor.ps1', 'TUNNEL_MONITOR.md')) {
    $source = Join-Path $PSScriptRoot $name
    $destination = Join-Path $deployPath $name
    if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($destination)) {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}
$monitorRoot = Join-Path $deployPath 'runtime/pilot/monitor'
New-Item -ItemType Directory -Path $monitorRoot -Force | Out-Null
$control = Join-Path $monitorRoot 'control.json'
if (-not (Test-Path -LiteralPath $control)) {
    @{ enabled = $true } | ConvertTo-Json | Set-Content -LiteralPath $control -Encoding UTF8
}
$emailFile = Join-Path $monitorRoot 'email.local.json'
if (-not (Test-Path -LiteralPath $emailFile)) {
    $email = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'tunnel-monitor-email.example.json') -Raw | ConvertFrom-Json
    if ($Recipient) { $email.recipient = $Recipient }
    $email.delivery = $Delivery
    if ($Sender) { $email.sender = $Sender }
    if ($Delivery -eq 'outlook' -and $Recipient -and $Sender) { $email.enabled = $true }
    $email | ConvertTo-Json | Set-Content -LiteralPath $emailFile -Encoding UTF8
}
# Interactive user context gives the monitor this user's Docker Desktop access.
# No password is saved in Task Scheduler; the process runs without a console.
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('"{0}" --deployment-root "{1}"' -f $scriptPath, $deployPath) -WorkingDirectory $deployPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Checks the latest BOM Cloudflare URL, recovers the tunnel and emails verified replacement links.' -Force | Out-Null
if ($Start) { Start-ScheduledTask -TaskName $taskName }
Write-Output 'Tunnel monitor installed. Local status: http://127.0.0.1:13011'
Write-Output "Private email configuration: $emailFile"
