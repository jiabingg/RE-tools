import os
import sys
import threading
import subprocess
import webbrowser
import tkinter as tk
from tkinter import scrolledtext
from tkinter import ttk
import ttkbootstrap as tb
from datetime import datetime

"""
Launcher app with three sections (on the same page) for:
1) UIC (PPR, WBD Search, CRC File Search)
2) SQL Query (Prod Inj Status, Cum Volume, Well Status, Wellbores, Alloc Prod&Inj)
3) Wellbore Diagrams (Copy WBDs & Abandonment Check)

Each section has buttons to run scripts or open internal tools, and all script output streams to a shared log pane.
"""

class Launcher(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Project Utilities Launcher")
        self.geometry("1100x950")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        pane_height = 260  # consistent height for all panes

        # --- UIC (PPR + Web Tools) Section ---
        frame_uic = tb.LabelFrame(self, text="UIC", bootstyle="primary")
        frame_uic.pack(fill="x", padx=12, pady=8)
        frame_uic.configure(height=pane_height)
        frame_uic.pack_propagate(False)

        # PPR script
        btn_ppr = tb.Button(
            frame_uic,
            text="PPR",
            bootstyle="primary",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "Periodic Project Review", "ppr.py"),
                name="PPR"
            ),
        )
        btn_ppr.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        # ✅ New "WBD Search" button (opens internal site)
        btn_wbd_search = tb.Button(
            frame_uic,
            text="WBD Search",
            bootstyle="primary",
            command=lambda: self.open_website("http://aeraweb02/UICApp/WBD_Search/WBD_Bulk_Checker.html", "WBD Search"),
        )
        btn_wbd_search.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        # ✅ New "CRC File Search" button (opens internal site)
        btn_crc_search = tb.Button(
            frame_uic,
            text="CRC File Search",
            bootstyle="primary",
            command=lambda: self.open_website("http://aeraweb02/UICApp/Network_Folder_Search/CRC_File_Search.html", "CRC File Search"),
        )
        btn_crc_search.grid(row=0, column=2, padx=10, pady=10, sticky="w")

        tk.Frame(frame_uic).grid(row=1, column=0, pady=(pane_height//2, 0))

        # --- SQL Query Section ---
        frame_sql = tb.LabelFrame(self, text="SQL Query", bootstyle="info")
        frame_sql.pack(fill="x", padx=12, pady=8)
        frame_sql.configure(height=pane_height)
        frame_sql.pack_propagate(False)

        # Buttons: Prod Inj Status, Cum Volume, Well Status, Wellbores, Alloc Prod&Inj
        btn_prod_inj = tb.Button(
            frame_sql,
            text="Prod Inj Status",
            bootstyle="success",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "SQL", "ProdInj_Cum_Init_Last.py"),
                name="Prod Inj Status"
            ),
        )
        btn_prod_inj.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        btn_cum = tb.Button(
            frame_sql,
            text="Cum Volume",
            bootstyle="success",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "SQL", "CumVolume.py"),
                name="Cum Volume"
            ),
        )
        btn_cum.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        btn_well_status = tb.Button(
            frame_sql,
            text="Well Status",
            bootstyle="success",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "SQL", "WellStatus.py"),
                name="Well Status"
            ),
        )
        btn_well_status.grid(row=0, column=2, padx=10, pady=10, sticky="w")

        btn_wellbores = tb.Button(
            frame_sql,
            text="Wellbores",
            bootstyle="success",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "SQL", "Wellbores.py"),
                name="Wellbores"
            ),
        )
        btn_wellbores.grid(row=0, column=3, padx=10, pady=10, sticky="w")

        btn_alloc = tb.Button(
            frame_sql,
            text="Alloc Prod&Inj",
            bootstyle="success",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "SQL", "Prod_Inj_Alloc.py"),
                name="Alloc Prod&Inj"
            ),
        )
        btn_alloc.grid(row=0, column=4, padx=10, pady=10, sticky="w")

        tk.Frame(frame_sql).grid(row=1, column=0, pady=(pane_height//2, 0))

        # --- Wellbore Diagrams Section ---
        frame_wbd = tb.LabelFrame(self, text="Wellbore Diagrams", bootstyle="warning")
        frame_wbd.pack(fill="x", padx=12, pady=8)
        frame_wbd.configure(height=pane_height)
        frame_wbd.pack_propagate(False)

        btn_wbd = tb.Button(
            frame_wbd,
            text="Copy WBDs",
            bootstyle="warning",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "WBDs", "CopyFiles.py"),
                name="Copy WBDs"
            ),
        )
        btn_wbd.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        btn_abandon = tb.Button(
            frame_wbd,
            text="Abandonment Check",
            bootstyle="warning",
            command=lambda: self.run_script(
                os.path.join(self.base_dir, "WBDs", "WBD_Creation_Abandon_comp.py"),
                name="Abandonment Check"
            ),
        )
        btn_abandon.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        tk.Frame(frame_wbd).grid(row=1, column=0, pady=(pane_height//2, 0))

        # --- Log Output Area ---
        log_frame = tb.LabelFrame(self, text="Output Log", bootstyle="secondary")
        log_frame.pack(fill="both", expand=True, padx=12, pady=6)
        self.log = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Courier New", 10)
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

        # Footer info
        footer = tb.Frame(self)
        footer.pack(fill="x", padx=12, pady=6)
        tb.Label(
            footer, text="Scripts launch with the same Python interpreter as this app."
        ).pack(side="left")

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

    # ----------------------
    # Website launcher
    # ----------------------
    def open_website(self, url: str, name: str):
        self.log_line(f"Opening website: {name} → {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            self.log_line(f"ERROR opening {name}: {e}")


if __name__ == "__main__":
    app = Launcher()
    app.mainloop()
