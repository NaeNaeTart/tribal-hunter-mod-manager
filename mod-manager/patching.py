"""
patching.py - Unified multi-format binary patch dispatcher for Tribal Hunter Mod Manager.

Supported formats:
  - thpatch   : Native .thpatch format (pure Python, no external tools)
  - bspatch   : BSDiff 4.x (.bspatch) - pure Python application + bundled bsdiff.exe for creation
  - xdelta    : XDelta3 VCDIFF (.xdelta) - bundled xdelta3.exe for both creation and application

All formats share a common interface:
  - inspect_patch(patch_path)          -> dict with source/target sizes and SHA-256 hashes
  - apply_patch(source, patch, output) -> None; verifies source hash, writes output, verifies result
  - create_patch(source, target, output) -> dict with same fields as inspect_patch

Format is determined from the patch file extension, or explicitly passed as `fmt`.
"""

from __future__ import annotations

import bz2
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
import zipfile


def _bin_dir() -> Path:
    """Return the directory containing bundled external tool binaries."""
    meipass = Path(getattr(sys, '_MEIPASS', ''))
    candidates = [
        meipass / 'bin',
        meipass / 'assets' / 'bin',
        Path(__file__).resolve().parent / 'bin',
        Path(__file__).resolve().parent / 'assets' / 'bin',
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return Path(__file__).resolve().parent / 'bin'


def _find_tool(name: str) -> Path:
    """Locate a bundled executable, falling back to PATH."""
    exe_name = name + ('.exe' if os.name == 'nt' else '')
    bundle = _bin_dir() / exe_name
    if bundle.is_file():
        return bundle
    which = shutil.which(name)
    if which:
        return Path(which)
    raise FileNotFoundError(
        f"Required tool '{name}' not found. "
        f"Expected it at: {bundle}"
    )



def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 ** 2), b''):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()



EXTENSION_FORMAT = {
    '.thpatch': 'thpatch',
    '.bspatch': 'bspatch',
    '.bsdiff':  'bspatch',
    '.xdelta':  'xdelta',
    '.vcdiff':  'xdelta',
}

BSDIFF_MAGIC = b'BSDIFF40'
THPATCH_MAGIC = b'THDPAT01'


def detect_format(patch_path: Path) -> str:
    """Detect patch format from extension, then magic bytes if ambiguous."""
    ext = patch_path.suffix.lower()
    if ext in EXTENSION_FORMAT:
        return EXTENSION_FORMAT[ext]
    with patch_path.open('rb') as f:
        header = f.read(8)
    if header == THPATCH_MAGIC:
        return 'thpatch'
    if header == BSDIFF_MAGIC:
        return 'bspatch'
    if header[:3] == b'\xd6\xc3\xc4':
        return 'xdelta'
    raise ValueError(
        f"Cannot detect patch format for '{patch_path.name}'. "
        "Use a .thpatch, .bspatch, or .xdelta extension."
    )



import delta_patch as _dp


def _thpatch_inspect(patch_path: Path) -> dict:
    info = _dp.inspect_patch(patch_path)
    return {
        'format': 'thpatch',
        'source_size': info['source_size'],
        'target_size': info['target_size'],
        'source_sha256': info['source_sha256'],
        'target_sha256': info['target_sha256'],
    }


def _thpatch_apply(source: Path, patch: Path, output: Path):
    _dp.apply_patch(source, patch, output)


def _thpatch_create(source: Path, target: Path, output: Path) -> dict:
    info = _dp.create_patch(source, target, output)
    return {
        'format': 'thpatch',
        'source_size': info['source_size'],
        'target_size': info['target_size'],
        'source_sha256': info['source_sha256'],
        'target_sha256': info['target_sha256'],
    }



def _offtin(b: bytes) -> int:
    """BSDiff little-endian signed 64-bit int decode."""
    y = b[7] & 0x7F
    for i in range(6, -1, -1):
        y = (y << 8) | b[i]
    if b[7] & 0x80:
        y = -y
    return y


def _bspatch_apply_pure(old_bytes: bytes, patch_path: Path) -> bytes:
    """Apply a BSDiff 4.x patch entirely in pure Python (bzip2 compressed)."""
    with patch_path.open('rb') as f:
        header = f.read(32)
        if len(header) != 32 or header[:8] != BSDIFF_MAGIC:
            raise ValueError('Invalid BSDIFF40 magic in patch file.')
        ctrl_len = _offtin(header[8:16])
        diff_len = _offtin(header[16:24])
        new_size = _offtin(header[24:32])
        if ctrl_len < 0 or diff_len < 0 or new_size < 0:
            raise ValueError('BSDiff patch header contains invalid negative lengths.')
        patch_body = f.read()

    try:
        ctrl_block = bz2.decompress(patch_body[:ctrl_len])
        diff_block = bz2.decompress(patch_body[ctrl_len: ctrl_len + diff_len])
        extra_block = bz2.decompress(patch_body[ctrl_len + diff_len:])
    except OSError as exc:
        raise ValueError('BSDiff patch bzip2 decompression failed.') from exc

    new_buf = bytearray(new_size)
    old_pos = 0
    new_pos = 0
    diff_pos = 0
    extra_pos = 0
    ctrl_pos = 0
    ctrl_total = len(ctrl_block)

    while ctrl_pos < ctrl_total:
        if ctrl_pos + 24 > ctrl_total:
            raise ValueError('BSDiff patch control block is truncated.')
        x = _offtin(ctrl_block[ctrl_pos:ctrl_pos + 8])
        y = _offtin(ctrl_block[ctrl_pos + 8:ctrl_pos + 16])
        z = _offtin(ctrl_block[ctrl_pos + 16:ctrl_pos + 24])
        ctrl_pos += 24

        if x < 0 or y < 0:
            raise ValueError('BSDiff patch control values are negative.')
        if new_pos + x + y > new_size:
            raise ValueError('BSDiff patch writes beyond declared output size.')

        if x > 0:
            diff_slice = diff_block[diff_pos: diff_pos + x]
            for i in range(x):
                old_val = old_bytes[old_pos + i] if 0 <= old_pos + i < len(old_bytes) else 0
                new_buf[new_pos + i] = (old_val + diff_slice[i]) & 0xFF
            new_pos += x
            old_pos += x
            diff_pos += x

        if y > 0:
            new_buf[new_pos: new_pos + y] = extra_block[extra_pos: extra_pos + y]
            new_pos += y
            extra_pos += y

        old_pos += z

    if new_pos != new_size:
        raise ValueError('BSDiff patch produced incorrect output length.')
    return bytes(new_buf)


def _bspatch_inspect(patch_path: Path) -> dict:
    with patch_path.open('rb') as f:
        header = f.read(32)
    if len(header) != 32 or header[:8] != BSDIFF_MAGIC:
        raise ValueError('Invalid BSDIFF40 magic in patch file.')
    new_size = _offtin(header[24:32])
    if new_size < 0:
        raise ValueError('BSDiff patch header: negative target size.')
    trailer = _read_bspatch_trailer(patch_path)
    if trailer is not None:
        return {
            'format': 'bspatch',
            'source_size': trailer.get('source_size'),
            'target_size': new_size,
            'source_sha256': trailer.get('source_sha256'),
            'target_sha256': trailer.get('target_sha256'),
        }
    return {
        'format': 'bspatch',
        'source_size': None,
        'target_size': new_size,
        'source_sha256': None,
        'target_sha256': None,
    }


_BSPATCH_TRAILER_MAGIC = b'\x00THBP'


def _write_bspatch_trailer(patch_path: Path, source_size: int, source_sha256: str, target_sha256: str):
    trailer_data = json.dumps({
        'source_size': source_size,
        'source_sha256': source_sha256,
        'target_sha256': target_sha256,
    }).encode('utf-8')
    length_bytes = struct.pack('<I', len(trailer_data))
    with patch_path.open('ab') as f:
        f.write(_BSPATCH_TRAILER_MAGIC + trailer_data + length_bytes)


def _read_bspatch_trailer(patch_path: Path) -> dict | None:
    file_size = patch_path.stat().st_size
    if file_size < 5 + 4 + 2:
        return None
    try:
        with patch_path.open('rb') as f:
            f.seek(-4, 2)
            length = struct.unpack('<I', f.read(4))[0]
            trailer_size = 5 + length + 4
            if trailer_size > file_size:
                return None
            f.seek(-(trailer_size), 2)
            magic = f.read(5)
            if magic != _BSPATCH_TRAILER_MAGIC:
                return None
            return json.loads(f.read(length).decode('utf-8'))
    except Exception:
        return None


def _bspatch_apply(source: Path, patch: Path, output: Path):
    info = _bspatch_inspect(patch)
    src_stat = source.stat()
    if info['source_sha256'] is not None:
        if (info['source_size'] is not None and src_stat.st_size != info['source_size']) or sha256(source) != info['source_sha256']:
            raise ValueError('BSDiff patch requires the matching clean game file. Original files were preserved.')
    old_bytes = source.read_bytes()
    created = False
    try:
        result_bytes = _bspatch_apply_pure(old_bytes, patch)
        with output.open('xb') as f:
            created = True
            f.write(result_bytes)
            f.flush()
            os.fsync(f.fileno())
        if info['target_sha256'] is not None and sha256(output) != info['target_sha256']:
            raise ValueError('BSDiff patch output checksum failed. Original files were preserved.')
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise


def _bspatch_create(source: Path, target: Path, output: Path) -> dict:
    """Create a .bspatch file using bundled bsdiff.exe, and append hash trailer."""
    bsdiff_exe = _find_tool('bsdiff')
    source_hash = sha256(source)
    target_hash = sha256(target)
    source_size = source.stat().st_size
    temp = output.with_suffix('.building')
    try:
        result = subprocess.run(
            [str(bsdiff_exe), str(source), str(target), str(temp)],
            capture_output=True, creationflags=0x08000000 if os.name == 'nt' else 0
        )
        if result.returncode != 0 or not temp.is_file():
            raise RuntimeError('bsdiff failed to create the patch:\n' + result.stderr.decode(errors='replace'))
        _write_bspatch_trailer(temp, source_size, source_hash, target_hash)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return {
        'format': 'bspatch',
        'source_size': source_size,
        'target_size': target.stat().st_size,
        'source_sha256': source_hash,
        'target_sha256': target_hash,
    }



def _xdelta_inspect(patch_path: Path) -> dict:
    trailer = _read_xdelta_trailer(patch_path)
    if trailer is not None:
        return trailer
    return {
        'format': 'xdelta',
        'source_size': None,
        'target_size': None,
        'source_sha256': None,
        'target_sha256': None,
    }


_XDELTA_TRAILER_MAGIC = b'\x00THXD'


def _write_xdelta_trailer(patch_path: Path, source_size: int, target_size: int,
                           source_sha256: str, target_sha256: str):
    trailer_data = json.dumps({
        'format': 'xdelta',
        'source_size': source_size,
        'target_size': target_size,
        'source_sha256': source_sha256,
        'target_sha256': target_sha256,
    }).encode('utf-8')
    length_bytes = struct.pack('<I', len(trailer_data))
    with patch_path.open('ab') as f:
        f.write(_XDELTA_TRAILER_MAGIC + trailer_data + length_bytes)


def _read_xdelta_trailer(patch_path: Path) -> dict | None:
    file_size = patch_path.stat().st_size
    if file_size < 5 + 4 + 2:
        return None
    try:
        with patch_path.open('rb') as f:
            f.seek(-4, 2)
            length = struct.unpack('<I', f.read(4))[0]
            trailer_size = 5 + length + 4
            if trailer_size > file_size:
                return None
            f.seek(-trailer_size, 2)
            magic = f.read(5)
            if magic != _XDELTA_TRAILER_MAGIC:
                return None
            return json.loads(f.read(length).decode('utf-8'))
    except Exception:
        return None


def _xdelta_apply(source: Path, patch: Path, output: Path):
    info = _xdelta_inspect(patch)
    src_stat = source.stat()
    if info.get('source_sha256') is not None:
        if (info.get('source_size') is not None and src_stat.st_size != info['source_size']) or sha256(source) != info['source_sha256']:
            raise ValueError('XDelta patch requires the matching clean game file. Original files were preserved.')
    xdelta_exe = _find_tool('xdelta3')
    patch_size = patch.stat().st_size
    trailer = _read_xdelta_trailer(patch)
    temp_patch = output.with_name(output.name + '.patch_clean')
    temp_out   = output.with_name(output.name + '.xdtmp')
    temp_patch.unlink(missing_ok=True)
    temp_out.unlink(missing_ok=True)
    try:
        if trailer is not None:
            with patch.open('rb') as f:
                f.seek(-4, 2)
                trailer_json_len = struct.unpack('<I', f.read(4))[0]
            vcdiff_size = patch_size - (5 + trailer_json_len + 4)
            with patch.open('rb') as src_f, temp_patch.open('wb') as dst_f:
                remaining = vcdiff_size
                while remaining:
                    chunk = src_f.read(min(65536, remaining))
                    if not chunk:
                        break
                    dst_f.write(chunk)
                    remaining -= len(chunk)
            patch_to_use = temp_patch
        else:
            patch_to_use = patch

        result = subprocess.run(
            [str(xdelta_exe), '-d', '-s', str(source), str(patch_to_use), str(temp_out)],
            capture_output=True, creationflags=0x08000000 if os.name == 'nt' else 0
        )
        if result.returncode != 0 or not temp_out.is_file():
            err_msg = result.stderr.decode(errors='replace')
            if 'checksum mismatch' in err_msg.lower() or 'source file is incorrect' in err_msg.lower():
                raise ValueError('XDelta patch checksum failed: your game version does not match this mod.')
            raise RuntimeError('xdelta3 failed to apply the patch:\n' + err_msg)
        if info.get('target_sha256') is not None and sha256(temp_out) != info['target_sha256']:
            raise ValueError('XDelta patch output checksum failed. Original files were preserved.')
        if output.exists():
            raise ValueError('Patch output file already exists.')
        os.replace(temp_out, output)
    except Exception:
        temp_patch.unlink(missing_ok=True)
        temp_out.unlink(missing_ok=True)
        raise
    finally:
        temp_patch.unlink(missing_ok=True)


def _xdelta_create(source: Path, target: Path, output: Path) -> dict:
    """Create a .xdelta patch file using bundled xdelta3.exe, and append hash trailer."""
    xdelta_exe = _find_tool('xdelta3')
    source_hash = sha256(source)
    target_hash = sha256(target)
    source_size = source.stat().st_size
    target_size = target.stat().st_size
    temp = output.with_suffix('.building')
    try:
        result = subprocess.run(
            [str(xdelta_exe), '-e', '-s', str(source), str(target), str(temp)],
            capture_output=True, creationflags=0x08000000 if os.name == 'nt' else 0
        )
        if result.returncode != 0 or not temp.is_file():
            raise RuntimeError('xdelta3 failed to create the patch:\n' + result.stderr.decode(errors='replace'))
        _write_xdelta_trailer(temp, source_size, target_size, source_hash, target_hash)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return {
        'format': 'xdelta',
        'source_size': source_size,
        'target_size': target_size,
        'source_sha256': source_hash,
        'target_sha256': target_hash,
    }



def inspect_patch(patch_path: Path | str, fmt: str | None = None) -> dict:
    """
    Inspect a patch file and return its metadata dict:
      format, source_size, target_size, source_sha256, target_sha256
    """
    patch_path = Path(patch_path)
    fmt = fmt or detect_format(patch_path)
    if fmt == 'thpatch':
        return _thpatch_inspect(patch_path)
    if fmt == 'bspatch':
        return _bspatch_inspect(patch_path)
    if fmt == 'xdelta':
        return _xdelta_inspect(patch_path)
    raise ValueError(f"Unknown patch format: {fmt!r}")


def apply_patch(source: Path | str, patch: Path | str, output: Path | str, fmt: str | None = None):
    """
    Apply a patch to source, writing output.  Verifies source integrity before
    patching and output integrity after.  Cleans up partial output on failure.
    """
    source, patch, output = Path(source), Path(patch), Path(output)
    if output.resolve() in (source.resolve(), patch.resolve()):
        raise ValueError('Patch output must be a separate file.')
    fmt = fmt or detect_format(patch)
    if fmt == 'thpatch':
        return _thpatch_apply(source, patch, output)
    if fmt == 'bspatch':
        return _bspatch_apply(source, patch, output)
    if fmt == 'xdelta':
        return _xdelta_apply(source, patch, output)
    raise ValueError(f"Unknown patch format: {fmt!r}")


def create_patch(source: Path | str, target: Path | str, output: Path | str,
                 fmt: str = 'thpatch') -> dict:
    """
    Create a binary patch from source → target, writing it to output.
    Returns the same dict as inspect_patch.
    """
    source, target, output = Path(source), Path(target), Path(output)
    if output.resolve() in (source.resolve(), target.resolve()):
        raise ValueError('Patch output must be separate from game archives.')
    if not source.is_file():
        raise FileNotFoundError(f'Clean (source) file not found: {source}')
    if not target.is_file():
        raise FileNotFoundError(f'Modified (target) file not found: {target}')
    if fmt == 'thpatch':
        return _thpatch_create(source, target, output)
    if fmt == 'bspatch':
        return _bspatch_create(source, target, output)
    if fmt == 'xdelta':
        return _xdelta_create(source, target, output)
    raise ValueError(f"Unknown patch format: {fmt!r}. Choose thpatch, bspatch, or xdelta.")


def create_package(source: Path | str, target: Path | str, output_zip: Path | str,
                   name: str, version: str = '1.0.0', preview: Path | str | None = None,
                   description: str = '', fmt: str = 'thpatch',
                   target_file: str = 'data.win') -> dict:
    """
    Create a complete mod ZIP package containing a binary patch for the specified target file.

    Args:
        source:      Path to the clean (original) game file.
        target:      Path to the modified game file.
        output_zip:  Path to the .zip file to create.
        name:        Mod display name.
        version:     Mod version string.
        preview:     Optional path to a preview.png image.
        description: Optional description string.
        fmt:         Patch format: 'thpatch', 'bspatch', or 'xdelta'.
        target_file: The game file this patch targets, e.g. 'data.win'.
    """
    output_zip = Path(output_zip)
    if output_zip.suffix.lower() != '.zip':
        raise ValueError('Output must be a .zip file.')
    if output_zip.resolve() in (Path(source).resolve(), Path(target).resolve()):
        raise ValueError('Choose a separate .zip output file.')

    ext_map = {'thpatch': '.thpatch', 'bspatch': '.bspatch', 'xdelta': '.xdelta'}
    if fmt not in ext_map:
        raise ValueError(f"Unknown format: {fmt!r}. Choose thpatch, bspatch, or xdelta.")

    patch_filename = target_file + ext_map[fmt]
    patch_rel = f'patch/{patch_filename}'

    with tempfile.TemporaryDirectory(prefix='tribal-patch-') as tmp:
        patch_file = Path(tmp) / patch_filename
        info = create_patch(source, target, patch_file, fmt=fmt)
        meta = {
            'name': name,
            'version': version,
            'description': description,
            'kind': 'binary_patch',
            'format': fmt,
            'target_file': target_file,
            'patch_file': patch_rel,
            'original_data_sha256': info['source_sha256'],
            'result_data_sha256': info['target_sha256'],
        }
        readme_lines = [
            f'# {name} {version}',
            '',
            f'Patch format: **{fmt}** | Target file: `{target_file}`',
            '',
            'Requires the matching original Tribal Hunter installation and Mod Manager 1.1.0+.',
            'Import this ZIP, then Enable mod.',
            'The manager verifies your backup and applies the patch locally.',
            'Use Disable mod or Restore originals to roll back.',
        ]
        staging = output_zip.with_name('.' + output_zip.name + '.building')
        try:
            with zipfile.ZipFile(staging, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('mod.json', json.dumps(meta, indent=2) + '\n')
                zf.writestr('README.md', '\n'.join(readme_lines) + '\n')
                zf.write(patch_file, patch_rel)
                if preview:
                    zf.write(preview, 'preview.png')
            os.replace(staging, output_zip)
        finally:
            staging.unlink(missing_ok=True)

    return meta
