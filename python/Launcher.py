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

class Launcher(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Project Utilities Launcher")
        self.geometry("1100x950")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        # Define sections: (label, folder, bootstyle)
        sections = [
            ("ODW", "ODW", "primary"),
            ("EKPSPP", "EKPSPP", "warning"),
            ("SQL Query", "SQL", "success"),
        ]

        for label, folder, bootstyle in sections:
            frame = tb.LabelFrame(self, text=label)
            frame.pack(fill="x", padx=12, pady=8)
            folder_path = os.path.join(self.base_dir, folder)
            if os.path.isdir(folder_path):
                scripts = sorted([f for f in os.listdir(folder_path) if f.endswith('.py')])
                self.create_buttons(frame, folder, scripts, bootstyle)

    def create_buttons(self, frame, folder, scripts, bootstyle):
        max_cols = 4
        for i, script in enumerate(scripts):
            row = i // max_cols
            col = i % max_cols
            display_name = script[:-3]  # remove .py
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
        # No centering frame needed

        # No log or footer needed

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
