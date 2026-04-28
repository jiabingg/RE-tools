#Help: Recent Drilled Wells Performance Viewer — View inventory, tests, and charts 
"""
Recent Drilled Wells Performance Viewer  (v6)
===============================================
Tabs:
  1. Well Inventory   — filterable by Field + Engr Strategy
  2. Well Tests       — filterable by Field + Engr Strategy
  3. Well Test Chart  — filterable by Field + Engr Strategy

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
# SQL
# ─────────────────────────────────────────────────────────────────────────────
SQL_WELL_INVENTORY = """
WITH base AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld,
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
SELECT cmpl_nme, well_api_nbr, opnl_fld, prim_purp_type_cde, prim_matl_desc,
       engr_strg_nme, cmpl_state_type_desc, cmpl_state_eftv_dttm,
       bore_start_dttm, init_prod_dte, init_inj_dte, cmpl_fac_id, cmpl_dmn_key
FROM base WHERE rn = 1
ORDER BY prim_purp_type_cde DESC, bore_start_dttm DESC
"""
# Inventory column indices: 0=NME 1=API 2=FLD 3=PURP 4=MATL 5=ENGR 6=STATE
#   7=STATE_DT 8=SPUD 9=INIT_PROD 10=INIT_INJ 11=FAC_ID 12=DMN_KEY
INV_FLD_IDX  = 2
INV_ENGR_IDX = 5

SQL_WELL_TESTS_LATEST = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld, cd.engr_strg_nme,
           cd.cmpl_fac_id,
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
# Well tests column indices: 0=NME 1=API 2=FLD 3=ENGR 4=DATE 5=OIL 6=WTR 7=GAS 8=WC
WT_FLD_IDX  = 2
WT_ENGR_IDX = 3

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
"""

SQL_PRODUCERS_WITH_TESTS = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld, cd.engr_strg_nme,
           cd.prim_purp_type_cde, cd.prim_matl_desc, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'PROD'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
),
peak AS (
    SELECT dd.cmpl_fac_id, MAX(f.bopd_qty) AS peak_oil
    FROM dedup dd
    JOIN dwrptg.cmpl_prod_tst_fact f ON dd.cmpl_fac_id = f.cmpl_fac_id
    JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
    WHERE dd.drn = 1 AND d.use_for_aloc_indc = 'Y'
      AND f.prod_msmt_strt_dttm >= :spud_date AND f.bopd_qty > 0
    GROUP BY dd.cmpl_fac_id
)
SELECT DISTINCT dd.cmpl_nme, dd.opnl_fld, dd.engr_strg_nme,
       dd.prim_purp_type_cde, dd.prim_matl_desc, dd.cmpl_fac_id,
       NVL(pk.peak_oil, 0) AS peak_oil
FROM dedup dd
JOIN dwrptg.cmpl_prod_tst_fact f ON dd.cmpl_fac_id = f.cmpl_fac_id
JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
LEFT JOIN peak pk ON dd.cmpl_fac_id = pk.cmpl_fac_id
WHERE dd.drn = 1 AND d.use_for_aloc_indc = 'Y'
  AND f.prod_msmt_strt_dttm >= :spud_date
ORDER BY dd.opnl_fld, dd.cmpl_nme
"""
# prod_rows indices: 0=nme 1=fld 2=engr 3=purp 4=matl 5=fac_id 6=peak_oil

SQL_INJECTORS_WITH_DATA = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld, cd.engr_strg_nme,
           cd.prim_purp_type_cde, cd.prim_matl_desc, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'INJ'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
)
SELECT DISTINCT dd.cmpl_nme, dd.opnl_fld, dd.engr_strg_nme,
       dd.prim_purp_type_cde, dd.prim_matl_desc, dd.cmpl_fac_id,
       0 AS peak_oil
FROM dedup dd
JOIN dwrptg.cmpl_dly_fact cdf ON dd.cmpl_fac_id = cdf.cmpl_fac_id
WHERE dd.drn = 1 AND cdf.eftv_dttm >= :spud_date
  AND (NVL(cdf.aloc_stm_inj_vol_qty,0) > 0 OR NVL(cdf.aloc_wtr_inj_vol_qty,0) > 0)
  AND ROWNUM <= 1000
ORDER BY dd.opnl_fld, dd.cmpl_nme
"""
# inj_rows indices: 0=nme 1=fld 2=engr 3=purp 4=matl 5=fac_id 6=peak_oil(0)

SQL_PROD_WELL_TESTS = """
SELECT f.prod_msmt_strt_dttm AS TEST_DATE,
       f.bopd_qty AS OIL_BOPD,
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
SELECT cdf.eftv_dttm AS INJ_DATE,
       ROUND(cdf.aloc_stm_inj_vol_qty, 1) AS STEAM_BBL,
       ROUND(cdf.aloc_wtr_inj_vol_qty, 1) AS WATER_BBL
FROM dwrptg.cmpl_dly_fact cdf
WHERE cdf.cmpl_fac_id = :cmpl_fac_id
  AND cdf.eftv_dttm >= :start_date
ORDER BY cdf.eftv_dttm
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_connection():
    try: oracledb.init_oracle_client()
    except: pass
    return oracledb.connect(user=DB_USERNAME, password=DB_PASSWORD, dsn=TNS_ALIAS)

def run_query(sql, params=None):
    conn = get_connection(); cur = conn.cursor()
    cur.execute(sql, params or {})
    cols = [d[0] for d in cur.description]; rows = cur.fetchall()
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
    if val == "": return (1, 0, "")
    stripped = val.replace(",", "")
    try: return (0, float(stripped), "")
    except ValueError: pass
    return (0, 0, val)

def _sort_tree(tree, col, rev):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    data.sort(key=lambda t: _sort_key(t[0], rev), reverse=rev)
    for i, (_, k) in enumerate(data):
        tree.move(k, "", i)
        tree.item(k, tags=("even" if i%2==0 else "odd",))
        tree.set(k, "#", i+1)
    tree.heading(col, command=lambda: _sort_tree(tree, col, not rev))

def _sort_well_tree(tree, col, rev):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    data.sort(key=lambda t: _sort_key(t[0], rev), reverse=rev)
    for i, (_, k) in enumerate(data):
        tree.move(k, "", i)
        tree.item(k, tags=("even" if i%2==0 else "odd",))
    tree.heading(col, command=lambda: _sort_well_tree(tree, col, not rev))

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
# Filter bar builder (reused on all 3 tabs)
# ─────────────────────────────────────────────────────────────────────────────
def make_filter_bar(parent, fld_var, engr_var, on_change):
    """Create a Field + Engr Strategy filter row. Returns the frame."""
    bar = ttk.Frame(parent, padding=(8, 4))
    bar.pack(fill="x")

    ttk.Label(bar, text="Field:").pack(side="left")
    fld_cb = ttk.Combobox(bar, textvariable=fld_var, width=14, state="readonly")
    fld_cb.pack(side="left", padx=(4, 12))
    fld_cb.bind("<<ComboboxSelected>>", on_change)

    ttk.Label(bar, text="Engr Strategy:").pack(side="left")
    engr_cb = ttk.Combobox(bar, textvariable=engr_var, width=22, state="readonly")
    engr_cb.pack(side="left", padx=(4, 0))
    engr_cb.bind("<<ComboboxSelected>>", on_change)

    return bar, fld_cb, engr_cb


# ─────────────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────────────
class App:
    BG = "#f4f6f8"; ACCENT = "#1a5276"; PANEL = "#ffffff"; BORDER = "#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("Recent Drilled Wells — Performance Viewer")
        self.root.geometry("1400x850")
        self.root.minsize(1100, 650)

        # Raw data caches (unfiltered)
        self.raw_inv_cols = []; self.raw_inv_rows = []
        self.raw_wt_cols  = []; self.raw_wt_rows  = []
        # chart_wells: list of (name, fld, engr, purp, matl, fac_id, peak_oil)
        self.chart_wells = []
        self.spud_date_val = None

        self._style(); self._topbar(); self._notebook()
        self._tab1_inventory(); self._tab2_welltests(); self._tab3_chart()
        self._statusbar()
        self._set_status("Ready — select a spud date and click Pull Data.")

    def _style(self):
        s = ttk.Style(); s.theme_use("clam")
        self.root.configure(bg=self.BG)
        for n, kw in [
            ("TFrame",       dict(background=self.BG)),
            ("TLabel",       dict(background=self.BG, font=("Segoe UI",10))),
            ("TButton",      dict(font=("Segoe UI",10))),
            ("TNotebook",    dict(background=self.BG)),
            ("TNotebook.Tab",dict(padding=[14,6], font=("Segoe UI",10))),
            ("Header.TLabel",dict(font=("Segoe UI",13,"bold"), foreground=self.ACCENT, background=self.BG)),
            ("Sub.TLabel",   dict(font=("Segoe UI",9), foreground="#666", background=self.BG)),
            ("Status.TLabel",dict(font=("Segoe UI",9), background="#dde4ea", padding=(8,4))),
            ("Accent.TButton",dict(font=("Segoe UI",11,"bold"), padding=[18,6])),
            ("Treeview",     dict(font=("Consolas",9), rowheight=24)),
            ("Treeview.Heading", dict(font=("Segoe UI",9,"bold"), foreground="white", background=self.ACCENT)),
        ]: s.configure(n, **kw)
        s.map("Treeview.Heading", background=[("active","#1a6b9c")])
        s.map("Treeview", background=[("selected","#d4e6f1")])

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
        self.f1 = ttk.Frame(self.nb); self.f2 = ttk.Frame(self.nb); self.f3 = ttk.Frame(self.nb)
        self.nb.add(self.f1, text="  Well Inventory  ")
        self.nb.add(self.f2, text="  Well Tests  ")
        self.nb.add(self.f3, text="  Well Test Chart  ")

    # ── Tab 1 ────────────────────────────────────────────────────────────────
    def _tab1_inventory(self):
        # Filter bar
        self.inv_fld_var  = tk.StringVar(value="All")
        self.inv_engr_var = tk.StringVar(value="All")
        _, self.inv_fld_cb, self.inv_engr_cb = make_filter_bar(
            self.f1, self.inv_fld_var, self.inv_engr_var, self._on_inv_filter)

        top = ttk.Frame(self.f1, padding=(8,2)); top.pack(fill="x")
        self.inv_lbl = ttk.Label(top, text="No data.", style="Sub.TLabel")
        self.inv_lbl.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.t1,"inventory")).pack(side="right")
        frm = ttk.Frame(self.f1)
        frm.pack(fill="both", expand=True, padx=8, pady=(0,8))
        self.t1 = self._make_tree(frm)

    # ── Tab 2 ────────────────────────────────────────────────────────────────
    def _tab2_welltests(self):
        self.wt_fld_var  = tk.StringVar(value="All")
        self.wt_engr_var = tk.StringVar(value="All")
        _, self.wt_fld_cb, self.wt_engr_cb = make_filter_bar(
            self.f2, self.wt_fld_var, self.wt_engr_var, self._on_wt_filter)

        top = ttk.Frame(self.f2, padding=(8,2)); top.pack(fill="x")
        self.wt_lbl = ttk.Label(top, text="No data.", style="Sub.TLabel")
        self.wt_lbl.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.t2,"well_tests")).pack(side="right")
        frm = ttk.Frame(self.f2)
        frm.pack(fill="both", expand=True, padx=8, pady=(0,8))
        self.t2 = self._make_tree(frm)

    # ── Tab 3 ────────────────────────────────────────────────────────────────
    def _tab3_chart(self):
        outer = ttk.Frame(self.f3)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.columnconfigure(1, weight=1); outer.rowconfigure(0, weight=1)

        # LEFT PANEL
        left = tk.Frame(outer, bg=self.PANEL, bd=0,
                        highlightbackground=self.BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="ns", padx=(0,8))

        # Field filter
        fld_sec = tk.Frame(left, bg=self.PANEL)
        fld_sec.pack(fill="x", padx=10, pady=(8,0))
        tk.Label(fld_sec, text="FIELD", font=("Segoe UI",8,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(anchor="w")
        self.ch_fld_var = tk.StringVar(value="All")
        self.ch_fld_cb = ttk.Combobox(fld_sec, textvariable=self.ch_fld_var,
                                       width=28, state="readonly")
        self.ch_fld_cb.pack(fill="x", pady=(2,0))
        self.ch_fld_cb.bind("<<ComboboxSelected>>", self._on_ch_fld_filter)

        # Engr Strategy filter
        engr_sec = tk.Frame(left, bg=self.PANEL)
        engr_sec.pack(fill="x", padx=10, pady=(4,0))
        tk.Label(engr_sec, text="ENGR STRATEGY", font=("Segoe UI",8,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(anchor="w")
        self.ch_engr_var = tk.StringVar(value="All")
        self.ch_engr_cb = ttk.Combobox(engr_sec, textvariable=self.ch_engr_var,
                                        width=28, state="readonly")
        self.ch_engr_cb.pack(fill="x", pady=(2,0))
        self.ch_engr_cb.bind("<<ComboboxSelected>>", self._on_ch_engr_filter)

        # Separator
        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=10, pady=6)

        # Well count
        whdr = tk.Frame(left, bg=self.PANEL)
        whdr.pack(fill="x", padx=10)
        tk.Label(whdr, text="SELECT WELL", font=("Segoe UI",8,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(side="left")
        self.well_count_lbl = tk.Label(whdr, text="", font=("Segoe UI",8),
                                       fg="#888", bg=self.PANEL)
        self.well_count_lbl.pack(side="right")

        # Well selector Treeview
        tf = tk.Frame(left, bg=self.PANEL)
        tf.pack(fill="both", expand=True, padx=10, pady=(4,8))
        self.well_tree = ttk.Treeview(tf, columns=("WELL","TYPE","PEAK_OIL"),
                                       show="headings", selectmode="browse")
        self.well_tree.heading("WELL", text="Well",
                               command=lambda: _sort_well_tree(self.well_tree, "WELL", False))
        self.well_tree.heading("TYPE", text="Type",
                               command=lambda: _sort_well_tree(self.well_tree, "TYPE", False))
        self.well_tree.heading("PEAK_OIL", text="Peak Oil",
                               command=lambda: _sort_well_tree(self.well_tree, "PEAK_OIL", True))
        self.well_tree.column("WELL", width=150, anchor="w")
        self.well_tree.column("TYPE", width=72, anchor="w")
        self.well_tree.column("PEAK_OIL", width=62, anchor="e")
        wt_sb = ttk.Scrollbar(tf, orient="vertical", command=self.well_tree.yview)
        self.well_tree.configure(yscrollcommand=wt_sb.set)
        self.well_tree.pack(side="left", fill="both", expand=True)
        wt_sb.pack(side="right", fill="y")
        self.well_tree.tag_configure("even", background="#f0f4f8")
        self.well_tree.tag_configure("odd",  background="white")
        self.well_tree.bind("<<TreeviewSelect>>", self._on_well_select)

        # RIGHT PANEL
        right = tk.Frame(outer, bg=self.PANEL, bd=0,
                         highlightbackground=self.BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")
        ct = tk.Frame(right, bg=self.PANEL)
        ct.pack(fill="x", padx=10, pady=(8,0))
        self.chart_lbl = tk.Label(ct, text="Select a well to view chart",
                                  font=("Segoe UI",9), fg="#888", bg=self.PANEL)
        self.chart_lbl.pack(side="left")
        ca = tk.Frame(right, bg=self.PANEL)
        ca.pack(fill="both", expand=True, padx=6, pady=(4,6))
        if HAS_MPL:
            self.fig = Figure(figsize=(10,5), dpi=100, facecolor=self.PANEL)
            self.canvas = FigureCanvasTkAgg(self.fig, master=ca)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            tb = tk.Frame(ca, bg=self.PANEL); tb.pack(fill="x")
            NavigationToolbar2Tk(self.canvas, tb).update()

    def _make_tree(self, parent):
        tree = ttk.Treeview(parent, selectmode="extended")
        vsb = ttk.Scrollbar(parent, orient="vertical",  command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0,column=0,sticky="nsew")
        vsb.grid(row=0,column=1,sticky="ns"); hsb.grid(row=1,column=0,sticky="ew")
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
        self._set_status("Query failed."); messagebox.showerror("Error", msg)

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
            self.raw_inv_cols = ic; self.raw_inv_rows = ir

            # Well Tests
            self.root.after(0, self._set_status, "Querying well tests ...")
            lc, lr = run_query(SQL_WELL_TESTS_LATEST, p)
            pc, pr = run_query(SQL_WELL_TESTS_PEAK, p)
            mc, mr = self._merge_tests(lc, lr, pc, pr)
            self.raw_wt_cols = mc; self.raw_wt_rows = mr

            # Chart wells
            self.root.after(0, self._set_status, "Finding wells with data ...")
            _, prod_rows = run_query(SQL_PRODUCERS_WITH_TESTS, p)
            _, inj_rows  = run_query(SQL_INJECTORS_WITH_DATA, p)

            # chart_wells: (name, fld, engr, purp, matl, fac_id, peak_oil)
            cw = []
            for r in prod_rows:
                cw.append((r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5], r[6] or 0))
            for r in inj_rows:
                cw.append((r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5], 0))
            cw.sort(key=lambda t: (t[1], t[0]))
            self.chart_wells = cw

            n = len(ir)
            self.root.after(0, self._set_status,
                            f"Loaded {n} well(s).  Chart: "
                            f"{len(prod_rows)} producers + {len(inj_rows)} injectors with data.")
            self.root.after(0, self._populate_all_filters)

        except Exception as e:
            self.root.after(0, self._err, str(e))
        finally:
            self.root.after(0, lambda: self.pull_btn.config(state="normal"))

    def _merge_tests(self, lc, lr, pc, pr):
        pm = {}
        ni = pc.index("WELL_NME"); ndi = pc.index("PEAK_TEST_DATE"); noi = pc.index("PEAK_OIL_BOPD")
        for r in pr: pm[r[ni]] = (r[ndi], r[noi])
        mc = list(lc) + ["PEAK_TEST_DATE","PEAK_OIL_BOPD"]
        mr = [list(r) + list(pm.get(r[0],(None,None))) for r in lr]
        mr.sort(key=lambda x: x[-1] if x[-1] is not None else -1, reverse=True)
        return mc, mr

    # ═════════════════════════════════════════════════════════════════════════
    # Filter logic
    # ═════════════════════════════════════════════════════════════════════════
    def _populate_all_filters(self):
        """Populate all filter dropdowns from raw data, then apply filters."""
        # --- Tab 1 filters ---
        inv_flds  = sorted(set(r[INV_FLD_IDX]  or "" for r in self.raw_inv_rows if r[INV_FLD_IDX]))
        inv_engrs = sorted(set(r[INV_ENGR_IDX] or "" for r in self.raw_inv_rows if r[INV_ENGR_IDX]))
        self.inv_fld_cb["values"]  = ["All"] + inv_flds
        self.inv_engr_cb["values"] = ["All"] + inv_engrs
        self.inv_fld_var.set("All"); self.inv_engr_var.set("All")

        # --- Tab 2 filters ---
        wt_flds  = sorted(set(r[WT_FLD_IDX]  or "" for r in self.raw_wt_rows if r[WT_FLD_IDX]))
        wt_engrs = sorted(set(r[WT_ENGR_IDX] or "" for r in self.raw_wt_rows if r[WT_ENGR_IDX]))
        self.wt_fld_cb["values"]  = ["All"] + wt_flds
        self.wt_engr_cb["values"] = ["All"] + wt_engrs
        self.wt_fld_var.set("All"); self.wt_engr_var.set("All")

        # --- Tab 3 filters ---
        ch_flds  = sorted(set(f for _,f,_,_,_,_,_ in self.chart_wells if f))
        ch_engrs = sorted(set(e for _,_,e,_,_,_,_ in self.chart_wells if e))
        self.ch_fld_cb["values"]  = ["All"] + ch_flds
        self.ch_engr_cb["values"] = ["All"] + ch_engrs
        self.ch_fld_var.set("All"); self.ch_engr_var.set("All")

        # Apply
        self._apply_inv_filter()
        self._apply_wt_filter()
        self._refresh_well_tree()

    # ── Tab 1 filter ─────────────────────────────────────────────────────────
    def _on_inv_filter(self, _=None):
        # When field changes, update engr dropdown to show only relevant values
        fld = self.inv_fld_var.get()
        if fld == "All":
            engrs = sorted(set(r[INV_ENGR_IDX] or "" for r in self.raw_inv_rows if r[INV_ENGR_IDX]))
        else:
            engrs = sorted(set(r[INV_ENGR_IDX] or "" for r in self.raw_inv_rows
                               if r[INV_FLD_IDX] == fld and r[INV_ENGR_IDX]))
        self.inv_engr_cb["values"] = ["All"] + engrs
        if self.inv_engr_var.get() not in (["All"] + engrs):
            self.inv_engr_var.set("All")
        self._apply_inv_filter()

    def _apply_inv_filter(self):
        fld  = self.inv_fld_var.get()
        engr = self.inv_engr_var.get()
        filtered = []
        for r in self.raw_inv_rows:
            if fld  != "All" and (r[INV_FLD_IDX]  or "") != fld:  continue
            if engr != "All" and (r[INV_ENGR_IDX] or "") != engr: continue
            filtered.append(r[:11])  # display columns
        cols = self.raw_inv_cols[:11]
        w = {"CMPL_NME":170,"WELL_API_NBR":110,"OPNL_FLD":100,
             "PRIM_PURP_TYPE_CDE":70,"PRIM_MATL_DESC":80,"ENGR_STRG_NME":170,
             "CMPL_STATE_TYPE_DESC":120,"CMPL_STATE_EFTV_DTTM":110,
             "BORE_START_DTTM":100,"INIT_PROD_DTE":100,"INIT_INJ_DTE":100}
        populate_tree(self.t1, cols, filtered, w)
        self.inv_lbl.config(text=f"{len(filtered)} well(s)  (P&A excluded, one per API)")

    # ── Tab 2 filter ─────────────────────────────────────────────────────────
    def _on_wt_filter(self, _=None):
        fld = self.wt_fld_var.get()
        if fld == "All":
            engrs = sorted(set(r[WT_ENGR_IDX] or "" for r in self.raw_wt_rows if r[WT_ENGR_IDX]))
        else:
            engrs = sorted(set(r[WT_ENGR_IDX] or "" for r in self.raw_wt_rows
                               if r[WT_FLD_IDX] == fld and r[WT_ENGR_IDX]))
        self.wt_engr_cb["values"] = ["All"] + engrs
        if self.wt_engr_var.get() not in (["All"] + engrs):
            self.wt_engr_var.set("All")
        self._apply_wt_filter()

    def _apply_wt_filter(self):
        fld  = self.wt_fld_var.get()
        engr = self.wt_engr_var.get()
        filtered = []
        for r in self.raw_wt_rows:
            if fld  != "All" and (r[WT_FLD_IDX]  or "") != fld:  continue
            if engr != "All" and (r[WT_ENGR_IDX] or "") != engr: continue
            filtered.append(r)
        w = {"WELL_NME":170,"WELL_API_NBR":110,"FLD_NME":100,
             "ENGR_STRG_NME":160,"TEST_DATE":100,"OIL_BOPD":80,"WTR_BWPD":80,
             "GAS_MCFD":80,"WC_PCT":70,"PEAK_TEST_DATE":110,"PEAK_OIL_BOPD":100}
        populate_tree(self.t2, self.raw_wt_cols, filtered, w)
        self.wt_lbl.config(text=f"{len(filtered)} producer(s)  (sorted by Peak Oil)")

    # ── Tab 3 filters ────────────────────────────────────────────────────────
    def _on_ch_fld_filter(self, _=None):
        fld = self.ch_fld_var.get()
        if fld == "All":
            engrs = sorted(set(e for _,_,e,_,_,_,_ in self.chart_wells if e))
        else:
            engrs = sorted(set(e for _,f,e,_,_,_,_ in self.chart_wells if f == fld and e))
        self.ch_engr_cb["values"] = ["All"] + engrs
        if self.ch_engr_var.get() not in (["All"] + engrs):
            self.ch_engr_var.set("All")
        self._refresh_well_tree()

    def _on_ch_engr_filter(self, _=None):
        self._refresh_well_tree()

    def _refresh_well_tree(self):
        fld  = self.ch_fld_var.get()
        engr = self.ch_engr_var.get()
        self.well_tree.delete(*self.well_tree.get_children())
        count = 0
        for name, f, eg, purp, matl, fid, peak in self.chart_wells:
            if fld  != "All" and f  != fld:  continue
            if engr != "All" and eg != engr: continue
            wtype = "PROD" if purp == "PROD" else f"INJ-{matl}" if purp == "INJ" else purp
            peak_str = fmt(peak) if peak and peak > 0 else ""
            tag = "even" if count % 2 == 0 else "odd"
            self.well_tree.insert("", "end", values=(name, wtype, peak_str), tags=(tag,))
            count += 1
        self.well_count_lbl.config(text=f"{count} well(s)")
        children = self.well_tree.get_children()
        if children:
            self.well_tree.selection_set(children[0])
            self.well_tree.focus(children[0])
            self._on_well_select()
        else:
            if HAS_MPL: self.fig.clear(); self.canvas.draw()
            self.chart_lbl.config(text="No wells with data for selected filters.")

    def _get_selected_info(self):
        sel = self.well_tree.selection()
        if not sel: return None
        vals = self.well_tree.item(sel[0], "values")
        name = vals[0]
        fld  = self.ch_fld_var.get()
        engr = self.ch_engr_var.get()
        for n, f, eg, p, m, fid, pk in self.chart_wells:
            if n == name and (fld == "All" or f == fld) and (engr == "All" or eg == engr):
                return (n, f, p, m, fid, pk)
        return None

    def _on_well_select(self, _=None):
        info = self._get_selected_info()
        if not info or not HAS_MPL: return
        name, fld, purp, matl, fid, pk = info
        self.chart_lbl.config(text=f"Loading {name} ...", fg="#888")
        threading.Thread(target=self._chart_bg,
                         args=(name, fld, purp, matl, fid), daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # Chart
    # ═════════════════════════════════════════════════════════════════════════
    def _chart_bg(self, name, fld, purp, matl, fid):
        try:
            if purp == "PROD":
                c, r = run_query(SQL_PROD_WELL_TESTS,
                                 {"cmpl_fac_id": fid, "spud_date": self.spud_date_val})
                self.root.after(0, self._draw_prod, c, r, name, fld)
            elif purp == "INJ":
                c, r = run_query(SQL_INJ_DAILY,
                                 {"cmpl_fac_id": fid, "start_date": self.spud_date_val})
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
            dates.append(r[0]); oil.append(r[1] or 0); wtr.append(r[2] or 0); gas.append(r[3] or 0)
        ax1 = self.fig.add_subplot(111); lines = []
        if any(v>0 for v in oil):
            l, = ax1.plot(dates, oil, "o-", color="#27ae60", ms=5, lw=1.5, label="Oil (BOPD)"); lines.append(l)
        if any(v>0 for v in wtr):
            l, = ax1.plot(dates, wtr, "s-", color="#2980b9", ms=4, lw=1.2, label="Water (BWPD)"); lines.append(l)
        ax1.set_ylabel("Liquid Rate (bbl/d)", fontsize=9)
        ax1.tick_params(labelsize=8); ax1.grid(True, alpha=0.25)
        if any(v>0 for v in gas):
            ax2 = ax1.twinx()
            l, = ax2.plot(dates, gas, "x--", color="#e74c3c", ms=4, lw=1.2, label="Gas (MCFD)"); lines.append(l)
            ax2.set_ylabel("Gas (MCFD)", fontsize=9, color="#e74c3c")
            ax2.tick_params(labelsize=8, labelcolor="#e74c3c")
        ax1.legend(lines, [l.get_label() for l in lines], fontsize=8, loc="upper left", framealpha=0.85)
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
            if any(v>0 for v in stm): ax.plot(dates, stm, color="#e67e22", lw=0.9, label="Steam (bbl/d)")
            if any(v>0 for v in wtr): ax.plot(dates, wtr, color="#2980b9", lw=0.9, label="Water (bbl/d)")
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

def main():
    root = tk.Tk(); App(root); root.mainloop()

if __name__ == "__main__":
    main()