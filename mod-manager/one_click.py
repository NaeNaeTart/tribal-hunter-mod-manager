"""GameBanana protocol downloads. Import only; game deployment remains explicit."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlsplit, unquote
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

SCHEME = 'tribalhunter'
GAME_ID = 14084
MAX_DOWNLOAD = 2 * 1024**3
HEADERS = {'User-Agent': 'TribalHunterModManager/1.0.0', 'Accept': 'application/json'}

@dataclass(frozen=True)
class InstallLink:
    url: str
    file_id: int
    mod_id: int


def parse_link(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid GameBanana install link.')
    prefix, separator, payload = value.strip().partition(':')
    if not separator or prefix.lower() != SCHEME:
        raise ValueError('Expected a tribalhunter: GameBanana install link.')
    parts = unquote(payload).removeprefix('//').rsplit(',', 2)
    if len(parts) != 3 or parts[1].lower() != 'mod' or not re.fullmatch(r'[1-9][0-9]{0,11}', parts[2]):
        raise ValueError('This link must identify a GameBanana Mod.')
    url = urlsplit(parts[0])
    if url.scheme != 'https' or url.netloc not in ('gamebanana.com', 'www.gamebanana.com') or url.query or url.fragment:
        raise ValueError('Only official HTTPS GameBanana download links are supported.')
    match = re.fullmatch(r'/(?:mmdl|dl)/([1-9][0-9]{0,11})/?', url.path)
    if not match:
        raise ValueError('The GameBanana download link must identify a file.')
    file_id = int(match[1])
    return InstallLink(f'https://gamebanana.com/dl/{file_id}', file_id, int(parts[2]))


def allowed_remote(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443)
            and (host in ('gamebanana.com', 'www.gamebanana.com', 'files.gamebanana.com')
                 or re.fullmatch(r'filecache[0-9]+\.gamebanana\.com', host) is not None))


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_remote(newurl):
            raise ValueError('GameBanana redirected to an unsupported download server.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def import_link(manager, value, progress=lambda text: None, opener=None):
    link = parse_link(value)
    opener = opener or build_opener(OfficialRedirects())
    def open_remote(url):
        if not allowed_remote(url):
            raise ValueError('Unsupported GameBanana server.')
        response = opener.open(Request(url, headers=HEADERS), timeout=30)
        if not allowed_remote(response.geturl()):
            response.close()
            raise ValueError('Unsupported GameBanana download redirect.')
        return response
    progress('Checking GameBanana mod and game compatibility...')
    try:
        with open_remote(f'https://gamebanana.com/apiv11/Mod/{link.mod_id}/ProfilePage') as response:
            raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError('GameBanana returned an oversized mod profile.')
        profile = json.loads(raw)
        if profile.get('_aGame', {}).get('_idRow') != GAME_ID:
            raise ValueError('This GameBanana mod is not for Tribal Hunter.')
        if profile.get('_bIsTrashed') or profile.get('_bIsWithheld') or profile.get('_bIsPrivate'):
            raise ValueError('This mod is unavailable or private. Use a ZIP supplied by its author.')
        file = next((f for f in profile.get('_aFiles', []) if f.get('_idRow') == link.file_id), None)
        if not file or file.get('_bIsArchived'):
            raise ValueError('This file is not an available download of the linked mod.')
        filename = file.get('_sFile', '')
        if Path(filename).suffix.lower() != '.zip':
            raise ValueError('One-click installation supports mod ZIPs. Ask the author for a manager-compatible ZIP.')
        size = file.get('_nFilesize')
        checksum = file.get('_sMd5Checksum', '')
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DOWNLOAD:
            raise ValueError('Invalid or oversized GameBanana download (limit: 2 GB).')
        if not isinstance(checksum, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', checksum):
            raise ValueError('GameBanana did not provide a valid download checksum.')
        checksum = checksum.lower()
        source_key = f'Mod/{link.mod_id}/File/{link.file_id}'
        for mod in manager.catalog():
            if mod.get('gamebanana_source') == source_key and mod.get('download_md5') == checksum:
                return mod['id']
        if shutil.disk_usage(manager.home).free < size * 4:
            raise ValueError('Not enough free space for this download, extraction and library copy.')
        with tempfile.TemporaryDirectory(prefix='gamebanana-', dir=manager.home) as work:
            package = Path(work) / 'mod.zip'
            downloaded = 0
            md5 = hashlib.md5(usedforsecurity=False)
            next_report = 0
            with open_remote(link.url) as response, package.open('wb') as output:
                length = response.headers.get('Content-Length')
                if length and int(length) != size:
                    raise ValueError('GameBanana download size does not match its file record.')
                while True:
                    chunk = response.read(1024**2)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > size:
                        raise ValueError('GameBanana download exceeded its declared size.')
                    output.write(chunk); md5.update(chunk)
                    now = time.monotonic()
                    if now >= next_report:
                        progress(f'Downloading GameBanana mod: {downloaded * 100 // size}%')
                        next_report = now + 0.25
            if downloaded != size or md5.hexdigest() != checksum:
                raise ValueError('Download verification failed. No mod was imported. Try the link again.')
            progress('Verified download. Importing into your library...')
            mod_id = manager.import_mod(package)
            record_path = manager.mods / mod_id / 'mod.json'
            from manager import write_json
            record = json.loads(record_path.read_text('utf-8'))
            record.update(gamebanana_source=source_key, download_md5=checksum,
                          source_url=f'https://gamebanana.com/mods/{link.mod_id}')
            try:
                write_json(record_path, record)
            except Exception:
                shutil.rmtree(manager.mods / mod_id)
                raise
            return mod_id
    except HTTPError as exc:
        raise ValueError('GameBanana could not provide this mod. Private posts require a manual ZIP download; otherwise try again later.') from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError('Could not download from GameBanana. Check your connection and try again.') from exc


def queue_link(game_root, value):
    parse_link(value)
    folder = Path(game_root) / 'mod-manager/one-click/requests'
    folder.mkdir(parents=True, exist_ok=True)
    if len(list(folder.glob('*.json'))) >= 64:
        raise ValueError('Too many pending one-click requests. Open the manager to process them.')
    dest = folder / (hashlib.sha256(value.encode()).hexdigest() + '.json')
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.tmp', dir=folder, delete=False) as output:
        temp = Path(output.name)
        json.dump({'uri':value}, output)
    try:
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def take_link(game_root):
    folder = Path(game_root) / 'mod-manager/one-click/requests'
    if not folder.exists():
        return None
    for request in sorted(folder.glob('*.json'), key=lambda p:p.name):
        try:
            if request.is_symlink() or request.stat().st_size > 8192:
                raise ValueError('Invalid pending one-click request.')
            value = json.loads(request.read_text('utf-8'))['uri']
            parse_link(value)
            return value
        finally:
            request.unlink(missing_ok=True)
    return None


def register_protocol(executable):
    """Register the selected installation per-user, without administrator access."""
    if os.name != 'nt':
        raise RuntimeError('One-click registration requires Windows.')
    import winreg
    executable = Path(executable).resolve()
    if not executable.is_file() or '"' in str(executable):
        raise ValueError('Install the manager before registering one-click support.')
    base = 'Software\\Classes\\' + SCHEME
    changes = [(base, '', 'URL:Tribal Hunter Mod Manager'),
               (base, 'URL Protocol', ''),
               (base+'\\DefaultIcon', '', f'"{executable}",0'),
               (base+'\\shell\\open\\command', '', f'"{executable}" "%1"')]
    previous = []
    try:
        for path,name,value in changes:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,path) as key:
                try:
                    old = winreg.QueryValueEx(key,name)
                except FileNotFoundError:
                    old = None
                previous.append((path,name,old))
                winreg.SetValueEx(key,name,0,winreg.REG_SZ,value)
    except Exception:
        for path,name,old in reversed(previous):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,path) as key:
                if old is None:
                    try:winreg.DeleteValue(key,name)
                    except FileNotFoundError:pass
                else:winreg.SetValueEx(key,name,0,old[1],old[0])
        raise
