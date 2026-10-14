import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('manager', Path(__file__).with_name('manager.py'))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)

class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'game'
        self.root.mkdir()
        (self.root / 'data.win').write_bytes(b'clean game')
        (self.root / 'options.ini').write_bytes(b'clean options')
        self.manager = mm.Manager(self.root)
        self.patch = patch.object(mm, 'game_running', return_value=False)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def make_mod(self, label='test', data=b'modded game', name='data.win'):
        folder = self.base / label
        folder.mkdir()
        (folder / name).write_bytes(data)
        return self.manager.import_mod(folder)

    def test_enable_disable_preserves_original(self):
        mod = self.make_mod()
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'modded game')
        self.assertEqual((self.manager.backups / 'data.win').read_bytes(), b'clean game')
        self.manager = mm.Manager(self.root)
        self.manager.apply([])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.assertEqual(self.manager.state['active'], [])

    def test_open_manager_can_disable_after_another_manager_updates(self):
        one = self.make_mod('one', b'first build')
        two = self.make_mod('two', b'second build')
        self.manager.apply([one])
        other = mm.Manager(self.root)
        other.apply([two])
        # The first manager still holds the old hash, as the GUI did during the update.
        self.assertEqual(self.manager.state['active'], [one])
        self.manager.apply([])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.manager.apply([two])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'second build')
        self.manager.apply([])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')

    def test_refresh_reads_committed_active_mods(self):
        mod = self.make_mod()
        other = mm.Manager(self.root)
        other.apply([mod])
        self.assertEqual(self.manager.reload_state()['active'], [mod])
        self.assertEqual(self.manager.state['deployed'], other.state['deployed'])

    def make_patch(self):
        folder = self.base / 'visuals'
        payload = folder / 'patch'
        payload.mkdir(parents=True)
        (payload / 'vertex.bin').write_bytes(b'vertex')
        (payload / 'pixel.bin').write_bytes(b'pixel')
        (payload / 'create.gml').write_text('x=0;')
        bundle = {'format': 1, 'name': 'Visuals', 'shader': {'name': 'shd_test', 'vertex': 'vertex.bin', 'pixel': 'pixel.bin'},
                  'scripts': [], 'events': [{'object': 'obj_test', 'type': 'Create', 'subtype': 0, 'file': 'create.gml', 'operation': 'replace'}],
                  'persistent_objects': ['obj_test']}
        (payload / 'bundle.json').write_text(json.dumps(bundle))
        (folder / 'mod.json').write_text(json.dumps({'name': 'Visuals', 'kind': 'umt_patch'}))
        return self.manager.import_mod(folder)

    def test_patch_and_archive_enable_disable_independently(self):
        fps = self.make_mod('fps', b'fps archive')
        visuals = self.make_patch()
        def compose(base, output, packages):
            self.assertEqual(len(packages), 1)
            output.write_bytes(base.read_bytes() + b' + visuals')
        with patch.object(self.manager, 'compose_archive', side_effect=compose):
            self.manager.apply([fps, visuals])
            self.assertEqual((self.root / 'data.win').read_bytes(), b'fps archive + visuals')
            self.assertEqual(self.manager.state['active'], [fps, visuals])
            self.manager.apply([fps])
            self.assertEqual((self.root / 'data.win').read_bytes(), b'fps archive')
            self.manager.apply([visuals])
            self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game + visuals')
            self.manager.apply([])
            self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.assertEqual((self.manager.backups / 'data.win').read_bytes(), b'clean game')

    def test_failed_patch_compile_preserves_current_game(self):
        fps = self.make_mod('fps', b'fps archive')
        visuals = self.make_patch()
        self.manager.apply([fps])
        with patch.object(self.manager, 'compose_archive', side_effect=RuntimeError('compile failed')):
            with self.assertRaisesRegex(RuntimeError, 'compile failed'): self.manager.apply([fps, visuals])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'fps archive')
        self.assertEqual(self.manager.state['active'], [fps])
        self.assertFalse(self.manager.transaction.exists())

    def test_modified_patch_is_rejected(self):
        mod = self.make_patch()
        (self.manager.mods / mod / 'files' / 'pixel.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'): self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')

    def test_patch_manifest_cannot_read_outside_package(self):
        mod = self.make_patch()
        package = self.manager.mods / mod / 'files'
        bundle = json.loads((package / 'bundle.json').read_text())
        bundle['shader']['pixel'] = '../outside.bin'
        (package / 'bundle.json').write_text(json.dumps(bundle))
        with self.assertRaisesRegex(ValueError, 'Unsafe'): self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')

    def test_conflict_is_rejected_without_changes(self):
        one = self.make_mod('one')
        two = self.make_mod('two', b'other mod')
        self.manager.apply([one])
        with self.assertRaisesRegex(ValueError, 'Conflict'):
            self.manager.apply([one, two])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'modded game')
        self.assertEqual(self.manager.state['active'], [one])

    def test_external_changes_preserved(self):
        mod = self.make_mod()
        self.manager.apply([mod])
        (self.root / 'data.win').write_bytes(b'external edit')
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.manager.apply([])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'external edit')

    def test_modified_import_blocked(self):
        mod = self.make_mod()
        (self.manager.mods / mod / 'files' / 'data.win').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Reimport'):
            self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')

    def test_archive_traversal_rejected(self):
        archive = self.base / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('../outside.txt', 'bad')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.manager.import_mod(archive)
        self.assertFalse((self.manager.home / 'outside.txt').exists())

    def test_wrapped_zip_and_metadata(self):
        archive = self.base / 'good.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('Example/mod.json', json.dumps({'name': 'Example', 'version': '2'}))
            z.writestr('Example/files/data.win', b'zip game')
        mod = self.manager.import_mod(archive)
        self.manager.apply([mod])
        self.assertEqual(self.manager.catalog()[0]['name'], 'Example')
        self.assertEqual((self.root / 'data.win').read_bytes(), b'zip game')

    def test_failure_rolls_back_partial_deployment(self):
        folder = self.base / 'multiple'
        folder.mkdir()
        (folder / 'data.win').write_bytes(b'new data')
        (folder / 'options.ini').write_bytes(b'new options')
        mod = self.manager.import_mod(folder)
        replace = mm.os.replace
        def fail_on_second(source, dest):
            if Path(source).parent == self.manager.transaction / 'staged' and Path(source).name == 'options.ini':
                raise OSError('simulated deployment failure')
            return replace(source, dest)
        with patch.object(mm.os, 'replace', side_effect=fail_on_second):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.assertEqual((self.root / 'options.ini').read_bytes(), b'clean options')
        self.assertEqual(self.manager.state['active'], [])
        self.assertFalse(self.manager.transaction.exists())

    def test_restart_recovers_interrupted_update(self):
        transaction = self.manager.transaction
        (transaction / 'rollback').mkdir(parents=True)
        (transaction / 'rollback' / 'data.win').write_bytes(b'clean game')
        mm.write_json(transaction / 'journal.json', {'files': ['data.win'], 'state': self.manager.state})
        (self.root / 'data.win').write_bytes(b'partial update')
        mm.Manager(self.root)
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.assertFalse(transaction.exists())

    def test_running_game_blocks_changes(self):
        mod = self.make_mod()
        with patch.object(mm, 'game_running', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'Close'):
                self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')

    def test_independent_mods_and_remove(self):
        one = self.make_mod('one')
        two = self.make_mod('two', b'new settings', 'options.ini')
        self.manager.apply([one, two])
        with self.assertRaisesRegex(ValueError, 'Disable'):
            self.manager.remove(one)
        self.manager.apply([two])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game')
        self.assertEqual((self.root / 'options.ini').read_bytes(), b'new settings')
        self.manager.remove(one)
        self.assertEqual(len(self.manager.catalog()), 1)

    def test_unsupported_payload_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.make_mod('unsafe', b'executable', 'example.exe')
        self.assertEqual(self.manager.catalog(), [])

if __name__ == '__main__':
    unittest.main(verbosity=2)
