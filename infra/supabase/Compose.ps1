param(
    [ValidateSet('start', 'stop', 'status', 'logs', 'build', 'update-app')]
    [string]$Action = 'start',
    [string]$DeploymentRoot = $PSScriptRoot,
    [ValidateSet('frontend', 'backend')]
    [string]$Service = 'backend'
)
$ErrorActionPreference = 'Stop'
$stack = Join-Path $DeploymentRoot 'runtime/stack'
$envFile = Join-Path $stack '.env'
if (-not (Test-Path -LiteralPath (Join-Path $stack 'compose.app.yml'))) {
    throw 'App deployment is not prepared. Run install_app.py first; see START_HERE.md.'
}
# Generated deployment settings take precedence over a developer's shell.
$saved = @{}
$keys = @('COMPOSE_FILE', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PROFILES')
$keys += Get-Content -LiteralPath $envFile | ForEach-Object {
    if ($_ -match '^([A-Za-z_][A-Za-z0-9_]*)=') { $Matches[1] }
}
try {
    foreach ($key in ($keys | Select-Object -Unique)) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $null, 'Process')
    }
    $composeArgs = @('compose', '--project-name', 'bom-supabase', '--env-file', $envFile,
        '-f', (Join-Path $stack 'docker-compose.yml'),
        '-f', (Join-Path $stack 'compose.override.yml'),
        '-f', (Join-Path $stack 'compose.app.yml'))
    [string[]]$actionArgs = @(switch ($Action) {
        'start'      { @('up', '-d', '--no-build', '--wait', '--wait-timeout', '300') }
        'stop'       { @('stop') }
        'status'     { @('ps') }
        'logs'       { @('logs', '--tail', '100', '-f', $Service) }
        'build'      { @('build', 'backend', 'frontend') }
        'update-app' { @('up', '-d', '--no-deps', '--no-build', '--wait', '--wait-timeout', '180', 'backend', 'frontend') }
    })
    & docker @composeArgs @actionArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed (exit $LASTEXITCODE)." }
} finally {
    foreach ($key in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process')
    }
}
