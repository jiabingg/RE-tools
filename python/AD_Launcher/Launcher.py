import os
import re
import sys
import subprocess
import tkinter as tk
from tkinter import ttk
import ttkbootstrap as tb
from ttkbootstrap.constants import *

"""
Project Utilities Launcher
─────────────────────────
Dynamically discovers subfolders containing .py scripts and presents them
as categorized, icon-decorated launch buttons with flowing layout.

Companion .txt files (same base name) provide hover-based README tooltips.
Script filenames starting with "1. ", "02. ", etc. have the prefix stripped
from the display name while preserving sort order.
"""

# ── Strip leading number prefix ──────────────────────────────────────────
# Matches patterns like "1. ", "02. ", "12 - ", "3_", "01-" at the start
_NUMBER_PREFIX = re.compile(r'^\d+[\.\-_\s]+\s*')


def clean_display_name(filename_no_ext: str) -> str:
    """Remove leading number prefix from display name."""
    return _NUMBER_PREFIX.sub('', filename_no_ext)


# ── Icon assignment ──────────────────────────────────────────────────────
FOLDER_ICONS = {
    'uic':            '\U0001F6E2',   # 🛢
    'injection':      '\U0001F4A7',   # 💧
    'production':     '\u26F3',       # ⛳
    'surveillance':   '\U0001F50D',   # 🔍
    'monitoring':     '\U0001F4C8',   # 📈
    'quicklook':      '\u26A1',       # ⚡
    'quick reference': '\u26A1',      # ⚡
    'odw':            '\U0001F5C4',   # 🗄
    'database':       '\U0001F5C4',   # 🗄
    'data':           '\U0001F4CA',   # 📊
    'analysis':       '\U0001F4CA',   # 📊
    'reports':        '\U0001F4CB',   # 📋
    'wellbore':       '\U0001F529',   # 🔩
    'mechanical':     '\U0001F527',   # 🔧
    'completion':     '\U0001F3AF',   # 🎯
    'ekpspp':         '\U0001F3ED',   # 🏭
    'regulatory':     '\U0001F4DC',   # 📜
    'calgem':         '\U0001F4DC',   # 📜
    'compliance':     '\u2696',       # ⚖
    'tools':          '\U0001F6E0',   # 🛠
    'utilities':      '\U0001F6E0',   # 🛠
    'scripts':        '\U0001F4DD',   # 📝
    'mapping':        '\U0001F5FA',   # 🗺
    'maps':           '\U0001F5FA',   # 🗺
    'charts':         '\U0001F4C9',   # 📉
    'export':         '\U0001F4E4',   # 📤
    'import':         '\U0001F4E5',   # 📥
}

FALLBACK_ICONS = [
    '\U0001F4C2', '\U0001F4E6', '\U0001F9EA',
    '\U0001F4BB', '\U0001F680', '\U0001F50E',
]

SCRIPT_ICON_HINTS = {
    'chart':     '\U0001F4CA',  'plot':      '\U0001F4C8',
    'map':       '\U0001F5FA',  'report':    '\U0001F4CB',
    'export':    '\U0001F4E4',  'import':    '\U0001F4E5',
    'download':  '\u2B07',      'upload':    '\u2B06',
    'search':    '\U0001F50D',  'find':      '\U0001F50D',
    'viewer':    '\U0001F441',  'visual':    '\U0001F441',
    'wbd':       '\U0001F4D0',  'diagram':   '\U0001F4D0',
    'well':      '\U0001F6E2',  'field':     '\U0001F30D',
    'quicklook': '\u26A1',      'temp':      '\U0001F321',
    'survey':    '\U0001F4CF',  'passport':  '\U0001F4D8',
    'abandon':   '\U0001F6A7',  'aor':       '\U0001F4DC',
    'perf':      '\U0001F3AF',  'inject':    '\U0001F4A7',
    'prod':      '\U0001F4C8',  'create':    '\u2728',
    'new':       '\u2728',      'test':      '\U0001F9EA',
    'calc':      '\U0001F5A9',  'compare':   '\U0001F504',
}


def get_folder_icon(folder_name: str, idx: int) -> str:
    key = folder_name.lower().strip()
    if key in FOLDER_ICONS:
        return FOLDER_ICONS[key]
    return FALLBACK_ICONS[idx % len(FALLBACK_ICONS)]


def get_script_icon(script_name: str, folder_icon: str) -> str:
    name_lower = script_name.lower()
    for keyword, icon in SCRIPT_ICON_HINTS.items():
        if keyword in name_lower:
            return icon
    return folder_icon


# ── Tooltip ──────────────────────────────────────────────────────────────
class HelpTooltip:
    MAX_WIDTH = 500
    SHOW_DELAY = 400

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self._after_id = None
        self.widget.bind("<Enter>", self._schedule, add="+")
        self.widget.bind("<Leave>", self.hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.SHOW_DELAY, self._show)

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self.tooltip:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6

        self.tooltip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-alpha", 0.97)

        outer = tk.Frame(tw, background="#5a5a5a", padx=1, pady=1)
        outer.pack()
        inner = tk.Frame(outer, background="#fffef5")
        inner.pack()

        label = tk.Label(
            inner, text=self.text, justify="left", anchor="nw",
            background="#fffef5", foreground="#2a2a2a",
            font=("Consolas", 10),
            wraplength=self.MAX_WIDTH,
            padx=12, pady=10,
        )
        label.pack()

        tw.update_idletasks()
        tw_w = tw.winfo_reqwidth()
        tw_h = tw.winfo_reqheight()
        scr_w = self.widget.winfo_screenwidth()
        scr_h = self.widget.winfo_screenheight()
        if x + tw_w > scr_w:
            x = scr_w - tw_w - 10
        if y + tw_h > scr_h:
            y = self.widget.winfo_rooty() - tw_h - 6
        tw.wm_geometry(f"+{x}+{y}")

    def hide(self, event=None):
        self._cancel()
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


# ── Flow layout ──────────────────────────────────────────────────────────
class FlowFrame(ttk.Frame):
    def __init__(self, parent, pad_x=24, pad_y=16, **kwargs):
        super().__init__(parent, **kwargs)
        self.pad_x = pad_x
        self.pad_y = pad_y
        self._widgets = []
        self.bind("<Configure>", self._reflow)

    def add_widget(self, widget):
        self._widgets.append(widget)

    def _reflow(self, event=None):
        if not self._widgets:
            return
        frame_width = self.winfo_width()
        if frame_width <= 1:
            return

        x = self.pad_x
        y = self.pad_y
        row_height = 0

        for w in self._widgets:
            w.update_idletasks()
            w_width = w.winfo_reqwidth()
            w_height = w.winfo_reqheight()

            if x + w_width + self.pad_x > frame_width and x > self.pad_x:
                x = self.pad_x
                y += row_height + self.pad_y
                row_height = 0

            w.place(x=x, y=y)
            x += w_width + self.pad_x
            row_height = max(row_height, w_height)

        total_height = y + row_height + self.pad_y
        self.configure(height=total_height)


# ── Section colors ───────────────────────────────────────────────────────
SECTION_STYLES = [
    ("primary",   "#375a7f"),
    ("info",      "#3498db"),
    ("success",   "#00bc8c"),
    ("warning",   "#f39c12"),
    ("danger",    "#e74c3c"),
    ("secondary", "#6c757d"),
]


# ── Main Launcher ────────────────────────────────────────────────────────
class Launcher(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("RE Tools  \u2502  Project Utilities Launcher")

        # 80% of screen, centered
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = int(screen_w * 0.8)
        win_h = int(screen_h * 0.8)
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        # ── Button font — 25% larger ──
        s = ttk.Style()
        s.configure("TButton", font=("Segoe UI", 15, "bold"))

        # ── Header ──
        header = ttk.Frame(self)
        header.pack(fill="x", padx=24, pady=(22, 4))

        title_lbl = ttk.Label(
            header,
            text="\U0001F6E0  RE Tools Launcher",
            font=("Segoe UI", 22, "bold"),
        )
        title_lbl.pack(side="left")

        subtitle_lbl = ttk.Label(
            header,
            text="Hover any button for help  \u00b7  Click to launch",
            font=("Segoe UI", 11),
            foreground="#888888",
        )
        subtitle_lbl.pack(side="left", padx=(16, 0), pady=(8, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20, pady=(12, 0))

        # ── Scrollable body ──
        sections = self.discover_sections()

        container = ttk.Frame(self)
        container.pack(side="top", fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw",
                             tags="scroll_window")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_canvas_configure(event):
            canvas.itemconfig("scroll_window", width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for idx, folder_name in enumerate(sections):
            bootstyle, accent = SECTION_STYLES[idx % len(SECTION_STYLES)]
            folder_icon = get_folder_icon(folder_name, idx)
            folder_path = os.path.join(self.base_dir, folder_name)
            scripts = sorted([f for f in os.listdir(folder_path) if f.endswith('.py')])

            # Section header
            section_frame = ttk.Frame(self.scroll_frame)
            section_frame.pack(fill="x", padx=20, pady=(28, 0))

            section_label = ttk.Label(
                section_frame,
                text=f"{folder_icon}  {folder_name}",
                font=("Segoe UI", 16, "bold"),
                foreground=accent,
            )
            section_label.pack(side="left", padx=(4, 0))

            count_label = ttk.Label(
                section_frame,
                text=f"{len(scripts)} tool{'s' if len(scripts) != 1 else ''}",
                font=("Segoe UI", 10),
                foreground="#aaaaaa",
            )
            count_label.pack(side="left", padx=(12, 0), pady=(5, 0))

            accent_line = tk.Frame(self.scroll_frame, background=accent, height=2)
            accent_line.pack(fill="x", padx=24, pady=(6, 2))

            flow = FlowFrame(self.scroll_frame, pad_x=24, pad_y=16)
            flow.pack(fill="x", expand=True, padx=8)

            self.create_buttons(flow, folder_name, scripts, bootstyle, folder_icon)

        # Bottom spacer
        ttk.Frame(self.scroll_frame, height=30).pack()

    def discover_sections(self) -> list[str]:
        sections = []
        for entry in sorted(os.listdir(self.base_dir)):
            folder_path = os.path.join(self.base_dir, entry)
            if not os.path.isdir(folder_path):
                continue
            if entry.startswith(('.', '_')):
                continue
            py_files = [f for f in os.listdir(folder_path) if f.endswith('.py')]
            if py_files:
                sections.append(entry)
        return sections

    def read_help_file(self, script_path: str) -> str | None:
        txt_path = os.path.splitext(script_path)[0] + '.txt'
        if os.path.isfile(txt_path):
            try:
                with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                if content:
                    return content
            except Exception:
                pass
        return None

    def create_buttons(self, flow_frame, folder, scripts, bootstyle, folder_icon):
        for script in scripts:
            raw_name = script[:-3]  # remove .py
            display_name = clean_display_name(raw_name)
            script_path = os.path.join(self.base_dir, folder, script)
            help_text = self.read_help_file(script_path)

            icon = get_script_icon(display_name, folder_icon)
            button_text = f"{icon}  {display_name}"

            btn = tb.Button(
                flow_frame,
                text=button_text,
                bootstyle=bootstyle,
                padding=(20, 12),
                command=lambda s=script, f=folder: self.run_script(
                    os.path.join(self.base_dir, f, s)
                ),
            )

            if help_text:
                HelpTooltip(btn, help_text)

            flow_frame.add_widget(btn)

    def run_script(self, path: str, name: str = "Script"):
        if not os.path.isfile(path):
            return
        cmd = [sys.executable, path]
        try:
            subprocess.Popen(cmd, cwd=os.path.dirname(path) or self.base_dir)
        except Exception:
            pass


if __name__ == "__main__":
    app = Launcher()
    app.mainloop()