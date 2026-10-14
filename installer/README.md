# Tribal Hunter Mod Manager installer

Setup asks for the game folder containing Tribal Hunter.exe and data.win. It installs only the manager EXE and the bundled UndertaleModTool CLI patch compiler. No mod package or game archive is bundled or deployed. Existing game files, mods, enabled states, settings, saves and backups remain untouched. A running manager blocks setup; copying/replacement failures roll back the app and compiler update.

Build mod-manager/build.ps1 with OutputDirectory set to release/1.0.0, then installer/build.ps1. Build dependencies: Python, CustomTkinter, Pillow and PyInstaller. End users need only Tribal Hunter Mod Manager Setup.exe.

Run installer/test_setup.py for fresh-install, upgrade, validation and rollback tests. Setup --smoke-test opens the folder-selection screen, captures it, and exits without installing.

Setup uses the official game logo from the manager's offline artwork assets. Tribal Visuals has a separate manually composed thumbnail and ZIP. No generated image is used anywhere in the shipped artifacts.
