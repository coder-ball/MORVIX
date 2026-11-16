# Creates the MORVIX shortcuts: "MORVIX" on the Desktop, and a MORVIX folder in the Start menu
# (MORVIX, Stop MORVIX, MORVIX Email Setup, MORVIX Extension Folder). Safe to run again.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$launcher = Join-Path $root 'launcher'
$ico = Join-Path $root 'morvix.ico'
$ws = New-Object -ComObject WScript.Shell
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$psexe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'

function New-Link($path, $target, $arguments, $description) {
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = $target; $s.Arguments = $arguments; $s.WorkingDirectory = $root
    $s.IconLocation = "$ico,0"; $s.Description = $description; $s.Save()
    Write-Host "  $path"
}
$hidden = { param($ps1) "`"$(Join-Path $launcher 'run_hidden.vbs')`" `"$(Join-Path $launcher $ps1)`"" }

$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'MORVIX'
New-Item -ItemType Directory -Force $menu | Out-Null
Write-Host "Created shortcuts:"
New-Link (Join-Path ([Environment]::GetFolderPath('Desktop')) 'MORVIX.lnk') $wscript (& $hidden 'launch_morvix.ps1') 'Start MORVIX and open the parent dashboard'
New-Link (Join-Path $menu 'MORVIX.lnk') $wscript (& $hidden 'launch_morvix.ps1') 'Start MORVIX and open the parent dashboard'
New-Link (Join-Path $menu 'Stop MORVIX.lnk') $wscript (& $hidden 'stop_morvix.ps1') 'Stop the MORVIX background server'
New-Link (Join-Path $menu 'MORVIX Email Setup.lnk') $psexe "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $launcher 'setup_email.ps1')`"" 'Set the Gmail account that sends verification codes'
New-Link (Join-Path $menu 'MORVIX Extension Folder.lnk') (Join-Path $env:WINDIR 'explorer.exe') "`"$(Join-Path $root 'browser_extension')`"" 'Folder to choose in Chrome > Extensions > Load unpacked'
