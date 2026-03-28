"""
Well Basic Data Lookup Tool
============================
A Python GUI application that pulls well basic data from the CRC Oracle Data Warehouse (ODW).

Usage:
  1. Run the script:  python well_basic_data_app.py
  2. Paste a list of API numbers (one per line) into the input box
  3. Click "Pull Well Data" to query ODW
  4. View results in the table, export to CSV if needed

Requirements:
  pip install oracledb pandas

Connection:
  The app connects to ODW using oracledb in thin mode with your Windows credentials
  stored in the system keyring. It reads the TNS alias from your tnsnames.ora.
  
  If automatic connection fails, update the CONNECTION CONFIG section below.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import csv
import os
import sys
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════════
# CONNECTION CONFIG — Update these if automatic detection doesn't work
# ═══════════════════════════════════════════════════════════════════════════════
# Option A: TNS alias (preferred — uses your tnsnames.ora)
TNS_ALIAS = "ODW"

# Option B: Direct connection string (uncomment and fill in if TNS doesn't work)
# DSN_STRING = "your_host:1521/your_service_name"

# Credentials — will prompt if not set
DB_USERNAME = ""  # Leave blank to prompt at startup
DB_PASSWORD = ""  # Leave blank to prompt at startup
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import oracledb
except ImportError:
    print("ERROR: oracledb not installed. Run: pip install oracledb")
    sys.exit(1)

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# ─────────────────────────────────────────────────────────────────────────────
# SQL Query
# ─────────────────────────────────────────────────────────────────────────────
WELL_BASIC_SQL = """
SELECT
    wd.well_nme            AS WELL_NME,
    cd.well_api_nbr        AS WELL_API_NBR,
    cd.opnl_fld            AS FLD_NME,
    cd.prim_purp_type_cde  AS PRIM_PURP_TYPE_CDE,
    cd.engr_strg_nme       AS ENGR_STRG_NME,
    cd.cmpl_state_type_desc AS CMPL_STATE_TYPE_DESC,
    cd.cmpl_state_eftv_dttm AS CMPL_STATE_EFTV_DTTM
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.well_dmn wd ON cd.well_fac_id = wd.well_fac_id
WHERE TRIM(cd.well_api_nbr) IN ({placeholders})
  AND cd.actv_indc = 'Y'
ORDER BY cd.well_api_nbr, cd.cmpl_nme
"""

COLUMNS = [
    "WELL_NME", "WELL_API_NBR", "FLD_NME",
    "PRIM_PURP_TYPE_CDE", "ENGR_STRG_NME",
    "CMPL_STATE_TYPE_DESC", "CMPL_STATE_EFTV_DTTM"
]


# ─────────────────────────────────────────────────────────────────────────────
# Database Connection Helper
# ─────────────────────────────────────────────────────────────────────────────
class OracleConnection:
    """Manages the Oracle connection to ODW."""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.connection = None

    def connect(self):
        """Establish connection using oracledb thin mode."""
        oracledb.init_oracle_client()  # Uses thick mode if Instant Client available
        # Try TNS alias first, fall back to direct DSN
        dsn = globals().get("DSN_STRING", TNS_ALIAS)
        self.connection = oracledb.connect(
            user=self.username,
            password=self.password,
            dsn=dsn
        )
        return self.connection

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None

    def query(self, sql, params=None):
        """Execute query and return list of tuples + column names."""
        if not self.connection:
            raise RuntimeError("Not connected to database")
        cursor = self.connection.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        return columns, rows


# ─────────────────────────────────────────────────────────────────────────────
# Login Dialog
# ─────────────────────────────────────────────────────────────────────────────
class LoginDialog(tk.Toplevel):
    """Simple login dialog for ODW credentials."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("ODW Login")
        self.geometry("350x200")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.username = tk.StringVar(value=DB_USERNAME)
        self.password = tk.StringVar(value=DB_PASSWORD)
        self.result = None

        # Center on parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - 175
        y = parent.winfo_y() + (parent.winfo_height() // 2) - 100
        self.geometry(f"+{x}+{y}")

        self._build_ui()

        # If credentials already provided, auto-submit
        if self.username.get() and self.password.get():
            self.result = (self.username.get(), self.password.get())
            self.destroy()
            return

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window(self)

    def _build_ui(self):
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Oracle Data Warehouse Login",
                  font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 15))

        ttk.Label(frame, text="Username:").grid(row=1, column=0, sticky="e", padx=(0, 8))
        user_entry = ttk.Entry(frame, textvariable=self.username, width=25)
        user_entry.grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Password:").grid(row=2, column=0, sticky="e", padx=(0, 8))
        pass_entry = ttk.Entry(frame, textvariable=self.password, width=25, show="*")
        pass_entry.grid(row=2, column=1, pady=4)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(15, 0))
        ttk.Button(btn_frame, text="Connect", command=self._on_connect).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="left", padx=5)

        # Focus
        if self.username.get():
            pass_entry.focus_set()
        else:
            user_entry.focus_set()

        self.bind("<Return>", lambda e: self._on_connect())

    def _on_connect(self):
        u = self.username.get().strip()
        p = self.password.get().strip()
        if not u or not p:
            messagebox.showwarning("Missing Info", "Please enter both username and password.", parent=self)
            return
        self.result = (u, p)
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────
class WellDataApp:
    """Main application window."""

    def __init__(self, root):
        self.root = root
        self.root.title("Well Basic Data Lookup — CRC ODW")
        self.root.geometry("1100x700")
        self.root.minsize(900, 500)

        self.db = None
        self.result_data = []
        self.result_columns = COLUMNS

        self._apply_style()
        self._build_notebook()
        self._build_input_page()
        self._build_results_page()
        self._build_statusbar()

        # Show login after window renders
        self.root.after(200, self._login)

    # ── Styling ──────────────────────────────────────────────────────────────
    def _apply_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Color palette
        BG = "#f5f5f5"
        ACCENT = "#1a5276"
        BTN_BG = "#2980b9"
        BTN_FG = "white"

        self.root.configure(bg=BG)

        style.configure("TNotebook", background=BG)
        style.configure("TNotebook.Tab", padding=[12, 6], font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground=ACCENT, background=BG)
        style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#666", background=BG)
        style.configure("Status.TLabel", font=("Segoe UI", 9), background="#e0e0e0")

        # Treeview
        style.configure("Treeview", font=("Consolas", 9), rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                        foreground="white", background=ACCENT)
        style.map("Treeview.Heading", background=[("active", "#1a6b9c")])
        style.map("Treeview", background=[("selected", "#d4e6f1")])

        # Accent button
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"),
                        foreground=BTN_FG, background=BTN_BG, padding=[20, 8])
        style.map("Accent.TButton",
                  background=[("active", "#2471a3"), ("disabled", "#aab7c4")])

    # ── Notebook ─────────────────────────────────────────────────────────────
    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self.input_frame = ttk.Frame(self.notebook)
        self.results_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.input_frame, text="  1. Enter API Numbers  ")
        self.notebook.add(self.results_frame, text="  2. Results  ")

    # ── Page 1: Input ────────────────────────────────────────────────────────
    def _build_input_page(self):
        container = ttk.Frame(self.input_frame, padding=20)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Well Basic Data Lookup", style="Header.TLabel").pack(anchor="w")
        ttk.Label(container, text="Paste API numbers below (one per line), then click Pull Well Data.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 12))

        # Text input area
        text_frame = ttk.Frame(container)
        text_frame.pack(fill="both", expand=True)

        self.api_text = tk.Text(text_frame, font=("Consolas", 11), wrap="none",
                                borderwidth=1, relief="solid",
                                bg="white", fg="#333", insertbackground="#333",
                                selectbackground="#2980b9", selectforeground="white")
        scrollbar_y = ttk.Scrollbar(text_frame, orient="vertical", command=self.api_text.yview)
        self.api_text.configure(yscrollcommand=scrollbar_y.set)

        self.api_text.pack(side="left", fill="both", expand=True)
        scrollbar_y.pack(side="right", fill="y")

        # Hint
        ttk.Label(container, text="Tip: Accepts 14-digit API numbers (e.g., 04029276120000). "
                                  "Leading/trailing spaces are trimmed automatically.",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 0))

        # Button row
        btn_row = ttk.Frame(container)
        btn_row.pack(fill="x", pady=(12, 0))

        self.pull_btn = ttk.Button(btn_row, text="Pull Well Data",
                                   style="Accent.TButton", command=self._on_pull)
        self.pull_btn.pack(side="left")

        ttk.Button(btn_row, text="Clear", command=self._on_clear).pack(side="left", padx=(10, 0))

        self.api_count_label = ttk.Label(btn_row, text="0 APIs entered", style="Sub.TLabel")
        self.api_count_label.pack(side="right")

        # Update count on keypress
        self.api_text.bind("<KeyRelease>", self._update_api_count)

    # ── Page 2: Results ──────────────────────────────────────────────────────
    def _build_results_page(self):
        container = ttk.Frame(self.results_frame, padding=10)
        container.pack(fill="both", expand=True)

        # Top bar
        top_bar = ttk.Frame(container)
        top_bar.pack(fill="x", pady=(0, 8))

        self.results_label = ttk.Label(top_bar, text="No results yet.", style="Sub.TLabel")
        self.results_label.pack(side="left")

        ttk.Button(top_bar, text="Export to CSV", command=self._export_csv).pack(side="right")
        ttk.Button(top_bar, text="← Back to Input", command=lambda: self.notebook.select(0)).pack(side="right", padx=(0, 8))

        # Treeview table
        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=COLUMNS, show="headings", selectmode="extended")

        col_widths = {
            "WELL_NME": 140, "WELL_API_NBR": 130, "FLD_NME": 100,
            "PRIM_PURP_TYPE_CDE": 80, "ENGR_STRG_NME": 160,
            "CMPL_STATE_TYPE_DESC": 120, "CMPL_STATE_EFTV_DTTM": 130
        }
        for col in COLUMNS:
            self.tree.heading(col, text=col, anchor="w")
            self.tree.column(col, width=col_widths.get(col, 120), anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        # Alternating row colors
        self.tree.tag_configure("even", background="#f0f4f8")
        self.tree.tag_configure("odd", background="white")

    # ── Status Bar ───────────────────────────────────────────────────────────
    def _build_statusbar(self):
        self.statusbar = ttk.Label(self.root, text="Not connected", style="Status.TLabel",
                                   anchor="w", padding=(8, 3))
        self.statusbar.pack(fill="x", side="bottom")

    # ── Login Flow ───────────────────────────────────────────────────────────
    def _login(self):
        dlg = LoginDialog(self.root)
        if dlg.result is None:
            self.statusbar.config(text="Login cancelled — you can still enter APIs but won't be able to query.")
            return

        username, password = dlg.result
        self.statusbar.config(text="Connecting to ODW...")
        self.root.update_idletasks()

        try:
            self.db = OracleConnection(username, password)
            self.db.connect()
            self.statusbar.config(text=f"Connected to ODW as {username}")
        except Exception as e:
            self.db = None
            err_msg = str(e)
            # Common error hints
            if "ORA-12154" in err_msg:
                hint = "\n\nHint: TNS alias not found. Check your tnsnames.ora or update TNS_ALIAS in the script."
            elif "ORA-01017" in err_msg:
                hint = "\n\nHint: Invalid username/password."
            elif "DPI-1047" in err_msg:
                hint = "\n\nHint: Oracle Instant Client not found. Install it or use oracledb thin mode."
            else:
                hint = ""
            messagebox.showerror("Connection Failed", f"Could not connect to ODW:\n\n{err_msg}{hint}")
            self.statusbar.config(text="Connection failed — click Pull Well Data to retry.")

    # ── Actions ──────────────────────────────────────────────────────────────
    def _parse_apis(self):
        """Parse API numbers from the text box."""
        raw = self.api_text.get("1.0", "end").strip()
        if not raw:
            return []
        # Split by newlines, commas, or whitespace; strip each
        import re
        apis = re.split(r'[\n,;\s]+', raw)
        apis = [a.strip() for a in apis if a.strip()]
        return apis

    def _update_api_count(self, event=None):
        count = len(self._parse_apis())
        self.api_count_label.config(text=f"{count} API(s) entered")

    def _on_clear(self):
        self.api_text.delete("1.0", "end")
        self._update_api_count()

    def _on_pull(self):
        apis = self._parse_apis()
        if not apis:
            messagebox.showwarning("No Input", "Please enter at least one API number.")
            return

        if not self.db or not self.db.connection:
            # Retry login
            self._login()
            if not self.db or not self.db.connection:
                return

        # Disable button, run in thread
        self.pull_btn.config(state="disabled")
        self.statusbar.config(text=f"Querying {len(apis)} API(s)...")
        self.root.update_idletasks()

        thread = threading.Thread(target=self._run_query, args=(apis,), daemon=True)
        thread.start()

    def _run_query(self, apis):
        """Execute the query in a background thread."""
        try:
            # Build parameterized query — use bind variables for safety
            # Oracle bind: :1, :2, :3, ... for positional
            placeholders = ", ".join([f":{i+1}" for i in range(len(apis))])
            sql = WELL_BASIC_SQL.format(placeholders=placeholders)
            bind_vars = {str(i+1): api.strip() for i, api in enumerate(apis)}

            cursor = self.db.connection.cursor()
            cursor.execute(sql, bind_vars)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            cursor.close()

            # Format datetime columns
            formatted_rows = []
            for row in rows:
                formatted = []
                for val in row:
                    if isinstance(val, datetime):
                        formatted.append(val.strftime("%Y-%m-%d"))
                    elif val is None:
                        formatted.append("")
                    else:
                        formatted.append(str(val))
                formatted_rows.append(formatted)

            self.result_data = formatted_rows
            self.result_columns = columns

            # Update UI on main thread
            self.root.after(0, self._display_results, formatted_rows, columns, len(apis))

        except Exception as e:
            self.root.after(0, self._query_error, str(e))

    def _display_results(self, rows, columns, api_count):
        """Populate the treeview with results (runs on main thread)."""
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Insert rows with alternating colors
        for i, row in enumerate(rows):
            tag = "even" if i % 2 == 0 else "odd"
            self.tree.insert("", "end", values=row, tags=(tag,))

        # Update labels
        self.results_label.config(text=f"{len(rows)} completion(s) found for {api_count} API(s)")
        self.statusbar.config(text=f"Query complete — {len(rows)} rows returned")
        self.pull_btn.config(state="normal")

        # Switch to results tab
        self.notebook.select(1)

    def _query_error(self, error_msg):
        """Handle query error (runs on main thread)."""
        self.pull_btn.config(state="normal")
        self.statusbar.config(text="Query failed")
        messagebox.showerror("Query Error", f"Error executing query:\n\n{error_msg}")

    def _export_csv(self):
        """Export results to CSV."""
        if not self.result_data:
            messagebox.showinfo("No Data", "No results to export. Run a query first.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"well_basic_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not filepath:
            return

        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.result_columns)
                writer.writerows(self.result_data)
            messagebox.showinfo("Export Complete", f"Saved {len(self.result_data)} rows to:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Could not save file:\n{e}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = WellDataApp(root)
    root.mainloop()

    # Cleanup
    if app.db:
        app.db.close()


if __name__ == "__main__":
    main()