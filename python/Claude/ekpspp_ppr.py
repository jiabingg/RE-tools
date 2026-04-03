#!/usr/bin/env python3
"""
Well Data Viewer — EKPSPP Oracle Database
Tab 1: Well Input (10-digit API entry, validation, deduplication)
Tab 2: Basic Well Data (well name, field, API, type, status, etc.)

Tables Used:
  - ODS.BI_WELLCOMP_V  (primary completion master, ~109K rows)
  - ODS.BI_WELL         (well surface/location info, ~32K rows)
  - DSS.COMPMASTER      (completion master with teams/sectors, ~104K rows)

Join Logic:
  - BI_WELLCOMP_V.API_NO14 contains the 14-digit API. SUBSTR(API_NO14,1,10) = user's 10-digit input.
  - BI_WELL joined on API_NO10 for section/township/range/lat/lon.
  - DSS.COMPMASTER joined on PID (=API_NO14) for TEAM and SECTOR columns.
  - One API10 can have MULTIPLE API14s (multiple completions per wellbore).

NOTE: The user enters 10-digit APIs. The query matches using SUBSTR(API_NO14,1,10).
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re
import threading

# ---------------------------------------------------------------------------
# Oracle connection helper
# ---------------------------------------------------------------------------
def get_oracle_connection():
    """Return an cx_Oracle / oracledb connection to EKPSPP.
    Adjust connect string to match your environment."""
    try:
        import oracledb as ora
    except ImportError:
        import cx_Oracle as ora

    # ── UPDATE THESE TO MATCH YOUR ENVIRONMENT ──
    DSN = ora.makedsn("your_host", 1521, service_name="EKPSPP")
    conn = ora.connect(user="your_user", password="your_password", dsn=DSN)
    return conn


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
def autofit_columns(tree):
    """Resize every column so the widest cell (or header) fits."""
    style = ttk.Style()
    font_name = style.lookup("Treeview", "font") or "TkDefaultFont"
    try:
        import tkinter.font as tkfont
        measure_font = tkfont.nametofont(font_name)
    except Exception:
        import tkinter.font as tkfont
        measure_font = tkfont.nametofont("TkDefaultFont")

    for col in tree["columns"]:
        header_w = measure_font.measure(tree.heading(col, "text")) + 24  # padding
        max_w = header_w
        for iid in tree.get_children():
            cell = str(tree.set(iid, col))
            cell_w = measure_font.measure(cell) + 16
            if cell_w > max_w:
                max_w = cell_w
        tree.column(col, width=min(max_w, 350))  # cap at 350px


def copy_tree_to_clipboard(tree, root):
    """Copy every row (with headers) as tab-delimited text → Excel paste."""
    cols = tree["columns"]
    lines = ["\t".join(tree.heading(c, "text") for c in cols)]
    for iid in tree.get_children():
        lines.append("\t".join(str(tree.set(iid, c)) for c in cols))
    text = "\n".join(lines)
    root.clipboard_clear()
    root.clipboard_append(text)
    messagebox.showinfo("Copied", f"{len(lines)-1} rows copied to clipboard.")


def format_date(val):
    """Return YYYY-MM-DD from a datetime or string, or empty string."""
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 10:
        return s[:10]
    return s


# ---------------------------------------------------------------------------
# SQL for Tab 2 — Basic Well Data
# ---------------------------------------------------------------------------
def build_basic_well_sql(api10_list):
    """
    Build SQL for basic well data filtered by 10-digit APIs.

    Tables:
      ODS.BI_WELLCOMP_V  — primary completion view (well name, API14, area,
                           field, comp type/status, reservoir, lift method,
                           spud/completion/first-prod dates)
      ODS.BI_WELL        — surface info (section, township, range, lat/lon)
      DSS.COMPMASTER     — team and sector (pre-aggregated to avoid row mult.)

    One API10 can map to MULTIPLE API14s (completions).  We show all.
    """
    # Oracle IN-list limited to 1000 items; chunk if needed.
    bind_vars = {}
    placeholders = []
    for i, api in enumerate(api10_list):
        key = f"a{i}"
        bind_vars[key] = api
        placeholders.append(f":{key}")

    # Chunk into groups of 999 for Oracle IN-clause limit
    chunks = []
    keys = list(bind_vars.keys())
    for start in range(0, len(keys), 999):
        chunk_ph = ", ".join(f":{k}" for k in keys[start:start+999])
        chunks.append(f"SUBSTR(C.API_NO14, 1, 10) IN ({chunk_ph})")
    where_clause = " OR ".join(chunks)
    if len(chunks) > 1:
        where_clause = f"({where_clause})"

    sql = f"""
SELECT
    C.WELLCOMP_NAME                          AS WELL_NAME,
    SUBSTR(C.API_NO14, 1, 10)               AS API_10,
    C.API_NO14                               AS API_14,
    C.FIELD_NAME                             AS FIELD,
    C.ORGLEV4_NAME                           AS AREA,
    C.CURR_COMP_TYPE                         AS COMP_TYPE,
    C.CURR_COMP_STATUS                       AS STATUS,
    TO_CHAR(C.STATUS_EFF_DATE, 'YYYY-MM-DD') AS STATUS_DATE,
    C.RESERVOIR_CD                           AS RESERVOIR,
    C.CURR_METHOD_PROD                       AS LIFT_METHOD,
    CM.TEAM                                  AS TEAM,
    CM.SECTOR                                AS SECTOR,
    TO_CHAR(C.WELL_SPUD_DATE, 'YYYY-MM-DD') AS SPUD_DATE,
    TO_CHAR(C.COMPLETION_DATE,'YYYY-MM-DD')  AS COMPLETION_DATE,
    TO_CHAR(C.FIRST_PROD_DATE,'YYYY-MM-DD') AS FIRST_PROD_DATE,
    W.SECTION                                AS SECTION,
    W.TOWNSHIP                               AS TOWNSHIP,
    W.RANGE_NO                               AS RANGE_NO,
    ROUND(W.SURF_LATITUDE, 5)               AS LATITUDE,
    ROUND(W.SURF_LONGITUDE, 5)              AS LONGITUDE
FROM ODS.BI_WELLCOMP_V C
LEFT JOIN ODS.BI_WELL W
    ON SUBSTR(C.API_NO14, 1, 10) = W.API_NO10
LEFT JOIN (
    SELECT PID, MAX(TEAM) AS TEAM, MAX(SECTOR) AS SECTOR
    FROM DSS.COMPMASTER
    GROUP BY PID
) CM ON C.API_NO14 = CM.PID
WHERE {where_clause}
ORDER BY C.CURR_COMP_TYPE, SUBSTR(C.API_NO14, 1, 10), C.WELLCOMP_NAME
"""
    return sql, bind_vars


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class WellDataViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Well Data Viewer — EKPSPP")
        self.geometry("1400x750")
        self.minsize(900, 500)

        # Shared state
        self.validated_apis = []   # deduplicated, validated 10-digit APIs

        # Notebook (tabs)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        self._build_tab1()
        self._build_tab2()

    # -----------------------------------------------------------------------
    # TAB 1 — Well Input
    # -----------------------------------------------------------------------
    def _build_tab1(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Well Input  ")

        # Instructions
        instr = ttk.Label(
            tab,
            text=(
                "Enter 10-digit API numbers (one per line).  "
                "Leading zeros are preserved.  Duplicates are removed automatically."
            ),
            wraplength=900, justify="left",
        )
        instr.pack(anchor="w", padx=12, pady=(10, 4))

        # Text area
        frame_text = ttk.Frame(tab)
        frame_text.pack(fill="both", expand=True, padx=12, pady=4)

        self.api_text = scrolledtext.ScrolledText(
            frame_text, width=40, height=20, font=("Consolas", 11)
        )
        self.api_text.pack(side="left", fill="both", expand=True)

        # Side panel — summary
        side = ttk.LabelFrame(frame_text, text="Validation Summary", padding=10)
        side.pack(side="right", fill="y", padx=(10, 0))

        self.lbl_total   = ttk.Label(side, text="Lines entered: 0")
        self.lbl_valid   = ttk.Label(side, text="Valid APIs: 0")
        self.lbl_dupes   = ttk.Label(side, text="Duplicates removed: 0")
        self.lbl_invalid = ttk.Label(side, text="Invalid lines: 0")
        for lbl in (self.lbl_total, self.lbl_valid, self.lbl_dupes, self.lbl_invalid):
            lbl.pack(anchor="w", pady=2)

        self.invalid_detail = scrolledtext.ScrolledText(
            side, width=30, height=8, font=("Consolas", 9), state="disabled"
        )
        self.invalid_detail.pack(fill="both", expand=True, pady=(6, 0))

        # Buttons
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill="x", padx=12, pady=(4, 10))

        ttk.Button(btn_frame, text="Validate & Load",
                   command=self._validate_apis).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Clear",
                   command=self._clear_input).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Paste from Clipboard",
                   command=self._paste_clipboard).pack(side="left", padx=4)

        self.lbl_status = ttk.Label(btn_frame, text="", foreground="green")
        self.lbl_status.pack(side="left", padx=12)

    def _paste_clipboard(self):
        try:
            text = self.clipboard_get()
            self.api_text.insert("end", text)
        except tk.TclError:
            messagebox.showwarning("Clipboard", "Nothing on clipboard to paste.")

    def _clear_input(self):
        self.api_text.delete("1.0", "end")
        self.validated_apis.clear()
        self.lbl_total.config(text="Lines entered: 0")
        self.lbl_valid.config(text="Valid APIs: 0")
        self.lbl_dupes.config(text="Duplicates removed: 0")
        self.lbl_invalid.config(text="Invalid lines: 0")
        self.lbl_status.config(text="")
        self.invalid_detail.config(state="normal")
        self.invalid_detail.delete("1.0", "end")
        self.invalid_detail.config(state="disabled")

    def _validate_apis(self):
        raw = self.api_text.get("1.0", "end").strip()
        if not raw:
            messagebox.showinfo("Input", "Please enter at least one API number.")
            return

        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        valid = []
        invalid = []
        seen = set()
        dupes = 0

        for line in lines:
            cleaned = re.sub(r"[^0-9]", "", line)
            if len(cleaned) == 10 and cleaned.isdigit():
                if cleaned in seen:
                    dupes += 1
                else:
                    seen.add(cleaned)
                    valid.append(cleaned)
            else:
                invalid.append(line)

        self.validated_apis = valid

        # Update summary
        self.lbl_total.config(text=f"Lines entered: {len(lines)}")
        self.lbl_valid.config(text=f"Valid APIs: {len(valid)}")
        self.lbl_dupes.config(text=f"Duplicates removed: {dupes}")
        self.lbl_invalid.config(text=f"Invalid lines: {len(invalid)}")

        self.invalid_detail.config(state="normal")
        self.invalid_detail.delete("1.0", "end")
        if invalid:
            self.invalid_detail.insert("end", "\n".join(invalid))
        self.invalid_detail.config(state="disabled")

        if valid:
            self.lbl_status.config(
                text=f"✓ {len(valid)} APIs ready — switch to data tabs to query.",
                foreground="green",
            )
        else:
            self.lbl_status.config(text="No valid APIs found.", foreground="red")

    # -----------------------------------------------------------------------
    # TAB 2 — Basic Well Data
    # -----------------------------------------------------------------------
    def _build_tab2(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Basic Well Data  ")

        # Toolbar
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill="x", padx=8, pady=6)

        ttk.Button(toolbar, text="Fetch Data",
                   command=self._fetch_basic_data).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree2, self)).pack(
                       side="left", padx=4)

        # Sort options
        ttk.Label(toolbar, text="  Sort by:").pack(side="left", padx=(16, 4))
        self.sort_var = tk.StringVar(value="default")
        sorts = [("Default (Type → API)", "default"),
                 ("Well Name", "WELL_NAME"),
                 ("API 10", "API_10"),
                 ("Status", "STATUS"),
                 ("Comp Type", "COMP_TYPE")]
        for text, val in sorts:
            ttk.Radiobutton(toolbar, text=text, variable=self.sort_var,
                            value=val).pack(side="left", padx=2)

        self.lbl_tab2_status = ttk.Label(toolbar, text="")
        self.lbl_tab2_status.pack(side="right", padx=8)

        # Treeview with scrollbars
        tree_frame = ttk.Frame(tab)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        cols = (
            "WELL_NAME", "API_10", "API_14", "FIELD", "AREA",
            "COMP_TYPE", "STATUS", "STATUS_DATE", "RESERVOIR",
            "LIFT_METHOD", "TEAM", "SECTOR",
            "SPUD_DATE", "COMPLETION_DATE", "FIRST_PROD_DATE",
            "SECTION", "TOWNSHIP", "RANGE_NO", "LATITUDE", "LONGITUDE",
        )
        self.tree2 = ttk.Treeview(
            tree_frame, columns=cols, show="headings", selectmode="extended"
        )

        headings = {
            "WELL_NAME": "Well Name", "API_10": "API 10", "API_14": "API 14",
            "FIELD": "Field", "AREA": "Area", "COMP_TYPE": "Comp Type",
            "STATUS": "Status", "STATUS_DATE": "Status Date",
            "RESERVOIR": "Reservoir", "LIFT_METHOD": "Lift Method",
            "TEAM": "Team", "SECTOR": "Sector",
            "SPUD_DATE": "Spud Date", "COMPLETION_DATE": "Completion Date",
            "FIRST_PROD_DATE": "First Prod Date",
            "SECTION": "Section", "TOWNSHIP": "Township",
            "RANGE_NO": "Range", "LATITUDE": "Latitude",
            "LONGITUDE": "Longitude",
        }
        for c in cols:
            self.tree2.heading(c, text=headings.get(c, c),
                               command=lambda _c=c: self._sort_tree(self.tree2, _c))
            self.tree2.column(c, width=100, minwidth=50)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self.tree2.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                             command=self.tree2.xview)
        self.tree2.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree2.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # SQL display at bottom
        sql_frame = ttk.LabelFrame(tab, text="SQL Query Used", padding=4)
        sql_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.sql_display = scrolledtext.ScrolledText(
            sql_frame, height=5, font=("Consolas", 9), state="disabled", wrap="word"
        )
        self.sql_display.pack(fill="x")

    # ── Column-click sorting ──
    def _sort_tree(self, tree, col):
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            data.sort(key=lambda t: float(t[0]) if t[0] else 0)
        except ValueError:
            data.sort(key=lambda t: t[0].lower() if t[0] else "")
        for idx, (_, k) in enumerate(data):
            tree.move(k, "", idx)

    # ── Fetch in background thread ──
    def _fetch_basic_data(self):
        if not self.validated_apis:
            messagebox.showinfo(
                "No Input",
                "Go to the Well Input tab and validate your API list first."
            )
            return

        self.lbl_tab2_status.config(text="Querying…", foreground="blue")
        self.update_idletasks()

        thread = threading.Thread(target=self._run_basic_query, daemon=True)
        thread.start()

    def _run_basic_query(self):
        try:
            sql, bind_vars = build_basic_well_sql(self.validated_apis)

            # Show SQL
            self.after(0, self._show_sql, sql)

            conn = get_oracle_connection()
            cur = conn.cursor()
            cur.execute(sql, bind_vars)
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]
            cur.close()
            conn.close()

            self.after(0, self._populate_tree2, rows, col_names)

        except Exception as e:
            self.after(0, self._show_error, str(e))

    def _show_sql(self, sql):
        self.sql_display.config(state="normal")
        self.sql_display.delete("1.0", "end")
        self.sql_display.insert("end", sql.strip())
        self.sql_display.config(state="disabled")

    def _populate_tree2(self, rows, col_names):
        # Clear existing
        for iid in self.tree2.get_children():
            self.tree2.delete(iid)

        if not rows:
            self.lbl_tab2_status.config(
                text="No results found for those APIs.", foreground="orange"
            )
            messagebox.showinfo("Empty Results",
                                "No well data found for the entered APIs.\n"
                                "Verify your 10-digit API numbers are correct.")
            return

        # Map column names to tree columns
        tree_cols = list(self.tree2["columns"])
        col_map = {}
        for i, name in enumerate(col_names):
            if name in tree_cols:
                col_map[name] = i

        for row in rows:
            values = []
            for c in tree_cols:
                idx = col_map.get(c)
                if idx is not None:
                    val = row[idx]
                    val = "" if val is None else str(val)
                else:
                    val = ""
                values.append(val)
            self.tree2.insert("", "end", values=values)

        autofit_columns(self.tree2)

        self.lbl_tab2_status.config(
            text=f"{len(rows)} completions returned.", foreground="green"
        )

    def _show_error(self, msg):
        self.lbl_tab2_status.config(text="Error", foreground="red")
        messagebox.showerror(
            "Database Error",
            f"Failed to fetch data:\n\n{msg}\n\n"
            "Check your connection settings in get_oracle_connection()."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = WellDataViewer()
    app.mainloop()