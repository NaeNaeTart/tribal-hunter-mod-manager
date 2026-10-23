from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import customtkinter as ctk
from PIL import Image, ImageOps
from manager import Manager, GAME_ROOT, digest, game_running
import patching

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')
C = dict(bg='#191f1a', sidebar='#20251e', panel='#2c3027', selected='#3c4930',
         line='#74603e', text='#f5eedc', muted='#c9c6ab', accent='#add66e',
         accent_hover='#c3e794', ink='#23321b', warn='#edc578', error='#ffb3a0',
         danger_bg='#4e3029', hover='#444c35', gold='#e6bd73', wood='#6d5031')
RESOURCE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets'


def size_text(size):
    for suffix in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or suffix == 'GB':
            return f'{size:.1f} {suffix}' if suffix != 'B' else f'{size} B'
        size /= 1024


class App(ctk.CTk):
    def __init__(self, game_root=GAME_ROOT):
        super().__init__(fg_color=C['bg'])
        self.title('Tribal Hunter Mod Manager')
        self.geometry('1200x800')
        self.minsize(1000, 700)
        self.busy = False
        self.running = False
        self.page = 'Library'
        self.selected = None
        self.filter = 'All mods'
        self.search = tk.StringVar()
        self.search.trace_add('write', lambda *_: self.render_library())
        self.result_queue = queue.Queue()
        self.controls = []
        self.icons = {}
        self.art_cache = {}
        self.banner_job = None
        self.hero_source = Image.open(RESOURCE / 'artwork' / 'hero.jpg').convert('RGB')
        self.pixel_font_loaded = all(ctk.FontManager.load_font(str(RESOURCE / 'fonts' / name)) for name in ('Silkscreen-Regular.ttf', 'Silkscreen-Bold.ttf'))
        self.entries = []
        try:
            self.manager = Manager(game_root)
        except Exception as exc:
            messagebox.showerror('Could not open mod manager', str(exc), parent=self)
            self.destroy()
            raise SystemExit(1)
        self.log = logging.getLogger('tribal-mod-manager')
        self.log.setLevel(logging.INFO)
        if not self.log.handlers:
            handler = RotatingFileHandler(self.manager.home / 'app.log', maxBytes=1024*1024, backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            self.log.addHandler(handler)
        self.log.info('App started for %s', self.manager.root)
        if (RESOURCE / 'app.ico').exists():
            self.iconbitmap(str(RESOURCE / 'app.ico'))
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_sidebar()
        self.main = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        self.main.grid(row=0, column=1, sticky='nsew', padx=24, pady=(24, 18))
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(2, weight=1)
        self.header = ctk.CTkFrame(self.main, fg_color=C['sidebar'], corner_radius=8,
                                   height=136, border_width=2, border_color=C['wood'])
        self.header.grid(row=0, column=0, sticky='ew', pady=(0, 20))
        self.header.grid_propagate(False)
        self.header.grid_columnconfigure(0, weight=1)
        self.banner = ctk.CTkLabel(self.header, text='', width=1, height=1)
        self.banner.place(x=2, y=2)
        self.heading = self.label(self.header, 'Mod library', 26, 'bold', color=C['gold'])
        self.heading.grid(row=0, column=0, sticky='w', padx=22, pady=(32, 0))
        self.subheading = self.label(self.header, 'Manage the way you play.', color=C['muted'])
        self.subheading.grid(row=1, column=0, sticky='w', padx=22, pady=(4, 22))
        self.game_status = self.label(self.header, 'Checking game…', 12, color=C['muted'],
                                      fg_color=C['sidebar'], corner_radius=4, padx=10, height=26)
        self.game_status.grid(row=0, column=1, sticky='ne', padx=14, pady=14)
        self.header.bind('<Configure>', self.queue_banner)
        self.notice = ctk.CTkFrame(self.main, fg_color=C['selected'], corner_radius=6, border_width=1, border_color=C['line'])
        self.notice.grid_columnconfigure(0, weight=1)
        self.notice_text = self.label(self.notice, '', 13, wraplength=650, justify='left')
        self.notice_text.grid(row=0, column=0, sticky='w', padx=14, pady=10)
        self.button(self.notice, 'Dismiss', self.hide_notice, kind='quiet', width=72).grid(row=0, column=1, padx=8)
        self.content = ctk.CTkFrame(self.main, fg_color='transparent')
        self.content.grid(row=2, column=0, sticky='nsew')
        self.footer = ctk.CTkFrame(self.main, fg_color='transparent')
        self.footer.grid(row=3, column=0, sticky='ew', pady=(16, 0))
        self.footer.grid_columnconfigure(0, weight=1)
        self.operation = self.label(self.footer, 'Ready. Importing a mod leaves your game files untouched.', 12, color=C['muted'])
        self.operation.grid(row=0, column=0, sticky='w')
        self.progress = ctk.CTkProgressBar(self.footer, height=4, mode='indeterminate', progress_color=C['accent'], fg_color=C['line'])
        self.progress.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        self.progress.set(0)
        self.progress.grid_remove()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-o>', lambda _: self.import_zip())
        self.bind('<Control-f>', lambda _: self.focus_search())
        self.bind('<Control-p>', lambda _: self.open_create_patch_dialog())
        self.bind('<F5>', lambda _: self.reload())
        self.bind('<Escape>', lambda _: self.hide_notice())
        self.show_page('Library')
        self.after(100, self.poll)
        self.after(200, self.check_game)

    def label(self, parent, text, size=14, weight='normal', color=None, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color or C['text'],
                            font=ctk.CTkFont(family='Silkscreen' if size >= 20 else 'Segoe UI', size=size, weight=weight), **kwargs)

    def art(self, name, width, height):
        key = (name, width, height)
        if key not in self.art_cache:
            original = Image.open(RESOURCE / 'artwork' / name)
            self.art_cache[key] = ctk.CTkImage(original, size=(width, height))
        return self.art_cache[key]

    def queue_banner(self, _event=None):
        if self.banner_job:
            self.after_cancel(self.banner_job)
        self.banner_job = self.after(70, self.paint_banner)

    def paint_banner(self):
        self.banner_job = None
        if not self.header.winfo_exists():
            return
        scale = self.header._get_widget_scaling()
        width = max(1, round(self.header.winfo_width() / scale) - 4)
        height = 132
        raster_width, raster_height = round(width * scale), round(height * scale)
        plate = ImageOps.fit(self.hero_source, (raster_width, raster_height), centering=(0.65, 0.25))
        tint = Image.new('RGB', plate.size, C['sidebar'])
        mask = Image.new('L', plate.size)
        curve = [int(255 * max(0, min(1, (x / raster_width - 0.43) / 0.35))) for x in range(raster_width)]
        mask.putdata(curve * raster_height)
        plate = Image.composite(plate, tint, mask)
        self.banner_image = ctk.CTkImage(plate, size=(width, height))
        self.banner.configure(image=self.banner_image, width=width, height=height)

    def icon(self, name, size=20, color=None):
        color = color or C['accent']
        key = (name, size, color)
        if key not in self.icons:
            file = RESOURCE / (name + '.png')
            if file.exists():
                source = Image.open(file).convert('RGBA')
                tinted = Image.new('RGBA', source.size, color)
                tinted.putalpha(source.getchannel('A'))
                self.icons[key] = ctk.CTkImage(tinted, size=(size, size))
            else:
                self.icons[key] = None
        return self.icons[key]

    def button(self, parent, text, command, kind='secondary', width=140, icon=None):
        primary = kind == 'primary'
        quiet = kind == 'quiet'
        button = ctk.CTkButton(parent, text=text, command=command, width=width, height=38,
            corner_radius=5, fg_color=C['accent'] if primary else ('transparent' if quiet else C['panel']),
            hover_color=C['accent_hover'] if primary else C['hover'],
            text_color=C['ink'] if primary else C['text'], text_color_disabled='#979c85',
            font=ctk.CTkFont('Segoe UI', 13, weight='bold' if primary else 'normal'),
            border_width=1 if not quiet else 0, border_color=C['line'],
            image=self.icon(icon, color=C['ink'] if primary else C['accent']) if icon else None)
        self.keyboard_button(button, primary=primary)
        self.controls.append(button)
        return button

    def keyboard_button(self, button, primary=False):
        width = button.cget('border_width')
        border = button.cget('border_color')
        button._canvas.configure(takefocus=1)
        button.bind('<FocusIn>', lambda _: button.configure(border_width=3, border_color=C['ink'] if primary else C['accent']))
        button.bind('<FocusOut>', lambda _: button.configure(border_width=width, border_color=border))
        button.bind('<Return>', lambda _: button.invoke())
        button.bind('<space>', lambda _: button.invoke())

    def destroy(self):
        if getattr(self, 'banner_job', None):
            self.after_cancel(self.banner_job)
            self.banner_job = None
        if hasattr(self, 'log'):
            for handler in list(self.log.handlers):
                handler.close()
                self.log.removeHandler(handler)
        super().destroy()

    def build_sidebar(self):
        side = ctk.CTkFrame(self, width=208, fg_color=C['sidebar'], border_width=1, border_color=C['wood'], corner_radius=0)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(5, weight=1)
        brand = ctk.CTkFrame(side, fg_color='transparent')
        brand.grid(row=0, column=0, sticky='ew', padx=16, pady=(24, 24))
        self.label(brand, '', image=self.art('logo.png', 176, 96)).pack(pady=(0, 10))
        self.label(brand, 'MOD MANAGER', 13, 'bold', color=C['gold']).pack()
        ctk.CTkFrame(brand, height=2, fg_color=C['wood'], corner_radius=0).pack(fill='x', pady=(16, 0))
        self.nav = {}
        for row, (name, icon) in enumerate([('Library', 'library'), ('Backups', 'shield'), ('Guide', 'guide')], 1):
            btn = self.button(side, name, lambda n=name: self.show_page(n), kind='quiet', icon=icon)
            btn.configure(anchor='w', height=44)
            btn.grid(row=row, column=0, sticky='ew', padx=12, pady=3)
            self.nav[name] = btn
        self.library_count = self.label(side, '0 mods enabled', 12, color=C['muted'])
        self.library_count.grid(row=4, column=0, sticky='w', padx=24, pady=(18, 0))
        lower = ctk.CTkFrame(side, fg_color='transparent')
        lower.grid(row=6, column=0, sticky='ew', padx=16, pady=(0, 24))
        self.label(lower, 'Game folder', 12, color=C['muted']).pack(anchor='w', padx=4)
        location = str(self.manager.root)
        if len(location) > 32:
            location = '…/' + self.manager.root.name
        self.label(lower, location, 11, color=C['muted'], wraplength=166, justify='left', anchor='w').pack(anchor='w', padx=4, pady=(4, 12))
        self.button(lower, 'Open game folder', lambda: self.open_folder(self.manager.root), kind='quiet', width=176, icon='folder').pack(fill='x', pady=(0, 12))
        self.launch_button = self.button(lower, 'Launch game', self.launch, kind='primary', width=176, icon='play')
        self.launch_button.pack(fill='x')
        self.label(lower, 'Version 1.1.1', 11, color=C['muted']).pack(anchor='w', pady=(14, 0), padx=4)

    def show_page(self, page):
        if self.busy:
            return
        try:
            self.manager.reload_state()
        except Exception as exc:
            self.notice_message(str(exc), error=True)
            return
        self.page = page
        self.controls = [b for b in self.controls if b.winfo_exists() and not self.is_child(b, self.content)]
        for child in self.content.winfo_children():
            child.destroy()
        # Reset grid tracks left by other pages.
        for row in range(3):
            self.content.grid_rowconfigure(row, weight=0)
        for name, btn in self.nav.items():
            btn.configure(fg_color=C['selected'] if name == page else 'transparent',
                          text_color=C['accent'] if name == page else C['muted'])
        self.library_count.configure(text=f'{len(self.manager.state["active"])} mods enabled')
        if page == 'Library':
            self.heading.configure(text='Mod library')
            self.subheading.configure(text='Your collection. Your game.')
            self.build_library()
        elif page == 'Backups':
            self.heading.configure(text='Original files')
            self.subheading.configure(text='A way back to the game you started with.')
            self.build_backups()
        else:
            self.heading.configure(text='Getting started')
            self.subheading.configure(text='A few things to know before your first mod.')
            self.build_guide()

    @staticmethod
    def is_child(widget, ancestor):
        while widget:
            if widget == ancestor:
                return True
            widget = getattr(widget, 'master', None)
        return False

    def build_library(self):
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_columnconfigure(1, weight=0, minsize=280)
        self.content.grid_rowconfigure(1, weight=1)
        tools = ctk.CTkFrame(self.content, fg_color='transparent')
        tools.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 16))
        tools.grid_columnconfigure(0, weight=1)
        self.search_entry = ctk.CTkEntry(tools, placeholder_text='Search mods',
            height=38, fg_color=C['sidebar'], border_color=C['line'], text_color=C['text'],
            placeholder_text_color=C['muted'], corner_radius=8, font=('Segoe UI', 13))
        self.search_entry.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        if self.search.get():
            self.search_entry.insert(0, self.search.get())
        self.search_entry.bind('<KeyRelease>', lambda _: self.search.set(self.search_entry.get()))
        self.button(tools, 'Import folder', self.import_folder, width=114).grid(row=0, column=1, padx=(0, 8))
        self.button(tools, 'Import ZIP', self.import_zip, kind='primary', width=116).grid(row=0, column=2)
        self.button(tools, 'Create Patch', self.open_create_patch_dialog, width=116).grid(row=0, column=3, padx=(8, 0))
        left = ctk.CTkFrame(self.content, fg_color='transparent')
        left.grid(row=1, column=0, sticky='nsew', padx=(0, 18))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        self.tabs = ctk.CTkSegmentedButton(left, values=['All mods', 'Enabled', 'Disabled'], command=self.set_filter,
            font=('Segoe UI', 12), height=32, fg_color=C['sidebar'], selected_color=C['selected'],
            selected_hover_color=C['hover'], unselected_color=C['sidebar'], unselected_hover_color=C['panel'],
            text_color=C['text'])
        self.tabs.set(self.filter)
        self.tabs.grid(row=0, column=0, sticky='w', pady=(0, 12))
        self.list_holder = ctk.CTkFrame(left, fg_color=C['sidebar'], corner_radius=8, border_width=2, border_color=C['wood'])
        self.list_holder.grid(row=1, column=0, sticky='nsew')
        self.list_holder.grid_columnconfigure(0, weight=1)
        self.list_holder.grid_rowconfigure(0, weight=1)
        self.list_count = self.label(left, '', 12, color=C['muted'])
        self.list_count.grid(row=2, column=0, sticky='w', pady=(10, 0))
        self.details = ctk.CTkFrame(self.content, fg_color=C['panel'], corner_radius=8, width=280, border_width=2, border_color=C['wood'])
        self.details.grid(row=1, column=1, sticky='nsew')
        self.details.grid_propagate(False)
        self.render_library()

    def set_filter(self, value):
        self.filter = value
        self.render_library()

    def reload(self):
        if not self.busy:
            self.show_page(self.page)

    def render_library(self):
        if not hasattr(self, 'list_holder') or self.page != 'Library' or not self.list_holder.winfo_exists():
            return
        if self.busy:
            return
        for child in self.list_holder.winfo_children():
            child.destroy()
        self.entries = self.manager.catalog()
        active = self.manager.state['active']
        query = self.search.get().casefold().strip()
        items = [m for m in self.entries if query in (m['name'] + ' ' + m['description']).casefold()
                 and (self.filter == 'All mods' or (m['id'] in active) == (self.filter == 'Enabled'))]
        items.sort(key=lambda m: (m['id'] not in active, m['name'].casefold()))
        self.list_count.configure(text=f'{len(items)} shown  /  {len(self.entries)} installed')
        if not items:
            empty = ctk.CTkFrame(self.list_holder, fg_color='transparent')
            empty.grid(row=0, column=0)
            self.label(empty, '', image=self.icon('folder_large', 68)).pack(pady=(0, 16))
            self.label(empty, 'Add your first mod' if not self.entries else 'No matching mods', 20, 'bold').pack()
            self.label(empty, 'Import a ZIP or a mod folder to begin.' if not self.entries else 'Try a different search or filter.',
                       color=C['muted'], wraplength=300).pack(pady=(8, 20))
            if not self.entries:
                self.button(empty, 'Import a mod ZIP', self.import_zip, kind='primary', width=168).pack()
                self.label(empty, 'Supports patches and compatible game-file replacements.', 12, color=C['muted'], wraplength=280).pack(pady=(18, 0))
            elif query or self.filter != 'All mods':
                self.button(empty, 'Clear filters', self.clear_filters, kind='quiet').pack()
        else:
            scroll = ctk.CTkScrollableFrame(self.list_holder, fg_color='transparent', corner_radius=0,
                scrollbar_button_color=C['line'], scrollbar_button_hover_color=C['hover'])
            scroll.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)
            scroll.grid_columnconfigure(0, weight=1)
            for row, item in enumerate(items):
                enabled = item['id'] in active
                # Keep room for the state badge, even in the smallest window.
                text = item['name'][:32] + ('…' if len(item['name']) > 32 else '')
                subtitle = f'v{item["version"][:16]}  ·  ' + ('Game patch' if item.get('kind') == 'binary_patch' else 'Compatible game patch' if item.get('kind') == 'umt_patch' else f'{len(item["files"])} file(s)')
                row_frame = ctk.CTkFrame(scroll, fg_color=C['selected'] if item['id'] == self.selected else C['panel'], corner_radius=6, border_width=1, border_color=C['gold'] if item['id'] == self.selected else C['line'])
                row_frame.grid(row=row, column=0, sticky='ew', pady=(0, 8))
                row_frame.grid_columnconfigure(0, weight=1)
                b = ctk.CTkButton(row_frame, text=text + '\n' + subtitle,
                    command=lambda mid=item['id']: self.select_mod(mid), height=80, anchor='w',
                    fg_color=C['selected'] if item['id'] == self.selected else C['panel'], hover_color=C['hover'],
                    corner_radius=6, text_color=C['text'], font=('Segoe UI', 14), border_spacing=16)
                b.grid(row=0, column=0, sticky='ew', padx=1, pady=1)
                self.keyboard_button(b)
                badge = self.label(row_frame, 'Enabled' if enabled else 'Disabled', 11,
                                    color=C['accent'] if enabled else C['muted'],
                                    fg_color=C['selected'] if enabled else C['sidebar'], corner_radius=5, width=68, height=24)
                badge.grid(row=0, column=1, sticky='e', padx=(0, 14))
                badge.bind('<Button-1>', lambda _, mid=item['id']: self.select_mod(mid))
                self.controls.append(b)
        self.render_details()

    def clear_filters(self):
        self.filter = 'All mods'
        self.tabs.set(self.filter)
        self.search_entry.delete(0, 'end')
        self.search.set('')
        self.render_library()

    def select_mod(self, mod_id):
        self.selected = mod_id
        self.render_library()

    def render_details(self):
        self.controls = [b for b in self.controls if b.winfo_exists()]
        for child in self.details.winfo_children():
            child.destroy()
        self.details.grid_columnconfigure(0, weight=1)
        self.details.grid_rowconfigure(1, weight=1)
        self.label(self.details, 'Mod details', 16, 'bold').grid(row=0, column=0, sticky='w', padx=20, pady=(20, 12))
        item = next((m for m in self.entries if m['id'] == self.selected), None)
        scroll = ctk.CTkScrollableFrame(self.details, fg_color='transparent', corner_radius=0,
            scrollbar_button_color=C['line'], scrollbar_button_hover_color=C['hover'])
        scroll.grid(row=1, column=0, sticky='nsew', padx=12)
        scroll.grid_columnconfigure(0, weight=1)
        if not item:
            self.label(scroll, '', image=self.art('header.jpg', 224, 105)).grid(row=0, column=0, sticky='w', padx=8, pady=(8, 16))
            self.label(scroll, 'Select a mod', 18, 'bold').grid(row=1, column=0, sticky='w', padx=8, pady=(12, 8))
            self.label(scroll, 'See what it changes, check for conflicts, and enable it when you’re ready.',
                color=C['muted'], wraplength=218, justify='left').grid(row=2, column=0, sticky='w', padx=8)
            self.label(scroll, 'Original files are backed up before the first change.', 12,
                color=C['muted'], wraplength=218, justify='left').grid(row=3, column=0, sticky='w', padx=8, pady=(32, 0))
            return
        enabled = item['id'] in self.manager.state['active']
        title = ctk.CTkFrame(scroll, fg_color='transparent')
        title.grid(row=0, column=0, sticky='ew', padx=8, pady=(4, 4))
        preview = self.manager.mods / item['id'] / 'preview.png'
        if item.get('preview') == 'preview.png' and preview.is_file():
            try:
                with Image.open(preview) as source:
                    thumb = ctk.CTkImage(source.copy(), size=(218, 218))
                self.label(title, '', image=thumb).pack(anchor='w', pady=(0, 12))
            except (OSError, ValueError):
                pass
        self.label(title, item['name'], 18, 'bold', wraplength=218, justify='left').pack(anchor='w')
        self.label(scroll, 'Version ' + item['version'], 12, color=C['muted'], wraplength=218).grid(row=1, column=0, sticky='w', padx=8, pady=(0, 18))
        self.label(scroll, item['description'] or 'No description provided by this mod.', color=C['muted'],
                   wraplength=218, justify='left').grid(row=2, column=0, sticky='w', padx=8, pady=(0, 24))
        self.label(scroll, 'Game changes' if item.get('kind') in ('umt_patch', 'binary_patch') else 'Replacement files', 13, 'bold').grid(row=3, column=0, sticky='w', padx=8, pady=(0, 10))
        displayed_files = ['Rendering patch • combines with archive mods'] if item.get('kind') == 'umt_patch' else item['files']
        for row, name in enumerate(displayed_files, 4):
            self.label(scroll, name, 12, color=C['muted'], wraplength=218, justify='left').grid(row=row, column=0, sticky='w', padx=8, pady=3)
        conflicts = [m['name'] for m in self.entries if m['id'] != item['id'] and m['id'] in self.manager.state['active'] and set(m['files']) & set(item['files'])]
        row = 4 + len(displayed_files)
        if conflicts:
            self.label(scroll, 'Conflicts with ' + ', '.join(conflicts) + '. Disable the other mod first.', 12,
                       color=C['warn'], wraplength=218, justify='left').grid(row=row, column=0, sticky='w', padx=8, pady=(18, 8))
        actions = ctk.CTkFrame(self.details, fg_color='transparent')
        actions.grid(row=2, column=0, sticky='ew', padx=20, pady=20)
        self.toggle_button = self.button(actions, 'Disable mod' if enabled else 'Enable mod', self.toggle, kind='primary')
        self.toggle_button.pack(fill='x')
        if self.running or (conflicts and not enabled):
            self.toggle_button.configure(state='disabled')
        self.remove_button = self.button(actions, 'Remove from library', self.remove, kind='quiet')
        self.remove_button.pack(fill='x', pady=(6, 0))
        if enabled:
            self.remove_button.configure(state='disabled')

    def build_backups(self):
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_columnconfigure(1, weight=0, minsize=0)
        self.content.grid_rowconfigure(1, weight=1)
        intro = ctk.CTkFrame(self.content, fg_color=C['panel'], corner_radius=8, border_width=2, border_color=C['wood'])
        intro.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        self.label(intro, 'Restore with confidence', 20, 'bold').pack(anchor='w', padx=22, pady=(20, 8))
        self.label(intro, 'Originals are saved automatically before a file is first replaced. Restoring disables every mod.',
                   color=C['muted'], wraplength=710, justify='left').pack(anchor='w', padx=22, pady=(0, 20))
        scroll = ctk.CTkScrollableFrame(self.content, fg_color=C['sidebar'], corner_radius=8, border_width=2, border_color=C['wood'],
            scrollbar_button_color=C['line'], scrollbar_button_hover_color=C['hover'])
        scroll.grid(row=1, column=0, sticky='nsew')
        scroll.grid_columnconfigure(0, weight=1)
        originals = self.manager.state['originals']
        if not originals:
            self.label(scroll, 'No backups needed yet', 19, 'bold').grid(row=0, column=0, sticky='w', padx=18, pady=(26, 8))
            self.label(scroll, 'Enable your first mod and its original files will appear here.', color=C['muted'], wraplength=650, justify='left').grid(row=1, column=0, sticky='w', padx=18)
        for row, (name, hash_value) in enumerate(originals.items()):
            file = self.manager.backups / name
            info = size_text(file.stat().st_size) if file.exists() else 'Missing backup'
            self.label(scroll, name, 15, 'bold').grid(row=row*2, column=0, sticky='w', padx=18, pady=(18, 0))
            self.label(scroll, info + '  ·  SHA-256 ' + hash_value[:16] + '…', 12, color=C['muted']).grid(row=row*2+1, column=0, sticky='w', padx=18, pady=(4, 16))
        actions = ctk.CTkFrame(self.content, fg_color='transparent')
        actions.grid(row=2, column=0, sticky='ew', pady=(18, 0))
        self.restore_button = self.button(actions, 'Restore originals', self.restore, kind='primary')
        self.restore_button.pack(side='left', padx=(0, 10))
        self.verify_button = self.button(actions, 'Verify backups', self.verify_backups)
        self.verify_button.pack(side='left', padx=(0, 10))
        self.button(actions, 'Open backup folder', lambda: self.open_folder(self.manager.backups), kind='quiet', width=170).pack(side='left')
        if not originals or self.running:
            self.restore_button.configure(state='disabled')
        if not originals:
            self.verify_button.configure(state='disabled')

    def build_guide(self):
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_columnconfigure(1, weight=0, minsize=0)
        self.content.grid_rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(self.content, fg_color=C['sidebar'], corner_radius=8, border_width=2, border_color=C['wood'],
            scrollbar_button_color=C['line'], scrollbar_button_hover_color=C['hover'])
        paragraphs = [
            ('Import your mod', 'Choose Import ZIP or Import folder in the library. Mods can contain binary patches that require your original game, declared compiler patches, or supported replacements. Importing makes a separate copy and leaves the game untouched.'),
            ('Choose what to enable', 'Select a mod to review its description and replacement files, then choose Enable mod. Close Tribal Hunter before changing mods. Mods that replace the same file cannot be enabled together.'),
            ('Play, or return to the original', 'Launch the game from the sidebar. Disable individual mods in the library, or use Restore originals on the Backups page to disable everything.'),
            ('Making a mod ZIP', 'Edit a clean, matching data.win in UndertaleModTool and save your compiled archive. Put it in files/data.win beside mod.json, then ZIP those items. Import, enable and restore in a separate test game folder before uploading. The bundled author guide includes a manifest example.'),
            ('Keep your backups', 'Restore originals before updating the game. Do not delete the manager’s backups or state files while mods are enabled. Changes made outside the manager are detected and preserved; a warning explains what needs attention.'),
            ('GameBanana one-click install', 'Setup registers tribalhunter: links for your Windows user. A supported link downloads and verifies the mod ZIP, then selects it in your library. Enable it when ready. Manual ZIP import works offline.'),
            ('Keyboard shortcuts', 'Ctrl+O imports a ZIP. Ctrl+F searches your library. F5 refreshes the current page. Escape dismisses a notice. Tab and Shift+Tab move between controls.'),
        ]
        scroll.grid(row=0, column=0, sticky='nsew')
        scroll.grid_columnconfigure(0, weight=1)
        for row, (heading, body) in enumerate(paragraphs):
            self.label(scroll, heading, 18, 'bold').grid(row=row*2, column=0, sticky='w', padx=22, pady=(24, 8))
            self.label(scroll, body, color=C['muted'], wraplength=660, justify='left').grid(row=row*2+1, column=0, sticky='w', padx=22, pady=(0, 6))
        self.label(scroll, 'Artwork: Melonsoda Soft, from the official Steam assets. Typeface: Silkscreen, SIL Open Font License.', 12, color=C['muted'], wraplength=660, justify='left').grid(row=len(paragraphs)*2+4, column=0, sticky='w', padx=22, pady=(0, 20))
        self.button(scroll, 'Open full usage guide', lambda: self.open_file(self.manager.home / 'README.md'), kind='quiet', width=180).grid(row=len(paragraphs)*2, column=0, sticky='w', padx=22, pady=12)
        self.button(scroll, 'Mod author quick guide', lambda: self.open_file(self.manager.home / 'MAKING-MODS.md'), kind='quiet', width=180).grid(row=len(paragraphs)*2+1, column=0, sticky='w', padx=22, pady=8)
        self.button(scroll, 'Set up one-click links', self.register_one_click, kind='quiet', width=180).grid(row=len(paragraphs)*2+2, column=0, sticky='w', padx=22, pady=8)
        self.button(scroll, 'Import GameBanana link', self.import_gamebanana, kind='quiet', width=180).grid(row=len(paragraphs)*2+3, column=0, sticky='w', padx=22, pady=8)

    def notice_message(self, text, error=False):
        self.notice.configure(fg_color=C['danger_bg'] if error else C['selected'])
        self.notice_text.configure(text=text, text_color=C['error'] if error else C['text'])
        self.notice.grid(row=1, column=0, sticky='ew', pady=(0, 16))

    def hide_notice(self):
        self.notice.grid_remove()

    def run_job(self, label, fn, success):
        if self.busy:
            return
        self.busy = True
        self.hide_notice()
        self.operation.configure(text=label + ' Large game files may take a moment.')
        self.progress.grid()
        self.progress.start()
        for button in self.controls:
            if button.winfo_exists():
                button.configure(state='disabled')
        if self.page == 'Library':
            self.search_entry.configure(state='disabled')
            self.tabs.configure(state='disabled')
        def worker():
            try:
                value = fn()
                self.result_queue.put(('job', value, None, success))
            except Exception as exc:
                self.log.exception('%s failed', label)
                self.result_queue.put(('job', None, str(exc), success))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value, error, success = self.result_queue.get_nowait()
                if kind == 'progress':
                    self.operation.configure(text=value)
                    continue
                if kind == 'game':
                    if error:
                        self.game_status.configure(text='Game status unavailable', text_color=C['warn'])
                    else:
                        changed = self.running != value
                        self.running = value
                        self.game_status.configure(text='Game running' if value else 'Game closed · Ready to mod', text_color=C['warn'] if value else C['accent'])
                        self.launch_button.configure(text='Game is running' if value else 'Launch game')
                        if not self.busy:
                            previous_state = self.manager.state
                            try:
                                self.manager.reload_state()
                            except Exception as exc:
                                self.notice_message(str(exc), error=True)
                            else:
                                if changed or previous_state != self.manager.state:
                                    self.show_page(self.page)
                else:
                    self.busy = False
                    self.progress.stop()
                    self.progress.set(0)
                    self.progress.grid_remove()
                    for button in self.controls:
                        if button.winfo_exists():
                            button.configure(state='normal')
                    if error:
                        self.operation.configure(text='Operation stopped. See the message above.')
                        self.notice_message(error, error=True)
                    else:
                        if isinstance(value, str) and (self.manager.mods / value).is_dir():
                            self.selected = value
                        text = success(value) if callable(success) else success
                        self.operation.configure(text=text)
                        self.notice_message(text)
                        self.log.info(text)
                    self.show_page(self.page)
        except queue.Empty:
            pass
        if not self.busy:
            self.process_one_click()
        self.after(100, self.poll)

    def check_game(self):
        def worker():
            try:
                self.result_queue.put(('game', game_running(), None, None))
            except Exception as exc:
                self.result_queue.put(('game', None, str(exc), None))
        threading.Thread(target=worker, daemon=True).start()
        self.after(4000, self.check_game)

    def process_one_click(self):
        from one_click import take_link, import_link
        try:
            uri = take_link(self.manager.root)
        except Exception as exc:
            self.notice_message(str(exc), error=True)
            return
        if not uri:
            return
        self.deiconify(); self.lift()
        self.show_page('Library')
        self.run_job('Installing from GameBanana.',
                     lambda: import_link(self.manager, uri, lambda text: self.result_queue.put(('progress', text, None, None))),
                     'Mod installed in the library. Select Enable mod to apply it.')

    def import_gamebanana(self):
        if self.busy:
            return
        from one_click import queue_link
        uri = simpledialog.askstring('GameBanana install', 'Paste the tribalhunter: install link from the mod page.', parent=self)
        if uri:
            try:
                queue_link(self.manager.root, uri)
            except Exception as exc:
                self.notice_message(str(exc), error=True)

    def register_one_click(self):
        from one_click import register_protocol
        try:
            register_protocol(self.manager.root / 'Tribal Hunter Mod Manager.exe')
            self.notice_message('GameBanana one-click links now open this Mod Manager installation.')
        except Exception as exc:
            self.notice_message(str(exc), error=True)

    def import_zip(self):
        if self.busy:
            return
        file = filedialog.askopenfilename(parent=self, title='Import a mod ZIP', filetypes=[('Mod archive', '*.zip')])
        if file:
            self.run_job('Importing mod.', lambda: self.manager.import_mod(file), 'Mod imported. Select it in the library to enable it.')

    def open_create_patch_dialog(self):
        """Open the Create Mod Patch dialog for mod makers."""
        if self.busy:
            return
        _CreatePatchDialog(self)

    def import_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self, title='Select a mod folder')
        if folder:
            self.run_job('Importing mod.', lambda: self.manager.import_mod(folder), 'Mod imported. Select it in the library to enable it.')

    def toggle(self):
        if self.busy or not self.selected:
            return
        try:
            self.manager.reload_state()
        except Exception as exc:
            self.notice_message(str(exc), error=True)
            return
        active = list(self.manager.state['active'])
        enabled = self.selected in active
        if enabled:
            active.remove(self.selected)
        else:
            active.append(self.selected)
        self.run_job('Updating game files.', lambda: self.manager.apply(active), 'Mod disabled. Original files restored where needed.' if enabled else 'Mod enabled. You’re ready to play.')

    def remove(self):
        if self.busy or not self.selected:
            return
        item = next(m for m in self.entries if m['id'] == self.selected)
        if messagebox.askyesno('Remove mod?', f'Remove “{item["name"]}” from your library?\n\nYour source ZIP or folder will stay untouched.', parent=self):
            selected = self.selected
            self.selected = None
            self.run_job('Removing mod.', lambda: self.manager.remove(selected), 'Mod removed from the library.')

    def restore(self):
        if self.busy:
            return
        if messagebox.askyesno('Restore original game?', 'Disable all mods and restore the original game files saved by this manager?', parent=self):
            self.run_job('Restoring originals.', lambda: self.manager.apply([]), 'Original game files restored. All mods are disabled.')

    def verify_backups(self):
        def verify():
            for name, expected in self.manager.state['originals'].items():
                file = self.manager.backups / name
                if not file.is_file() or digest(file) != expected:
                    raise RuntimeError('Backup is missing or damaged: ' + name + '. Preserve your game files and recover a clean copy before restoring.')
            return len(self.manager.state['originals'])
        self.run_job('Verifying backups.', verify, lambda count: f'{count} original backup(s) verified successfully.')

    def launch(self):
        if self.busy:
            return
        try:
            exe = self.manager.root / 'Tribal Hunter.exe'
            if not exe.is_file():
                raise RuntimeError('Tribal Hunter.exe is missing. Keep the mod manager beside the game executable.')
            if not game_running():
                subprocess.Popen([str(exe)], cwd=self.manager.root)
                self.operation.configure(text='Game launched. Close it before changing mods.')
        except Exception as exc:
            self.notice_message(str(exc), error=True)

    def focus_search(self):
        if not self.busy:
            if self.page != 'Library':
                self.show_page('Library')
            self.search_entry.focus_set()

    def open_folder(self, folder):
        try:
            os.startfile(folder)
        except Exception as exc:
            self.notice_message(str(exc), error=True)

    def open_file(self, file):
        self.open_folder(file)

    def close(self):
        if self.busy:
            self.notice_message('A file operation is in progress. Wait for it to finish before closing the app.')
        else:
            self.destroy()



class _CreatePatchDialog(ctk.CTkToplevel):
    """Dialog for mod makers to create patch packages with support for thpatch, bspatch, and xdelta."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Create Mod Patch Package")
        self.geometry("660x720")
        self.resizable(False, False)
        self.configure(fg_color=C['bg'])
        self.transient(app)
        self.grab_set()
        if (RESOURCE / 'app.ico').exists():
            self.iconbitmap(str(RESOURCE / 'app.ico'))

        self.clean_path = ctk.StringVar()
        self.mod_path = ctk.StringVar()
        self.output_path = ctk.StringVar()
        self.mod_name = ctk.StringVar(value="My Mod")
        self.mod_version = ctk.StringVar(value="1.0.0")
        self.target_file = ctk.StringVar(value="data.win")
        self.format_var = ctk.StringVar(value="thpatch")
        self.preview_path = ctk.StringVar()
        self.status_var = ctk.StringVar(value="Ready. Choose your original and modified files.")

        default_clean = self.app.manager.root / "data.win"
        if default_clean.is_file():
            self.clean_path.set(str(default_clean))

        self.build_ui()

    def build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=24, pady=20)

        header = ctk.CTkFrame(container, fg_color=C['sidebar'], corner_radius=8, border_width=1, border_color=C['wood'])
        header.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(header, text="MOD MAKER: CREATE PATCH", font=ctk.CTkFont('Silkscreen', 18, 'bold'),
                     text_color=C['gold']).pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(header, text="Generate a clean binary patch mod without distributing original game files.",
                     font=ctk.CTkFont('Segoe UI', 12), text_color=C['muted']).pack(anchor="w", padx=16, pady=(0, 12))

        form = ctk.CTkScrollableFrame(container, fg_color=C['panel'], corner_radius=8, border_width=1, border_color=C['line'],
                                      scrollbar_button_color=C['line'], scrollbar_button_hover_color=C['hover'])
        form.pack(fill="both", expand=True, pady=(0, 14))

        self._row_picker(form, "Clean / Original File:", self.clean_path, self._pick_clean,
                         "Path to the unmodded file (e.g. vanilla data.win)")

        self._row_picker(form, "Modified File:", self.mod_path, self._pick_mod,
                         "Path to the edited file (from UndertaleModTool or patcher)")

        ctk.CTkLabel(form, text="Target Game File:", font=ctk.CTkFont('Segoe UI', 13, 'bold'), text_color=C['text']).pack(anchor="w", padx=14, pady=(8, 2))
        from manager import ALLOWED
        targets = sorted(list(ALLOWED))
        target_menu = ctk.CTkOptionMenu(form, variable=self.target_file, values=targets,
                                        fg_color=C['sidebar'], button_color=C['wood'], text_color=C['text'],
                                        dropdown_fg_color=C['panel'], dropdown_text_color=C['text'])
        target_menu.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(form, text="The game archive or asset this patch applies to inside the game folder.",
                     font=ctk.CTkFont('Segoe UI', 11), text_color=C['muted']).pack(anchor="w", padx=14, pady=(0, 8))

        self._row_picker(form, "Output Mod Package (.zip):", self.output_path, self._pick_output,
                         "Where to save the generated GameBanana-ready mod ZIP")

        meta_frame = ctk.CTkFrame(form, fg_color="transparent")
        meta_frame.pack(fill="x", padx=14, pady=(4, 8))
        meta_frame.grid_columnconfigure(0, weight=3)
        meta_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(meta_frame, text="Mod Name:", font=ctk.CTkFont('Segoe UI', 13, 'bold'), text_color=C['text']).grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(meta_frame, text="Version:", font=ctk.CTkFont('Segoe UI', 13, 'bold'), text_color=C['text']).grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 2))

        ctk.CTkEntry(meta_frame, textvariable=self.mod_name, fg_color=C['sidebar'], border_color=C['line'], text_color=C['text']).grid(row=1, column=0, sticky="ew")
        ctk.CTkEntry(meta_frame, textvariable=self.mod_version, fg_color=C['sidebar'], border_color=C['line'], text_color=C['text']).grid(row=1, column=1, sticky="ew", padx=(10, 0))

        ctk.CTkLabel(form, text="Patch Engine / Format:", font=ctk.CTkFont('Segoe UI', 13, 'bold'), text_color=C['text']).pack(anchor="w", padx=14, pady=(8, 2))
        fmt_bar = ctk.CTkSegmentedButton(form, values=["thpatch", "bspatch", "xdelta"], variable=self.format_var,
                                         command=self._on_format_changed,
                                         fg_color=C['sidebar'], selected_color=C['selected'],
                                         selected_hover_color=C['hover'], unselected_color=C['sidebar'],
                                         text_color=C['text'])
        fmt_bar.pack(fill="x", padx=14, pady=(0, 4))
        self.fmt_desc = ctk.CTkLabel(form, text="thpatch: Native pure-Python stream patcher (fast, standalone, zero dependencies)",
                                     font=ctk.CTkFont('Segoe UI', 11), text_color=C['muted'])
        self.fmt_desc.pack(anchor="w", padx=14, pady=(0, 8))

        self._row_picker(form, "Thumbnail Preview (optional):", self.preview_path, self._pick_preview,
                         "Optional PNG preview image for the mod manager library")

        status_bar = ctk.CTkLabel(container, textvariable=self.status_var, font=ctk.CTkFont('Segoe UI', 12), text_color=C['muted'])
        status_bar.pack(anchor="w", pady=(0, 4))

        self.pbar = ctk.CTkProgressBar(container, height=4, mode="indeterminate", progress_color=C['accent'], fg_color=C['line'])
        self.pbar.pack(fill="x", pady=(0, 10))
        self.pbar.set(0)
        self.pbar.pack_forget()

        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x")
        
        self.create_btn = ctk.CTkButton(btn_row, text="Create Mod Patch", command=self._start_create,
                                        fg_color=C['accent'], hover_color=C['accent_hover'],
                                        text_color=C['ink'], font=ctk.CTkFont('Segoe UI', 13, 'bold'),
                                        height=36, corner_radius=6)
        self.create_btn.pack(side="left", padx=(0, 10))

        ctk.CTkButton(btn_row, text="Cancel", command=self.destroy,
                      fg_color=C['panel'], hover_color=C['hover'], text_color=C['text'],
                      border_width=1, border_color=C['line'], height=36, corner_radius=6).pack(side="left")

    def _row_picker(self, parent, title, var, picker_cmd, hint):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont('Segoe UI', 13, 'bold'), text_color=C['text']).pack(anchor="w", padx=14, pady=(6, 2))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 2))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=var, fg_color=C['sidebar'], border_color=C['line'], text_color=C['text']).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(row, text="Browse…", width=76, command=picker_cmd, fg_color=C['wood'], hover_color=C['hover'],
                      text_color=C['text'], height=32).grid(row=0, column=1)
        if hint:
            ctk.CTkLabel(parent, text=hint, font=ctk.CTkFont('Segoe UI', 11), text_color=C['muted']).pack(anchor="w", padx=14, pady=(0, 6))

    def _on_format_changed(self, value):
        descs = {
            "thpatch": "thpatch: Native pure-Python stream patcher (fast, standalone, zero dependencies)",
            "bspatch": "bspatch: Colin Percival BSDiff 4.x format (high binary compression, bzip2)",
            "xdelta": "xdelta: RFC 3284 VCDIFF / XDelta3 format (industry standard for large game diffs)"
        }
        self.fmt_desc.configure(text=descs.get(value, ""))

    def _pick_clean(self):
        f = filedialog.askopenfilename(parent=self, title="Select Clean Game File",
                                       filetypes=[("Game Archives / Files", "*.*")])
        if f:
            self.clean_path.set(f)
            p = Path(f)
            if p.name in ['data.win', 'options.ini', 'opening_cinematic.mp4', 'titlescreen_loop.mp4',
                          'titlescreen_opening.avi', 'titlevideo.mp4']:
                self.target_file.set(p.name)

    def _pick_mod(self):
        f = filedialog.askopenfilename(parent=self, title="Select Modified Game File",
                                       filetypes=[("Modified Game Files", "*.*")])
        if f:
            self.mod_path.set(f)
            if not self.output_path.get():
                p = Path(f)
                suggested = p.parent / (self.mod_name.get().replace(" ", "_") + "-1.0.0.zip")
                self.output_path.set(str(suggested))

    def _pick_output(self):
        f = filedialog.asksaveasfilename(parent=self, title="Save Mod Package ZIP",
                                         defaultextension=".zip",
                                         filetypes=[("ZIP Mod Package", "*.zip")])
        if f:
            self.output_path.set(f)

    def _pick_preview(self):
        f = filedialog.askopenfilename(parent=self, title="Select Mod Thumbnail Preview",
                                       filetypes=[("PNG Image", "*.png")])
        if f:
            self.preview_path.set(f)

    def _start_create(self):
        clean = self.clean_path.get().strip()
        mod = self.mod_path.get().strip()
        out = self.output_path.get().strip()
        name = self.mod_name.get().strip()
        ver = self.mod_version.get().strip() or "1.0.0"
        fmt = self.format_var.get()
        target = self.target_file.get()
        preview = self.preview_path.get().strip() or None

        if not clean or not os.path.isfile(clean):
            messagebox.showerror("Validation Error", "Please select a valid clean (original) file.", parent=self)
            return
        if not mod or not os.path.isfile(mod):
            messagebox.showerror("Validation Error", "Please select a valid modified file.", parent=self)
            return
        if not out:
            messagebox.showerror("Validation Error", "Please specify an output ZIP file path.", parent=self)
            return
        if not name:
            messagebox.showerror("Validation Error", "Please enter a mod name.", parent=self)
            return

        self.create_btn.configure(state="disabled")
        self.status_var.set(f"Generating {fmt} patch… please wait.")
        self.pbar.pack(fill="x", pady=(0, 10))
        self.pbar.start()

        def worker():
            try:
                patching.create_package(
                    source=clean,
                    target=mod,
                    output_zip=out,
                    name=name,
                    version=ver,
                    preview=preview,
                    description="",
                    fmt=fmt,
                    target_file=target,
                )
                self.after(0, lambda: self._on_success(out))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_failure(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, zip_path):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.create_btn.configure(state="normal")
        self.status_var.set("Mod patch created successfully!")
        ans = messagebox.askyesno(
            "Patch Created",
            f"Successfully created mod package:\n{zip_path}\n\nWould you like to import it into your Mod Manager library now?",
            parent=self
        )
        if ans:
            try:
                self.app.manager.import_mod(zip_path)
                self.app.reload()
                self.app.notice_message(f"Imported '{self.mod_name.get()}' into your library.")
            except Exception as exc:
                self.app.notice_message(str(exc), error=True)
        self.destroy()

    def _on_failure(self, exc):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.create_btn.configure(state="normal")
        self.status_var.set(f"Error: {exc}")
        messagebox.showerror("Patch Creation Failed", str(exc), parent=self)
