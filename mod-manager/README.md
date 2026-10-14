# Tribal Hunter Mod Manager 1.0.0

Run `Tribal Hunter Mod Manager Setup.exe` and choose the game folder containing `Tribal Hunter.exe` and `data.win`. Setup installs the standalone manager, bundled patch compiler, usage guides and per-user GameBanana one-click handler. Python and administrator access are not required. Existing mods, backups, saves and game files are preserved.

Open `Tribal Hunter Mod Manager.exe` in that folder. Import a mod ZIP or folder, review its details, then select Enable mod with the game closed. Disable mod removes its effects; Backups > Restore originals disables all mods and restores the manager's original files. Importing alone leaves the game unchanged.

GameBanana `tribalhunter:` links download a verified Tribal Hunter ZIP into the library. They work with the manager open or closed. Enable the imported mod when ready. Setup registers the handler; Guide > Set up one-click links repairs registration. Guide > Import GameBanana link accepts an install link directly. Public downloads require an internet connection; local imports work offline. Private GameBanana posts need a manually downloaded ZIP. Native site integration is managed by GameBanana.

Supported packages include binary patch ZIPs that reconstruct `data.win` from the matching local original, declared `umt_patch` bundles, and local replacements for supported files. For GameBanana releases, distribute a patch rather than a complete game archive. The compiler applies declared GML events and precompiled shaders; it does not run arbitrary C#, Python or shell scripts from mods. Replacements of the same file conflict. A declared `original_data_sha256` must match the clean game backup before deployment. See [MAKING-MODS.md](MAKING-MODS.md) for the short compile/package tutorial.

The app keeps imported packages in `mod-manager/mods`, originals in `mod-manager/backups`, and deployment state in `mod-manager/state.json`. Hash checks, conflict checks and a recovery journal protect installation. Keep these files together and leave space for backups and temporary copies. Restore originals before updating the game. Outside changes are detected and preserved.

Ctrl+O imports a ZIP, Ctrl+F searches, F5 refreshes, and Escape dismisses a notice. The Guide includes the mod-author tutorial and one-click controls. Artwork is credited to Melonsoda Soft; a leaf asset from Tribal Hunter is used for the app icon. Silkscreen is bundled under the SIL Open Font License. No generated art is included.

Build with `build.ps1`; source entry is `manager.py` and interface is `desktop_ui.py`. Python, CustomTkinter, Pillow and PyInstaller are development dependencies. Test with `test_manager.py`, `test_one_click.py`, `test_ui.py` and `installer/test_setup.py`, which use temporary games rather than the installed archive.
