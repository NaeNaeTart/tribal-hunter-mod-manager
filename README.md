# Tribal Hunter Mod Manager

A Windows desktop mod manager for Tribal Hunter by **NaeNae**. Release version **1.0.0**.

Import mod ZIPs, enable or disable them with the game closed, and restore original files from verified backups. The interface uses Tribal Hunter artwork, a leaf asset from the game for its icon, and readable forest and gold panels.

## Install

Download **Tribal Hunter Mod Manager Setup.exe** from this repository's [Releases](https://github.com/NaeNaeTart/tribal-hunter-mod-manager/releases) or its [GameBanana Tool post](https://gamebanana.com/tools/24491). Run Setup and choose the folder containing `Tribal Hunter.exe` and `data.win`. Python and administrator access are not required for the packaged app.

Open the manager, import a mod ZIP, then select **Enable mod**. Importing alone does not change the game. **Disable mod** removes its changes; **Backups > Restore originals** restores the manager's original files. Keep the backups and state folder with the installation. Restore originals before updating the game.

## Features

- Mod library, details, thumbnails, search and enabled/disabled filters.
- Original backups, SHA-256 verification, conflict checks, staged deployment and recovery journal.
- Binary patch ZIPs that require the matching local original game archive.
- Declared UndertaleModTool patch bundles and supported local replacements.
- Per-user `tribalhunter:` handler for verified GameBanana mod ZIP imports, including forwarding to an already open manager.
- Built-in `--make-patch` package command and a short [mod-author guide](mod-manager/MAKING-MODS.md).

GameBanana's native one-click buttons depend on site registration. The handler downloads and imports a mod; enabling it remains a separate action. The manager supports Windows; game compatibility and original-archive requirements are specified by each mod.

## Development

Tested with Python 3.14 on Windows. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
.\scripts\test.ps1
```

The CI template is inactive. To enable it, copy `ci/windows-tests.yml` to `.github/workflows/tests.yml` using a GitHub session or token with workflow upload permission.

The tests use temporary, synthetic game files. The committed GUI fixture is synthetic and compilation is mocked. No game archive, installed mods, backups, saves or browser data is included in this repository.

### Build the app and installer

```powershell
.\scripts\get-compiler.ps1
.\build.ps1
```

The compiler script downloads the official UndertaleModTool CLI **0.9.2.0** archive and verifies its SHA-256. Build output is `release/1.0.0`. The app and installer are separate executables. Source build scripts resolve paths relative to the checkout; generated PyInstaller specs and output files are ignored.

For GUI integration checks on an interactive Windows desktop:

```powershell
python .\mod-manager\test_ui.py
python .\mod-manager\test_one_click_ui.py
```

Do not place a real `data.win` in the repository to run tests. For a game session, use the packaged installer and choose a separate installed game folder.

## Source layout

- `mod-manager/`: application, UI, protocol handler, patch format, tests and runtime assets.
- `installer/`: folder-selection installer and isolated setup tests.
- `scripts/`: compiler preparation and test runner.
- `ci/windows-tests.yml`: Windows unit-test CI template.

## Credits and permissions

Manager by **NaeNae**. Tribal Hunter and game artwork by **Melonsoda Soft**. Compiler by **UndertaleModTool contributors**. Silkscreen font by **The Silkscreen Project Authors**. No generated artwork is used.

This repository makes the source available under the existing permission policy: **ask before modifying or redistributing the author's work**. See [LICENSE.md](LICENSE.md) and [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Third-party rights remain unchanged.

For help and bug reports, contact **@naenaetart** on Discord first. Include your game version, manager version, exact error and steps to reproduce it.
