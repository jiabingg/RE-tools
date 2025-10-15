import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import scrolledtext
from tkinter import ttk
import ttkbootstrap as tb
from datetime import datetime

"""
Launcher app with three sections (on the same page) for:
1) UIC (PPR)
2) SQL Query (Cum Volume)
3) Wellbore Diagrams (Copy WBDs)

Each section has a button to run the respective script, and all output streams to a single log pane.
Modifications:
- Pane heights increased ~3x via fixed height + pack_propagate(False)
- Removed the word "Run" from the button text labels
"""

class Launcher(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Project Utilities Launcher")
        self.geometry("1000x900")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        # Target pane height (~3x bigger than before)
        pane_height = 240  # adjust as desired

        # --- UIC (PPR) Section ---
        frame_uic = tb.LabelFrame(self, text="UIC", bootstyle="primary")
        frame_uic.pack(fill="x", padx=12, pady=8)
        frame_uic.configure(height=pane_height)
        frame_uic.pack_propagate(False)  # keep the height regardless of child size
        btn_ppr = tb.Button(
            frame_uic,
            text="PPR",
            bootstyle="primary",
            command=lambda: self.run_script(os.path.join(self.base_dir, "Periodic Project Review", "ppr.py"), name="PPR"),
        )
        btn_ppr.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        # Spacer to visually fill the pane
        tk.Frame(frame_uic).grid(row=1, column=0, pady=(pane_height//2, 0))

        # --- SQL Query (Cum Volume) Section ---
        frame_sql = tb.LabelFrame(self, text="SQL Query", bootstyle="info")
        frame_sql.pack(fill="x", padx=12, pady=8)
        frame_sql.configure(height=pane_height)
        frame_sql.pack_propagate(False)

        # Existing "Cum Volume" button
        btn_cum = tb.Button(
            frame_sql,
            text="Cum Volume",
            bootstyle="success",
            command=lambda: self.run_script(os.path.join(self.base_dir, "CumVolume.py"), name="Cum Volume"),
        )
        btn_cum.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        # ✅ New "Prod & Inj" button
        btn_prod_inj = tb.Button(
            frame_sql,
            text="Prod & Inj",
            bootstyle="success",
            command=lambda: self.run_script(os.path.join(self.base_dir, "ProdInj_Cum_Init_Last.py"), name="Prod & Inj"),
        )
        btn_prod_inj.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        tk.Frame(frame_sql).grid(row=1, column=0, pady=(pane_height//2, 0))

        # --- Wellbore Diagrams (Copy WBDs) Section ---
        frame_wbd = tb.LabelFrame(self, text="Wellbore Diagrams", bootstyle="warning")
        frame_wbd.pack(fill="x", padx=12, pady=8)
        frame_wbd.configure(height=pane_height)
        frame_wbd.pack_propagate(False)
        btn_wbd = tb.Button(
            frame_wbd,
            text="Copy WBDs",
            bootstyle="warning",
            command=lambda: self.run_script(os.path.join(self.base_dir, "WBDs", "CopyFiles.py"), name="Copy WBDs"),
        )
        btn_wbd.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        tk.Frame(frame_wbd).grid(row=1, column=0, pady=(pane_height//2, 0))

        # --- Log Output Area ---
        log_frame = tb.LabelFrame(self, text="Output Log", bootstyle="secondary")
        log_frame.pack(fill="both", expand=True, padx=12, pady=6)
        self.log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

        # Footer info
        footer = tb.Frame(self)
        footer.pack(fill="x", padx=12, pady=6)
        tb.Label(footer, text="Scripts launch with the same Python interpreter as this app.").pack(side="left")

    # ----------------------
    # Logging and subprocess management
    # ----------------------
    def log_line(self, text: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log.insert(tk.END, f"[{ts}] {text}\n")
        self.log.see(tk.END)

    def run_script(self, path: str, name: str = "Script"):
        if not os.path.isfile(path):
            self.log_line(f"ERROR: {name} not found at: {path}")
            return

        self.log_line(f"Launching {name}: {path}")
        cmd = [sys.executable, path]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(path) or self.base_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.log_line(f"ERROR starting {name}: {e}")
            return

        header = f"----- {name} started (PID {proc.pid}) -----"
        self.log_line(header)

        def reader():
            try:
                for line in proc.stdout:
                    self.log_line(line.rstrip())
            finally:
                code = proc.wait()
                self.log_line(f"----- {name} exited with code {code} -----")

        t = threading.Thread(target=reader, daemon=True)
        t.start()


if __name__ == "__main__":
    app = Launcher()
    app.mainloop()