# MORVIX one-time setup for a new computer.
# Run by double-clicking "SETUP MORVIX.bat" in the MORVIX folder.
# Steps: find Python 3.11/3.12 -> create .venv -> install packages -> download the AI models
#        (skipped if the models folder was copied along) -> create .env -> create shortcuts -> optional Gmail setup.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host $text -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

Write-Host "MORVIX setup" -ForegroundColor Cyan
Write-Host "Folder: $root"
Write-Host "This needs an internet connection and about 6 GB of free disk space. It can take 15-40 minutes."

# 1. Python ------------------------------------------------------------------------------------
Step 1 "Looking for Python 3.12 or 3.11"
$python = $null
foreach ($cand in @(@('py', '-3.12'), @('py', '-3.11'), @('python'), @('python3'))) {
    try {
        $exe = $cand[0]; $pre = @($cand | Select-Object -Skip 1)
        $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -in @('3.11', '3.12')) { $python = $cand; Write-Host "  Found Python $v ($($cand -join ' '))"; break }
    } catch { }
}
if (-not $python) {
    Fail ("Python 3.12 was not found.`n`n1. Download Python 3.12 from https://www.python.org/downloads/windows/`n" +
          "2. In the installer, tick 'Add python.exe to PATH', then click Install Now.`n3. Run SETUP MORVIX.bat again.")
}

# 2. Virtual environment ------------------------------------------------------------------------
Step 2 "Creating the MORVIX Python environment (.venv)"
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$ok = $false
if (Test-Path $venvPy) { try { & $venvPy -c "import sys" 2>$null; $ok = ($LASTEXITCODE -eq 0) } catch { } }
if (-not $ok) {
    if (Test-Path .venv) { Write-Host "  The existing .venv does not work on this computer; recreating it."; Remove-Item -Recurse -Force .venv }
    $exe = $python[0]; $pre = @($python | Select-Object -Skip 1)
    & $exe @pre -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment." }
} else { Write-Host "  Already there." }

# 3. Packages ----------------------------------------------------------------------------------
Step 3 "Installing packages (PyTorch is large; this is the slowest step)"
& $venvPy -m pip install --upgrade pip --disable-pip-version-check -q
& $venvPy -m pip install -r requirements-ml.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Package installation failed. Check the internet connection and run SETUP MORVIX.bat again." }

# 4. Models ------------------------------------------------------------------------------------
Step 4 "Checking the AI models"
$needed = 'deberta-v3-base-zeroshot-v2.0', 'Text-Moderation', 'suicidality', 'toxic-bert', 'vit-base-violence-detection', 'clip-vit-base-patch32'
$missing = @($needed | Where-Object { -not (Test-Path (Join-Path $root "models\$_")) })
if ($missing.Count -eq 0) { Write-Host "  All 6 models are present (copied with the folder). Getting the small optional audio model only." }
else { Write-Host "  Missing: $($missing -join ', '). Downloading about 2.7 GB from Hugging Face ..." }
& $venvPy scripts\download_models.py
if ($LASTEXITCODE -ne 0) { Fail "Model download failed. Check the internet connection and run SETUP MORVIX.bat again (finished downloads are kept)." }

# 5. Settings ----------------------------------------------------------------------------------
Step 5 "Creating settings (.env) and shortcuts"
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "  Created .env from .env.example." } else { Write-Host "  .env already exists; kept." }
New-Item -ItemType Directory -Force (Join-Path $root 'data') | Out-Null
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'launcher\install_shortcuts.ps1')

# 6. Email + extension -------------------------------------------------------------------------
Step 6 "Email for sign-up codes"
Write-Host "  New parent accounts must confirm a 6-digit code sent by email."
Write-Host "  You need a Gmail account with 2-Step Verification and an App Password (myaccount.google.com/apppasswords)."
$a = Read-Host "  Set up the sending Gmail now? (Y/n)"
if ($a -notmatch '^[nN]') { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'launcher\setup_email.ps1') }
else { Write-Host "  Skipped. Later: Start menu > MORVIX > MORVIX Email Setup." }

Write-Host ""
Write-Host "MORVIX is installed." -ForegroundColor Green
Write-Host "Last step, add the extension to Chrome (or Edge):"
Write-Host "  1. Chrome is opening chrome://extensions - turn on Developer mode (top right)."
Write-Host "  2. Click 'Load unpacked' and choose this folder (its path is copied to your clipboard):"
Write-Host "     $root\browser_extension"
Write-Host "  3. Double-click MORVIX on the Desktop, create your account, click 'Generate pairing code',"
Write-Host "     then paste the code in the MORVIX extension popup."
Set-Clipboard -Value (Join-Path $root 'browser_extension')
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) { Start-Process $chrome 'chrome://extensions/' }
Start-Process explorer.exe "`"$(Join-Path $root 'browser_extension')`""
Read-Host "Press Enter to close"
