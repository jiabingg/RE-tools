import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk
import ttkbootstrap as tb

"""
Launcher app with dynamically generated sections for different folders.
Buttons are created automatically by scanning Python files in the specified folders at runtime.
"""

class ToolTip:
    """Simple tooltip for buttons."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tooltip:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tooltip, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Helvetica", 10)
        )
        label.pack()

    def hide(self, event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


class Launcher(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Project Utilities Launcher")
        self.geometry("1100x665")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        # Define sections: (label, folder, bootstyle)
        sections = [
            ("UIC", "UIC", "primary"),
            ("Quick Reference", "Quick Reference", "info"),
            ("ODW", "ODW", "warning"),
            ("EKPSPP", "EKPSPP", "success"),
        ]

        for label, folder, bootstyle in sections:
            frame = tb.LabelFrame(self, text=label)
            frame.pack(fill="x", padx=12, pady=8)
            if label in ["UIC", "ODW", "EKPSPP"]:
                frame.configure(height=1120)
            else:
                frame.configure(height=560)
            frame.pack_propagate(False)
            folder_path = os.path.join(self.base_dir, folder)
            if os.path.isdir(folder_path):
                scripts = sorted([f for f in os.listdir(folder_path) if f.endswith('.py')])
                self.create_buttons(frame, folder, scripts, bootstyle)

    def get_script_help(self, script_path: str) -> str | None:
        """Read the first line of a script to check for #Help comment."""
        try:
            with open(script_path, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                if first_line.startswith('#Help'):
                    return first_line[5:].strip(': -').lstrip(': ')  # Return text after #Help
        except Exception:
            pass
        return None

    def create_buttons(self, frame, folder, scripts, bootstyle):
        max_cols = 4
        for i, script in enumerate(scripts):
            row = i // max_cols
            col = i % max_cols
            display_name = script[:-3]  # remove .py
            script_path = os.path.join(self.base_dir, folder, script)
            help_text = self.get_script_help(script_path)
            btn = tb.Button(
                frame,
                text=display_name,
                bootstyle=bootstyle,
                command=lambda s=script, n=display_name: self.run_script(
                    os.path.join(self.base_dir, folder, s),
                    name=n
                ),
            )
            btn.grid(row=row, column=col, padx=10, pady=10, sticky="w")
            # Add tooltip if help text is available
            if help_text:
                ToolTip(btn, help_text)

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
