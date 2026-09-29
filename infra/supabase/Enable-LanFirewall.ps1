param(
    [string]$DeploymentRoot = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Windows administrator rights are required. Run this script in an elevated PowerShell window.'
}
$settings = Get-Content -LiteralPath (Join-Path $DeploymentRoot 'runtime/lan.json') -Raw | ConvertFrom-Json
$clientAddresses = @($settings.client_cidr)
if ($settings.PSObject.Properties.Name -contains 'client_addresses') {
    $clientAddresses = @($settings.client_addresses)
}
$address = [ipaddress]::Parse($settings.address)
if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'Expected the configured IPv4 LAN address.'
}
$interface = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $settings.address -ErrorAction Stop
$ruleName = 'BOM-Assistant-Pilot-LAN-13010'
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 13010 -LocalAddress $settings.address `
        -RemoteAddress $clientAddresses -InterfaceAlias $interface.InterfaceAlias `
        -Profile Any -EdgeTraversalPolicy Block
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName 'BOM Assistant password gateway - internal pilot' `
        -Description 'Password gateway only; bound to the configured Ethernet IP and internal client address ranges.' `
        -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort 13010 `
        -LocalAddress $settings.address -RemoteAddress $clientAddresses `
        -InterfaceAlias $interface.InterfaceAlias -Profile Any -EdgeTraversalPolicy Block | Out-Null
}
$effective = Get-NetFirewallRule -PolicyStore ActiveStore -Name $ruleName -ErrorAction Stop
$result = @{applied_utc=[DateTime]::UtcNow.ToString('o');rule=$ruleName;address=$settings.address;port=13010;clients=$clientAddresses;
    primary_status=[string]$effective.PrimaryStatus;enforcement_status=[string]$effective.EnforcementStatus;
    remote_access_verified=$false}
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DeploymentRoot 'runtime/lan-firewall-result.json') -Encoding UTF8
if ([string]$effective.EnforcementStatus -ne 'Full') {
    throw "Rule saved but not fully enforced: $($effective.PrimaryStatus) / $($effective.EnforcementStatus). Have IT check the active firewall provider and endpoint policy. Remote access is not verified."
}
Write-Output "Windows firewall rule enforced: $($settings.url), clients $($clientAddresses -join ', '). Verify access from another PC."
