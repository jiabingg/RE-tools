"""
Recent Drilled Wells Performance Viewer
=========================================
A Python GUI application for CRC reservoir engineers to review performance
of recently drilled wells from the Oracle Data Warehouse (ODW).

Tabs:
  1. Well Inventory — basic data for all wells drilled after a user-selected date
  2. Well Tests     — latest & peak oil well test for producers
  3. Prod & Inj     — monthly production/injection charts for selected wells

Requirements:
  pip install oracledb matplotlib

Run:
  python recent_wells_performance.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import csv
import os
import sys
from datetime import datetime, date

# ═══════════════════════════════════════════════════════════════════════════════
# CONNECTION CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
TNS_ALIAS = "ODW"
DB_USERNAME = "rptguser"
DB_PASSWORD = "allusers"
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import oracledb
except ImportError:
    print("ERROR: oracledb not installed.  Run:  pip install oracledb")
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("WARNING: matplotlib not installed — chart tab will be disabled.")

# ─────────────────────────────────────────────────────────────────────────────
# SQL Queries
# ─────────────────────────────────────────────────────────────────────────────

SQL_WELL_INVENTORY = """
SELECT
    cd.cmpl_nme           AS WELL_NME,
    cd.well_api_nbr       AS WELL_API_NBR,
    cd.opnl_fld           AS FLD_NME,
    cd.prim_purp_type_cde AS PRIM_PURP_TYPE_CDE,
    cd.engr_strg_nme      AS ENGR_STRG_NME,
    cd.cmpl_state_type_desc AS CMPL_STATE_TYPE_DESC,
    cd.cmpl_state_eftv_dttm AS CMPL_STATE_EFTV_DTTM,
    wd.bore_start_dttm    AS SPUD_DATE,
    cd.init_prod_dte      AS INIT_PROD_DTE,
    cd.cmpl_fac_id        AS CMPL_FAC_ID,
    cd.cmpl_dmn_key       AS CMPL_DMN_KEY
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
WHERE wd.bore_start_dttm >= :spud_date
  AND cd.actv_indc = 'Y'
ORDER BY wd.bore_start_dttm DESC, cd.cmpl_nme
"""

SQL_WELL_TESTS_LATEST = """
WITH ranked_tests AS (
    SELECT
        cd.cmpl_nme          AS WELL_NME,
        cd.well_api_nbr      AS WELL_API_NBR,
        cd.opnl_fld          AS FLD_NME,
        cd.engr_strg_nme     AS ENGR_STRG_NME,
        f.prod_msmt_strt_dttm AS TEST_DATE,
        f.bopd_qty           AS OIL_BOPD,
        f.gros_wtr_prod_vol_qty AS WTR_BWPD,
        ROUND(f.bopd_qty * NVL(f.prod_gas_oil_rat_qty, 0) / 1000, 2) AS GAS_MCFD,
        f.prod_wtr_cut_pct   AS WC_PCT,
        ROW_NUMBER() OVER (PARTITION BY cd.cmpl_fac_id
                           ORDER BY f.prod_msmt_strt_dttm DESC) AS rn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    JOIN dwrptg.cmpl_prod_tst_fact f ON cd.cmpl_fac_id = f.cmpl_fac_id
    JOIN dwrptg.cmpl_prod_tst_dmn d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y'
      AND cd.prim_purp_type_cde = 'PROD'
      AND d.use_for_aloc_indc = 'Y'
)
SELECT WELL_NME, WELL_API_NBR, FLD_NME, ENGR_STRG_NME,
       TEST_DATE, OIL_BOPD, WTR_BWPD, GAS_MCFD, WC_PCT
FROM ranked_tests
WHERE rn = 1
ORDER BY OIL_BOPD DESC NULLS LAST
"""

SQL_WELL_TESTS_PEAK = """
WITH ranked_peak AS (
    SELECT
        cd.cmpl_nme          AS WELL_NME,
        cd.well_api_nbr      AS WELL_API_NBR,
        f.prod_msmt_strt_dttm AS PEAK_TEST_DATE,
        f.bopd_qty           AS PEAK_OIL_BOPD,
        ROW_NUMBER() OVER (PARTITION BY cd.cmpl_fac_id
                           ORDER BY f.bopd_qty DESC NULLS LAST) AS rn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    JOIN dwrptg.cmpl_prod_tst_fact f ON cd.cmpl_fac_id = f.cmpl_fac_id
    JOIN dwrptg.cmpl_prod_tst_dmn d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y'
      AND cd.prim_purp_type_cde = 'PROD'
      AND d.use_for_aloc_indc = 'Y'
      AND f.bopd_qty > 0
)
SELECT WELL_NME, WELL_API_NBR, PEAK_TEST_DATE, PEAK_OIL_BOPD
FROM ranked_peak
WHERE rn = 1
ORDER BY PEAK_OIL_BOPD DESC NULLS LAST
"""

SQL_MONTHLY_PROD_INJ = """
SELECT
    cd.cmpl_nme                           AS WELL_NME,
    cd.prim_purp_type_cde                 AS PURP,
    cmf.eftv_dttm                         AS MONTH,
    ROUND(cmf.aloc_oil_prod_dly_rte_qty, 1)       AS OIL_BOPD,
    ROUND(cmf.aloc_wtr_prod_dly_rte_qty, 1)       AS WTR_BWPD,
    ROUND(cmf.aloc_gas_prod_dly_rte_qty, 1)       AS GAS_MCFD,
    ROUND(cmf.aloc_wtr_inj_dly_rte_qty, 1)        AS WTR_INJ_BWIPD,
    ROUND(cmf.aloc_stm_inj_dly_rte_qty, 1)        AS STM_INJ_BSPD
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.cmpl_nme IN ({placeholders})
  AND cd.actv_indc = 'Y'
ORDER BY cd.cmpl_nme, cmf.eftv_dttm
"""


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_connection():
    """Create and return an Oracle connection."""
    try:
        oracledb.init_oracle_client()
    except Exception:
        pass  # Already initialized or thin mode
    conn = oracledb.connect(user=DB_USERNAME, password=DB_PASSWORD, dsn=TNS_ALIAS)
    return conn


def run_query(sql, params=None):
    """Execute SQL, return (columns, rows)."""
    conn = get_connection()
    cur = conn.cursor()
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return cols, rows


def fmt(val):
    """Format a cell value for display."""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, float):
        return f"{val:,.1f}"
    return str(val)


# ─────────────────────────────────────────────────────────────────────────────
# Treeview helpers
# ─────────────────────────────────────────────────────────────────────────────
def populate_tree(tree, columns, rows, col_widths=None):
    """Clear and populate a Treeview."""
    tree.delete(*tree.get_children())
    tree["columns"] = columns
    tree["show"] = "headings"
    for col in columns:
        tree.heading(col, text=col, anchor="w",
                     command=lambda c=col: _sort_tree(tree, c, False))
        w = (col_widths or {}).get(col, max(80, len(col) * 9))
        tree.column(col, width=w, anchor="w")
    for i, row in enumerate(rows):
        tag = "even" if i % 2 == 0 else "odd"
        tree.insert("", "end", values=[fmt(v) for v in row], tags=(tag,))


def _sort_tree(tree, col, reverse):
    """Sort treeview by column when header is clicked."""
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    try:
        data.sort(key=lambda t: float(t[0].replace(",", "")), reverse=reverse)
    except ValueError:
        data.sort(key=lambda t: t[0], reverse=reverse)
    for idx, (_, k) in enumerate(data):
        tree.move(k, "", idx)
        tree.item(k, tags=("even" if idx % 2 == 0 else "odd",))
    tree.heading(col, command=lambda: _sort_tree(tree, col, not reverse))


def export_tree(tree, title="export"):
    """Export Treeview contents to CSV."""
    children = tree.get_children()
    if not children:
        messagebox.showinfo("No Data", "No data to export.")
        return
    path = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV", "*.csv")],
        initialfile=f"{title}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    if not path:
        return
    cols = tree["columns"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for ch in children:
            w.writerow(tree.item(ch, "values"))
    messagebox.showinfo("Saved", f"{len(children)} rows → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────
class RecentWellsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Recent Drilled Wells — Performance Viewer")
        self.root.geometry("1300x800")
        self.root.minsize(1000, 600)

        # Data caches
        self.inventory_rows = []
        self.inventory_cols = []
        self.well_names = []         # for chart selection
        self.spud_date_val = None

        self._style()
        self._build_topbar()
        self._build_notebook()
        self._build_tab_inventory()
        self._build_tab_welltests()
        self._build_tab_prodchart()
        self._build_statusbar()

        self._set_status("Ready — select a spud date and click Pull Data.")

    # ── Style ────────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        BG = "#f4f6f8"
        ACCENT = "#1a5276"
        self.root.configure(bg=BG)

        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, font=("Segoe UI", 10))
        s.configure("TButton", font=("Segoe UI", 10))
        s.configure("TNotebook", background=BG)
        s.configure("TNotebook.Tab", padding=[14, 6], font=("Segoe UI", 10))
        s.configure("Header.TLabel", font=("Segoe UI", 13, "bold"),
                     foreground=ACCENT, background=BG)
        s.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#666",
                     background=BG)
        s.configure("Status.TLabel", font=("Segoe UI", 9), background="#dde4ea",
                     padding=(8, 4))
        s.configure("Accent.TButton", font=("Segoe UI", 11, "bold"),
                     padding=[18, 6])
        s.configure("Treeview", font=("Consolas", 9), rowheight=24)
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                     foreground="white", background=ACCENT)
        s.map("Treeview.Heading", background=[("active", "#1a6b9c")])
        s.map("Treeview", background=[("selected", "#d4e6f1")])

    # ── Top bar (date picker + pull button) ──────────────────────────────────
    def _build_topbar(self):
        bar = ttk.Frame(self.root, padding=(12, 10))
        bar.pack(fill="x")

        ttk.Label(bar, text="Recent Drilled Wells Performance",
                  style="Header.TLabel").pack(side="left")

        # Right side controls
        right = ttk.Frame(bar)
        right.pack(side="right")

        ttk.Label(right, text="Spud Date ≥ ").pack(side="left")

        # Year
        self.year_var = tk.StringVar(value="2024")
        year_cb = ttk.Combobox(right, textvariable=self.year_var, width=6,
                               values=[str(y) for y in range(2015, 2027)],
                               state="readonly")
        year_cb.pack(side="left", padx=2)

        ttk.Label(right, text="-").pack(side="left")

        # Month
        self.month_var = tk.StringVar(value="01")
        month_cb = ttk.Combobox(right, textvariable=self.month_var, width=4,
                                values=[f"{m:02d}" for m in range(1, 13)],
                                state="readonly")
        month_cb.pack(side="left", padx=2)

        ttk.Label(right, text="-").pack(side="left")

        # Day
        self.day_var = tk.StringVar(value="01")
        day_cb = ttk.Combobox(right, textvariable=self.day_var, width=4,
                              values=[f"{d:02d}" for d in range(1, 32)],
                              state="readonly")
        day_cb.pack(side="left", padx=2)

        self.pull_btn = ttk.Button(right, text="  Pull Data  ",
                                   style="Accent.TButton",
                                   command=self._on_pull)
        self.pull_btn.pack(side="left", padx=(15, 0))

    # ── Notebook ─────────────────────────────────────────────────────────────
    def _build_notebook(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(4, 0))
        self.tab_inv = ttk.Frame(self.nb)
        self.tab_wt = ttk.Frame(self.nb)
        self.tab_chart = ttk.Frame(self.nb)
        self.nb.add(self.tab_inv, text="  Well Inventory  ")
        self.nb.add(self.tab_wt, text="  Well Tests  ")
        self.nb.add(self.tab_chart, text="  Prod & Inj  ")

    # ── Tab 1: Inventory ─────────────────────────────────────────────────────
    def _build_tab_inventory(self):
        top = ttk.Frame(self.tab_inv, padding=(8, 6))
        top.pack(fill="x")
        self.inv_label = ttk.Label(top, text="No data loaded.", style="Sub.TLabel")
        self.inv_label.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.inv_tree, "well_inventory")
                   ).pack(side="right")

        frm = ttk.Frame(self.tab_inv)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.inv_tree = ttk.Treeview(frm, selectmode="extended")
        vsb = ttk.Scrollbar(frm, orient="vertical", command=self.inv_tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=self.inv_tree.xview)
        self.inv_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.inv_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)
        self.inv_tree.tag_configure("even", background="#f0f4f8")
        self.inv_tree.tag_configure("odd", background="white")

    # ── Tab 2: Well Tests ────────────────────────────────────────────────────
    def _build_tab_welltests(self):
        top = ttk.Frame(self.tab_wt, padding=(8, 6))
        top.pack(fill="x")
        self.wt_label = ttk.Label(top, text="No data loaded.", style="Sub.TLabel")
        self.wt_label.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.wt_tree, "well_tests")
                   ).pack(side="right")

        frm = ttk.Frame(self.tab_wt)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.wt_tree = ttk.Treeview(frm, selectmode="extended")
        vsb = ttk.Scrollbar(frm, orient="vertical", command=self.wt_tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=self.wt_tree.xview)
        self.wt_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.wt_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)
        self.wt_tree.tag_configure("even", background="#f0f4f8")
        self.wt_tree.tag_configure("odd", background="white")

    # ── Tab 3: Prod & Inj Chart ─────────────────────────────────────────────
    def _build_tab_prodchart(self):
        # Top control bar
        ctrl = ttk.Frame(self.tab_chart, padding=(8, 6))
        ctrl.pack(fill="x")

        ttk.Label(ctrl, text="Select well(s):").pack(side="left")

        # Listbox for well selection (multi-select)
        lb_frame = ttk.Frame(ctrl)
        lb_frame.pack(side="left", padx=(6, 0))
        self.well_listbox = tk.Listbox(lb_frame, selectmode="extended",
                                       width=35, height=5, exportselection=False,
                                       font=("Consolas", 9))
        lb_sb = ttk.Scrollbar(lb_frame, orient="vertical",
                              command=self.well_listbox.yview)
        self.well_listbox.configure(yscrollcommand=lb_sb.set)
        self.well_listbox.pack(side="left", fill="y")
        lb_sb.pack(side="left", fill="y")

        btn_frame = ttk.Frame(ctrl)
        btn_frame.pack(side="left", padx=(10, 0))
        self.chart_btn = ttk.Button(btn_frame, text="Plot Chart",
                                    command=self._on_plot_chart)
        self.chart_btn.pack(anchor="nw")
        ttk.Button(btn_frame, text="Select All",
                   command=self._select_all_wells).pack(anchor="nw", pady=(4, 0))
        ttk.Button(btn_frame, text="Clear Selection",
                   command=lambda: self.well_listbox.selection_clear(0, "end")
                   ).pack(anchor="nw", pady=(4, 0))

        self.chart_status = ttk.Label(ctrl, text="", style="Sub.TLabel")
        self.chart_status.pack(side="right")

        # Chart area
        self.chart_frame = ttk.Frame(self.tab_chart)
        self.chart_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        if HAS_MPL:
            self.fig = Figure(figsize=(12, 7), dpi=100, facecolor="#f4f6f8")
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            toolbar = NavigationToolbar2Tk(self.canvas, self.chart_frame)
            toolbar.update()
        else:
            ttk.Label(self.chart_frame,
                      text="matplotlib not installed — charting disabled.",
                      font=("Segoe UI", 12)).pack(expand=True)

    # ── Status bar ───────────────────────────────────────────────────────────
    def _build_statusbar(self):
        self.statusbar = ttk.Label(self.root, text="", style="Status.TLabel",
                                   anchor="w")
        self.statusbar.pack(fill="x", side="bottom")

    def _set_status(self, msg):
        self.statusbar.config(text=msg)
        self.root.update_idletasks()

    # ── Pull Data (all tabs at once) ─────────────────────────────────────────
    def _on_pull(self):
        try:
            y = int(self.year_var.get())
            m = int(self.month_var.get())
            d = int(self.day_var.get())
            self.spud_date_val = datetime(y, m, d)
        except ValueError:
            messagebox.showerror("Invalid Date", "Please select a valid date.")
            return

        self.pull_btn.config(state="disabled")
        self._set_status(f"Querying wells drilled since {self.spud_date_val:%Y-%m-%d} ...")
        threading.Thread(target=self._pull_thread, daemon=True).start()

    def _pull_thread(self):
        try:
            # ── Query 1: Well Inventory ──────────────────────────────────────
            inv_cols, inv_rows = run_query(
                SQL_WELL_INVENTORY, {"spud_date": self.spud_date_val})
            self.inventory_cols = inv_cols
            self.inventory_rows = inv_rows

            # Build well name list (unique, producers + injectors)
            self.well_names = sorted(set(r[0] for r in inv_rows if r[0]))

            # Display columns (hide internal IDs)
            display_cols = inv_cols[:9]  # exclude CMPL_FAC_ID, CMPL_DMN_KEY
            display_rows = [r[:9] for r in inv_rows]

            self.root.after(0, self._show_inventory, display_cols, display_rows)

            # ── Query 2: Latest Well Tests ───────────────────────────────────
            self.root.after(0, self._set_status, "Querying latest well tests...")
            latest_cols, latest_rows = run_query(
                SQL_WELL_TESTS_LATEST, {"spud_date": self.spud_date_val})

            # ── Query 3: Peak Oil Tests ──────────────────────────────────────
            self.root.after(0, self._set_status, "Querying peak oil tests...")
            peak_cols, peak_rows = run_query(
                SQL_WELL_TESTS_PEAK, {"spud_date": self.spud_date_val})

            # Merge latest + peak into one table
            merged_cols, merged_rows = self._merge_tests(latest_cols, latest_rows,
                                                          peak_cols, peak_rows)
            self.root.after(0, self._show_welltests, merged_cols, merged_rows)

            # Done
            n = len(inv_rows)
            self.root.after(0, self._set_status,
                            f"Loaded {n} completion(s) drilled since "
                            f"{self.spud_date_val:%Y-%m-%d}.")
            self.root.after(0, self._populate_well_listbox)

        except Exception as e:
            self.root.after(0, self._query_error, str(e))
        finally:
            self.root.after(0, lambda: self.pull_btn.config(state="normal"))

    def _merge_tests(self, lat_cols, lat_rows, peak_cols, peak_rows):
        """Merge latest-test and peak-test data by WELL_NME."""
        # Build dict of peak data keyed by WELL_NME
        peak_idx_name = peak_cols.index("WELL_NME")
        peak_idx_date = peak_cols.index("PEAK_TEST_DATE")
        peak_idx_oil = peak_cols.index("PEAK_OIL_BOPD")
        peak_map = {}
        for r in peak_rows:
            peak_map[r[peak_idx_name]] = (r[peak_idx_date], r[peak_idx_oil])

        merged_cols = list(lat_cols) + ["PEAK_TEST_DATE", "PEAK_OIL_BOPD"]
        merged_rows = []
        for r in lat_rows:
            name = r[0]
            peak = peak_map.get(name, (None, None))
            merged_rows.append(list(r) + list(peak))
        return merged_cols, merged_rows

    # ── Display callbacks (main thread) ──────────────────────────────────────
    def _show_inventory(self, cols, rows):
        widths = {"WELL_NME": 170, "WELL_API_NBR": 110, "FLD_NME": 100,
                  "PRIM_PURP_TYPE_CDE": 70, "ENGR_STRG_NME": 170,
                  "CMPL_STATE_TYPE_DESC": 110, "CMPL_STATE_EFTV_DTTM": 110,
                  "SPUD_DATE": 100, "INIT_PROD_DTE": 100}
        populate_tree(self.inv_tree, cols, rows, widths)
        self.inv_label.config(text=f"{len(rows)} completion(s) loaded")

    def _show_welltests(self, cols, rows):
        widths = {"WELL_NME": 170, "WELL_API_NBR": 110, "FLD_NME": 100,
                  "ENGR_STRG_NME": 160, "TEST_DATE": 100,
                  "OIL_BOPD": 80, "WTR_BWPD": 80, "GAS_MCFD": 80,
                  "WC_PCT": 70, "PEAK_TEST_DATE": 110, "PEAK_OIL_BOPD": 100}
        populate_tree(self.wt_tree, cols, rows, widths)
        self.wt_label.config(
            text=f"{len(rows)} producer(s) with well test data")

    def _populate_well_listbox(self):
        self.well_listbox.delete(0, "end")
        for name in self.well_names:
            self.well_listbox.insert("end", name)

    def _select_all_wells(self):
        self.well_listbox.selection_set(0, "end")

    def _query_error(self, msg):
        self._set_status("Query failed.")
        messagebox.showerror("Query Error", f"Error:\n\n{msg}")

    # ── Tab 3: Chart ─────────────────────────────────────────────────────────
    def _on_plot_chart(self):
        if not HAS_MPL:
            messagebox.showinfo("Unavailable", "matplotlib is not installed.")
            return
        sel = self.well_listbox.curselection()
        if not sel:
            messagebox.showwarning("No Selection",
                                   "Select one or more wells from the list.")
            return

        selected_names = [self.well_listbox.get(i) for i in sel]
        self.chart_btn.config(state="disabled")
        self.chart_status.config(text=f"Loading {len(selected_names)} well(s)...")
        threading.Thread(target=self._chart_thread,
                         args=(selected_names,), daemon=True).start()

    def _chart_thread(self, well_names):
        try:
            placeholders = ", ".join([f":{i+1}" for i in range(len(well_names))])
            sql = SQL_MONTHLY_PROD_INJ.format(placeholders=placeholders)
            bind = {str(i + 1): n for i, n in enumerate(well_names)}
            cols, rows = run_query(sql, bind)
            self.root.after(0, self._draw_chart, cols, rows, well_names)
        except Exception as e:
            self.root.after(0, self._query_error, str(e))
        finally:
            self.root.after(0, lambda: self.chart_btn.config(state="normal"))

    def _draw_chart(self, cols, rows, well_names):
        if not rows:
            self.chart_status.config(text="No monthly data found for selected wells.")
            return

        self.fig.clear()

        # Parse data into dict: well_name -> {month: {oil, wtr, gas, wi, si}}
        well_data = {}
        for r in rows:
            name = r[0]
            month = r[2]
            if name not in well_data:
                well_data[name] = {"months": [], "oil": [], "wtr": [],
                                   "gas": [], "wi": [], "si": []}
            d = well_data[name]
            d["months"].append(month)
            d["oil"].append(r[3] or 0)
            d["wtr"].append(r[4] or 0)
            d["gas"].append(r[5] or 0)
            d["wi"].append(r[6] or 0)
            d["si"].append(r[7] or 0)

        n_wells = len(well_data)
        single = n_wells == 1

        if single:
            # Single well — 5 subplots (one per stream)
            axes = self.fig.subplots(5, 1, sharex=True)
            name = list(well_data.keys())[0]
            d = well_data[name]
            months = d["months"]

            configs = [
                ("oil", "Oil (BOPD)", "#2ecc71"),
                ("wtr", "Water Prod (BWPD)", "#3498db"),
                ("gas", "Gas (MCFD)", "#e74c3c"),
                ("wi", "Water Inj (BWIPD)", "#9b59b6"),
                ("si", "Steam Inj (BSPD)", "#e67e22"),
            ]
            for ax, (key, label, color) in zip(axes, configs):
                vals = d[key]
                ax.fill_between(months, vals, alpha=0.3, color=color)
                ax.plot(months, vals, color=color, linewidth=1.2)
                ax.set_ylabel(label, fontsize=8)
                ax.tick_params(labelsize=7)
                ax.grid(True, alpha=0.3)
            axes[0].set_title(f"{name}  —  Monthly Allocated Rates",
                              fontsize=11, fontweight="bold")
            axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            self.fig.autofmt_xdate(rotation=45)
        else:
            # Multiple wells — 5 subplots, one line per well per stream
            axes = self.fig.subplots(5, 1, sharex=True)
            configs = [
                ("oil", "Oil (BOPD)"),
                ("wtr", "Water Prod (BWPD)"),
                ("gas", "Gas (MCFD)"),
                ("wi", "Water Inj (BWIPD)"),
                ("si", "Steam Inj (BSPD)"),
            ]
            cmap = matplotlib.colormaps.get_cmap("tab10")
            for ax, (key, label) in zip(axes, configs):
                for idx, (name, d) in enumerate(well_data.items()):
                    color = cmap(idx % 10)
                    ax.plot(d["months"], d[key], color=color,
                            linewidth=1.1, label=name if key == "oil" else None)
                ax.set_ylabel(label, fontsize=8)
                ax.tick_params(labelsize=7)
                ax.grid(True, alpha=0.3)

            # Legend only on first subplot
            axes[0].legend(fontsize=7, ncol=min(n_wells, 4),
                           loc="upper left", framealpha=0.8)
            axes[0].set_title(
                f"{n_wells} Wells  —  Monthly Allocated Rates",
                fontsize=11, fontweight="bold")
            axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            self.fig.autofmt_xdate(rotation=45)

        self.fig.tight_layout()
        self.canvas.draw()
        self.chart_status.config(
            text=f"Plotted {n_wells} well(s), {len(rows)} data points.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = RecentWellsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()