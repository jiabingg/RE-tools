#!/usr/bin/env python3
"""
Well Data Viewer — EKPSPP Oracle Database
Tab 1: Well Input (10-digit API entry, validation, deduplication)
Tab 2: Basic Well Data (well name, field, API, type, status, etc.)

Performance Strategy (3-step query):
  Step 1: LIKE-based API14 lookup from BI_WELLCOMP_V  (avoids SUBSTR in WHERE)
  Step 2: Direct API14 IN-list main query on BI_WELLCOMP_V  (index-friendly)
  Step 3: Separate BI_WELL + COMPMASTER lookups, joined in Python (no SUBSTR joins)
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re
import threading
import traceback
import time

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  EKPSPP Connection                                                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
DB_TNS_ALIAS = "EKPSPP.WORLD"
DB_USER      = "oxy_read"
DB_PASSWORD  = "oxy_read"
# ═════════════════════════════════════════════════════════════════════════════

# Pre-loaded test APIs (clear for production use)
DEFAULT_TEST_APIS = """0403031235
0402918665
0402918827
0402918828
0402918832
0402918833
0402918834
0402918836
0402918837
0402918895
0402918897
0402918898
0402918899
0402918903
0402918905
0402918907
0402918907
0402918908
0402918910
0402986052
0402986052"""


def get_oracle_connection():
    """Return a cx_Oracle / oracledb connection to EKPSPP using TNS alias."""
    try:
        import cx_Oracle as ora
        return ora.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_TNS_ALIAS)
    except ImportError:
        pass
    try:
        import oracledb as ora
        try:
            ora.init_oracle_client()
        except ora.ProgrammingError:
            pass
        return ora.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_TNS_ALIAS)
    except ImportError:
        pass
    raise ImportError(
        "Neither 'cx_Oracle' nor 'oracledb' is installed.\n"
        "Install with:  pip install cx_Oracle   or   pip install oracledb"
    )


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
def autofit_columns(tree):
    """Resize every column so the widest cell (or header) fits."""
    import tkinter.font as tkfont
    try:
        style = ttk.Style()
        font_name = style.lookup("Treeview", "font") or "TkDefaultFont"
        measure_font = tkfont.nametofont(font_name)
    except Exception:
        measure_font = tkfont.nametofont("TkDefaultFont")

    for col in tree["columns"]:
        header_w = measure_font.measure(tree.heading(col, "text")) + 24
        max_w = header_w
        for iid in tree.get_children():
            cell = str(tree.set(iid, col))
            cell_w = measure_font.measure(cell) + 16
            if cell_w > max_w:
                max_w = cell_w
        tree.column(col, width=min(max_w, 350))


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


def _quoted_list(items):
    """Return 'a','b','c' string for SQL IN-clause."""
    return ", ".join(f"'{v}'" for v in items)


def _chunked_in(column, items, limit=999):
    """Build Oracle-safe IN clause, chunking at 999."""
    chunks = []
    items = list(items)
    for start in range(0, len(items), limit):
        batch = items[start:start + limit]
        chunks.append(f"{column} IN ({_quoted_list(batch)})")
    if len(chunks) == 1:
        return chunks[0]
    return "(" + " OR ".join(chunks) + ")"


# ---------------------------------------------------------------------------
# 3-Step Query Strategy
# ---------------------------------------------------------------------------
def run_three_step_query(api10_list, conn, log_fn):
    """
    Step 1: API10 → API14 lookup using LIKE (index-friendly, no SUBSTR in WHERE)
    Step 2: Main completion data using direct API14 IN-list (fast index hit)
    Step 3: BI_WELL + COMPMASTER lookups by API10/API14 IN-list, joined in Python

    Returns: (rows_list_of_dicts, sql_text_for_display)
    """
    cur = conn.cursor()
    all_sql = []

    # ── Step 1: API10 → API14 mapping via LIKE ──
    t0 = time.time()
    like_clauses = " OR ".join(f"API_NO14 LIKE '{api}%'" for api in api10_list)
    sql1 = f"SELECT API_NO14 FROM ODS.BI_WELLCOMP_V WHERE {like_clauses}"
    all_sql.append(f"-- Step 1: API14 lookup ({len(api10_list)} APIs)\n{sql1}")

    cur.execute(sql1)
    api14_rows = cur.fetchall()
    api14_list = [str(r[0]) for r in api14_rows]
    elapsed1 = time.time() - t0
    log_fn(f"Step 1: Found {len(api14_list)} API14s in {elapsed1:.1f}s")

    if not api14_list:
        return [], "\n\n".join(all_sql)

    # Build API10 set from API14s for BI_WELL lookup
    api10_from_14 = list(set(a[:10] for a in api14_list))

    # ── Step 2: Main completion data by direct API14 ──
    t1 = time.time()
    where2 = _chunked_in("C.API_NO14", api14_list)
    sql2 = f"""
SELECT
    C.WELLCOMP_NAME                          AS WELL_NAME,
    C.API_NO14                               AS API_14,
    C.FIELD_NAME                             AS FIELD,
    C.ORGLEV4_NAME                           AS AREA,
    C.CURR_COMP_TYPE                         AS COMP_TYPE,
    C.CURR_COMP_STATUS                       AS STATUS,
    TO_CHAR(C.STATUS_EFF_DATE, 'YYYY-MM-DD') AS STATUS_DATE,
    C.RESERVOIR_CD                           AS RESERVOIR,
    C.CURR_METHOD_PROD                       AS LIFT_METHOD,
    TO_CHAR(C.WELL_SPUD_DATE, 'YYYY-MM-DD') AS SPUD_DATE,
    TO_CHAR(C.COMPLETION_DATE,'YYYY-MM-DD')  AS COMPLETION_DATE,
    TO_CHAR(C.FIRST_PROD_DATE,'YYYY-MM-DD') AS FIRST_PROD_DATE
FROM ODS.BI_WELLCOMP_V C
WHERE {where2}
ORDER BY C.CURR_COMP_TYPE, C.API_NO14"""
    all_sql.append(f"-- Step 2: Main completion data\n{sql2}")

    cur.execute(sql2)
    comp_rows = cur.fetchall()
    comp_cols = [d[0] for d in cur.description]
    elapsed2 = time.time() - t1
    log_fn(f"Step 2: Fetched {len(comp_rows)} completions in {elapsed2:.1f}s")

    # ── Step 3a: BI_WELL surface data by API10 ──
    t2 = time.time()
    where3a = _chunked_in("W.API_NO10", api10_from_14)
    sql3a = f"""
SELECT W.API_NO10, W.SECTION, W.TOWNSHIP, W.RANGE_NO,
       ROUND(W.SURF_LATITUDE, 5) AS LATITUDE,
       ROUND(W.SURF_LONGITUDE, 5) AS LONGITUDE
FROM ODS.BI_WELL W
WHERE {where3a}"""
    all_sql.append(f"-- Step 3a: BI_WELL surface data\n{sql3a}")

    cur.execute(sql3a)
    well_rows = cur.fetchall()
    # Build dict keyed by API10
    well_map = {}
    for r in well_rows:
        well_map[str(r[0])] = {
            "SECTION": r[1], "TOWNSHIP": r[2], "RANGE_NO": r[3],
            "LATITUDE": r[4], "LONGITUDE": r[5]
        }
    elapsed3a = time.time() - t2
    log_fn(f"Step 3a: BI_WELL lookup {len(well_rows)} rows in {elapsed3a:.1f}s")

    # ── Step 3b: COMPMASTER team/sector by API14 ──
    t3 = time.time()
    where3b = _chunked_in("PID", api14_list)
    sql3b = f"""
SELECT PID, MAX(TEAM) AS TEAM, MAX(SECTOR) AS SECTOR
FROM DSS.COMPMASTER
WHERE {where3b}
GROUP BY PID"""
    all_sql.append(f"-- Step 3b: COMPMASTER team/sector\n{sql3b}")

    cur.execute(sql3b)
    cm_rows = cur.fetchall()
    cm_map = {}
    for r in cm_rows:
        cm_map[str(r[0])] = {"TEAM": r[1], "SECTOR": r[2]}
    elapsed3b = time.time() - t3
    log_fn(f"Step 3b: COMPMASTER lookup {len(cm_rows)} rows in {elapsed3b:.1f}s")

    cur.close()

    # ── Merge in Python ──
    results = []
    for row in comp_rows:
        d = dict(zip(comp_cols, row))
        api14 = str(d["API_14"])
        api10 = api14[:10]
        d["API_10"] = api10

        # Merge BI_WELL
        w = well_map.get(api10, {})
        d["SECTION"]   = w.get("SECTION", "")
        d["TOWNSHIP"]  = w.get("TOWNSHIP", "")
        d["RANGE_NO"]  = w.get("RANGE_NO", "")
        d["LATITUDE"]  = w.get("LATITUDE", "")
        d["LONGITUDE"] = w.get("LONGITUDE", "")

        # Merge COMPMASTER
        cm = cm_map.get(api14, {})
        d["TEAM"]   = cm.get("TEAM", "")
        d["SECTOR"] = cm.get("SECTOR", "")

        results.append(d)

    total = elapsed1 + elapsed2 + elapsed3a + elapsed3b
    log_fn(f"All steps complete: {len(results)} rows, total {total:.1f}s "
           f"(Step1: {elapsed1:.1f}s, Step2: {elapsed2:.1f}s, "
           f"Step3a: {elapsed3a:.1f}s, Step3b: {elapsed3b:.1f}s)")

    return results, "\n\n".join(all_sql)


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class WellDataViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Well Data Viewer — EKPSPP")
        self.geometry("1400x780")
        self.minsize(900, 500)

        self.validated_apis = []

        # Log bar at bottom
        log_frame = ttk.LabelFrame(self, text="Log", padding=2)
        log_frame.pack(side="bottom", fill="x", padx=6, pady=(0, 4))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=4, font=("Consolas", 9), state="disabled", wrap="word"
        )
        self.log_text.pack(fill="x")

        # Notebook
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=(6, 2))

        self._build_tab1()
        self._build_tab2()

        self.log("Application started.  Driver detection…")
        self.after(200, self._detect_driver)

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _detect_driver(self):
        driver = "none"
        try:
            import cx_Oracle
            driver = f"cx_Oracle {cx_Oracle.version}"
        except ImportError:
            try:
                import oracledb
                driver = f"oracledb {oracledb.__version__}"
            except ImportError:
                pass
        self.log(f"Oracle driver: {driver}")
        if driver == "none":
            self.log("WARNING — No Oracle driver found.")

    # -----------------------------------------------------------------------
    # TAB 1 — Well Input
    # -----------------------------------------------------------------------
    def _build_tab1(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Well Input  ")

        instr = ttk.Label(
            tab,
            text=(
                "Enter 10-digit API numbers (one per line).  "
                "Leading zeros are preserved.  Duplicates are removed automatically."
            ),
            wraplength=900, justify="left",
        )
        instr.pack(anchor="w", padx=12, pady=(10, 4))

        frame_text = ttk.Frame(tab)
        frame_text.pack(fill="both", expand=True, padx=12, pady=4)

        self.api_text = scrolledtext.ScrolledText(
            frame_text, width=40, height=20, font=("Consolas", 11)
        )
        self.api_text.pack(side="left", fill="both", expand=True)
        if DEFAULT_TEST_APIS.strip():
            self.api_text.insert("1.0", DEFAULT_TEST_APIS.strip())

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

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill="x", padx=12, pady=(4, 10))

        ttk.Button(btn_frame, text="Validate & Load",
                   command=self._validate_apis).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Clear",
                   command=self._clear_input).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Paste from Clipboard",
                   command=self._paste_clipboard).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Test Connection",
                   command=self._test_connection).pack(side="left", padx=12)

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
        valid, invalid, seen, dupes = [], [], set(), 0

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
        self.lbl_total.config(text=f"Lines entered: {len(lines)}")
        self.lbl_valid.config(text=f"Valid APIs: {len(valid)}")
        self.lbl_dupes.config(text=f"Duplicates removed: {dupes}")
        self.lbl_invalid.config(text=f"Invalid lines: {len(invalid)}")

        self.invalid_detail.config(state="normal")
        self.invalid_detail.delete("1.0", "end")
        if invalid:
            self.invalid_detail.insert("end", "\n".join(invalid))
        self.invalid_detail.config(state="disabled")

        self.log(f"Validated {len(valid)} unique APIs, {dupes} dupes, {len(invalid)} invalid.")
        if valid:
            self.lbl_status.config(
                text=f"✓ {len(valid)} APIs ready — switch to data tabs to query.",
                foreground="green")
        else:
            self.lbl_status.config(text="No valid APIs found.", foreground="red")

    def _test_connection(self):
        self.log("Testing connection to EKPSPP.WORLD …")
        self.lbl_status.config(text="Testing…", foreground="blue")
        self.update_idletasks()

        def _worker():
            t0 = time.time()
            try:
                conn = get_oracle_connection()
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM DUAL")
                cur.fetchone()
                elapsed = time.time() - t0
                cur.close()
                conn.close()
                self.after(0, self.log, f"Connection OK ({elapsed:.1f}s)")
                self.after(0, lambda: self.lbl_status.config(
                    text=f"✓ Connected ({elapsed:.1f}s)", foreground="green"))
            except Exception as e:
                elapsed = time.time() - t0
                self.after(0, self.log, f"Connection FAILED: {e}")
                self.after(0, self.log, traceback.format_exc())
                self.after(0, lambda: self.lbl_status.config(
                    text="✗ Failed — see log", foreground="red"))

        threading.Thread(target=_worker, daemon=True).start()

    # -----------------------------------------------------------------------
    # TAB 2 — Basic Well Data
    # -----------------------------------------------------------------------
    def _build_tab2(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Basic Well Data  ")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill="x", padx=8, pady=6)

        ttk.Button(toolbar, text="Fetch Data",
                   command=self._fetch_basic_data).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree2, self)).pack(
                       side="left", padx=4)

        ttk.Label(toolbar, text="  Sort by:").pack(side="left", padx=(16, 4))
        self.sort_var = tk.StringVar(value="default")
        for text, val in [("Default (Type → API)", "default"),
                          ("Well Name", "WELL_NAME"), ("API 10", "API_10"),
                          ("Status", "STATUS"), ("Comp Type", "COMP_TYPE")]:
            ttk.Radiobutton(toolbar, text=text, variable=self.sort_var,
                            value=val).pack(side="left", padx=2)

        self.lbl_tab2_status = ttk.Label(toolbar, text="")
        self.lbl_tab2_status.pack(side="right", padx=8)

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

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree2.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree2.xview)
        self.tree2.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree2.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        sql_frame = ttk.LabelFrame(tab, text="SQL Queries Used (3-step)", padding=4)
        sql_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.sql_display = scrolledtext.ScrolledText(
            sql_frame, height=6, font=("Consolas", 9), state="disabled", wrap="word"
        )
        self.sql_display.pack(fill="x")

    def _sort_tree(self, tree, col):
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            data.sort(key=lambda t: float(t[0]) if t[0] else 0)
        except ValueError:
            data.sort(key=lambda t: t[0].lower() if t[0] else "")
        for idx, (_, k) in enumerate(data):
            tree.move(k, "", idx)

    def _fetch_basic_data(self):
        if not self.validated_apis:
            messagebox.showinfo(
                "No Input",
                "Go to the Well Input tab, enter APIs, and click 'Validate & Load' first."
            )
            return

        self.lbl_tab2_status.config(text="Querying (3 steps)…", foreground="blue")
        self.log(f"Fetching basic well data for {len(self.validated_apis)} APIs (3-step)…")
        self.update_idletasks()

        threading.Thread(target=self._run_basic_query, daemon=True).start()

    def _run_basic_query(self):
        t0 = time.time()
        try:
            conn = get_oracle_connection()
            self.after(0, self.log, "Connected to EKPSPP.")

            def log_from_thread(msg):
                self.after(0, self.log, msg)

            results, sql_text = run_three_step_query(
                self.validated_apis, conn, log_from_thread
            )
            conn.close()

            self.after(0, self._show_sql, sql_text)
            self.after(0, self._populate_tree2, results)

        except Exception as e:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            self.after(0, self.log, f"ERROR after {elapsed:.1f}s: {e}")
            self.after(0, self.log, tb)
            self.after(0, self._show_error, str(e))

    def _show_sql(self, sql):
        self.sql_display.config(state="normal")
        self.sql_display.delete("1.0", "end")
        self.sql_display.insert("end", sql.strip())
        self.sql_display.config(state="disabled")

    def _populate_tree2(self, results):
        for iid in self.tree2.get_children():
            self.tree2.delete(iid)

        if not results:
            self.lbl_tab2_status.config(
                text="No results found.", foreground="orange")
            self.log("No rows returned.  Check APIs exist in ODS.BI_WELLCOMP_V.")
            messagebox.showinfo("Empty Results",
                                "No well data found for the entered APIs.\n"
                                "Verify your 10-digit API numbers are correct.")
            return

        tree_cols = list(self.tree2["columns"])

        for d in results:
            values = []
            for c in tree_cols:
                val = d.get(c, "")
                values.append("" if val is None else str(val))
            self.tree2.insert("", "end", values=values)

        autofit_columns(self.tree2)
        self.lbl_tab2_status.config(
            text=f"{len(results)} completions returned.", foreground="green")
        self.log(f"Treeview populated with {len(results)} rows.")

    def _show_error(self, msg):
        self.lbl_tab2_status.config(text="Error", foreground="red")
        messagebox.showerror(
            "Database Error",
            f"Failed to fetch data:\n\n{msg}\n\n"
            "Check the Log panel for full details.\n\n"
            "Common fixes:\n"
            "  1. Verify CRC network / VPN\n"
            "  2. Click 'Test Connection' on Well Input tab\n"
            "  3. Ensure TNS_ADMIN is set with EKPSPP.WORLD"
        )


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = WellDataViewer()
    app.mainloop()