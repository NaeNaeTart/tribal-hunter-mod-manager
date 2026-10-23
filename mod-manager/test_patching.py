"""Tests for patching.py — unified multi-format patcher dispatcher."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patching
import delta_patch as dp


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tools_available() -> bool:
    """Return True if bundled bsdiff / xdelta3 executables can be found."""
    try:
        patching._find_tool('bsdiff')
        patching._find_tool('xdelta3')
        return True
    except FileNotFoundError:
        return False


TOOLS_AVAILABLE = _tools_available()
_skip_if_no_tools = unittest.skipUnless(TOOLS_AVAILABLE, 'bundled bsdiff/xdelta3 not found')



class TestFormatDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _make_file(self, name, data=b''):
        p = self.base / name
        p.write_bytes(data)
        return p

    def test_extension_thpatch(self):
        f = self._make_file('a.thpatch')
        self.assertEqual(patching.detect_format(f), 'thpatch')

    def test_extension_bspatch(self):
        self.assertEqual(patching.detect_format(self._make_file('a.bspatch')), 'bspatch')

    def test_extension_bsdiff(self):
        self.assertEqual(patching.detect_format(self._make_file('a.bsdiff')), 'bspatch')

    def test_extension_xdelta(self):
        self.assertEqual(patching.detect_format(self._make_file('a.xdelta')), 'xdelta')

    def test_extension_vcdiff(self):
        self.assertEqual(patching.detect_format(self._make_file('a.vcdiff')), 'xdelta')

    def test_magic_thpatch(self):
        f = self._make_file('a.bin', patching.THPATCH_MAGIC + b'\x00' * 64)
        self.assertEqual(patching.detect_format(f), 'thpatch')

    def test_magic_bsdiff(self):
        f = self._make_file('a.bin', patching.BSDIFF_MAGIC + b'\x00' * 24)
        self.assertEqual(patching.detect_format(f), 'bspatch')

    def test_magic_xdelta(self):
        f = self._make_file('a.bin', b'\xd6\xc3\xc4\x00rest...')
        self.assertEqual(patching.detect_format(f), 'xdelta')

    def test_unknown_raises(self):
        f = self._make_file('a.dat', b'\x00' * 16)
        with self.assertRaises(ValueError):
            patching.detect_format(f)



class TestThpatchViaDispatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src_data = b'FORM' + struct.pack('<I', 16) + b'ABCD' + struct.pack('<I', 8) + b'original'
        self.mod_data = b'FORM' + struct.pack('<I', 16) + b'ABCD' + struct.pack('<I', 8) + b'modified'
        self.source = self.base / 'clean.win'
        self.target = self.base / 'modded.win'
        self.patch  = self.base / 'out.thpatch'
        self.output = self.base / 'result.win'
        self.source.write_bytes(self.src_data)
        self.target.write_bytes(self.mod_data)

    def test_create_and_inspect(self):
        info = patching.create_patch(self.source, self.target, self.patch, fmt='thpatch')
        self.assertEqual(info['format'], 'thpatch')
        self.assertEqual(info['source_sha256'], _sha(self.src_data))
        self.assertEqual(info['target_sha256'], _sha(self.mod_data))
        inspected = patching.inspect_patch(self.patch)
        self.assertEqual(inspected, info)

    def test_apply_roundtrip(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='thpatch')
        patching.apply_patch(self.source, self.patch, self.output)
        self.assertEqual(self.output.read_bytes(), self.mod_data)
        self.assertEqual(self.source.read_bytes(), self.src_data)

    def test_apply_wrong_source_rejected(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='thpatch')
        self.source.write_bytes(b'completely different game build')
        with self.assertRaises(ValueError):
            patching.apply_patch(self.source, self.patch, self.output)
        self.assertFalse(self.output.exists())

    def test_output_same_as_source_rejected(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='thpatch')
        with self.assertRaises(ValueError):
            patching.apply_patch(self.source, self.patch, self.source)

    def test_create_auto_detect_extension(self):
        """create_patch with explicit fmt should produce a file detectable by extension."""
        patching.create_patch(self.source, self.target, self.patch, fmt='thpatch')
        self.assertEqual(patching.detect_format(self.patch), 'thpatch')



@_skip_if_no_tools
class TestBspatchViaDispatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src_data = bytes(range(256)) * 400 + b'original section here'
        self.mod_data = bytes(range(256)) * 400 + b'MODIFIED section here'
        self.source = self.base / 'clean.win'
        self.target = self.base / 'modded.win'
        self.patch  = self.base / 'out.bspatch'
        self.output = self.base / 'result.win'
        self.source.write_bytes(self.src_data)
        self.target.write_bytes(self.mod_data)

    def test_create_and_inspect(self):
        info = patching.create_patch(self.source, self.target, self.patch, fmt='bspatch')
        self.assertEqual(info['format'], 'bspatch')
        self.assertEqual(info['source_sha256'], _sha(self.src_data))
        self.assertEqual(info['target_sha256'], _sha(self.mod_data))
        inspected = patching.inspect_patch(self.patch)
        self.assertEqual(inspected['source_sha256'], info['source_sha256'])
        self.assertEqual(inspected['target_sha256'], info['target_sha256'])

    def test_apply_roundtrip(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='bspatch')
        patching.apply_patch(self.source, self.patch, self.output)
        self.assertEqual(self.output.read_bytes(), self.mod_data)
        self.assertEqual(self.source.read_bytes(), self.src_data)

    def test_apply_wrong_source_rejected_before_output(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='bspatch')
        self.source.write_bytes(b'wrong game build' + b'\x00' * 100)
        with self.assertRaises(ValueError):
            patching.apply_patch(self.source, self.patch, self.output)
        self.assertFalse(self.output.exists())

    def test_raw_bspatch_supported(self):
        bsdiff_exe = patching._find_tool('bsdiff')
        raw_patch = self.base / 'raw.bspatch'
        subprocess.check_call(
            [str(bsdiff_exe), str(self.source), str(self.target), str(raw_patch)],
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        info = patching.inspect_patch(raw_patch, fmt='bspatch')
        self.assertEqual(info['format'], 'bspatch')
        self.assertIsNone(info['source_sha256'])
        patching.apply_patch(self.source, raw_patch, self.output)
        self.assertEqual(self.output.read_bytes(), self.mod_data)

    def test_corrupted_patch_cleans_up_output(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='bspatch')
        data = self.patch.read_bytes()
        self.patch.write_bytes(data[:50] + b'\x00' * 50 + data[100:])
        with self.assertRaises((ValueError, OSError)):
            patching.apply_patch(self.source, self.patch, self.output)
        self.assertFalse(self.output.exists())



@_skip_if_no_tools
class TestXdeltaViaDispatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src_data = b'Tribal Hunter game data ' * 2000
        self.mod_data = b'Tribal Hunter MOD DATA! ' * 2000
        self.source = self.base / 'clean.win'
        self.target = self.base / 'modded.win'
        self.patch  = self.base / 'out.xdelta'
        self.output = self.base / 'result.win'
        self.source.write_bytes(self.src_data)
        self.target.write_bytes(self.mod_data)

    def test_create_and_inspect(self):
        info = patching.create_patch(self.source, self.target, self.patch, fmt='xdelta')
        self.assertEqual(info['format'], 'xdelta')
        self.assertEqual(info['source_sha256'], _sha(self.src_data))
        self.assertEqual(info['target_sha256'], _sha(self.mod_data))
        inspected = patching.inspect_patch(self.patch)
        self.assertEqual(inspected['source_sha256'], info['source_sha256'])
        self.assertEqual(inspected['target_sha256'], info['target_sha256'])

    def test_apply_roundtrip(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='xdelta')
        patching.apply_patch(self.source, self.patch, self.output)
        self.assertEqual(self.output.read_bytes(), self.mod_data)
        self.assertEqual(self.source.read_bytes(), self.src_data)

    def test_apply_wrong_source_rejected_before_output(self):
        patching.create_patch(self.source, self.target, self.patch, fmt='xdelta')
        self.source.write_bytes(b'wrong game build' * 500)
        with self.assertRaises(ValueError):
            patching.apply_patch(self.source, self.patch, self.output)
        self.assertFalse(self.output.exists())

    def test_raw_xdelta_supported(self):
        xdelta_exe = patching._find_tool('xdelta3')
        raw_patch = self.base / 'raw.xdelta'
        subprocess.check_call(
            [str(xdelta_exe), '-e', '-s', str(self.source), str(self.target), str(raw_patch)],
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        info = patching.inspect_patch(raw_patch, fmt='xdelta')
        self.assertEqual(info['format'], 'xdelta')
        self.assertIsNone(info['source_sha256'])
        patching.apply_patch(self.source, raw_patch, self.output)
        self.assertEqual(self.output.read_bytes(), self.mod_data)



class TestCreatePackageThpatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src = self.base / 'clean.win'
        self.mod = self.base / 'modded.win'
        self.src.write_bytes(b'clean game file contents here 1234')
        self.mod.write_bytes(b'modded game file contents here 5678')

    def _make_zip(self, **kwargs):
        out = self.base / 'mod.zip'
        meta = patching.create_package(self.src, self.mod, out, 'Test Mod', **kwargs)
        return out, meta

    def test_default_thpatch_package_structure(self):
        out, meta = self._make_zip()
        self.assertEqual(meta['format'], 'thpatch')
        self.assertEqual(meta['target_file'], 'data.win')
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        self.assertIn('mod.json', names)
        self.assertIn('README.md', names)
        self.assertIn('patch/data.win.thpatch', names)
        self.assertNotIn('data.win', names)

    def test_mod_json_fields(self):
        out, meta = self._make_zip(version='2.3.0', description='A cool mod')
        with zipfile.ZipFile(out) as zf:
            stored = json.loads(zf.read('mod.json'))
        self.assertEqual(stored['name'], 'Test Mod')
        self.assertEqual(stored['version'], '2.3.0')
        self.assertEqual(stored['description'], 'A cool mod')
        self.assertEqual(stored['kind'], 'binary_patch')
        self.assertEqual(stored['format'], 'thpatch')
        self.assertIn('original_data_sha256', stored)
        self.assertIn('result_data_sha256', stored)

    def test_preview_included_when_given(self):
        preview = self.base / 'preview.png'
        preview.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8)
        out, _ = self._make_zip(preview=preview)
        with zipfile.ZipFile(out) as zf:
            self.assertIn('preview.png', zf.namelist())

    def test_non_zip_output_rejected(self):
        with self.assertRaises(ValueError):
            patching.create_package(self.src, self.mod, self.base / 'out.tar', 'Bad')

    def test_output_same_as_source_rejected(self):
        with self.assertRaises(ValueError):
            patching.create_package(self.src, self.mod, self.src, 'Bad')

    def test_unknown_format_rejected(self):
        with self.assertRaises(ValueError):
            patching.create_package(self.src, self.mod, self.base / 'out.zip', 'Bad', fmt='vcdiff3')

    def test_missing_source_raises(self):
        with self.assertRaises(FileNotFoundError):
            patching.create_package(self.base / 'nope.win', self.mod, self.base / 'out.zip', 'Bad')


@_skip_if_no_tools
class TestCreatePackageAllFormats(unittest.TestCase):
    """Round-trip create_package → import_mod → apply for bspatch and xdelta."""

    def setUp(self):
        from manager import Manager
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.game_root = self.base / 'game'
        self.game_root.mkdir()
        self.clean = b'game data clean ' * 1000
        self.modded = b'game data MOD!! ' * 1000
        (self.game_root / 'data.win').write_bytes(self.clean)
        self.manager = Manager(self.game_root)
        self._patch_running = patch('manager.game_running', return_value=False)
        self._patch_running.start()
        self.addCleanup(self._patch_running.stop)

    def _roundtrip(self, fmt):
        src = self.base / 'clean.win'
        mod = self.base / 'modded.win'
        src.write_bytes(self.clean)
        mod.write_bytes(self.modded)
        out = self.base / f'mod-{fmt}.zip'
        patching.create_package(src, mod, out, f'TestMod {fmt}', fmt=fmt)
        mod_id = self.manager.import_mod(out)
        record = next(m for m in self.manager.catalog() if m['id'] == mod_id)
        self.assertEqual(record['kind'], 'binary_patch')
        self.assertEqual(record['format'], fmt)
        self.manager.apply([mod_id])
        self.assertEqual((self.game_root / 'data.win').read_bytes(), self.modded)
        self.manager.apply([])
        self.assertEqual((self.game_root / 'data.win').read_bytes(), self.clean)

    def test_bspatch_end_to_end(self):
        self._roundtrip('bspatch')

    def test_xdelta_end_to_end(self):
        self._roundtrip('xdelta')

    def test_thpatch_end_to_end(self):
        self._roundtrip('thpatch')



class TestImportValidation(unittest.TestCase):
    def setUp(self):
        from manager import Manager
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.game_root = self.base / 'game'
        self.game_root.mkdir()
        (self.game_root / 'data.win').write_bytes(b'original game data')
        self.manager = Manager(self.game_root)

    def _make_zip(self, meta_override=None, extra_files=None, fmt='thpatch'):
        src = self.base / 'clean.win'
        mod = self.base / 'modded.win'
        src.write_bytes(b'original game data')
        mod.write_bytes(b'modified game data')
        out = self.base / 'mod.zip'
        patching.create_package(src, mod, out, 'Test', fmt=fmt)
        if meta_override is not None or extra_files:
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
                contents = {n: zf.read(n) for n in names}
            if meta_override is not None:
                meta = json.loads(contents['mod.json'])
                meta.update(meta_override)
                contents['mod.json'] = json.dumps(meta).encode()
            if extra_files:
                contents.update(extra_files)
            out.unlink()
            with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
                for name, data in contents.items():
                    zf.writestr(name, data)
        return out

    def test_wrong_format_field_rejected(self):
        out = self._make_zip(meta_override={'format': 'unknown_format'})
        with self.assertRaisesRegex(ValueError, 'format'):
            self.manager.import_mod(out)

    def test_unsafe_patch_file_path_rejected(self):
        out = self._make_zip(meta_override={'patch_file': '../evil.thpatch'})
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.manager.import_mod(out)

    def test_extra_file_in_zip_rejected(self):
        out = self._make_zip(extra_files={'data.win': b'full game archive'})
        with self.assertRaisesRegex(ValueError, 'unsupported file'):
            self.manager.import_mod(out)

    def test_wrong_original_hash_rejected(self):
        out = self._make_zip(meta_override={'original_data_sha256': 'a' * 64})
        with self.assertRaisesRegex(ValueError, 'original_data_sha256'):
            self.manager.import_mod(out)

    def test_wrong_result_hash_rejected(self):
        out = self._make_zip(meta_override={'result_data_sha256': 'b' * 64})
        with self.assertRaisesRegex(ValueError, 'result_data_sha256'):
            self.manager.import_mod(out)

    def test_missing_hashes_use_patch_embedded_values(self):
        out = self._make_zip(meta_override={'original_data_sha256': None, 'result_data_sha256': None})
        mod_id = self.manager.import_mod(out)
        record = next(m for m in self.manager.catalog() if m['id'] == mod_id)
        self.assertIsNotNone(record.get('original_data_sha256'))
        self.assertIsNotNone(record.get('result_data_sha256'))

    def test_unsupported_target_file_rejected(self):
        out = self._make_zip(meta_override={'target_file': 'malware.exe'})
        with self.assertRaisesRegex(ValueError, 'target file'):
            self.manager.import_mod(out)



class TestToolDiscovery(unittest.TestCase):
    def test_find_bundled_tool_found(self):
        if not TOOLS_AVAILABLE:
            self.skipTest('bundled tools not available')
        p = patching._find_tool('bsdiff')
        self.assertTrue(p.is_file())

    def test_find_nonexistent_tool_raises(self):
        with self.assertRaises(FileNotFoundError):
            patching._find_tool('definitely_nonexistent_tool_xyz')


if __name__ == '__main__':
    unittest.main()
