@echo off
rem One-time MORVIX setup for a new computer (see INSTALL_FOR_GROUPMATES.md).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\setup_morvix.ps1"
