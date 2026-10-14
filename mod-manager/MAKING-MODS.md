# Make a mod for Tribal Hunter Mod Manager 1.0.0

1. Copy a clean `data.win` from the game build you support. Edit that copy in UndertaleModTool and save the modified archive separately. Never build from an already modded archive.
2. Create a **patch ZIP** with the updated Mod Manager's built-in command below. It automatically writes the manifest and records the clean input and patched output hashes. Optionally add `--preview .\preview.png` for your manually made thumbnail.
3. Import and enable the ZIP in a separate test game folder. Test Disable mod and Restore originals. The manager verifies the original archive and reconstructs the modified file locally before deployment.
4. Upload the ZIP to a Tribal Hunter Mods post on GameBanana. **Do not upload data.win or bundle the original game archive.** One-click uses the same patch ZIP.

```powershell
& '.\Tribal Hunter Mod Manager.exe' --make-patch '.\clean\data.win' '.\modified\data.win' '.\MyMod-1.0.0.zip' --name 'My Mod' --version '1.0.0'
```

To compile an existing UMT script first:

```powershell
.\UndertaleModCli.exe load .\clean\data.win -s .\patch.csx -o .\modified\data.win -f
```

The generated package contains:

```text
MyMod-1.0.0.zip
  mod.json
  README.md
  preview.png                  (optional)
  patch/
    data.win.thpatch
```

The manifest uses `kind: binary_patch`, `patch_file: patch/data.win.thpatch`, and both `original_data_sha256` and `result_data_sha256`. The patch is a compressed stream of copy instructions and changed bytes. It requires the matching original game archive; it cannot supply the game's unchanged assets by itself. Binary patches changing data.win conflict with other full changes to that archive.

Local replacement packages and declared `umt_patch` bundles remain supported. EXEs and DLLs are not mod payloads. Arbitrary scripts are not executed by ZIP import.

GameBanana links use `tribalhunter:https://gamebanana.com/mmdl/FILE_ID,Mod,POST_ID`, with actual numeric IDs. Setup registers the protocol for your Windows user. The manager verifies the linked game's identity, ZIP size and checksum, imports the package, and lets you Enable mod. Native site buttons require the manager integration to be registered. Manual ZIP import remains available.
