# Saves the Gmail account MORVIX uses to send sign-up verification codes, then sends a test email.
# The app password is typed here by you; it is written only to MORVIX's local .env file.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
Write-Host "MORVIX email setup" -ForegroundColor Cyan
Write-Host "Before you continue, in the Gmail account that will SEND the codes:"
Write-Host "  1. Turn on 2-Step Verification (myaccount.google.com > Security)."
Write-Host "  2. Create an App Password (myaccount.google.com/apppasswords). It is 16 letters."
Write-Host ""
$user = (Read-Host "Gmail address that sends the codes").Trim()
if ($user -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Write-Host "That is not an email address." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
$sec = Read-Host "App password (input hidden)" -AsSecureString
$pw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)) -replace '\s', ''
if ($pw.Length -lt 8) { Write-Host "The app password looks too short." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$values = [ordered]@{ MORVIX_EMAIL_MODE = 'smtp'; MORVIX_SMTP_HOST = 'smtp.gmail.com'; MORVIX_SMTP_PORT = '587'; MORVIX_SMTP_USER = $user; MORVIX_SMTP_PASSWORD = $pw; MORVIX_SMTP_FROM = "MORVIX <$user>" }
$lines = Get-Content .env | Where-Object { $_ -notmatch '^(MORVIX_EMAIL_MODE|MORVIX_SMTP_[A-Z]+)=' }
$lines += $values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
Set-Content .env $lines -Encoding ascii
Write-Host "Saved to .env." -ForegroundColor Green

Write-Host "Sending a test email to $user ..."
foreach ($kv in $values.GetEnumerator()) { Set-Item -Path "env:$($kv.Key)" -Value $kv.Value }
& .\.venv\Scripts\python.exe -c "from backend import mailer; mailer.send_mail('$user','MORVIX email test','MORVIX can send verification codes from this account.'); print('Test email sent. Check the inbox of $user.')"
if ($LASTEXITCODE -ne 0) { Write-Host "Sending failed. Check the address and the app password, then run this setup again." -ForegroundColor Red }
else {
    # restart the server so it picks up the new settings
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*backend.main:app*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "Done. Open MORVIX with the desktop shortcut and create your parent account." -ForegroundColor Green
}
Read-Host "Press Enter to close"
