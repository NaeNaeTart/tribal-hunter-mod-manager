from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import subprocess
import tempfile
import uuid
import zipfile
import delta_patch
import patching

GAME_ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
ALLOWED = {'data.win', 'options.ini', 'opening_cinematic.mp4', 'titlescreen_loop.mp4',
           'titlescreen_opening.avi', 'titlevideo.mp4'}


def digest(file):
    h = hashlib.sha256()
    with Path(file).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(file, value):
    tmp = file.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, file)


def game_running():
    if os.name != 'nt':
        return False
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Tribal Hunter.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, creationflags=0x08000000)
    if result.returncode:
        raise RuntimeError('Unable to check whether the game is running. Close it and retry.')
    return '"tribal hunter.exe"' in result.stdout.lower()


class Manager:
    def __init__(self, root=GAME_ROOT):
        self.root = Path(root).resolve()
        self.home = self.root / 'mod-manager'
        self.mods = self.home / 'mods'
        self.backups = self.home / 'backups'
        self.transaction = self.home / 'transaction'
        self.state_file = self.home / 'state.json'
        self.mods.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(exist_ok=True)
        self.reload_state()
        self.recover()

    def reload_state(self):
        """Read the committed state, including changes made by another manager."""
        state = json.loads(self.state_file.read_text('utf-8')) if self.state_file.exists() else {
            'version': 1, 'active': [], 'originals': {}, 'deployed': {}}
        if state.get('version') != 1:
            raise RuntimeError('Unsupported manager state version.')
        self.state = state
        return state

    def recover(self):
        journal = self.transaction / 'journal.json'
        if journal.exists():
            if game_running():
                raise RuntimeError('Close Tribal Hunter, then reopen the manager to recover an interrupted update.')
            info = json.loads(journal.read_text('utf-8'))
            for name in info['files']:
                if name not in ALLOWED:
                    raise RuntimeError('Invalid recovery journal.')
                saved = self.transaction / 'rollback' / name
                # Copy to same-volume temporary file; rollback stays available until recovery completes.
                temp = self.root / ('.mod-manager-recover-' + name)
                shutil.copy2(saved, temp)
                os.replace(temp, self.root / name)
            write_json(self.state_file, info['state'])
            self.state = info['state']
        if self.transaction.exists():
            shutil.rmtree(self.transaction)

    def catalog(self):
        entries = []
        for folder in sorted(self.mods.iterdir()):
            manifest = folder / 'mod.json'
            if folder.is_dir() and manifest.is_file():
                item = json.loads(manifest.read_text('utf-8'))
                item['id'] = folder.name
                entries.append(item)
        return entries

    def import_mod(self, source):
        source = Path(source).resolve()
        if source.is_dir() and (source == self.root or source in self.home.parents or self.home == source or self.home in source.parents):
            raise ValueError('Choose a separate mod folder, outside the game and manager folders.')
        with tempfile.TemporaryDirectory(prefix='import-', dir=self.home) as work:
            work = Path(work)
            unpacked = work / 'unpacked'
            unpacked.mkdir()
            if source.is_dir():
                shutil.copytree(source, unpacked, dirs_exist_ok=True, symlinks=True)
            elif source.suffix.lower() == '.zip':
                with zipfile.ZipFile(source) as archive:
                    if sum(x.file_size for x in archive.infolist()) > 8 * 1024**3:
                        raise ValueError('Archive exceeds the 8 GB unpacked size limit.')
                    seen = set()
                    for member in archive.infolist():
                        raw = member.filename.replace('\\', '/')
                        parts = PurePosixPath(raw).parts
                        if not parts or raw.startswith('/') or any(p in ('.', '..') or ':' in p or p.endswith((' ', '.')) for p in parts):
                            raise ValueError('Unsafe archive path: ' + member.filename)
                        key = raw.rstrip('/').lower()
                        if key in seen:
                            raise ValueError('Archive contains duplicate paths.')
                        seen.add(key)
                        if (member.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError('Symbolic links are not supported.')
                        dest = unpacked.joinpath(*parts)
                        if member.is_dir():
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(member) as inp, dest.open('wb') as out:
                                shutil.copyfileobj(inp, out)
            else:
                raise ValueError('Choose a mod folder or a ZIP archive.')
            base = unpacked
            children = list(base.iterdir())
            if len(children) == 1 and children[0].is_dir() and children[0].name != 'files':
                base = children[0]
            for entry in base.rglob('*'):
                if entry.is_symlink():
                    raise ValueError('Symbolic links are not supported.')
            meta_file = base / 'mod.json'
            meta = json.loads(meta_file.read_text('utf-8-sig')) if meta_file.exists() else {}
            if not isinstance(meta, dict):
                raise ValueError('mod.json must contain a JSON object.')
            if meta.get('kind') == 'umt_patch':
                return self.import_patch(base, meta, work)
            if meta.get('kind') == 'binary_patch':
                return self.import_binary_patch(base, meta, work)

            # Auto-detect standalone binary patch (e.g. .xdelta, .bspatch, .thpatch) without mod.json
            patch_candidates = [
                f for f in base.rglob('*')
                if f.is_file() and f.suffix.lower() in ('.xdelta', '.vcdiff', '.bspatch', '.bsdiff', '.thpatch')
            ]
            if patch_candidates:
                if len(patch_candidates) > 1:
                    raise ValueError('Multiple patch files found in mod package. Expected only one.')
                patch_file = patch_candidates[0]
                patch_ext = patch_file.suffix.lower()
                fmt = 'xdelta' if patch_ext in ('.xdelta', '.vcdiff') else 'bspatch' if patch_ext in ('.bspatch', '.bsdiff') else 'thpatch'
                rel_parts = list(patch_file.relative_to(base).parts)
                # Determine target file
                target_file = 'data.win'
                for allowed in ALLOWED:
                    if allowed.lower() in patch_file.name.lower():
                        target_file = allowed
                        break
                # Synthesize binary patch structure into work
                safe_patch_dir = base / 'patch'
                safe_patch_dir.mkdir(parents=True, exist_ok=True)
                dest_patch = safe_patch_dir / (target_file + patch_ext)
                if patch_file != dest_patch:
                    shutil.move(str(patch_file), str(dest_patch))
                # Remove extraneous helper executables from third-party zips (e.g. DeltaPatcher.exe)
                for f in list(base.rglob('*')):
                    if f.is_file() and f != dest_patch and f.name != 'preview.png':
                        if f.suffix.lower() in ('.exe', '.dll') or f.name.lower().startswith(('readme', 'changelog', 'license')):
                            try:
                                f.unlink()
                            except OSError:
                                pass
                auto_meta = {
                    'name': str(meta.get('name', patch_file.stem.replace('_', ' ').strip()))[:120],
                    'version': str(meta.get('version', '1.0'))[:60],
                    'description': str(meta.get('description', ''))[:4000],
                    'kind': 'binary_patch',
                    'format': fmt,
                    'target_file': target_file,
                    'patch_file': f'patch/{target_file}{patch_ext}',
                }
                return self.import_binary_patch(base, auto_meta, work)

            payload = base / 'files' if (base / 'files').is_dir() else base
            files = {}
            for file in payload.rglob('*'):
                if not file.is_file():
                    continue
                rel = file.relative_to(payload).as_posix()
                if rel.lower() in ('mod.json', 'preview.png') or rel.lower().startswith(('readme', 'license')):
                    continue
                if rel not in ALLOWED or not (self.root / rel).is_file():
                    raise ValueError('Unsupported replacement: ' + rel + '\nThis manager supports data.win, options.ini and the installed video assets.')
                files[rel] = digest(file)
            if not files:
                raise ValueError('No supported replacement files found in this mod.')
            mod_id = uuid.uuid4().hex
            dest = work / 'ready'
            (dest / 'files').mkdir(parents=True)
            for name in files:
                shutil.copy2(payload / name, dest / 'files' / name)
            record = {'name': str(meta.get('name', source.stem))[:120],
                      'version': str(meta.get('version', '1.0'))[:60],
                      'description': str(meta.get('description', ''))[:4000], 'files': files,
                      'original_data_sha256': self.original_hash(meta.get('original_data_sha256'))}
            self.import_preview(base, dest, record)
            write_json(dest / 'mod.json', record)
            os.replace(dest, self.mods / mod_id)
            return mod_id

    def import_binary_patch(self, base, meta, work):
        fmt = meta.get('format', 'thpatch')
        target_file = meta.get('target_file', 'data.win')
        _VALID_FORMATS = ('thpatch', 'bspatch', 'xdelta')
        if fmt not in _VALID_FORMATS:
            raise ValueError(f'Unsupported binary patch format: {fmt!r}. Supported: ' + ', '.join(_VALID_FORMATS))
        if target_file not in ALLOWED:
            raise ValueError(f'Unsupported binary patch target file: {target_file!r}')

        patch_rel = meta.get('patch_file') or 'patch/data.win.thpatch'
        try:
            parts = PurePosixPath(patch_rel).parts
            if not parts or parts[0] != 'patch' or '..' in parts or patch_rel.startswith('/'):
                raise ValueError()
        except Exception:
            raise ValueError(f'Binary patch has an unsafe patch_file path: {patch_rel!r}')

        _DOCS = {'readme.md', 'readme.txt', 'license', 'license.md', 'license.txt'}
        allowed_members = {'mod.json', 'preview.png', patch_rel}
        for file in base.rglob('*'):
            if file.is_file():
                rel = file.relative_to(base).as_posix()
                if rel not in allowed_members and rel.lower() not in _DOCS:
                    raise ValueError('Binary patch package contains an unsupported file: ' + rel)

        patch = base.joinpath(*PurePosixPath(patch_rel).parts)
        if not patch.is_file():
            raise ValueError(f'Binary patch file not found inside the package: {patch_rel}')

        info = patching.inspect_patch(patch, fmt=fmt)
        original = self.original_hash(meta.get('original_data_sha256'))
        result = self.original_hash(meta.get('result_data_sha256'))
        if original is not None and original != info['source_sha256']:
            raise ValueError('Binary patch declared original_data_sha256 does not match the patch header.')
        if result is not None and result != info['target_sha256']:
            raise ValueError('Binary patch declared result_data_sha256 does not match the patch header.')
        if original is None:
            original = info['source_sha256']
        if result is None:
            result = info['target_sha256']

        _EXT = {'thpatch': '.thpatch', 'bspatch': '.bspatch', 'xdelta': '.xdelta'}
        stored_patch_name = target_file + _EXT[fmt]

        mod_id = uuid.uuid4().hex
        dest = work / 'ready'
        (dest / 'files').mkdir(parents=True)
        shutil.copy2(patch, dest / 'files' / stored_patch_name)
        record = {'name': str(meta.get('name', 'Game patch'))[:120],
                  'version': str(meta.get('version', '1.0'))[:60],
                  'description': str(meta.get('description', ''))[:4000],
                  'kind': 'binary_patch', 'format': fmt, 'target_file': target_file,
                  'files': {target_file: result or 'unverified'},
                  'patch_file': stored_patch_name, 'patch_sha256': digest(patch),
                  'original_data_sha256': original, 'result_data_sha256': result}
        self.import_preview(base, dest, record)
        write_json(dest / 'mod.json', record)
        os.replace(dest, self.mods / mod_id)
        return mod_id

    @staticmethod
    def original_hash(value):
        import re
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', value):
            raise ValueError('original_data_sha256 must be a SHA-256 hex string.')
        return value.lower()

    @staticmethod
    def import_preview(base, dest, record):
        """Keep artwork outside the deployed game files."""
        preview = base / 'preview.png'
        if preview.is_file():
            from PIL import Image
            if preview.stat().st_size > 8 * 1024**2:
                raise ValueError('Mod thumbnail exceeds 8 MB.')
            with Image.open(preview) as image:
                if image.width > 4096 or image.height > 4096:
                    raise ValueError('Mod thumbnail exceeds 4096 pixels.')
                image.convert('RGB').save(dest / 'preview.png')
            record['preview'] = 'preview.png'

    def import_patch(self, base, meta, work):
        payload = base / 'patch'
        if not (payload / 'bundle.json').is_file():
            raise ValueError('Patch is missing patch/bundle.json.')
        self.validate_patch(payload)
        files = {f.relative_to(payload).as_posix(): digest(f) for f in payload.rglob('*') if f.is_file()}
        if sum(f.stat().st_size for f in payload.rglob('*') if f.is_file()) > 32 * 1024**2:
            raise ValueError('Patch package exceeds 32 MB.')
        mod_id = uuid.uuid4().hex
        dest = work / 'ready'
        shutil.copytree(payload, dest / 'files')
        record = {'name': str(meta.get('name', 'Game patch'))[:120], 'version': str(meta.get('version', '1.0'))[:60],
                  'description': str(meta.get('description', ''))[:4000], 'kind': 'umt_patch', 'files': files,
                  'original_data_sha256': self.original_hash(meta.get('original_data_sha256'))}
        self.import_preview(base, dest, record)
        write_json(dest / 'mod.json', record)
        os.replace(dest, self.mods / mod_id)
        return mod_id

    @staticmethod
    def validate_patch(payload):
        import re
        bundle = json.loads((payload / 'bundle.json').read_text('utf-8-sig'))
        if bundle.get('format') != 1:
            raise ValueError('Unsupported patch format.')
        def file(rel):
            if not isinstance(rel, str) or not rel or '\\' in rel:
                raise ValueError('Invalid patch path.')
            parts = PurePosixPath(rel).parts
            if rel.startswith('/') or any(p in ('.', '..') or ':' in p for p in parts):
                raise ValueError('Unsafe patch path: ' + rel)
            target = payload.joinpath(*parts)
            if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(payload.resolve()):
                raise ValueError('Missing or unsafe patch file: ' + rel)
        def identifier(name):
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise ValueError('Invalid patch asset name.')
        shader = bundle['shader']
        identifier(shader['name']); file(shader['vertex']); file(shader['pixel'])
        for script in bundle['scripts']:
            identifier(script['name']); file(script['file'])
        for event in bundle['events']:
            identifier(event['object']); file(event['file'])
            if event['type'] not in ('Create', 'Step', 'Draw', 'CleanUp') or not isinstance(event['subtype'], int) or not 0 <= event['subtype'] <= 100:
                raise ValueError('Unsupported patch event.')
            if event['operation'] not in ('append', 'prepend', 'replace'):
                raise ValueError('Unsupported patch operation.')
        for obj in bundle['persistent_objects']: identifier(obj)

    def compose_archive(self, base, output, packages):
        tool = self.home / 'tools' / 'umt' / 'UndertaleModCli.exe'
        resource = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        script = resource / 'compose_patch.csx'
        if not tool.is_file() or not script.is_file():
            raise RuntimeError('Patch compiler is missing. Rebuild/install the current manager and UndertaleModTool CLI.')
        plan = self.transaction / 'patch-plan.json'
        write_json(plan, {'packages': [str(p) for p in packages]})
        env = dict(os.environ, TRIBAL_PATCH_PLAN=str(plan))
        result = subprocess.run([str(tool), 'load', str(base), '-s', str(script), '-o', str(output), '-f'],
                                env=env, capture_output=True, text=True, creationflags=0x08000000)
        if result.returncode or not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError('Patch compilation failed. Original files were preserved.\n' + (result.stdout + result.stderr)[-3500:])

    def apply(self, active):
        if game_running():
            raise RuntimeError('Close Tribal Hunter before changing mods.')
        self.reload_state()
        catalog = {m['id']: m for m in self.catalog()}
        desired = {}
        patches = []
        binary_patches = {}
        for mod_id in active:
            if mod_id not in catalog:
                raise ValueError('An enabled mod is missing. Restore originals to recover.')
            if catalog[mod_id].get('kind') == 'umt_patch':
                package = self.mods / mod_id / 'files'
                self.validate_patch(package)
                for name, expected in catalog[mod_id]['files'].items():
                    source = package / name
                    if source.is_symlink() or not source.resolve().is_relative_to(package.resolve()) or digest(source) != expected:
                        raise ValueError('Imported patch files changed. Reimport: ' + catalog[mod_id]['name'])
                patches.append((catalog[mod_id], package))
                continue
            if catalog[mod_id].get('kind') == 'binary_patch':
                record = catalog[mod_id]
                patch_file = record.get('patch_file')
                fmt = record.get('format', 'thpatch')
                target_file = record.get('target_file', 'data.win')
                _VALID_FMTS = ('thpatch', 'bspatch', 'xdelta')
                if fmt not in _VALID_FMTS:
                    raise ValueError('Imported binary patch has unknown format: ' + fmt + '. Reimport: ' + record['name'])
                if target_file not in ALLOWED:
                    raise ValueError('Imported binary patch has unsupported target: ' + target_file + '. Reimport: ' + record['name'])
                if not patch_file or set(record['files']) != {target_file}:
                    raise ValueError('Imported binary patch manifest is invalid. Reimport: ' + record['name'])
                source = self.mods / mod_id / 'files' / patch_file
                if source.is_symlink() or digest(source) != record.get('patch_sha256'):
                    raise ValueError('Imported binary patch changed. Reimport: ' + record['name'])
                info = patching.inspect_patch(source, fmt=fmt)
                if info.get('source_sha256') is not None and info['source_sha256'] != record.get('original_data_sha256'):
                    raise ValueError('Imported binary patch hashes are invalid. Reimport: ' + record['name'])
                if info.get('target_sha256') is not None and info['target_sha256'] != record['files'][target_file]:
                    raise ValueError('Imported binary patch hashes are invalid. Reimport: ' + record['name'])
                if target_file in desired:
                    raise ValueError('Conflict: ' + target_file + ' is changed by two mods. Disable the other mod first.')
                binary_patches[mod_id] = (source, fmt, target_file)
                expected_target_hash = info.get('target_sha256') or record['files'][target_file]
                desired[target_file] = (mod_id, source, expected_target_hash)
                continue
            for name, expected in catalog[mod_id]['files'].items():
                if name not in ALLOWED:
                    raise ValueError('Unsupported file in mod manifest.')
                if name in desired:
                    raise ValueError('Conflict: ' + name + ' is replaced by both ' + catalog[desired[name][0]]['name'] + ' and ' + catalog[mod_id]['name'] + '. Disable the other mod first.')
                source = self.mods / mod_id / 'files' / name
                if source.is_symlink() or digest(source) != expected:
                    raise ValueError('Imported mod files changed. Reimport the mod: ' + catalog[mod_id]['name'])
                desired[name] = (mod_id, source, expected)
        if patches and 'data.win' not in desired:
            desired['data.win'] = (None, None, None)
        targets = sorted(set(desired) | set(self.state['deployed']))
        if not targets:
            return
        originals = dict(self.state['originals'])
        for name in targets:
            current = self.root / name
            if current.is_symlink():
                raise ValueError('Game file is a symbolic link: ' + name)
            expected = self.state['deployed'].get(name, originals.get(name))
            actual = digest(current)
            if expected and actual != expected:
                raise ValueError('Game file changed outside the manager: ' + name + '\nNo files were changed. Preserve the external edit and reconcile it with the backup before retrying.')
            backup = self.backups / name
            if name not in originals:
                temp = self.backups / (name + '.tmp')
                shutil.copy2(current, temp)
                if digest(temp) != actual:
                    raise RuntimeError('Game file changed during backup: ' + name)
                os.replace(temp, backup)
                originals[name] = actual
            if digest(backup) != originals[name]:
                raise RuntimeError('Original backup is missing or damaged: ' + name)
        for mod_id in active:
            record = catalog[mod_id]
            if record.get('kind') != 'umt_patch' and 'data.win' in record['files'] and record.get('original_data_sha256'):
                if record['original_data_sha256'] != originals.get('data.win'):
                    raise ValueError('Mod does not match this original game build: ' + record['name'])
        # Backups are recorded before deployment, including if staging subsequently fails.
        self.state['originals'] = originals
        write_json(self.state_file, self.state)
        self.transaction.mkdir()
        (self.transaction / 'rollback').mkdir()
        (self.transaction / 'staged').mkdir()
        try:
            for name in targets:
                shutil.copy2(self.root / name, self.transaction / 'rollback' / name)
                source = desired[name][1] if name in desired else self.backups / name
                staged = self.transaction / 'staged' / name
                binary_patch_applied_to_staged = False
                if name in desired and desired[name][0] in binary_patches:
                    patch_source, bp_fmt, bp_target = binary_patches[desired[name][0]]
                    bp_out = self.transaction / ('binary-base.' + name) if (name == 'data.win' and patches) else staged
                    patching.apply_patch(self.backups / name, patch_source, bp_out, fmt=bp_fmt)
                    if name == 'data.win' and patches:
                        source = bp_out
                    else:
                        binary_patch_applied_to_staged = True
                if name == 'data.win' and patches:
                    for record, package in patches:
                        if record.get('original_data_sha256') and record['original_data_sha256'] != originals['data.win']:
                            raise ValueError('Patch does not match this original game build: ' + record['name'])
                    self.compose_archive(source or self.backups / name, staged, [p for _, p in patches])
                    expected = digest(staged)
                    desired[name] = (None, staged, expected)
                else:
                    if not binary_patch_applied_to_staged and source != staged:
                        shutil.copy2(source, staged)
                    staged_digest = digest(staged)
                    if name in desired:
                        expected = staged_digest if desired[name][2] == 'unverified' else desired[name][2]
                        if desired[name][2] == 'unverified':
                            desired[name] = (desired[name][0], desired[name][1], staged_digest)
                    else:
                        expected = originals[name]
                if digest(staged) != expected:
                    raise RuntimeError('File changed while staging: ' + name)
            if game_running():
                raise RuntimeError('The game started while staging. Close it before changing mods.')
            for name in targets:
                if digest(self.root / name) != digest(self.transaction / 'rollback' / name):
                    raise RuntimeError('Game file changed while staging: ' + name)
            write_json(self.transaction / 'journal.json', {'files': targets, 'state': self.state})
            for name in targets:
                os.replace(self.transaction / 'staged' / name, self.root / name)
            updated = {'version': 1, 'active': list(active), 'originals': originals,
                       'deployed': {name: value[2] for name, value in desired.items()}}
            write_json(self.state_file, updated)
        except Exception:
            self.recover()
            raise
        else:
            # Removing the journal commits the transaction. Leftover staging is harmless.
            (self.transaction / 'journal.json').unlink()
            self.state = updated
            shutil.rmtree(self.transaction)

    def remove(self, mod_id):
        self.reload_state()
        if mod_id in self.state['active']:
            raise ValueError('Disable the mod before removing it.')
        if not any(m['id'] == mod_id for m in self.catalog()):
            raise ValueError('Mod not found.')
        shutil.rmtree(self.mods / mod_id)



def main():
    from desktop_ui import App
    app = App()
    if '--smoke-test' in sys.argv:
        def report():
            write_json(app.manager.home / 'packaged-smoke.json', {
                'game_root': str(app.manager.root), 'frozen': bool(getattr(sys, 'frozen', False)),
                'title': app.title(), 'library_loaded': app.page == 'Library',
                'mod_count': len(app.manager.catalog())})
            from PIL import ImageGrab
            app.update_idletasks()
            x, y = app.winfo_rootx(), app.winfo_rooty()
            captures = app.manager.root / '.impeccable' / 'review'
            captures.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(bbox=(x, y, x+app.winfo_width(), y+app.winfo_height())).save(captures / 'packaged-empty.png')
            app.destroy()
        app.after(2000, report)
    app.mainloop()


if __name__ == '__main__':
    if '--make-patch' in sys.argv:
        import argparse
        parser = argparse.ArgumentParser(
            description='Create a mod ZIP containing a binary patch.',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=(
                'Supported formats:\n'
                '  thpatch  Native format (default, pure Python)\n'
                '  bspatch  BSDiff 4.x binary diff (bundled bsdiff.exe)\n'
                '  xdelta   XDelta3 VCDIFF (bundled xdelta3.exe)\n'
                '\n'
                'Examples:\n'
                '  manager.py --make-patch clean\\data.win mod\\data.win MyMod.zip --name "My Mod"\n'
                '  manager.py --make-patch clean\\data.win mod\\data.win MyMod.zip --name "My Mod" --format bspatch\n'
                '  manager.py --make-patch clean\\titlescreen_loop.mp4 mod\\titlescreen_loop.mp4 MyVid.zip --name "My Video" --format xdelta --target-file titlescreen_loop.mp4\n'
            ),
        )
        parser.add_argument('--make-patch', nargs=3, required=True, metavar=('CLEAN', 'MODIFIED', 'ZIP'))
        parser.add_argument('--name', required=True)
        parser.add_argument('--version', default='1.0.0')
        parser.add_argument('--format', default='thpatch', choices=('thpatch', 'bspatch', 'xdelta'),
                            dest='fmt', metavar='FORMAT',
                            help='Patch format: thpatch (default), bspatch, or xdelta')
        parser.add_argument('--target-file', default='data.win', dest='target_file',
                            help='Game file this patch targets (default: data.win)')
        parser.add_argument('--preview')
        parser.add_argument('--quiet', action='store_true')
        args = parser.parse_args()
        try:
            patching.create_package(
                *args.make_patch, args.name, args.version, args.preview,
                description='', fmt=args.fmt, target_file=args.target_file,
            )
        except Exception as exc:
            if getattr(sys, 'frozen', False):
                if not args.quiet:
                    ctypes.windll.user32.MessageBoxW(None, str(exc), 'Create mod patch', 0x10)
            else:
                print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        if getattr(sys, 'frozen', False) and not args.quiet:
            ctypes.windll.user32.MessageBoxW(None, 'Patch ZIP created: ' + args.make_patch[2], 'Create mod patch', 0)
        raise SystemExit(0)
    from one_click import SCHEME, queue_link, register_protocol
    if '--register-protocol' in sys.argv:
        register_protocol(GAME_ROOT / 'Tribal Hunter Mod Manager.exe')
        raise SystemExit(0)
    links = [arg for arg in sys.argv[1:] if arg.lower().startswith(SCHEME + ':')]
    if links:
        try:
            if len(links) != 1:
                raise ValueError('Open one GameBanana install link at a time.')
            queue_link(GAME_ROOT, links[0])
        except Exception as exc:
            if os.name == 'nt':
                ctypes.windll.user32.MessageBoxW(None, str(exc), 'GameBanana install', 0x10)
            raise SystemExit(1)
    # Forward links to the running app instead of starting another deployment engine.
    mutex = None
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, 'Local\\TribalHunterModManager')
        if not mutex:
            raise RuntimeError('Could not acquire the mod manager lock.')
        if kernel.GetLastError() == 183:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
            if not links:
                ctypes.windll.user32.MessageBoxW(None, 'The mod manager is already open.', 'Tribal Hunter Mod Manager', 0)
            raise SystemExit(0)
    try:
        main()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.CloseHandle(mutex)
