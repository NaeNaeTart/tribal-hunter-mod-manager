# Make a mod for Tribal Hunter Mod Manager 1.1.0

## Supported Patch Engines

Tribal Hunter Mod Manager supports multiple binary patch formats, giving mod makers flexibility based on performance and tooling preferences:

| Engine | Format | Extension | Description |
|---|---|---|---|
| **thpatch** | Native | `.thpatch` | Pure Python stream patcher (fastest, standalone, zero dependencies) |
| **bspatch** | BSDiff 4.x | `.bspatch` | Colin Percival binary diff algorithm with bzip2 compression |
| **xdelta** | VCDIFF / XDelta3 | `.xdelta` | Industry standard delta compression for large binary archives |

All formats embed SHA-256 verification metadata to guarantee that only matching game versions are patched, and original files can always be cleanly restored.

---

## 1. Creating a Patch via the Mod Manager GUI

The simplest way to create a patch package is using the built-in GUI tool:

1. Launch **Tribal Hunter Mod Manager**.
2. Click the **Create Patch** button in the top toolbar (or press `Ctrl+P`).
3. Select:
   - **Clean / Original File**: Path to the unmodded file (e.g. vanilla `data.win`).
   - **Modified File**: Path to your edited file (e.g. saved from UndertaleModTool).
   - **Target Game File**: Game file name to patch (`data.win`, `options.ini`, video files, etc.).
   - **Output Mod Package**: Path where the `.zip` will be saved.
   - **Patch Engine**: Select `thpatch`, `bspatch`, or `xdelta`.
   - **Mod Name & Version**: Display name and version string.
   - **Thumbnail Preview** *(optional)*: PNG image for the library card.
4. Click **Create Mod Patch**.
5. Once built, the Mod Manager offers to import the new mod immediately for local testing!

---

## 2. Creating a Patch via the Command Line

You can also generate mod packages from PowerShell or Command Prompt using `--make-patch`:

### Default (`thpatch`, targets `data.win`):
```powershell
& '.\Tribal Hunter Mod Manager.exe' --make-patch '.\clean\data.win' '.\modified\data.win' '.\MyMod-1.0.0.zip' --name 'My Mod' --version '1.0.0'
```

### Using BSDiff / bspatch:
```powershell
& '.\Tribal Hunter Mod Manager.exe' --make-patch '.\clean\data.win' '.\modified\data.win' '.\MyMod-1.0.0.zip' --name 'My Mod' --version '1.0.0' --format bspatch
```

### Using XDelta3:
```powershell
& '.\Tribal Hunter Mod Manager.exe' --make-patch '.\clean\data.win' '.\modified\data.win' '.\MyMod-1.0.0.zip' --name 'My Mod' --version '1.0.0' --format xdelta
```

### Targeting another game asset (e.g. video or options):
```powershell
& '.\Tribal Hunter Mod Manager.exe' --make-patch '.\clean\titlescreen_loop.mp4' '.\modified\titlescreen_loop.mp4' '.\MyVid-1.0.0.zip' --name 'Custom Title' --format xdelta --target-file titlescreen_loop.mp4
```

---

## 3. Package Structure & Manifest Specification

The generated ZIP package contains:

```text
MyMod-1.0.0.zip
  mod.json
  README.md
  preview.png                  (optional)
  patch/
    data.win.thpatch           (or data.win.bspatch / data.win.xdelta)
```

Example `mod.json` manifest:

```json
{
  "name": "My Mod",
  "version": "1.0.0",
  "description": "Mod description",
  "kind": "binary_patch",
  "format": "bspatch",
  "target_file": "data.win",
  "patch_file": "patch/data.win.bspatch",
  "original_data_sha256": "c2ee1bfa07db7f49976ca7a5e957b822...",
  "result_data_sha256": "49867cd76f20c8dbaee2fec00df9bf6a..."
}
```

- `kind`: Always `"binary_patch"`.
- `format`: `"thpatch"`, `"bspatch"`, or `"xdelta"` (defaults to `"thpatch"` for backwards compatibility).
- `target_file`: Name of the game file modified (defaults to `"data.win"`).
- `patch_file`: Relative path to the patch payload inside the archive.
- `original_data_sha256` & `result_data_sha256`: Checksums verifying clean input and modified output.

---

## 4. Testing & Publishing

1. Import and enable the ZIP in a test game folder.
2. Verify game behavior, then test **Disable mod** and **Restore originals** to confirm clean rollback.
3. Upload the ZIP to a Tribal Hunter Mods submission on GameBanana.
4. **Never upload raw `data.win` or original game executables.** Binary patches ensure full compliance with redistribution policies.
