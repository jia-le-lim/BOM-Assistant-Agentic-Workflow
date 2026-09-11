param(
    [ValidateSet('build', 'start', 'local-start', 'stop', 'tunnel-stop', 'status', 'link', 'logs')]
    [string]$Action = 'status',
    [string]$DeploymentRoot = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$pilotRoot = Join-Path $DeploymentRoot 'runtime/pilot'
$composeArgs = @('compose', '--project-name', 'bom-pilot', '--env-file', (Join-Path $pilotRoot 'proxy.env'),
    '-f', (Join-Path $pilotRoot 'compose.yml'))
$lanLayer = Join-Path $pilotRoot 'compose.lan.yml'
if (Test-Path -LiteralPath $lanLayer) { $composeArgs += @('-f', $lanLayer) }
function Invoke-PilotCompose {
    & docker @composeArgs @args
    if ($LASTEXITCODE -ne 0) { throw "Pilot Docker command failed (exit $LASTEXITCODE)." }
}
function Show-PilotLink {
    $container = Invoke-PilotCompose ps -q tunnel
    if (-not $container) { throw 'Pilot tunnel is stopped.' }
    & docker @composeArgs exec -T pilot-gateway sh -c 'wget -q -O /dev/null http://tunnel:20241/ready >/dev/null 2>&1'
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel is not connected to Cloudflare. Use Pilot.ps1 logs.' }
    $started = & docker inspect --format '{{.State.StartedAt}}' $container
    $log = Invoke-PilotCompose logs --no-color --since $started tunnel | Out-String
    $links = [regex]::Matches($log, 'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($links.Count -eq 0) { throw 'Cloudflare has not supplied a pilot URL yet.' }
    $link = $links[$links.Count - 1].Value
    Set-Content -LiteralPath (Join-Path $pilotRoot 'url.txt') -Value $link
    Write-Output "Pilot link: $link"
    Write-Output "Use your existing pilot login. Private account file: $DeploymentRoot\pilot-accounts.local.json"
}
switch ($Action) {
    'build' { Invoke-PilotCompose build tunnel }
    'local-start' {
        Invoke-PilotCompose up -d --no-deps pilot-gateway
        $lanSettings = Join-Path $DeploymentRoot 'runtime/lan.json'
        if (Test-Path -LiteralPath $lanSettings) {
            $settings = Get-Content -LiteralPath $lanSettings -Raw | ConvertFrom-Json
            Write-Output "Internal pilot: $($settings.url). Cloudflare tunnel not started."
        }
    }
    'start' {
        Invoke-PilotCompose up -d pilot-gateway
        # Verify the login gate before opening any public tunnel.
        $http = New-Object System.Net.WebClient
        $http.Proxy = $null
        try {
            $protected = $false
            for ($attempt = 0; $attempt -lt 10; $attempt++) {
                try { $null = $http.DownloadString('http://127.0.0.1:13010/'); break }
                catch [System.Net.WebException] {
                    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) {
                        $protected = $true; break
                    }
                    Start-Sleep -Seconds 1
                }
            }
            if (-not $protected) { throw 'Pilot login gate did not return 401; tunnel was not opened.' }
        } finally { $http.Dispose() }
        Invoke-PilotCompose up -d --no-build --no-deps tunnel
        $connected = $false
        for ($attempt = 0; $attempt -lt 35; $attempt++) {
            & docker @composeArgs exec -T pilot-gateway sh -c 'wget -q -O /dev/null http://tunnel:20241/ready >/dev/null 2>&1'
            if ($LASTEXITCODE -eq 0) { $connected = $true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $connected) {
            Invoke-PilotCompose stop tunnel
            throw 'Cloudflare did not connect within 70 seconds; tunnel stopped. Check Pilot.ps1 logs and corporate proxy connectivity.'
        }
        Show-PilotLink
    }
    'stop' { Invoke-PilotCompose stop; Write-Output 'Pilot closed. Local application and database remain running.' }
    'tunnel-stop' { Invoke-PilotCompose stop tunnel; Write-Output 'Public tunnel closed. Local gateway and application remain running.' }
    'status' { Invoke-PilotCompose ps }
    'link' { Show-PilotLink }
    'logs' { Invoke-PilotCompose logs --tail 80 tunnel }
}
