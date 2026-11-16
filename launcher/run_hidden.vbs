' Runs a MORVIX PowerShell script without flashing a console window.
' Usage: wscript run_hidden.vbs <script.ps1>
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & WScript.Arguments(0) & """", 0, False
