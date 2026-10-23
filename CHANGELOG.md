## [1.1.1] - 2026-10-24

### Fixed
- Automatically detect loose / standalone binary patch files (.xdelta, .bspatch, .thpatch) when importing third-party mod ZIP archives (such as *Tribal Hunter: Rebalanced!*).
- Support untrailed/raw third-party binary patches without requiring a pre-existing manifest or custom metadata trailer.
- Safely filter out bundled third-party helper utilities (e.g. DeltaPatcher.exe) during mod import so only the game patch is registered.
- Provide clear error messaging if an external patch fails due to a mismatched game version.

# Changelog

## 1.1.0

- Added multi-engine binary patch support: native thpatch, BSDiff bspatch, and VCDIFF xdelta.
- Added built-in Mod Maker patch creation dialog in the desktop UI (Ctrl+P) with format selection and progress indicator.
- Enhanced --make-patch CLI with --format and --target-file options to patch arbitrary game assets.
- Added scripts/get-patchers.ps1 to download standalone patcher binaries with verified checksums.

## 1.0.0

First release of Tribal Hunter Mod Manager with the themed GUI, mod library, original backups and restore, conflict and integrity checks, declared compiler patches, binary patch ZIP support, GameBanana protocol imports and a game-folder installer.

This source snapshot includes the corrected patch-compatible manager build. Tribal Visuals is distributed separately.
