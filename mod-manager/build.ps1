param([string]$OutputDirectory = (Split-Path -Parent $PSScriptRoot))
$gameRoot = Split-Path -Parent $PSScriptRoot
python -m PyInstaller --noconfirm --clean --onefile --windowed --name 'Tribal Hunter Mod Manager' --distpath $OutputDirectory --workpath "$PSScriptRoot\build" --specpath $PSScriptRoot --icon "$PSScriptRoot\assets\app.ico" --version-file "$PSScriptRoot\version.txt" --add-data "$PSScriptRoot\assets;assets" --add-data "$PSScriptRoot\compose_patch.csx;." --collect-all customtkinter --exclude-module numpy "$PSScriptRoot\manager.py"
if ($LASTEXITCODE -ne 0) { throw 'App build failed.' }
