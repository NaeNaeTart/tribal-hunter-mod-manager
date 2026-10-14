"""Bounded COPY/ADD binary patches. Game archives are read locally, never bundled."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile
import zlib

MAGIC = b'THDPAT01'
HEADER = struct.Struct('<8sQQ32s32s')
MAX_FILE = 8 * 1024**3
MAX_PATCH = 32 * 1024**2
BLOCK = 64 * 1024
MAX_LITERAL = 1024 * 1024


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def inspect_patch(path):
    path = Path(path)
    if not HEADER.size < path.stat().st_size <= MAX_PATCH:
        raise ValueError('Binary patch size is invalid or exceeds 32 MB.')
    with path.open('rb') as stream:
        header = stream.read(HEADER.size)
    if len(header) != HEADER.size:
        raise ValueError('Binary patch header is truncated.')
    magic, source_size, target_size, source_hash, target_hash = HEADER.unpack(header)
    if magic != MAGIC or not 0 < source_size <= MAX_FILE or not 0 < target_size <= MAX_FILE:
        raise ValueError('Unsupported binary patch or invalid archive size.')
    return {'source_size': source_size, 'target_size': target_size,
            'source_sha256': source_hash.hex(), 'target_sha256': target_hash.hex()}


class _Reader:
    def __init__(self, stream):
        self.parts = self._inflate(stream)
        self.buffer = bytearray()
        self.finished = False

    @staticmethod
    def _inflate(stream):
        decoder = zlib.decompressobj()
        while not decoder.eof:
            compressed = decoder.unconsumed_tail or stream.read(BLOCK)
            if not compressed:
                raise ValueError('Binary patch is truncated.')
            try:
                decoded = decoder.decompress(compressed, BLOCK)
            except zlib.error as exc:
                raise ValueError('Binary patch compression is corrupt.') from exc
            if decoded:
                yield decoded
        if decoder.unused_data or stream.read(1):
            raise ValueError('Binary patch has trailing compressed data.')

    def read(self, count):
        while len(self.buffer) < count and not self.finished:
            try:
                self.buffer.extend(next(self.parts))
            except StopIteration:
                self.finished = True
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def exact(self, count):
        result = self.read(count)
        if len(result) != count:
            raise ValueError('Binary patch instruction is truncated.')
        return result


def apply_patch(source, patch, output):
    source, patch, output = map(Path, (source, patch, output))
    if output.resolve() in (source.resolve(), patch.resolve()):
        raise ValueError('Patch output must be a separate file.')
    info = inspect_patch(patch)
    if source.stat().st_size != info['source_size'] or sha256(source) != info['source_sha256']:
        raise ValueError('Patch requires the matching clean game archive. Original files were preserved.')
    created = False
    try:
        with source.open('rb') as original, patch.open('rb') as delta, output.open('xb') as result:
            created = True
            delta.seek(HEADER.size)
            reader = _Reader(delta)
            written = 0
            result_hash = hashlib.sha256()
            instructions = 0
            while True:
                opcode = reader.exact(1)[0]
                if opcode == 255:
                    if written != info['target_size'] or reader.read(1):
                        raise ValueError('Binary patch output length or terminator is invalid.')
                    break
                instructions += 1
                if instructions > 1_000_000:
                    raise ValueError('Binary patch contains too many instructions.')
                if opcode == 0:
                    offset, count = struct.unpack('<QI', reader.exact(12))
                    if not count or offset + count > info['source_size']:
                        raise ValueError('Binary patch copy lies outside the clean archive.')
                    original.seek(offset)
                elif opcode == 1:
                    count = struct.unpack('<I', reader.exact(4))[0]
                    if not 0 < count <= MAX_LITERAL:
                        raise ValueError('Binary patch literal exceeds its limit.')
                else:
                    raise ValueError('Unknown binary patch instruction.')
                if written + count > info['target_size']:
                    raise ValueError('Binary patch exceeds its declared output size.')
                remaining = count
                while remaining:
                    size = min(BLOCK, remaining)
                    block = original.read(size) if opcode == 0 else reader.exact(size)
                    if len(block) != size:
                        raise ValueError('Clean archive changed while patching.')
                    result.write(block)
                    result_hash.update(block)
                    remaining -= size
                written += count
            if result_hash.hexdigest() != info['target_sha256']:
                raise ValueError('Patched archive checksum failed. Original files were preserved.')
            result.flush()
            os.fsync(result.fileno())
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise
    return info


def _regions(path):
    """Align archive sections for the publisher; the installer is format agnostic."""
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(8)
        if len(header) != 8 or header[:4] != b'FORM' or struct.unpack('<I', header[4:])[0] != size - 8:
            return [(b'FILE', 0, size)]
        regions = [(b'FORM', 0, 8)]
        while stream.tell() < size:
            offset = stream.tell()
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('Game archive chunk header is truncated.')
            tag, length = struct.unpack('<4sI', header)
            if offset + 8 + length > size:
                raise ValueError('Game archive chunk is truncated.')
            regions.append((tag, offset, length + 8))
            stream.seek(length, 1)
        return regions


def create_patch(source, target, output):
    source, target, output = map(Path, (source, target, output))
    if output.resolve() in (source.resolve(), target.resolve()):
        raise ValueError('Patch output must be separate from game archives.')
    sizes = source.stat().st_size, target.stat().st_size
    if not all(0 < size <= MAX_FILE for size in sizes):
        raise ValueError('Invalid game archive size.')
    source_hash, target_hash = sha256(source), sha256(target)
    old = {tag: (offset, length) for tag, offset, length in _regions(source)}
    regions = _regions(target)
    temp = output.with_name(output.name + '.building')
    if temp.exists():
        raise FileExistsError('An unfinished patch already exists: ' + str(temp))
    try:
        with source.open('rb') as original, target.open('rb') as modified, temp.open('xb') as patch:
            patch.write(HEADER.pack(MAGIC, *sizes, bytes.fromhex(source_hash), bytes.fromhex(target_hash)))
            compressor = zlib.compressobj(9)
            pending_copy = None
            pending_literal = bytearray()
            def emit(data):
                patch.write(compressor.compress(data))
            def flush_copy():
                nonlocal pending_copy
                if pending_copy:
                    emit(b'\0' + struct.pack('<QI', *pending_copy))
                    pending_copy = None
            def flush_literal():
                if pending_literal:
                    emit(b'\1' + struct.pack('<I', len(pending_literal)) + pending_literal)
                    pending_literal.clear()
            def copy(offset, length):
                nonlocal pending_copy
                flush_literal()
                if pending_copy and pending_copy[0] + pending_copy[1] == offset and pending_copy[1] + length <= 0xffffffff:
                    pending_copy[1] += length
                else:
                    flush_copy()
                    pending_copy = [offset, length]
            def literal(data):
                flush_copy()
                if len(pending_literal) + len(data) > MAX_LITERAL:
                    flush_literal()
                pending_literal.extend(data)
                if len(pending_literal) >= MAX_LITERAL:
                    flush_literal()
            for tag, target_offset, length in regions:
                source_offset, available = old.get(tag, (0, 0))
                modified.seek(target_offset)
                for relative in range(0, length, BLOCK):
                    count = min(BLOCK, length - relative)
                    new_block = modified.read(count)
                    original.seek(source_offset + relative)
                    old_block = original.read(min(count, max(0, available - relative)))
                    if old_block == new_block:
                        copy(source_offset + relative, count)
                    else:
                        for position in range(0, count, 16):
                            part = new_block[position:position + 16]
                            if old_block[position:position + len(part)] == part:
                                copy(source_offset + relative + position, len(part))
                            else:
                                literal(part)
            flush_copy()
            flush_literal()
            emit(b'\xff')
            patch.write(compressor.flush())
            patch.flush()
            os.fsync(patch.fileno())
        if temp.stat().st_size > MAX_PATCH:
            raise ValueError('Patch exceeds the 32 MB package limit.')
        if sha256(source) != source_hash or sha256(target) != target_hash:
            raise ValueError('An input archive changed during patch creation.')
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return inspect_patch(output)


def create_package(source, target, output, name, version='1.0.0', preview=None, description=''):
    output = Path(output)
    if output.suffix.lower() != '.zip' or output.resolve() in (Path(source).resolve(), Path(target).resolve()):
        raise ValueError('Choose a separate .zip output file.')
    with tempfile.TemporaryDirectory(prefix='tribal-patch-') as temp:
        patch = Path(temp) / 'data.win.thpatch'
        info = create_patch(source, target, patch)
        meta = {'name': name, 'version': version, 'description': description,
                'kind': 'binary_patch', 'patch_file': 'patch/data.win.thpatch',
                'original_data_sha256': info['source_sha256'], 'result_data_sha256': info['target_sha256']}
        with tempfile.NamedTemporaryFile(prefix='.' + output.name + '-', suffix='.building', dir=output.parent, delete=False) as handle:
            staging = Path(handle.name)
        try:
            with zipfile.ZipFile(staging, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('mod.json', json.dumps(meta, indent=2) + '\n')
                archive.write(patch, 'patch/data.win.thpatch')
                archive.writestr('README.md', f'# {name} {version}\n\nRequires the matching original Tribal Hunter archive and the updated Mod Manager 1.0.0 with binary patch support. Import this ZIP, then Enable mod. It contains no data.win or original game archive. The manager builds the mod from your local original backup and verifies the result. Disable mod or Restore originals to roll back.\n')
                if preview:
                    archive.write(preview, 'preview.png')
            os.replace(staging, output)
        finally:
            staging.unlink(missing_ok=True)
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('target')
    parser.add_argument('output')
    parser.add_argument('--name', required=True)
    parser.add_argument('--version', default='1.0.0')
    parser.add_argument('--preview')
    args = parser.parse_args()
    print(json.dumps(create_package(args.source, args.target, args.output, args.name, args.version, args.preview), indent=2))
