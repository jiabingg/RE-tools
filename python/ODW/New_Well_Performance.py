"""
Recent Drilled Wells Performance Viewer  (v4)
===============================================
Tabs:
  1. Well Inventory   — one row per well (largest cmpl_fac_id), P&A excluded
  2. Well Tests       — latest + peak allocated tests (after spud date only)
  3. Well Test Chart  — producer: well-test scatter; injector: daily measured inj

Requirements:   pip install oracledb matplotlib
Run:            python recent_wells_performance.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, csv, sys
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════════
TNS_ALIAS   = "ODW"
DB_USERNAME = "rptguser"
DB_PASSWORD = "allusers"
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import oracledb
except ImportError:
    sys.exit("ERROR: oracledb not installed.  Run:  pip install oracledb")

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 1: Inventory (one row per API — largest cmpl_fac_id)
# ─────────────────────────────────────────────────────────────────────────────
SQL_WELL_INVENTORY = """
WITH base AS (
    SELECT
        cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld,
        cd.prim_purp_type_cde, cd.prim_matl_desc, cd.engr_strg_nme,
        cd.cmpl_state_type_desc, cd.cmpl_state_eftv_dttm,
        wd.bore_start_dttm, cd.init_prod_dte, cd.init_inj_dte,
        cd.cmpl_fac_id, cd.cmpl_dmn_key,
        ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                           ORDER BY cd.cmpl_fac_id DESC) AS rn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
)
SELECT cmpl_nme           AS WELL_NME,
       well_api_nbr       AS WELL_API_NBR,
       opnl_fld           AS FLD_NME,
       prim_purp_type_cde AS PRIM_PURP_TYPE_CDE,
       prim_matl_desc     AS PRIM_MATL_DESC,
       engr_strg_nme      AS ENGR_STRG_NME,
       cmpl_state_type_desc AS CMPL_STATE_TYPE_DESC,
       cmpl_state_eftv_dttm AS CMPL_STATE_EFTV_DTTM,
       bore_start_dttm    AS SPUD_DATE,
       init_prod_dte      AS INIT_PROD_DTE,
       init_inj_dte       AS INIT_INJ_DTE,
       cmpl_fac_id        AS CMPL_FAC_ID,
       cmpl_dmn_key       AS CMPL_DMN_KEY
FROM base WHERE rn = 1
ORDER BY prim_purp_type_cde DESC, bore_start_dttm DESC
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 2: Well Tests (allocated only, after spud date)
# ─────────────────────────────────────────────────────────────────────────────
SQL_WELL_TESTS_LATEST = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld, cd.engr_strg_nme,
           cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y'
      AND cd.prim_purp_type_cde = 'PROD'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
),
ranked AS (
    SELECT dd.cmpl_nme AS WELL_NME, dd.well_api_nbr AS WELL_API_NBR,
           dd.opnl_fld AS FLD_NME, dd.engr_strg_nme AS ENGR_STRG_NME,
           f.prod_msmt_strt_dttm AS TEST_DATE,
           f.bopd_qty AS OIL_BOPD,
           f.gros_wtr_prod_vol_qty AS WTR_BWPD,
           ROUND(f.bopd_qty * NVL(f.prod_gas_oil_rat_qty,0)/1000, 2) AS GAS_MCFD,
           f.prod_wtr_cut_pct AS WC_PCT,
           ROW_NUMBER() OVER (PARTITION BY dd.cmpl_fac_id
                              ORDER BY f.prod_msmt_strt_dttm DESC) AS rn
    FROM dedup dd
    JOIN dwrptg.cmpl_prod_tst_fact f ON dd.cmpl_fac_id = f.cmpl_fac_id
    JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
    WHERE dd.drn = 1 AND d.use_for_aloc_indc = 'Y'
      AND f.prod_msmt_strt_dttm >= :spud_date
)
SELECT WELL_NME, WELL_API_NBR, FLD_NME, ENGR_STRG_NME,
       TEST_DATE, OIL_BOPD, WTR_BWPD, GAS_MCFD, WC_PCT
FROM ranked WHERE rn = 1
ORDER BY OIL_BOPD DESC NULLS LAST
"""

SQL_WELL_TESTS_PEAK = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'PROD'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
),
ranked AS (
    SELECT dd.cmpl_nme AS WELL_NME, dd.well_api_nbr AS WELL_API_NBR,
           f.prod_msmt_strt_dttm AS PEAK_TEST_DATE,
           f.bopd_qty AS PEAK_OIL_BOPD,
           ROW_NUMBER() OVER (PARTITION BY dd.cmpl_fac_id
                              ORDER BY f.bopd_qty DESC NULLS LAST) AS rn
    FROM dedup dd
    JOIN dwrptg.cmpl_prod_tst_fact f ON dd.cmpl_fac_id = f.cmpl_fac_id
    JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
    WHERE dd.drn = 1 AND d.use_for_aloc_indc = 'Y'
      AND f.prod_msmt_strt_dttm >= :spud_date AND f.bopd_qty > 0
)
SELECT WELL_NME, WELL_API_NBR, PEAK_TEST_DATE, PEAK_OIL_BOPD
FROM ranked WHERE rn = 1
ORDER BY PEAK_OIL_BOPD DESC NULLS LAST
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3: producers with >=1 allocated test after spud date
# ─────────────────────────────────────────────────────────────────────────────
SQL_PRODUCERS_WITH_TESTS = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld,
           cd.prim_purp_type_cde, cd.prim_matl_desc, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'PROD'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
)
SELECT DISTINCT dd.cmpl_nme, dd.opnl_fld, dd.prim_purp_type_cde,
       dd.prim_matl_desc, dd.cmpl_fac_id
FROM dedup dd
JOIN dwrptg.cmpl_prod_tst_fact f ON dd.cmpl_fac_id = f.cmpl_fac_id
JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
WHERE dd.drn = 1 AND d.use_for_aloc_indc = 'Y'
  AND f.prod_msmt_strt_dttm >= :spud_date
ORDER BY dd.opnl_fld, dd.cmpl_nme
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3: injectors with >=1 daily record after spud date
# ─────────────────────────────────────────────────────────────────────────────
SQL_INJECTORS_WITH_DATA = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld,
           cd.prim_purp_type_cde, cd.prim_matl_desc, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'INJ'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
)
SELECT DISTINCT dd.cmpl_nme, dd.opnl_fld, dd.prim_purp_type_cde,
       dd.prim_matl_desc, dd.cmpl_fac_id
FROM dedup dd
JOIN dwrptg.cmpl_dly_fact cdf ON dd.cmpl_fac_id = cdf.cmpl_fac_id
WHERE dd.drn = 1 AND cdf.eftv_dttm >= :spud_date
  AND (NVL(cdf.aloc_stm_inj_vol_qty,0) > 0 OR NVL(cdf.aloc_wtr_inj_vol_qty,0) > 0)
  AND ROWNUM <= 1000
ORDER BY dd.opnl_fld, dd.cmpl_nme
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3 chart data
# ─────────────────────────────────────────────────────────────────────────────
SQL_PROD_WELL_TESTS = """
SELECT f.prod_msmt_strt_dttm AS TEST_DATE,
       f.bopd_qty            AS OIL_BOPD,
       f.gros_wtr_prod_vol_qty AS WTR_BWPD,
       ROUND(f.bopd_qty * NVL(f.prod_gas_oil_rat_qty,0)/1000, 2) AS GAS_MCFD
FROM dwrptg.cmpl_prod_tst_fact f
JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
WHERE f.cmpl_fac_id = :cmpl_fac_id
  AND d.use_for_aloc_indc = 'Y'
  AND f.prod_msmt_strt_dttm >= :spud_date
ORDER BY f.prod_msmt_strt_dttm
"""

SQL_INJ_DAILY = """
SELECT cdf.eftv_dttm                       AS INJ_DATE,
       ROUND(cdf.aloc_stm_inj_vol_qty, 1)  AS STEAM_BBL,
       ROUND(cdf.aloc_wtr_inj_vol_qty, 1)  AS WATER_BBL
FROM dwrptg.cmpl_dly_fact cdf
WHERE cdf.cmpl_fac_id = :cmpl_fac_id
  AND cdf.eftv_dttm >= :start_date
ORDER BY cdf.eftv_dttm
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_connection():
    try:
        oracledb.init_oracle_client()
    except Exception:
        pass
    return oracledb.connect(user=DB_USERNAME, password=DB_PASSWORD, dsn=TNS_ALIAS)

def run_query(sql, params=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params or {})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close(); conn.close()
    return cols, rows

def fmt(val):
    if val is None: return ""
    if isinstance(val, datetime): return val.strftime("%Y-%m-%d")
    if isinstance(val, float):
        return f"{int(val):,}" if val == int(val) else f"{val:,.1f}"
    return str(val)


# ─────────────────────────────────────────────────────────────────────────────
# Treeview helpers
# ─────────────────────────────────────────────────────────────────────────────
def populate_tree(tree, columns, rows, col_widths=None):
    tree.delete(*tree.get_children())
    dcols = ["#"] + list(columns)
    tree["columns"] = dcols; tree["show"] = "headings"
    tree.heading("#", text="#", anchor="center")
    tree.column("#", width=45, anchor="center", stretch=False)
    for c in columns:
        tree.heading(c, text=c, anchor="w",
                     command=lambda col=c: _sort_tree(tree, col, False))
        tree.column(c, width=(col_widths or {}).get(c, max(80, len(c)*9)), anchor="w")
    for i, row in enumerate(rows):
        tree.insert("", "end", values=[i+1]+[fmt(v) for v in row],
                    tags=("even" if i%2==0 else "odd",))

def _sort_key(val, reverse):
    """Return a sort key that sorts numbers numerically and non-numbers after."""
    if val == "":
        # Blanks always sort last regardless of direction
        return (1, 0, "")
    # Strip commas for numeric parsing
    stripped = val.replace(",", "")
    try:
        return (0, float(stripped), "")
    except ValueError:
        pass
    # Try date parsing (YYYY-MM-DD)
    if len(val) == 10 and val[4:5] == "-" and val[7:8] == "-":
        return (0, 0, val)  # dates sort lexically fine in YYYY-MM-DD format
    return (0, 0, val)

def _sort_tree(tree, col, rev):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    data.sort(key=lambda t: _sort_key(t[0], rev), reverse=rev)
    for i, (_, k) in enumerate(data):
        tree.move(k, "", i)
        tree.item(k, tags=("even" if i % 2 == 0 else "odd",))
        tree.set(k, "#", i + 1)
    tree.heading(col, command=lambda: _sort_tree(tree, col, not rev))

def export_tree(tree, title="export"):
    ch = tree.get_children()
    if not ch: messagebox.showinfo("No Data","Nothing to export."); return
    path = filedialog.asksaveasfilename(
        defaultextension=".csv", filetypes=[("CSV","*.csv")],
        initialfile=f"{title}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    if not path: return
    cols = [c for c in tree["columns"] if c!="#"]
    with open(path,"w",newline="",encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols)
        for c in ch: w.writerow(tree.item(c,"values")[1:])
    messagebox.showinfo("Saved",f"{len(ch)} rows -> {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────────────
class App:
    BG      = "#f4f6f8"
    ACCENT  = "#1a5276"
    PANEL   = "#ffffff"
    BORDER  = "#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("Recent Drilled Wells — Performance Viewer")
        self.root.geometry("1400x850")
        self.root.minsize(1100, 650)

        self.inv_data = []
        self.chart_wells = []
        self.spud_date_val = None

        self._style()
        self._topbar()
        self._notebook()
        self._tab1_inventory()
        self._tab2_welltests()
        self._tab3_chart()
        self._statusbar()
        self._set_status("Ready — select a spud date and click Pull Data.")

    # ── Style ────────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(); s.theme_use("clam")
        self.root.configure(bg=self.BG)
        cfg = {
            "TFrame":        dict(background=self.BG),
            "TLabel":        dict(background=self.BG, font=("Segoe UI",10)),
            "TButton":       dict(font=("Segoe UI",10)),
            "TNotebook":     dict(background=self.BG),
            "TNotebook.Tab": dict(padding=[14,6], font=("Segoe UI",10)),
            "Header.TLabel": dict(font=("Segoe UI",13,"bold"), foreground=self.ACCENT, background=self.BG),
            "Sub.TLabel":    dict(font=("Segoe UI",9), foreground="#666", background=self.BG),
            "Status.TLabel": dict(font=("Segoe UI",9), background="#dde4ea", padding=(8,4)),
            "Accent.TButton":dict(font=("Segoe UI",11,"bold"), padding=[18,6]),
            "Treeview":      dict(font=("Consolas",9), rowheight=24),
            "Treeview.Heading": dict(font=("Segoe UI",9,"bold"), foreground="white", background=self.ACCENT),
        }
        for name, kw in cfg.items(): s.configure(name, **kw)
        s.map("Treeview.Heading", background=[("active","#1a6b9c")])
        s.map("Treeview", background=[("selected","#d4e6f1")])

    # ── Top bar ──────────────────────────────────────────────────────────────
    def _topbar(self):
        bar = ttk.Frame(self.root, padding=(12,10)); bar.pack(fill="x")
        ttk.Label(bar, text="Recent Drilled Wells Performance", style="Header.TLabel").pack(side="left")
        r = ttk.Frame(bar); r.pack(side="right")
        ttk.Label(r, text="Spud Date >= ").pack(side="left")
        self.yr = tk.StringVar(value="2024")
        ttk.Combobox(r, textvariable=self.yr, width=6,
                     values=[str(y) for y in range(2015,2028)], state="readonly").pack(side="left",padx=2)
        ttk.Label(r,text="-").pack(side="left")
        self.mo = tk.StringVar(value="01")
        ttk.Combobox(r, textvariable=self.mo, width=4,
                     values=[f"{m:02d}" for m in range(1,13)], state="readonly").pack(side="left",padx=2)
        ttk.Label(r,text="-").pack(side="left")
        self.dy = tk.StringVar(value="01")
        ttk.Combobox(r, textvariable=self.dy, width=4,
                     values=[f"{d:02d}" for d in range(1,32)], state="readonly").pack(side="left",padx=2)
        self.pull_btn = ttk.Button(r, text="  Pull Data  ", style="Accent.TButton", command=self._on_pull)
        self.pull_btn.pack(side="left", padx=(15,0))

    def _notebook(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(4,0))
        self.f1 = ttk.Frame(self.nb)
        self.f2 = ttk.Frame(self.nb)
        self.f3 = ttk.Frame(self.nb)
        self.nb.add(self.f1, text="  Well Inventory  ")
        self.nb.add(self.f2, text="  Well Tests  ")
        self.nb.add(self.f3, text="  Well Test Chart  ")

    # ── Tab 1 ────────────────────────────────────────────────────────────────
    def _tab1_inventory(self):
        top = ttk.Frame(self.f1, padding=(8,6)); top.pack(fill="x")
        self.inv_lbl = ttk.Label(top, text="No data.", style="Sub.TLabel")
        self.inv_lbl.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.t1,"inventory")).pack(side="right")
        frm = ttk.Frame(self.f1)
        frm.pack(fill="both", expand=True, padx=8, pady=(0,8))
        self.t1 = self._make_tree(frm)

    # ── Tab 2 ────────────────────────────────────────────────────────────────
    def _tab2_welltests(self):
        top = ttk.Frame(self.f2, padding=(8,6)); top.pack(fill="x")
        self.wt_lbl = ttk.Label(top, text="No data.", style="Sub.TLabel")
        self.wt_lbl.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.t2,"well_tests")).pack(side="right")
        frm = ttk.Frame(self.f2)
        frm.pack(fill="both", expand=True, padx=8, pady=(0,8))
        self.t2 = self._make_tree(frm)

    # ── Tab 3 — Well Test Chart ──────────────────────────────────────────────
    def _tab3_chart(self):
        outer = ttk.Frame(self.f3)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # ── LEFT PANEL ───────────────────────────────────────────────────────
        left = tk.Frame(outer, bg=self.PANEL, bd=0,
                        highlightbackground=self.BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="ns", padx=(0,8))

        # -- Field filter section --
        fld_section = tk.Frame(left, bg=self.PANEL)
        fld_section.pack(fill="x", padx=12, pady=(10,0))
        tk.Label(fld_section, text="FIELD", font=("Segoe UI",9,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(anchor="w")
        self.fld_var = tk.StringVar(value="All")
        self.fld_cb = ttk.Combobox(fld_section, textvariable=self.fld_var,
                                    width=28, state="readonly")
        self.fld_cb.pack(fill="x", pady=(4,0))
        self.fld_cb.bind("<<ComboboxSelected>>", self._on_field_filter)

        # -- Separator --
        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=12, pady=8)

        # -- Well list header --
        well_hdr = tk.Frame(left, bg=self.PANEL)
        well_hdr.pack(fill="x", padx=12)
        tk.Label(well_hdr, text="SELECT WELL", font=("Segoe UI",9,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(side="left")
        self.well_count_lbl = tk.Label(well_hdr, text="", font=("Segoe UI",8),
                                       fg="#888", bg=self.PANEL)
        self.well_count_lbl.pack(side="right")

        # -- Well listbox (fills remaining height) --
        lb_frame = tk.Frame(left, bg=self.PANEL)
        lb_frame.pack(fill="both", expand=True, padx=12, pady=(4,10))

        self.well_lb = tk.Listbox(
            lb_frame, selectmode="browse", width=30,
            exportselection=False, font=("Consolas",9),
            bg="white", fg="#333", selectbackground="#d4e6f1",
            selectforeground="#1a5276", bd=1, relief="solid",
            highlightthickness=0, activestyle="none")
        lb_sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.well_lb.yview)
        self.well_lb.configure(yscrollcommand=lb_sb.set)
        self.well_lb.pack(side="left", fill="both", expand=True)
        lb_sb.pack(side="right", fill="y")
        self.well_lb.bind("<<ListboxSelect>>", self._on_well_select)

        # ── RIGHT PANEL ──────────────────────────────────────────────────────
        right = tk.Frame(outer, bg=self.PANEL, bd=0,
                         highlightbackground=self.BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")

        # Chart status
        chart_top = tk.Frame(right, bg=self.PANEL)
        chart_top.pack(fill="x", padx=10, pady=(8,0))
        self.chart_lbl = tk.Label(chart_top, text="Select a well to view chart",
                                  font=("Segoe UI",9), fg="#888", bg=self.PANEL)
        self.chart_lbl.pack(side="left")

        # Chart canvas
        chart_area = tk.Frame(right, bg=self.PANEL)
        chart_area.pack(fill="both", expand=True, padx=6, pady=(4,6))

        if HAS_MPL:
            self.fig = Figure(figsize=(10,5), dpi=100, facecolor=self.PANEL)
            self.canvas = FigureCanvasTkAgg(self.fig, master=chart_area)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            tb_frame = tk.Frame(chart_area, bg=self.PANEL)
            tb_frame.pack(fill="x")
            NavigationToolbar2Tk(self.canvas, tb_frame).update()

    # ── shared helpers ───────────────────────────────────────────────────────
    def _make_tree(self, parent):
        tree = ttk.Treeview(parent, selectmode="extended")
        vsb = ttk.Scrollbar(parent, orient="vertical",  command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0,column=0,sticky="nsew")
        vsb.grid(row=0,column=1,sticky="ns")
        hsb.grid(row=1,column=0,sticky="ew")
        parent.columnconfigure(0,weight=1); parent.rowconfigure(0,weight=1)
        tree.tag_configure("even", background="#f0f4f8")
        tree.tag_configure("odd",  background="white")
        return tree

    def _statusbar(self):
        self.sb = ttk.Label(self.root, text="", style="Status.TLabel", anchor="w")
        self.sb.pack(fill="x", side="bottom")

    def _set_status(self, msg):
        self.sb.config(text=msg); self.root.update_idletasks()

    def _err(self, msg):
        self._set_status("Query failed.")
        messagebox.showerror("Error", msg)

    # ═════════════════════════════════════════════════════════════════════════
    # Pull Data
    # ═════════════════════════════════════════════════════════════════════════
    def _on_pull(self):
        try:
            self.spud_date_val = datetime(
                int(self.yr.get()), int(self.mo.get()), int(self.dy.get()))
        except ValueError:
            messagebox.showerror("Bad Date","Invalid date."); return
        self.pull_btn.config(state="disabled")
        self._set_status(f"Querying wells spudded since {self.spud_date_val:%Y-%m-%d} ...")
        threading.Thread(target=self._pull_bg, daemon=True).start()

    def _pull_bg(self):
        try:
            p = {"spud_date": self.spud_date_val}

            # Inventory
            ic, ir = run_query(SQL_WELL_INVENTORY, p)
            self.inv_data = ir
            disp_cols = ic[:11]; disp_rows = [r[:11] for r in ir]
            self.root.after(0, self._show_inv, disp_cols, disp_rows)

            # Well Tests
            self.root.after(0, self._set_status, "Querying latest well tests ...")
            lc, lr = run_query(SQL_WELL_TESTS_LATEST, p)
            self.root.after(0, self._set_status, "Querying peak oil tests ...")
            pc, pr = run_query(SQL_WELL_TESTS_PEAK, p)
            mc, mr = self._merge_tests(lc, lr, pc, pr)
            self.root.after(0, self._show_wt, mc, mr)

            # Chart wells — only those WITH data
            self.root.after(0, self._set_status,
                            "Finding producers with allocated tests ...")
            _, prod_rows = run_query(SQL_PRODUCERS_WITH_TESTS, p)
            self.root.after(0, self._set_status,
                            "Finding injectors with injection data ...")
            _, inj_rows = run_query(SQL_INJECTORS_WITH_DATA, p)

            cw = []
            for r in prod_rows:
                cw.append((r[0], r[1] or "", r[2] or "", r[3] or "", r[4]))
            for r in inj_rows:
                cw.append((r[0], r[1] or "", r[2] or "", r[3] or "", r[4]))
            cw.sort(key=lambda t: (t[1], t[0]))
            self.chart_wells = cw

            n = len(ir)
            self.root.after(0, self._set_status,
                            f"Loaded {n} well(s).  Chart: "
                            f"{len(prod_rows)} producers + {len(inj_rows)} injectors with data.")
            self.root.after(0, self._populate_chart_controls)

        except Exception as e:
            self.root.after(0, self._err, str(e))
        finally:
            self.root.after(0, lambda: self.pull_btn.config(state="normal"))

    def _merge_tests(self, lc, lr, pc, pr):
        pm = {}
        ni = pc.index("WELL_NME"); ndi = pc.index("PEAK_TEST_DATE")
        noi = pc.index("PEAK_OIL_BOPD")
        for r in pr: pm[r[ni]] = (r[ndi], r[noi])
        mc = list(lc) + ["PEAK_TEST_DATE","PEAK_OIL_BOPD"]
        mr = [list(r) + list(pm.get(r[0],(None,None))) for r in lr]
        mr.sort(key=lambda x: x[-1] if x[-1] is not None else -1, reverse=True)
        return mc, mr

    # ── display ──────────────────────────────────────────────────────────────
    def _show_inv(self, cols, rows):
        w = {"WELL_NME":170,"WELL_API_NBR":110,"FLD_NME":100,
             "PRIM_PURP_TYPE_CDE":70,"PRIM_MATL_DESC":80,"ENGR_STRG_NME":170,
             "CMPL_STATE_TYPE_DESC":120,"CMPL_STATE_EFTV_DTTM":110,
             "SPUD_DATE":100,"INIT_PROD_DTE":100,"INIT_INJ_DTE":100}
        populate_tree(self.t1, cols, rows, w)
        self.inv_lbl.config(text=f"{len(rows)} well(s)  (P&A excluded, one per API)")

    def _show_wt(self, cols, rows):
        w = {"WELL_NME":170,"WELL_API_NBR":110,"FLD_NME":100,
             "ENGR_STRG_NME":160,"TEST_DATE":100,"OIL_BOPD":80,"WTR_BWPD":80,
             "GAS_MCFD":80,"WC_PCT":70,"PEAK_TEST_DATE":110,"PEAK_OIL_BOPD":100}
        populate_tree(self.t2, cols, rows, w)
        self.wt_lbl.config(text=f"{len(rows)} producer(s)  "
                                f"(sorted by Peak Oil, tests after spud date only)")

    # ── chart controls ───────────────────────────────────────────────────────
    def _populate_chart_controls(self):
        fields = sorted(set(f for _,f,_,_,_ in self.chart_wells if f))
        self.fld_cb["values"] = ["All"] + fields
        self.fld_var.set("All")
        self._refresh_well_list()

    def _on_field_filter(self, _=None):
        self._refresh_well_list()

    def _refresh_well_list(self):
        fld = self.fld_var.get()
        self.well_lb.delete(0, "end")
        count = 0
        for name, f, purp, matl, fid in self.chart_wells:
            if fld != "All" and f != fld:
                continue
            tag = "PROD" if purp == "PROD" else f"INJ-{matl}" if purp == "INJ" else purp
            self.well_lb.insert("end", f"{name}  [{tag}]")
            count += 1
        self.well_count_lbl.config(text=f"{count} well(s)")
        if count > 0:
            self.well_lb.selection_set(0)
            self._on_well_select()
        else:
            if HAS_MPL:
                self.fig.clear(); self.canvas.draw()
            self.chart_lbl.config(text="No wells with data for selected field.")

    def _get_selected_info(self):
        sel = self.well_lb.curselection()
        if not sel: return None
        text = self.well_lb.get(sel[0])
        name = text.split("  [")[0]
        fld = self.fld_var.get()
        for n, f, p, m, fid in self.chart_wells:
            if n == name and (fld == "All" or f == fld):
                return (n, f, p, m, fid)
        return None

    def _on_well_select(self, _=None):
        info = self._get_selected_info()
        if not info or not HAS_MPL: return
        name, fld, purp, matl, fid = info
        self.chart_lbl.config(text=f"Loading {name} ...", fg="#888")
        threading.Thread(target=self._chart_bg,
                         args=(name,fld,purp,matl,fid), daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # Chart
    # ═════════════════════════════════════════════════════════════════════════
    def _chart_bg(self, name, fld, purp, matl, fid):
        try:
            if purp == "PROD":
                c, r = run_query(SQL_PROD_WELL_TESTS,
                                 {"cmpl_fac_id":fid, "spud_date":self.spud_date_val})
                self.root.after(0, self._draw_prod, c, r, name, fld)
            elif purp == "INJ":
                c, r = run_query(SQL_INJ_DAILY,
                                 {"cmpl_fac_id":fid, "start_date":self.spud_date_val})
                self.root.after(0, self._draw_inj, c, r, name, fld, matl)
        except Exception as e:
            self.root.after(0, self._err, str(e))

    def _draw_prod(self, cols, rows, name, fld):
        self.fig.clear()
        if not rows:
            self.chart_lbl.config(text=f"{name}: no data.", fg="#c0392b")
            self.canvas.draw(); return

        dates, oil, wtr, gas = [], [], [], []
        for r in rows:
            dates.append(r[0]); oil.append(r[1] or 0)
            wtr.append(r[2] or 0); gas.append(r[3] or 0)

        ax1 = self.fig.add_subplot(111)
        lines = []
        if any(v>0 for v in oil):
            l, = ax1.plot(dates, oil, "o-", color="#27ae60", ms=5, lw=1.5, label="Oil (BOPD)")
            lines.append(l)
        if any(v>0 for v in wtr):
            l, = ax1.plot(dates, wtr, "s-", color="#2980b9", ms=4, lw=1.2, label="Water (BWPD)")
            lines.append(l)
        ax1.set_ylabel("Liquid Rate (bbl/d)", fontsize=9)
        ax1.tick_params(labelsize=8); ax1.grid(True, alpha=0.25)

        if any(v>0 for v in gas):
            ax2 = ax1.twinx()
            l, = ax2.plot(dates, gas, "x--", color="#e74c3c", ms=4, lw=1.2, label="Gas (MCFD)")
            lines.append(l)
            ax2.set_ylabel("Gas (MCFD)", fontsize=9, color="#e74c3c")
            ax2.tick_params(labelsize=8, labelcolor="#e74c3c")

        ax1.legend(lines, [l.get_label() for l in lines],
                   fontsize=8, loc="upper left", framealpha=0.85)
        t = f"{name}  —  Allocated Well Tests"
        if fld: t += f"  ({fld})"
        ax1.set_title(t, fontsize=11, fontweight="bold", pad=10)
        self._fmt_x(ax1, dates)
        self.fig.tight_layout(); self.canvas.draw()
        self.chart_lbl.config(text=f"{name} — {len(rows)} well tests", fg=self.ACCENT)

    def _draw_inj(self, cols, rows, name, fld, matl):
        self.fig.clear()
        if not rows:
            self.chart_lbl.config(text=f"{name}: no injection data.", fg="#c0392b")
            self.canvas.draw(); return

        dates, stm, wtr = [], [], []
        for r in rows:
            dates.append(r[0]); stm.append(r[1] or 0); wtr.append(r[2] or 0)

        ax = self.fig.add_subplot(111)
        if matl == "Steam":
            ax.plot(dates, stm, color="#e67e22", lw=0.9, alpha=0.85, label="Steam Inj (bbl/d)")
            ax.fill_between(dates, stm, alpha=0.15, color="#e67e22")
            ax.set_ylabel("Steam Injection (bbl/d)", fontsize=9, color="#e67e22")
        elif matl == "Water":
            ax.plot(dates, wtr, color="#2980b9", lw=0.9, alpha=0.85, label="Water Inj (bbl/d)")
            ax.fill_between(dates, wtr, alpha=0.15, color="#2980b9")
            ax.set_ylabel("Water Injection (bbl/d)", fontsize=9, color="#2980b9")
        else:
            if any(v>0 for v in stm):
                ax.plot(dates, stm, color="#e67e22", lw=0.9, label="Steam (bbl/d)")
            if any(v>0 for v in wtr):
                ax.plot(dates, wtr, color="#2980b9", lw=0.9, label="Water (bbl/d)")
            ax.set_ylabel("Injection (bbl/d)", fontsize=9)

        ax.tick_params(labelsize=8); ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8, loc="upper left", framealpha=0.85)
        t = f"{name}  —  Daily Measured Injection"
        if fld: t += f"  ({fld})"
        ax.set_title(t, fontsize=11, fontweight="bold", pad=10)
        self._fmt_x(ax, dates)
        self.fig.tight_layout(); self.canvas.draw()
        self.chart_lbl.config(text=f"{name} — {len(rows):,} daily points", fg=self.ACCENT)

    def _fmt_x(self, ax, dates):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        span = (max(dates)-min(dates)).days if len(dates)>1 else 30
        if   span > 720: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        elif span > 360: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        elif span > 120: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        self.fig.autofmt_xdate(rotation=45)


# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()