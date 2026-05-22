import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk
import ttkbootstrap as tb

"""
Launcher app with dynamically generated sections for different folders.
Buttons are created automatically by scanning Python files in the specified folders at runtime.
Buttons reflow dynamically when the window is resized.

Each script can have a companion .txt file (same name, .txt extension) that serves as
a README. Hovering over the launch button shows the README content as a tooltip.
"""


# ---------------------------------------------------------------------------
# Hover tooltip — shows .txt README content on the button itself
# ---------------------------------------------------------------------------
class HelpTooltip:
    """Tooltip that shows on hover with a slight delay."""

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


class Launcher(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
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

        self.base_dir = os.path.dirname(os.path.abspath(__file__))

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

            section = tb.LabelFrame(self.scroll_frame, text=folder_name)
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

            btn = tb.Button(
                flow_frame,
                text=display_name,
                bootstyle=bootstyle,
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