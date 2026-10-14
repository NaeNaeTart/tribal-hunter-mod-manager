import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib
import zipfile
import delta_patch as dp
from manager import Manager


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'clean.win'
        self.delta = self.base / 'data.win.thpatch'
        self.output = self.base / 'patched.win'
        self.original = b'0123456789' * 30000
        self.source.write_bytes(self.original)

    def fixture(self, instructions, target, source=None):
        source = self.original if source is None else source
        header = dp.HEADER.pack(dp.MAGIC, len(source), len(target), hashlib.sha256(source).digest(), hashlib.sha256(target).digest())
        self.delta.write_bytes(header + zlib.compress(instructions))

    def test_copy_and_literal_across_stream_boundaries(self):
        target = self.original[100:250100] + b'new code' + self.original[:100]
        self.fixture(b'\0' + struct.pack('<QI', 100, 250000) + b'\1' + struct.pack('<I', 8) + b'new code' + b'\0' + struct.pack('<QI', 0, 100) + b'\xff', target)
        dp.apply_patch(self.source, self.delta, self.output)
        self.assertEqual(self.output.read_bytes(), target)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_wrong_source_rejected_before_output(self):
        self.fixture(b'\0' + struct.pack('<QI', 0, len(self.original)) + b'\xff', self.original)
        self.source.write_bytes(b'X' + self.original[1:])
        with self.assertRaisesRegex(ValueError, 'matching clean'):
            dp.apply_patch(self.source, self.delta, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_instructions_remove_partial_output(self):
        cases = [b'\0' + struct.pack('<QI', len(self.original) - 2, 10) + b'\xff',
                 b'\1' + struct.pack('<I', dp.MAX_LITERAL + 1), b'\1' + struct.pack('<I', 0),
                 b'\2', b'\1' + struct.pack('<I', 4) + b'ab', b'\xff']
        for instruction in cases:
            with self.subTest(instruction=instruction[:6]):
                self.fixture(instruction, b'test')
                with self.assertRaises(ValueError):
                    dp.apply_patch(self.source, self.delta, self.output)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.source.read_bytes(), self.original)

    def test_output_hash_and_trailing_data_rejected(self):
        instruction = b'\1' + struct.pack('<I', 4) + b'bad!' + b'\xff'
        for suffix in (b'', b'extra'):
            self.fixture(instruction + suffix, b'good')
            with self.assertRaises(ValueError):
                dp.apply_patch(self.source, self.delta, self.output)
            self.assertFalse(self.output.exists())
        self.fixture(b'\1' + struct.pack('<I', 4) + b'good' + b'\xff', b'good')
        self.delta.write_bytes(self.delta.read_bytes() + b'trailer')
        with self.assertRaisesRegex(ValueError, 'trailing'):
            dp.apply_patch(self.source, self.delta, self.output)

    def test_truncated_compression_and_existing_output_preserved(self):
        self.fixture(b'\0' + struct.pack('<QI', 0, len(self.original)) + b'\xff', self.original)
        self.delta.write_bytes(self.delta.read_bytes()[:-3])
        with self.assertRaises(ValueError):
            dp.apply_patch(self.source, self.delta, self.output)
        self.assertFalse(self.output.exists())
        self.output.write_bytes(b'preexisting')
        with self.assertRaises(FileExistsError):
            dp.apply_patch(self.source, self.delta, self.output)
        self.assertEqual(self.output.read_bytes(), b'preexisting')

    def test_publisher_roundtrip_shifted_archive_sections(self):
        def archive(chunks):
            body = b''.join(tag + struct.pack('<I', len(data)) + data for tag, data in chunks)
            return b'FORM' + struct.pack('<I', len(body)) + body
        self.source.write_bytes(archive([(b'CODE', b'old code'), (b'AUDO', self.original)]))
        target = archive([(b'CODE', b'much longer new code'), (b'AUDO', self.original)])
        modified = self.base / 'modified.win';modified.write_bytes(target)
        dp.create_patch(self.source, modified, self.delta)
        self.assertLess(self.delta.stat().st_size, 2000)
        dp.apply_patch(self.source, self.delta, self.output)
        self.assertEqual(self.output.read_bytes(), target)

    def test_large_literals_are_bounded(self):
        target = b'new' * (dp.MAX_LITERAL // 3 + 20)
        modified = self.base / 'modified.win';modified.write_bytes(target)
        dp.create_patch(self.source, modified, self.delta)
        dp.apply_patch(self.source, self.delta, self.output)
        self.assertEqual(self.output.read_bytes(), target)


class ManagerDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name);self.root = self.base / 'game';self.root.mkdir()
        (self.root / 'data.win').write_bytes(b'clean game archive')
        self.modified = self.base / 'modified.win';self.modified.write_bytes(b'new code and game archive')
        self.package = self.base / 'mod.zip'
        dp.create_package(self.root / 'data.win', self.modified, self.package, 'Test patch')
        self.manager = Manager(self.root)
        self.running = patch('manager.game_running', return_value=False);self.running.start();self.addCleanup(self.running.stop)

    def test_zip_contains_no_archive_and_enable_disable_restore(self):
        with zipfile.ZipFile(self.package) as archive:
            self.assertEqual(set(archive.namelist()), {'mod.json', 'README.md', 'patch/data.win.thpatch'})
        mod = self.manager.import_mod(self.package)
        self.assertEqual(self.manager.catalog()[0]['kind'], 'binary_patch')
        self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), self.modified.read_bytes())
        self.manager.apply([])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game archive')
        self.manager.apply([mod]);self.manager.apply([])
        self.assertEqual(self.manager.state['active'], [])

    def test_wrong_build_and_tampered_payload_preserve_game(self):
        mod = self.manager.import_mod(self.package)
        (self.root / 'data.win').write_bytes(b'different game build')
        with self.assertRaisesRegex(ValueError, 'match this original'):
            self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'different game build')
        self.assertEqual(self.manager.state['active'], [])
        (self.manager.mods / mod / 'files/data.win.thpatch').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'patch changed'):
            self.manager.apply([mod])

    def test_conflicts_both_orders_and_staging_failure(self):
        mod = self.manager.import_mod(self.package)
        replacement = self.base / 'replacement';replacement.mkdir();(replacement / 'data.win').write_bytes(b'other')
        other = self.manager.import_mod(replacement)
        for active in ([mod, other], [other, mod]):
            with self.assertRaisesRegex(ValueError, 'Conflict'):
                self.manager.apply(active)
        with patch('delta_patch.apply_patch', side_effect=ValueError('damaged delta')):
            with self.assertRaisesRegex(ValueError, 'damaged'):
                self.manager.apply([mod])
        self.assertEqual((self.root / 'data.win').read_bytes(), b'clean game archive')
        self.assertEqual(self.manager.state['active'], [])
        self.assertFalse(self.manager.transaction.exists())

    def test_missing_hash_and_included_full_archive_rejected(self):
        unpacked = self.base / 'unpacked'
        with zipfile.ZipFile(self.package) as archive:archive.extractall(unpacked)
        meta_file = unpacked / 'mod.json';meta = json.loads(meta_file.read_text());meta.pop('original_data_sha256');meta_file.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, 'declared game hashes'):
            self.manager.import_mod(unpacked)
        meta['original_data_sha256'] = dp.sha256(self.root / 'data.win');meta_file.write_text(json.dumps(meta));(unpacked / 'data.win').write_bytes(b'full archive')
        with self.assertRaisesRegex(ValueError, 'unsupported file'):
            self.manager.import_mod(unpacked)


if __name__ == '__main__':unittest.main()
