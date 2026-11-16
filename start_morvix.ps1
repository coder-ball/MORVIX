# Start MORVIX (API + parent dashboard in one local process).
# Usage, from the MORVIX_V3 folder:  powershell -ExecutionPolicy Bypass -File .\start_morvix.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Creating virtual environment (.venv) ...'
    python -m venv .venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
}
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host ''
Write-Host 'MORVIX dashboard:  http://127.0.0.1:8000'
Write-Host 'API docs:          http://127.0.0.1:8000/docs'
Write-Host 'Load the extension from the browser_extension folder (chrome://extensions > Load unpacked).'
Write-Host 'Press Ctrl+C to stop.'
Write-Host ''
& $py -m uvicorn backend.main:app --env-file .env --host 127.0.0.1 --port 8000
