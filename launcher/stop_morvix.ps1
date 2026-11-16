# Stops the background MORVIX server started by the MORVIX shortcut.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Add-Type -AssemblyName System.Windows.Forms
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*backend.main:app*' }
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path $root 'data\server.pid') -ErrorAction SilentlyContinue
$msg = if ($procs) { 'MORVIX has been stopped. The extension keeps using its last saved policy until MORVIX starts again.' } else { 'MORVIX was not running.' }
[System.Windows.Forms.MessageBox]::Show($msg, 'MORVIX', 'OK', 'Information') | Out-Null
