$ErrorActionPreference = 'Stop'
$repositoryDirectory = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $repositoryDirectory 'mod-manager')
try {
    python -m unittest test_manager test_delta_patch test_one_click -v
    if ($LASTEXITCODE -ne 0) { throw 'Manager tests failed.' }
} finally { Pop-Location }
Push-Location (Join-Path $repositoryDirectory 'installer')
try {
    python -m unittest test_setup -v
    if ($LASTEXITCODE -ne 0) { throw 'Installer tests failed.' }
} finally { Pop-Location }
