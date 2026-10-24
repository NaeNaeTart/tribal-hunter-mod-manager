$ErrorActionPreference = 'Stop'
$gameRoot = Split-Path -Parent $PSScriptRoot
$release = Join-Path $gameRoot 'release\1.2.0'
python -m PyInstaller --noconfirm --clean --onefile --windowed --name 'Tribal Hunter Mod Manager Setup' --distpath $release --workpath "$PSScriptRoot\build" --specpath $PSScriptRoot --paths "$gameRoot\mod-manager" --icon "$gameRoot\mod-manager\assets\app.ico" --version-file "$PSScriptRoot\version.txt" --add-data "$gameRoot\mod-manager\assets;assets" --add-data "$gameRoot\mod-manager\tools\umt;compiler-tools" --add-data "$release\Tribal Hunter Mod Manager.exe;." --add-data "$gameRoot\mod-manager\README.md;manager-guides" --add-data "$gameRoot\mod-manager\MAKING-MODS.md;manager-guides" --collect-all customtkinter --exclude-module numpy "$PSScriptRoot\setup.py"
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
