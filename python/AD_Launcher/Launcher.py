import os
import sys
import ast
import subprocess
import importlib
import tkinter as tk
from tkinter import ttk

"""
Launcher app with dynamically generated sections for different folders.
Buttons are created automatically by scanning Python files in the specified folders at runtime.
On startup, scans all scripts for third-party imports, checks which are missing,
installs them automatically, and reports status in a bottom label.
Buttons reflow dynamically when the window is resized.

Each script can have a companion .txt file (same name, .txt extension) that serves as
a README. Hovering over the launch button shows the README content as a tooltip.
"""

# Standard library module names (Python 3.10+). Used to filter out non-pip packages.
STDLIB_MODULES = {
    'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio', 'asyncore',
    'atexit', 'audioop', 'base64', 'bdb', 'binascii', 'binhex', 'bisect',
    'builtins', 'bz2', 'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd',
    'code', 'codecs', 'codeop', 'collections', 'colorsys', 'compileall',
    'concurrent', 'configparser', 'contextlib', 'contextvars', 'copy', 'copyreg',
    'cProfile', 'crypt', 'csv', 'ctypes', 'curses', 'dataclasses', 'datetime',
    'dbm', 'decimal', 'difflib', 'dis', 'distutils', 'doctest', 'email',
    'encodings', 'enum', 'errno', 'faulthandler', 'fcntl', 'filecmp', 'fileinput',
    'fnmatch', 'fractions', 'ftplib', 'functools', 'gc', 'getopt', 'getpass',
    'gettext', 'glob', 'grp', 'gzip', 'hashlib', 'heapq', 'hmac', 'html',
    'http', 'idlelib', 'imaplib', 'imghdr', 'imp', 'importlib', 'inspect', 'io',
    'ipaddress', 'itertools', 'json', 'keyword', 'lib2to3', 'linecache', 'locale',
    'logging', 'lzma', 'mailbox', 'mailcap', 'marshal', 'math', 'mimetypes',
    'mmap', 'modulefinder', 'msvcrt', 'multiprocessing', 'netrc', 'nis', 'nntplib',
    'numbers', 'operator', 'optparse', 'os', 'ossaudiodev', 'pathlib',
    'pdb', 'pickle', 'pickletools', 'pipes', 'pkgutil', 'platform', 'plistlib',
    'poplib', 'posix', 'posixpath', 'pprint', 'profile', 'pstats', 'pty',
    'pwd', 'py_compile', 'pyclbr', 'pydoc', 'queue', 'quopri', 'random', 're',
    'readline', 'reprlib', 'resource', 'rlcompleter', 'runpy', 'sched', 'secrets',
    'select', 'selectors', 'shelve', 'shlex', 'shutil', 'signal', 'site',
    'smtpd', 'smtplib', 'sndhdr', 'socket', 'socketserver', 'spwd', 'sqlite3',
    'sre_compile', 'sre_constants', 'sre_parse', 'ssl', 'stat', 'statistics',
    'string', 'stringprep', 'struct', 'subprocess', 'sunau', 'symtable', 'sys',
    'sysconfig', 'syslog', 'tabnanny', 'tarfile', 'telnetlib', 'tempfile',
    'termios', 'test', 'textwrap', 'threading', 'time', 'timeit', 'tkinter',
    'token', 'tokenize', 'tomllib', 'trace', 'traceback', 'tracemalloc', 'tty',
    'turtle', 'turtledemo', 'types', 'typing', 'unicodedata', 'unittest', 'urllib',
    'uu', 'uuid', 'venv', 'warnings', 'wave', 'weakref', 'webbrowser', 'winreg',
    'winsound', 'wsgiref', 'xdrlib', 'xml', 'xmlrpc', 'zipapp', 'zipfile',
    'zipimport', 'zlib', '_thread', '__future__',
}

# Map import names that differ from their pip package names
IMPORT_TO_PACKAGE = {
    'cv2': 'opencv-python',
    'PIL': 'Pillow',
    'sklearn': 'scikit-learn',
    'skimage': 'scikit-image',
    'bs4': 'beautifulsoup4',
    'yaml': 'PyYAML',
    'wx': 'wxPython',
    'gi': 'PyGObject',
    'attr': 'attrs',
    'dateutil': 'python-dateutil',
    'dotenv': 'python-dotenv',
    'serial': 'pyserial',
    'usb': 'pyusb',
    'docx': 'python-docx',
    'pptx': 'python-pptx',
    'win32com': 'pywin32',
    'win32api': 'pywin32',
    'win32gui': 'pywin32',
    'pywintypes': 'pywin32',
    'pythoncom': 'pywin32',
    'tb': 'ttkbootstrap',
    'cx_Oracle': 'cx_Oracle',
    'oracledb': 'oracledb',
}


def scan_imports_from_file(filepath: str) -> set[str]:
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, ValueError):
        return set()
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imports.add(node.module.split('.')[0])
    return imports


def scan_all_imports(base_dir: str) -> dict[str, str]:
    all_imports = set()
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))]
        for f in files:
            if f.endswith('.py'):
                all_imports |= scan_imports_from_file(os.path.join(root, f))
    packages = {}
    for mod in all_imports:
        if mod in STDLIB_MODULES:
            continue
        pip_name = IMPORT_TO_PACKAGE.get(mod, mod)
        packages[mod] = pip_name
    return packages


def check_missing(packages: dict[str, str]) -> dict[str, str]:
    missing = {}
    for import_name, pip_name in packages.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing[import_name] = pip_name
    return missing


def install_packages(pip_names: list[str]) -> tuple[bool, str, list[str]]:
    """Install packages one by one. Returns (all_ok, message, failed_list)."""
    unique = sorted(set(pip_names))
    installed = []
    failed = []
    for pkg in unique:
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', pkg, '--quiet'],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                installed.append(pkg)
            else:
                failed.append(pkg)
        except Exception:
            failed.append(pkg)

    if failed and installed:
        msg = (f"Installed {len(installed)} package(s): {', '.join(installed)}. "
               f"Failed: {', '.join(failed)}")
        return False, msg, failed
    elif failed:
        return False, f"Failed to install: {', '.join(failed)}", failed
    else:
        return True, f"Installed {len(installed)} package(s): {', '.join(installed)}", []


# ---------------------------------------------------------------------------
# Package check runs BEFORE ttkbootstrap import
# ---------------------------------------------------------------------------
_base_dir = os.path.dirname(os.path.abspath(__file__))
_all_packages = scan_all_imports(_base_dir)
_missing = check_missing(_all_packages)
_startup_status = ""

if _missing:
    _ok, _msg, _failed = install_packages(list(_missing.values()))
    if _ok:
        _startup_status = f"\u2705  {_msg}"
    else:
        _startup_status = f"\u26a0\ufe0f  {_msg}"
else:
    _startup_status = f"\u2705  All {len(_all_packages)} required packages detected"

try:
    import ttkbootstrap as tb
except ImportError:
    tb = None


# ---------------------------------------------------------------------------
# Hover tooltip — shows .txt README content on the button itself
# ---------------------------------------------------------------------------
class HelpTooltip:
    """Tooltip that shows on hover with a slight delay. Stays on screen."""

    MAX_WIDTH = 480
    SHOW_DELAY = 400  # ms

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self._after_id = None
        self.widget.bind("<Enter>", self._schedule_show)
        self.widget.bind("<Leave>", self.hide)

    def _schedule_show(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.SHOW_DELAY, self._show)

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self.tooltip:
            return
        # Position below the button
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4

        self.tooltip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)

        frame = tk.Frame(tw, background="#fffde7", relief="solid", borderwidth=1)
        frame.pack()

        label = tk.Label(
            frame, text=self.text, justify="left", anchor="nw",
            background="#fffde7", foreground="#333333",
            font=("Consolas", 10),
            wraplength=self.MAX_WIDTH,
            padx=10, pady=8,
        )
        label.pack()

        # Keep tooltip on screen
        tw.update_idletasks()
        tw_w = tw.winfo_reqwidth()
        tw_h = tw.winfo_reqheight()
        scr_w = self.widget.winfo_screenwidth()
        scr_h = self.widget.winfo_screenheight()
        if x + tw_w > scr_w:
            x = scr_w - tw_w - 10
        if y + tw_h > scr_h:
            y = self.widget.winfo_rooty() - tw_h - 4
        tw.wm_geometry(f"+{x}+{y}")

    def hide(self, event=None):
        self._cancel()
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


# ---------------------------------------------------------------------------
# Flow layout frame — wraps buttons dynamically on resize
# ---------------------------------------------------------------------------
class FlowFrame(ttk.Frame):
    """Lays out child widgets in a wrapping flow layout."""

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


BOOTSTYLES = ["primary", "info", "warning", "success", "danger", "secondary"]


class Launcher(tb.Window if tb else tk.Tk):
    def __init__(self):
        if tb:
            super().__init__(themename="flatly")
        else:
            super().__init__()

        self.title("Project Utilities Launcher")

        # Size to 80% of screen, centered
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = int(screen_w * 0.8)
        win_h = int(screen_h * 0.8)
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.base_dir = _base_dir

        # --- Status bar at bottom (pack first so it stays visible) ---
        status_frame = ttk.Frame(self)
        status_frame.pack(side="bottom", fill="x")
        ttk.Separator(status_frame, orient="horizontal").pack(fill="x")
        self.status_label = ttk.Label(
            status_frame, text=_startup_status,
            font=("Helvetica", 10), padding=(12, 6),
        )
        self.status_label.pack(anchor="w")

        # --- Scrollable sections ---
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
            bootstyle = BOOTSTYLES[idx % len(BOOTSTYLES)]
            folder_path = os.path.join(self.base_dir, folder_name)
            scripts = sorted([f for f in os.listdir(folder_path) if f.endswith('.py')])

            if tb:
                section = tb.LabelFrame(self.scroll_frame, text=folder_name)
            else:
                section = ttk.LabelFrame(self.scroll_frame, text=folder_name)
            section.pack(fill="x", padx=12, pady=8)

            flow = FlowFrame(section)
            flow.pack(fill="x", expand=True)

            self.create_buttons(flow, folder_name, scripts, bootstyle)

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
        """Check for a companion .txt file and return its content."""
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

    def create_buttons(self, flow_frame, folder, scripts, bootstyle):
        for script in scripts:
            display_name = script[:-3]
            script_path = os.path.join(self.base_dir, folder, script)
            help_text = self.read_help_file(script_path)

            if tb:
                btn = tb.Button(
                    flow_frame,
                    text=display_name,
                    bootstyle=bootstyle,
                    command=lambda s=script, f=folder: self.run_script(
                        os.path.join(self.base_dir, f, s)
                    ),
                )
            else:
                btn = ttk.Button(
                    flow_frame,
                    text=display_name,
                    command=lambda s=script, f=folder: self.run_script(
                        os.path.join(self.base_dir, f, s)
                    ),
                )

            # Attach tooltip directly to the button if .txt exists
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