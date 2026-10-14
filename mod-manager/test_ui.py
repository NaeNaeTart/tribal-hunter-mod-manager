from pathlib import Path
import tempfile
import shutil
import json
import time
import ctypes
from unittest.mock import patch
from PIL import ImageGrab
import desktop_ui

out = Path(__file__).resolve().parent.parent / '.impeccable' / 'review'
out.mkdir(parents=True, exist_ok=True)
errors = []

def pump(app, duration=0.25):
    end = time.monotonic() + duration
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)

def capture(app, name):
    app.geometry("+20+20")
    app.attributes("-topmost", True)
    app.lift()
    pump(app, 0.8)
    x,y = app.winfo_rootx(), app.winfo_rooty()
    ancestor = ctypes.windll.user32.GetAncestor
    ancestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    ancestor.restype = ctypes.c_void_p
    hwnd = ancestor(app.winfo_id(), 2)
    ImageGrab.grab(bbox=(x,y,x+app.winfo_width(),y+app.winfo_height())).save(out / name)
    app.attributes("-topmost", False)

def wait_job(app):
    deadline = time.monotonic() + 10
    while app.busy and time.monotonic() < deadline:
        pump(app, 0.1)
    assert not app.busy, 'Background operation did not finish'

with tempfile.TemporaryDirectory() as work, patch.object(desktop_ui, 'game_running', return_value=False):
    root = Path(work) / 'game'
    root.mkdir()
    (root / 'data.win').write_bytes(b'original game')
    (root / 'options.ini').write_bytes(b'original settings')
    app = desktop_ui.App(root)
    assert app.pixel_font_loaded, 'Bundled pixel typeface did not load'
    app.report_callback_exception = lambda kind, value, trace: errors.append(str(value))
    capture(app, 'desktop-empty.png')
    source = Path(work) / 'mod'
    source.mkdir()
    (source / 'data.win').write_bytes(b'test game')
    desktop_ui.Image.new('RGB',(64,64),'green').save(source/'preview.png')
    (source / 'mod.json').write_text('{"name":"UI test archive","version":"1.2","description":"Temporary test fixture. Replaces the game archive to exercise import, enable, conflict detection, and restore."}')
    app.run_job('Importing test fixture.', lambda: app.manager.import_mod(source), 'Test mod imported.')
    wait_job(app)
    assert len(app.entries) == 1
    assert app.selected
    assert app.entries[0]["preview"] == "preview.png"
    assert (root / 'data.win').read_bytes() == b'original game'
    app.toggle()
    wait_job(app)
    assert (root / 'data.win').read_bytes() == b'test game'
    assert app.toggle_button.cget('text') == 'Disable mod'
    assert app.remove_button.cget('state') == 'disabled'
    capture(app, 'desktop-library.png')
    app.launch_button._canvas.focus_force()
    pump(app)
    capture(app, 'keyboard-focus.png')
    app.geometry('1000x700')
    capture(app, 'compact-library.png')
    app.search.set('does not exist')
    pump(app)
    assert app.list_count.cget('text').startswith('0 shown')
    app.clear_filters()
    pump(app)
    assert app.list_count.cget('text').startswith('1 shown')
    app.set_filter('Disabled')
    assert app.list_count.cget('text').startswith('0 shown')
    app.clear_filters()
    app.show_page('Backups')
    app.verify_backups()
    wait_job(app)
    assert 'verified successfully' in app.operation.cget('text')
    capture(app, 'backups.png')
    with patch.object(desktop_ui.messagebox, 'askyesno', return_value=True):
        app.restore()
        wait_job(app)
    assert (root / 'data.win').read_bytes() == b'original game'
    assert not app.manager.state['active']
    other = desktop_ui.Manager(root)
    other.apply([app.selected])
    app.reload()
    assert app.manager.state['active'] == [app.selected]
    app.show_page('Library')
    assert app.toggle_button.cget('text') == 'Disable mod'
    app.toggle()
    wait_job(app)
    assert (root / 'data.win').read_bytes() == b'original game'
    app.toggle()
    wait_job(app)
    assert (root / 'data.win').read_bytes() == b'test game'
    other.apply([])
    # No F5: toggle must read the committed state before choosing enable/disable.
    app.toggle()
    wait_job(app)
    assert (root / 'data.win').read_bytes() == b'test game'
    other.apply([])
    pump(app, 4.3)
    assert not app.manager.state['active'], 'Periodic refresh missed external change'
    other.apply([app.selected])
    # Restore must also work without a refresh first.
    with patch.object(desktop_ui.messagebox, 'askyesno', return_value=True):
        app.restore()
        wait_job(app)
    assert (root / 'data.win').read_bytes() == b'original game'
    assert not app.manager.state['active']
    app.show_page('Guide')
    capture(app, 'guide.png')
    app.show_page('Library')
    app.hide_notice()
    app.notice_message('Test error: an imported archive changed. Reimport the mod to continue.', error=True)
    capture(app, 'error.png')
    app.hide_notice()
    archive_id=app.selected
    patch_source=Path(work)/'visuals'
    shutil.copytree(Path(__file__).resolve().parent/'tests/fixtures/visuals-patch',patch_source)
    metadata=json.loads((patch_source/'mod.json').read_text());metadata.pop('original_data_sha256',None)
    (patch_source/'mod.json').write_text(json.dumps(metadata))
    app.run_job('Importing compatible patch.',lambda:app.manager.import_mod(patch_source),'Imported.')
    wait_job(app)
    patch_id=app.selected
    assert len(app.entries)==2
    def compose(base,output,packages):output.write_bytes(base.read_bytes()+b' + visuals')
    with patch.object(app.manager,'compose_archive',side_effect=compose):
        app.toggle();wait_job(app)
        app.select_mod(archive_id);app.toggle();wait_job(app)
        assert (root/'data.win').read_bytes()==b'test game + visuals'
        assert set(app.manager.state['active'])=={patch_id,archive_id}
        app.select_mod(patch_id);app.toggle();wait_job(app)
        assert (root/'data.win').read_bytes()==b'test game'
        app.select_mod(archive_id);app.toggle();wait_job(app)
        assert (root/'data.win').read_bytes()==b'original game'
    app.select_mod(patch_id)
    with patch.object(desktop_ui.messagebox,'askyesno',return_value=True):app.remove();wait_job(app)
    app.select_mod(archive_id)
    with patch.object(desktop_ui.messagebox, 'askyesno', return_value=True):
        app.remove()
        wait_job(app)
    assert not app.entries
    assert not errors, errors
    app.destroy()
    desktop_ui.ctk.set_widget_scaling(1.25)
    desktop_ui.ctk.set_window_scaling(1.25)
    scaled = desktop_ui.App(root)
    scaled.geometry('1000x700')
    capture(scaled, 'dpi125-startup.png')
    scaled.destroy()
print('UI integration passed: import, enable, filters, selection, verify, restore, remove, and page navigation.')
print('Captured empty, populated, compact, backups, guide, and error states.')

