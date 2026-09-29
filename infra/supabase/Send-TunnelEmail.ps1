param(
    [Parameter(Mandatory=$true)][string]$MessageFile,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$message = Get-Content -LiteralPath $MessageFile -Raw | ConvertFrom-Json
foreach ($name in @('sender', 'recipient')) {
    $value = [string]$message.$name
    if (-not $value -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'An email address is missing or invalid.' }
    $address = New-Object System.Net.Mail.MailAddress($value)
    if ($address.Address -ne $value) { throw 'Use a single plain email address.' }
}
if ([string]$message.url -cnotmatch '^https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com$') {
    throw 'Only a verified Cloudflare Quick Tunnel URL can be emailed.'
}
$outlook = $null
$session = $null
$accounts = $null
$account = $null
$mail = $null
try {
    try { $outlook = [Runtime.InteropServices.Marshal]::GetActiveObject('Outlook.Application') }
    catch {
        # Requires an interactive signed-in Windows user. COM activation starts
        # classic Outlook in automation mode; it does not read mailbox messages.
        $outlook = New-Object -ComObject Outlook.Application
    }
    $session = $outlook.Session
    $accounts = $session.Accounts
    for ($index = 1; $index -le $accounts.Count; $index++) {
        $candidate = $accounts.Item($index)
        if ([string]$candidate.SmtpAddress -ieq [string]$message.sender) {
            $account = $candidate
            break
        }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($candidate)
    }
    if (-not $account) { throw 'The configured sender is not signed into classic Outlook. Open Outlook and sign in with that mailbox.' }
    if ($CheckOnly) { Write-Output 'The configured Outlook sending account is available.'; return }
    $mail = $outlook.CreateItem(0)
    $mail.SendUsingAccount = $account
    $mail.To = [string]$message.recipient
    $mail.Subject = 'BOM Assistant: new Cloudflare link'
    $mail.Body = "The BOM Assistant public link is ready:`r`n`r`n$($message.url)`r`n`r`nVerified at: $($message.created_at)`r`nUse your existing pilot login. Previous temporary links may no longer work.`r`n"
    $mail.Send()
    Write-Output 'Outlook accepted the notification for delivery.'
} finally {
    # Do not quit the user's Outlook or modify its security settings.
    foreach ($value in @($mail, $account, $accounts, $session, $outlook)) {
        if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
            try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($value) } catch { }
        }
    }
}
