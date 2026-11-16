# Starts the MORVIX server in the background (if it is not already running)
# and opens the parent dashboard in its own app window. Used by the MORVIX desktop shortcut.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$url = 'http://127.0.0.1:8000'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

function Test-Up { try { (Invoke-WebRequest "$url/api/v1/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }
function Show-Error($text) { [System.Windows.Forms.MessageBox]::Show($text, 'MORVIX', 'OK', 'Error') | Out-Null }

if (-not (Test-Up)) {
    $py = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { Show-Error "MORVIX is not installed yet.`n`nRun these once in PowerShell inside:`n$root`n`npython -m venv .venv`n.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt"; exit 1 }
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    New-Item -ItemType Directory -Force (Join-Path $root 'data') | Out-Null
    $log = Join-Path $root 'data\server.log'; $err = Join-Path $root 'data\server-error.log'

    # small "starting" window so the double-click visibly does something
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'MORVIX'; $form.Size = New-Object System.Drawing.Size(360, 130); $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.TopMost = $true
    $form.BackColor = [System.Drawing.Color]::FromArgb(30, 27, 75)
    if (Test-Path (Join-Path $root 'morvix.ico')) { $form.Icon = New-Object System.Drawing.Icon (Join-Path $root 'morvix.ico') }
    $label = New-Object System.Windows.Forms.Label
    $label.Text = "Starting MORVIX...`nLoading the local classifiers (about 10-20 seconds)."
    $label.ForeColor = [System.Drawing.Color]::White; $label.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $label.AutoSize = $false; $label.Dock = 'Fill'; $label.TextAlign = 'MiddleCenter'
    $form.Controls.Add($label); $form.Show(); [System.Windows.Forms.Application]::DoEvents()

    $p = Start-Process -FilePath $py -ArgumentList '-m', 'uvicorn', 'backend.main:app', '--env-file', '.env', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err -PassThru
    Set-Content -Path (Join-Path $root 'data\server.pid') -Value $p.Id
    $ok = $false
    for ($i = 0; $i -lt 180; $i++) {
        Start-Sleep -Milliseconds 500; [System.Windows.Forms.Application]::DoEvents()
        if ($p.HasExited) { break }
        if (Test-Up) { $ok = $true; break }
    }
    $form.Close()
    if (-not $ok) {
        $tail = if (Test-Path $err) { (Get-Content $err -Tail 12) -join "`n" } else { '' }
        Show-Error "MORVIX could not start.`n`n$tail`n`nFull log: $err"; exit 1
    }
}

# Open the dashboard as an app window (Chrome, else Edge, else the default browser)
$browsers = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
$b = $browsers | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($b) { Start-Process $b "--app=$url/" } else { Start-Process "$url/" }
