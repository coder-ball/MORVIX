# Builds a ZIP of MORVIX that is safe to give to a groupmate.
#   powershell -ExecutionPolicy Bypass -File launcher\make_share_package.ps1              (small, ~1 MB; models download during setup)
#   powershell -ExecutionPolicy Bypass -File launcher\make_share_package.ps1 -IncludeModels   (~2.4 GB; no model download needed)
# Never included: .env (your Gmail App Password), data\morvix.db (your account and history), logs,
# the .venv (it only works on this computer), caches and editor folders.
param([switch]$IncludeModels)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$parent = Split-Path -Parent $root
$name = 'MORVIX_V3'
$stage = Join-Path ([IO.Path]::GetTempPath()) ("morvix_share_" + [guid]::NewGuid().ToString('N'))
$zip = Join-Path $parent ($(if ($IncludeModels) { 'MORVIX_V3_share_with_models.zip' } else { 'MORVIX_V3_share.zip' }))
$dest = Join-Path $stage $name

$excludeDirs = @('.venv', '.pytest_cache', '__pycache__', '.claude', '.vscode', 'uploads', '_preview')
if (-not $IncludeModels) { $excludeDirs += 'models' }
# robocopy: /E all folders, /XD skip folders, /XF skip files; data\eval and data\evaluation.json are kept (measured results)
$excludeFiles = @('.env', '*.db', '*.db-journal', '*.log', '*.pid', '*.pyc')
robocopy $root $dest /E /NFL /NDL /NJH /NJS /NP /XD $excludeDirs /XF $excludeFiles | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copy failed (robocopy exit $LASTEXITCODE)" }

# safety check: nothing private made it in
$leak = Get-ChildItem $dest -Recurse -Force -File | Where-Object { $_.Name -eq '.env' -or $_.Extension -in '.db', '.log' }
if ($leak) { Remove-Item -Recurse -Force $stage; throw "Private file found in package: $($leak.FullName -join ', ')" }

if (Test-Path $zip) { Remove-Item $zip -Force }
tar.exe -a -c -f $zip -C $stage $name
if ($LASTEXITCODE -ne 0) { throw "Could not create the ZIP" }
$files = (Get-ChildItem $dest -Recurse -File).Count
Remove-Item -Recurse -Force $stage
Write-Host "Created $zip"
Write-Host ("{0:N1} MB, {1} files. Contains no .env, database, logs or .venv." -f ((Get-Item $zip).Length / 1MB), $files)
