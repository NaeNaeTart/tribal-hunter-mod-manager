from pathlib import Path
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import tempfile
from contextlib import contextmanager
from one_click import register_protocol

RES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
@contextmanager
def manager_lock():
    handle = None
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel.CreateMutexW(None, False, 'Local\\TribalHunterModManager')
        if not handle:
            raise RuntimeError('Could not acquire the mod manager lock.')
        if kernel.GetLastError() == 183:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
            raise RuntimeError('Close the Tribal Hunter Mod Manager, then try again.')
    try:
        yield
    finally:
        if handle:
            ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.CloseHandle(handle)

def install(root, manager_exe, compiler_tools, progress=lambda message: None, guides=None):
    root = Path(root).resolve()
    if not (root / 'Tribal Hunter.exe').is_file() or not (root / 'data.win').is_file():
        raise ValueError('Choose the game folder containing Tribal Hunter.exe and data.win.')
    manager_exe = Path(manager_exe)
    compiler_tools = Path(compiler_tools)
    if not manager_exe.is_file() or not (compiler_tools / 'UndertaleModCli.exe').is_file():
        raise ValueError('Setup payload is incomplete. Re-download the installer.')
    with manager_lock(), tempfile.TemporaryDirectory(prefix='.mod-manager-setup-', dir=root) as temp:
        stage = Path(temp)
        target_exe = root / 'Tribal Hunter Mod Manager.exe'
        target_tools = root / 'mod-manager/tools/umt'
        progress('Preparing Mod Manager and patch compiler…')
        shutil.copy2(manager_exe, stage / 'manager.exe')
        shutil.copytree(compiler_tools, stage / 'umt')
        old_exe = target_exe.exists()
        if old_exe:
            shutil.copy2(target_exe, stage / 'previous.exe')
        target_tools.parent.mkdir(parents=True, exist_ok=True)
        old_tools = target_tools.exists()
        tools_installed = False
        exe_installed = False
        written_guides = []
        guide_source = Path(guides) if guides else RES / 'manager-guides'
        try:
            progress('Installing Mod Manager…')
            if old_tools:
                os.replace(target_tools, stage / 'previous-tools')
            os.replace(stage / 'umt', target_tools)
            tools_installed = True
            os.replace(stage / 'manager.exe', target_exe)
            exe_installed = True
            for name in ('README.md', 'MAKING-MODS.md'):
                source = guide_source / name
                if source.is_file():
                    target = root / 'mod-manager' / name
                    previous = stage / ('previous-' + name)
                    if target.exists():
                        shutil.copy2(target, previous)
                    shutil.copy2(source, stage / name)
                    os.replace(stage / name, target)
                    written_guides.append((target, previous))
            progress('Registering GameBanana one-click links...')
            register_protocol(target_exe)
        except Exception:
            for target, previous in reversed(written_guides):
                if previous.exists():
                    os.replace(previous, target)
                else:
                    target.unlink(missing_ok=True)
            if exe_installed:
                if old_exe:
                    os.replace(stage / 'previous.exe', target_exe)
                else:
                    target_exe.unlink()
            if tools_installed:
                os.replace(target_tools, stage / 'failed-tools')
            if (stage / 'previous-tools').exists():
                os.replace(stage / 'previous-tools', target_tools)
            raise
        return root


def main():
    import customtkinter as ctk
    from tkinter import filedialog
    from PIL import Image
    ctk.set_appearance_mode('dark')
    app = ctk.CTk(fg_color='#191f1a')
    app.title('Tribal Hunter Mod Manager Setup')
    app.geometry('850x620')
    app.minsize(800, 580)
    if (RES / 'assets/app.ico').exists():
        app.iconbitmap(str(RES / 'assets/app.ico'))
    for name in ('Silkscreen-Regular.ttf', 'Silkscreen-Bold.ttf'):
        ctk.FontManager.load_font(str(RES / 'assets/fonts' / name))
    art = ctk.CTkImage(Image.open(RES / 'assets/artwork/logo.png'), size=(260, 141))
    panel = ctk.CTkFrame(app, fg_color='#20251e', border_width=2, border_color='#6d5031')
    panel.pack(fill='both', expand=True, padx=24, pady=24)
    ctk.CTkLabel(panel, text='', image=art).pack(side='left', padx=(24, 20), anchor='n', pady=30)
    body = ctk.CTkFrame(panel, fg_color='transparent')
    body.pack(side='left', fill='both', expand=True, padx=(0, 24), pady=30)
    def label(text, **kwargs):
        widget = ctk.CTkLabel(body, text=text, text_color='#f5eedc', font=('Segoe UI', 14), justify='left', wraplength=430, **kwargs)
        widget.pack(anchor='w', pady=(0, 14))
        return widget
    heading = label('MOD MANAGER')
    heading.configure(font=('Silkscreen', 25, 'bold'), text_color='#e6bd73')
    label('Tribal Hunter · Mod Manager 1.2.0')
    label('Choose your Tribal Hunter game folder. Setup installs the Mod Manager, patch compiler and GameBanana one-click support.')
    label('Game folder')
    path = ctk.StringVar(value='')
    entry = ctk.CTkEntry(body, textvariable=path, height=40, fg_color='#2c3027', border_color='#74603e', placeholder_text='Select the game folder')
    entry.pack(fill='x', pady=(0, 8))
    browse = ctk.CTkButton(body, text='Browse…', command=lambda: path.set(filedialog.askdirectory(parent=app, title='Choose Tribal Hunter game folder') or path.get()), fg_color='#2c3027', hover_color='#444c35', border_width=1, border_color='#74603e', text_color='#f5eedc')
    browse.pack(anchor='w', pady=(0, 18))
    label('Import and manage mods after setup. Your game files, saves, installed mods and backups stay as they are.')
    status = label('Ready to choose a game folder.')
    bar = ctk.CTkProgressBar(body, mode='indeterminate', progress_color='#add66e')
    messages = queue.Queue()
    busy = False
    installed_root = None
    def start():
        nonlocal busy
        if not path.get().strip():
            status.configure(text='Choose the game folder first.', text_color='#ffb3a0')
            return
        busy = True
        for widget in (entry, browse, button): widget.configure(state='disabled')
        bar.pack(fill='x', pady=(0, 12)); bar.start()
        root = Path(path.get().strip())
        def worker():
            try:
                install(root, RES / 'Tribal Hunter Mod Manager.exe', RES / 'compiler-tools', lambda text: messages.put(('progress', text)))
                messages.put(('done', root))
            except Exception as exc:
                messages.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()
    def open_manager():
        subprocess.Popen([str(installed_root / 'Tribal Hunter Mod Manager.exe')], cwd=installed_root)
        app.destroy()
    button = ctk.CTkButton(body, text='Install Mod Manager', command=start, height=42, fg_color='#add66e', hover_color='#c3e794', text_color='#23321b', font=('Segoe UI', 14, 'bold'))
    button.pack(fill='x')
    def poll():
        nonlocal busy, installed_root
        while not messages.empty():
            kind, value = messages.get()
            if kind == 'progress': status.configure(text=value, text_color='#c9c6ab')
            else:
                busy = False
                bar.stop(); bar.pack_forget()
                if kind == 'done':
                    installed_root = value
                    status.configure(text='Installed. Open the manager to import and manage mods.', text_color='#add66e')
                    button.configure(text='Open Mod Manager', command=open_manager, state='normal')
                else:
                    status.configure(text=value, text_color='#ffb3a0')
                    for widget in (entry, browse, button): widget.configure(state='normal')
        app.after(100, poll)
    app.protocol('WM_DELETE_WINDOW', lambda: status.configure(text='Please wait for installation to finish.') if busy else app.destroy())
    poll()
    if '--smoke-test' in sys.argv:
        def capture():
            from PIL import ImageGrab
            app.attributes('-topmost', True);app.update()
            x,y=app.winfo_rootx(),app.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+app.winfo_width(),y+app.winfo_height())).save(Path.cwd()/'installer-smoke.png')
            app.destroy()
        app.after(2500, capture)
    app.mainloop()

if __name__ == '__main__':
    main()
