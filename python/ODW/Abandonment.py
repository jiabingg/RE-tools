"""
Abandoned Wells Performance Viewer  (v2)
==========================================
Tabs:
  1. Abandoned Inventory  — one row per abandoned well with max detail
  2. Well Timeline Chart   — production history + status events for selected well
                             Left panel: 3-column sortable well selector (Name, Field, Cum Oil)

Filter: Wells where cmpl_state_type_desc = 'Permanently Abandoned'
        and cmpl_state_eftv_dttm >= user-selected date

Requirements:   pip install oracledb matplotlib
Run:            python abandoned_wells_viewer.py
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
# SQL — Tab 1: Abandoned Well Inventory (maximum detail)
# ─────────────────────────────────────────────────────────────────────────────
SQL_ABANDONED_INVENTORY = """
WITH abandoned AS (
    SELECT
        cd.cmpl_nme,
        cd.well_api_nbr,
        cd.opnl_fld,
        cd.fncl_fld_nme,
        cd.prdu_nme,
        cd.area_nme,
        cd.sub_area_nme,
        cd.engr_strg_nme,
        cd.rsvr_engr_strg_nme,
        cd.strg_nme,
        cd.prim_purp_type_cde,
        cd.prim_matl_desc,
        cd.cmpl_state_type_desc,
        cd.cmpl_state_type_cde,
        cd.cmpl_state_eftv_dttm AS abandon_date,
        cd.init_prod_dte,
        cd.init_inj_dte,
        cd.cmpl_fac_id,
        cd.cmpl_dmn_key,
        cd.well_fac_id,
        cd.actv_indc,
        cd.in_svc_indc,
        wd.bore_start_dttm AS spud_date,
        wd.total_dpth_qty AS td_md,
        wd.total_dpth_tvd_qty AS td_tvd,
        wd.plug_back_dpth_qty AS pbk_md,
        wd.wlbr_incl_type_desc AS well_type,
        wd.wlbr_fac_id,
        ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                           ORDER BY cd.cmpl_state_eftv_dttm DESC) AS rn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE cd.cmpl_state_type_desc = 'Permanently Abandoned'
      AND cd.cmpl_state_eftv_dttm >= :abandon_after_date
)
SELECT
    cmpl_nme, well_api_nbr, opnl_fld, fncl_fld_nme, prdu_nme,
    area_nme, sub_area_nme,
    engr_strg_nme, rsvr_engr_strg_nme, strg_nme,
    prim_purp_type_cde, prim_matl_desc,
    cmpl_state_type_desc, abandon_date,
    spud_date, init_prod_dte, init_inj_dte,
    well_type, td_md, td_tvd, pbk_md,
    actv_indc, in_svc_indc,
    cmpl_fac_id, cmpl_dmn_key, well_fac_id, wlbr_fac_id
FROM abandoned
WHERE rn = 1
ORDER BY abandon_date DESC, opnl_fld, cmpl_nme
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 2: Well selector list for chart
# ─────────────────────────────────────────────────────────────────────────────
SQL_CHART_WELL_LIST = """
WITH abandoned AS (
    SELECT
        cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld,
        cd.prim_purp_type_cde, cd.prim_matl_desc,
        cd.cmpl_state_eftv_dttm AS abandon_date,
        cd.cmpl_fac_id, cd.cmpl_dmn_key,
        ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                           ORDER BY cd.cmpl_state_eftv_dttm DESC) AS rn
    FROM dwrptg.cmpl_dmn cd
    WHERE cd.cmpl_state_type_desc = 'Permanently Abandoned'
      AND cd.cmpl_state_eftv_dttm >= :abandon_after_date
),
cum AS (
    SELECT a.cmpl_fac_id,
           ROUND(NVL(SUM(cmf.aloc_oil_prod_vol_qty), 0) / 1000, 1) AS cum_oil_mbo
    FROM abandoned a
    JOIN dwrptg.cmpl_mnly_fact cmf ON a.cmpl_dmn_key = cmf.cmpl_dmn_key
    WHERE a.rn = 1
    GROUP BY a.cmpl_fac_id
)
SELECT DISTINCT
    a.cmpl_nme, a.opnl_fld, a.prim_purp_type_cde, a.prim_matl_desc,
    a.cmpl_fac_id, a.cmpl_dmn_key, a.abandon_date,
    NVL(c.cum_oil_mbo, 0) AS cum_oil_mbo
FROM abandoned a
LEFT JOIN cum c ON a.cmpl_fac_id = c.cmpl_fac_id
WHERE a.rn = 1
ORDER BY a.opnl_fld, a.cmpl_nme
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3: Monthly production history for a single well (full life)
# ─────────────────────────────────────────────────────────────────────────────
SQL_MONTHLY_PROD_HISTORY = """
SELECT
    cmf.eftv_dttm AS PROD_MONTH,
    ROUND(cmf.aloc_oil_prod_dly_rte_qty, 1) AS OIL_BOPD,
    ROUND(cmf.aloc_gros_prod_dly_rte_qty, 1) AS GROSS_BFPD,
    ROUND(cmf.aloc_wtr_prod_dly_rte_qty, 1) AS WATER_BWPD,
    ROUND(CASE WHEN NVL(cmf.aloc_gros_prod_dly_rte_qty, 0) > 0
          THEN cmf.aloc_wtr_prod_dly_rte_qty / cmf.aloc_gros_prod_dly_rte_qty * 100
          ELSE NULL END, 1) AS WC_PCT,
    ROUND(cmf.aloc_cnts_stm_inj_dly_rte_qty, 1) AS STM_INJ_BSPD,
    ROUND(cmf.aloc_wtr_inj_dly_rte_qty, 1) AS WTR_INJ_BWPD,
    ROUND(cmf.avg_flw_line_temp_qty, 1) AS FLOWLINE_TEMP
FROM dwrptg.cmpl_mnly_fact cmf
WHERE cmf.cmpl_dmn_key = :cmpl_dmn_key
ORDER BY cmf.eftv_dttm
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3: Well test history for a single well (full life)
# ─────────────────────────────────────────────────────────────────────────────
SQL_WELL_TEST_HISTORY = """
SELECT
    f.prod_msmt_strt_dttm AS TEST_DATE,
    f.bopd_qty AS OIL_BOPD,
    f.gros_wtr_prod_vol_qty AS WTR_BWPD,
    ROUND(f.bopd_qty * NVL(f.prod_gas_oil_rat_qty, 0) / 1000, 2) AS GAS_MCFD,
    f.prod_wtr_cut_pct AS WC_PCT,
    f.test_temp_qty AS TEST_TEMP
FROM dwrptg.cmpl_prod_tst_fact f
JOIN dwrptg.cmpl_prod_tst_dmn d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
WHERE f.cmpl_fac_id = :cmpl_fac_id
  AND d.use_for_aloc_indc = 'Y'
ORDER BY f.prod_msmt_strt_dttm
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3: Status history for a single well
# ─────────────────────────────────────────────────────────────────────────────
SQL_STATUS_HISTORY = """
SELECT
    cosf.opnl_stat_eftv_dttm AS STATUS_START,
    cosf.opnl_stat_term_dttm AS STATUS_END,
    cosf.cmpl_stat_type_cde AS ON_OFF,
    cosf.off_rsn_type_cde AS OFF_REASON,
    cosf.off_rsn_sub_type_cde AS OFF_SUB_REASON,
    ROUND(NVL(cosf.opnl_stat_term_dttm, SYSDATE) - cosf.opnl_stat_eftv_dttm, 0) AS DURATION_DAYS
FROM dwrptg.cmpl_opnl_stat_fact cosf
WHERE cosf.cmpl_fac_id = :cmpl_fac_id
ORDER BY cosf.opnl_stat_eftv_dttm
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Tab 3: WRA notes for a single well
# ─────────────────────────────────────────────────────────────────────────────
SQL_WRA_NOTES = """
SELECT
    wnc.well_notes_cmnt_dte AS NOTE_DATE,
    wnc.well_notes_cmnt_txt AS NOTE_TEXT
FROM dwrptg.well_notes_cmnt_tb wnc
WHERE wnc.well_fac_id = :well_fac_id
ORDER BY wnc.well_notes_cmnt_dte DESC
FETCH FIRST 50 ROWS ONLY
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
    try:
        if isinstance(val, (int, float)):
            if isinstance(val, float) and val == int(val):
                return f"{int(val):,}"
            elif isinstance(val, float):
                return f"{val:,.1f}"
            else:
                return f"{val:,}"
    except (ValueError, OverflowError):
        pass
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
    if val == "":
        return (1, 0, "")
    stripped = val.replace(",", "")
    try:
        return (0, float(stripped), "")
    except ValueError:
        pass
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

def _sort_well_tree(tree, col, rev, callback):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    data.sort(key=lambda t: _sort_key(t[0], rev), reverse=rev)
    for i, (_, k) in enumerate(data):
        tree.move(k, "", i)
        tree.item(k, tags=("even" if i % 2 == 0 else "odd",))
    tree.heading(col, command=lambda: _sort_well_tree(tree, col, not rev, callback))


# ─────────────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────────────
class App:
    BG      = "#f4f6f8"
    ACCENT  = "#8e1600"    # dark red accent for abandonment theme
    PANEL   = "#ffffff"
    BORDER  = "#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("Abandoned Wells — Performance Viewer")
        self.root.geometry("1500x900")
        self.root.minsize(1200, 700)

        self.inv_data = []
        self.chart_wells = []  # (name, fld, purp, matl, fac_id, dmn_key, abandon_dt, cum_mbo)
        self.abandon_date_val = None

        self._style()
        self._topbar()
        self._notebook()
        self._tab1_inventory()
        self._tab3_chart()
        self._statusbar()
        self._set_status("Ready — select a P&A date cutoff and click Pull Data.")

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
        s.map("Treeview.Heading", background=[("active","#b02000")])
        s.map("Treeview", background=[("selected","#f2d5d0")])

    # ── Top bar ──────────────────────────────────────────────────────────────
    def _topbar(self):
        bar = ttk.Frame(self.root, padding=(12,10)); bar.pack(fill="x")
        ttk.Label(bar, text="Abandoned Wells Performance Viewer", style="Header.TLabel").pack(side="left")
        r = ttk.Frame(bar); r.pack(side="right")
        ttk.Label(r, text="P&A Date >= ").pack(side="left")
        self.yr = tk.StringVar(value="2023")
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
        self.f3 = ttk.Frame(self.nb)
        self.nb.add(self.f1, text="  Abandoned Inventory  ")
        self.nb.add(self.f3, text="  Well Timeline  ")

    # ── Tab 1 — Abandoned Inventory ──────────────────────────────────────────
    def _tab1_inventory(self):
        top = ttk.Frame(self.f1, padding=(8,6)); top.pack(fill="x")
        self.inv_lbl = ttk.Label(top, text="No data.", style="Sub.TLabel")
        self.inv_lbl.pack(side="left")
        ttk.Button(top, text="Export CSV",
                   command=lambda: export_tree(self.t1,"abandoned_inventory")).pack(side="right")
        frm = ttk.Frame(self.f1)
        frm.pack(fill="both", expand=True, padx=8, pady=(0,8))
        self.t1 = self._make_tree(frm)

    # ── Tab 2 — Well Timeline Chart ─────────────────────────────────────────
    def _tab3_chart(self):
        outer = ttk.Frame(self.f3)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # ── LEFT PANEL ───────────────────────────────────────────────────────
        left = tk.Frame(outer, bg=self.PANEL, bd=0,
                        highlightbackground=self.BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="ns", padx=(0,8))

        # -- Field filter --
        fld_section = tk.Frame(left, bg=self.PANEL)
        fld_section.pack(fill="x", padx=10, pady=(8,0))
        tk.Label(fld_section, text="FIELD", font=("Segoe UI",9,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(anchor="w")
        self.fld_var = tk.StringVar(value="All")
        self.fld_cb = ttk.Combobox(fld_section, textvariable=self.fld_var,
                                    width=32, state="readonly")
        self.fld_cb.pack(fill="x", pady=(4,0))
        self.fld_cb.bind("<<ComboboxSelected>>", self._on_field_filter)

        # -- Purpose filter --
        purp_section = tk.Frame(left, bg=self.PANEL)
        purp_section.pack(fill="x", padx=10, pady=(6,0))
        tk.Label(purp_section, text="PURPOSE", font=("Segoe UI",9,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(anchor="w")
        self.purp_var = tk.StringVar(value="All")
        self.purp_cb = ttk.Combobox(purp_section, textvariable=self.purp_var,
                                     width=32, state="readonly")
        self.purp_cb.pack(fill="x", pady=(4,0))
        self.purp_cb.bind("<<ComboboxSelected>>", self._on_field_filter)

        # -- Separator --
        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=10, pady=6)

        # -- Well count --
        well_hdr = tk.Frame(left, bg=self.PANEL)
        well_hdr.pack(fill="x", padx=10)
        tk.Label(well_hdr, text="SELECT WELL", font=("Segoe UI",9,"bold"),
                 fg=self.ACCENT, bg=self.PANEL).pack(side="left")
        self.well_count_lbl = tk.Label(well_hdr, text="", font=("Segoe UI",8),
                                       fg="#888", bg=self.PANEL)
        self.well_count_lbl.pack(side="right")

        # -- Well selector Treeview (3 columns: Well, Field, Cum Oil MBO) --
        tree_frame = tk.Frame(left, bg=self.PANEL)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(4,8))

        self.well_tree = ttk.Treeview(tree_frame, columns=("WELL","FIELD","CUM_OIL"),
                                       show="headings", selectmode="browse")
        self.well_tree.heading("WELL", text="Well",
                               command=lambda: _sort_well_tree(self.well_tree, "WELL", False, None))
        self.well_tree.heading("FIELD", text="Field",
                               command=lambda: _sort_well_tree(self.well_tree, "FIELD", False, None))
        self.well_tree.heading("CUM_OIL", text="Cum MBO",
                               command=lambda: _sort_well_tree(self.well_tree, "CUM_OIL", True, None))
        self.well_tree.column("WELL", width=155, anchor="w")
        self.well_tree.column("FIELD", width=85, anchor="w")
        self.well_tree.column("CUM_OIL", width=70, anchor="e")

        wt_sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.well_tree.yview)
        self.well_tree.configure(yscrollcommand=wt_sb.set)
        self.well_tree.pack(side="left", fill="both", expand=True)
        wt_sb.pack(side="right", fill="y")

        self.well_tree.tag_configure("even", background="#fdf0ee")
        self.well_tree.tag_configure("odd",  background="white")

        self.well_tree.bind("<<TreeviewSelect>>", self._on_well_select)

        # ── RIGHT PANEL ──────────────────────────────────────────────────────
        right = tk.Frame(outer, bg=self.PANEL, bd=0,
                         highlightbackground=self.BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")

        chart_top = tk.Frame(right, bg=self.PANEL)
        chart_top.pack(fill="x", padx=10, pady=(8,0))
        self.chart_lbl = tk.Label(chart_top, text="Select a well to view timeline",
                                  font=("Segoe UI",9), fg="#888", bg=self.PANEL)
        self.chart_lbl.pack(side="left")

        # -- Notes toggle button --
        self.notes_btn = tk.Button(chart_top, text="Show WRA Notes", font=("Segoe UI",8),
                                   command=self._toggle_notes, state="disabled",
                                   bg=self.PANEL, fg=self.ACCENT, relief="groove")
        self.notes_btn.pack(side="right", padx=4)

        chart_area = tk.Frame(right, bg=self.PANEL)
        chart_area.pack(fill="both", expand=True, padx=6, pady=(4,0))

        if HAS_MPL:
            self.fig = Figure(figsize=(10,5), dpi=100, facecolor=self.PANEL)
            self.canvas = FigureCanvasTkAgg(self.fig, master=chart_area)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            tb_frame = tk.Frame(chart_area, bg=self.PANEL)
            tb_frame.pack(fill="x")
            NavigationToolbar2Tk(self.canvas, tb_frame).update()

        # -- Notes text area (hidden by default) --
        self.notes_frame = tk.Frame(right, bg=self.PANEL)
        self.notes_text = tk.Text(self.notes_frame, height=8, font=("Consolas",9),
                                  wrap="word", bg="#fff8f6", fg="#333")
        notes_sb = ttk.Scrollbar(self.notes_frame, orient="vertical",
                                 command=self.notes_text.yview)
        self.notes_text.configure(yscrollcommand=notes_sb.set)
        self.notes_text.pack(side="left", fill="both", expand=True, padx=(6,0), pady=(0,6))
        notes_sb.pack(side="right", fill="y", pady=(0,6), padx=(0,6))
        self.notes_visible = False
        self._current_well_fac_id = None

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
        tree.tag_configure("even", background="#fdf0ee")
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
            self.abandon_date_val = datetime(
                int(self.yr.get()), int(self.mo.get()), int(self.dy.get()))
        except ValueError:
            messagebox.showerror("Bad Date","Invalid date."); return
        self.pull_btn.config(state="disabled")
        self._set_status(f"Querying wells abandoned since {self.abandon_date_val:%Y-%m-%d} ...")
        threading.Thread(target=self._pull_bg, daemon=True).start()

    def _pull_bg(self):
        try:
            p = {"abandon_after_date": self.abandon_date_val}

            # Tab 1: Inventory
            ic, ir = run_query(SQL_ABANDONED_INVENTORY, p)
            # Display columns: first 23 (skip internal IDs at end)
            disp_cols = ic[:23]; disp_rows = [r[:23] for r in ir]
            self.inv_data = ir
            self.root.after(0, self._show_inv, disp_cols, disp_rows)

            # Tab 2: Chart well list
            self.root.after(0, self._set_status, "Building chart well list ...")
            _, cw_rows = run_query(SQL_CHART_WELL_LIST, p)
            # (name, fld, purp, matl, fac_id, dmn_key, abandon_dt, cum_mbo)
            cw = []
            for r in cw_rows:
                cw.append((r[0] or "", r[1] or "", r[2] or "", r[3] or "",
                           r[4], r[5], r[6], r[7] or 0))
            cw.sort(key=lambda t: (t[1] or "", t[0] or ""))
            self.chart_wells = cw

            n_inv = len(ir)
            n_prod = len([r for r in ir if (r[10] or "") == 'PROD'])
            n_inj = len([r for r in ir if (r[10] or "") == 'INJ'])
            self.root.after(0, self._set_status,
                            f"Loaded {n_inv} abandoned well(s):  "
                            f"{n_prod} producers, {n_inj} injectors, "
                            f"{n_inv - n_prod - n_inj} other.")
            self.root.after(0, self._populate_chart_controls)

        except Exception as e:
            self.root.after(0, self._err, str(e))
        finally:
            self.root.after(0, lambda: self.pull_btn.config(state="normal"))

    # ── display ──────────────────────────────────────────────────────────────
    def _show_inv(self, cols, rows):
        w = {"CMPL_NME":160, "WELL_API_NBR":110, "OPNL_FLD":100,
             "FNCL_FLD_NME":120, "PRDU_NME":120, "AREA_NME":100, "SUB_AREA_NME":100,
             "ENGR_STRG_NME":160, "RSVR_ENGR_STRG_NME":160, "STRG_NME":130,
             "PRIM_PURP_TYPE_CDE":70, "PRIM_MATL_DESC":80,
             "CMPL_STATE_TYPE_DESC":140, "ABANDON_DATE":100,
             "SPUD_DATE":100, "INIT_PROD_DTE":100, "INIT_INJ_DTE":100,
             "WELL_TYPE":90, "TD_MD":70, "TD_TVD":70, "PBK_MD":70,
             "ACTV_INDC":50, "IN_SVC_INDC":50}
        populate_tree(self.t1, cols, rows, w)
        fld_counts = {}
        for r in rows:
            f = r[2] or "Unknown"
            fld_counts[f] = fld_counts.get(f, 0) + 1
        fld_str = ", ".join(f"{k}: {v}" for k, v in sorted(fld_counts.items()))
        self.inv_lbl.config(text=f"{len(rows)} abandoned well(s)  |  {fld_str}")

    # ── chart controls ───────────────────────────────────────────────────────
    def _populate_chart_controls(self):
        fields = sorted(set(f for _,f,_,_,_,_,_,_ in self.chart_wells if f))
        self.fld_cb["values"] = ["All"] + fields
        self.fld_var.set("All")
        purps = sorted(set(p for _,_,p,_,_,_,_,_ in self.chart_wells if p))
        self.purp_cb["values"] = ["All"] + purps
        self.purp_var.set("All")
        self._refresh_well_tree()

    def _on_field_filter(self, _=None):
        self._refresh_well_tree()

    def _refresh_well_tree(self):
        fld = self.fld_var.get()
        purp = self.purp_var.get()
        self.well_tree.delete(*self.well_tree.get_children())
        count = 0
        for name, f, p, matl, fid, dkey, adate, cum in self.chart_wells:
            if fld != "All" and f != fld:
                continue
            if purp != "All" and p != purp:
                continue
            cum_str = fmt(cum) if cum and cum > 0 else ""
            tag = "even" if count % 2 == 0 else "odd"
            self.well_tree.insert("", "end", values=(name, f, cum_str), tags=(tag,))
            count += 1
        self.well_count_lbl.config(text=f"{count} well(s)")
        children = self.well_tree.get_children()
        if children:
            self.well_tree.selection_set(children[0])
            self.well_tree.focus(children[0])
            self._on_well_select()
        else:
            if HAS_MPL:
                self.fig.clear(); self.canvas.draw()
            self.chart_lbl.config(text="No wells for selected filters.")

    def _get_selected_info(self):
        sel = self.well_tree.selection()
        if not sel:
            return None
        vals = self.well_tree.item(sel[0], "values")
        name = vals[0]
        for n, f, p, m, fid, dkey, adate, cum in self.chart_wells:
            if n == name:
                return (n, f, p, m, fid, dkey, adate, cum)
        return None

    def _on_well_select(self, _=None):
        info = self._get_selected_info()
        if not info or not HAS_MPL:
            return
        name, fld, purp, matl, fid, dkey, adate, cum = info
        self.chart_lbl.config(text=f"Loading {name} ...", fg="#888")
        self.notes_btn.config(state="disabled")
        # Find well_fac_id from inventory data
        wfid = None
        for r in self.inv_data:
            if r[0] == name:
                wfid = r[25]  # well_fac_id position in inventory query
                break
        self._current_well_fac_id = wfid
        self._current_well_name = name
        # Hide notes if visible
        if self.notes_visible:
            self.notes_frame.pack_forget()
            self.notes_visible = False
            self.notes_btn.config(text="Show WRA Notes")
        threading.Thread(target=self._chart_bg,
                         args=(name, fld, purp, matl, fid, dkey, adate), daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # Chart — Monthly Production + Well Tests + Status Events
    # ═════════════════════════════════════════════════════════════════════════
    def _chart_bg(self, name, fld, purp, matl, fid, dkey, adate):
        try:
            # Monthly production (full life)
            mc, mr = run_query(SQL_MONTHLY_PROD_HISTORY, {"cmpl_dmn_key": dkey})

            # Well tests (full life) — producers only
            tc, tr = [], []
            if purp == "PROD":
                tc, tr = run_query(SQL_WELL_TEST_HISTORY, {"cmpl_fac_id": fid})

            # Status history
            sc, sr = run_query(SQL_STATUS_HISTORY, {"cmpl_fac_id": fid})

            self.root.after(0, self._draw_timeline, mc, mr, tc, tr, sc, sr,
                           name, fld, purp, matl, adate)
        except Exception as e:
            self.root.after(0, self._err, str(e))

    def _draw_timeline(self, mc, mr, tc, tr, sc, sr, name, fld, purp, matl, adate):
        self.fig.clear()

        has_prod = len(mr) > 0
        has_tests = len(tr) > 0

        if not has_prod and not has_tests:
            self.chart_lbl.config(text=f"{name}: no production or test data.", fg="#c0392b")
            self.canvas.draw()
            self.notes_btn.config(state="normal" if self._current_well_fac_id else "disabled")
            return

        # ── Parse monthly data ──
        m_dates, m_oil, m_gross, m_wc, m_temp, m_stm, m_wtr_inj = [], [], [], [], [], [], []
        for r in mr:
            m_dates.append(r[0])
            m_oil.append(r[1] or 0)
            m_gross.append(r[2] or 0)
            m_wc.append(r[4])           # keep None for gaps
            m_temp.append(r[7])         # keep None for gaps
            m_stm.append(r[5] or 0)
            m_wtr_inj.append(r[6] or 0)

        # ── Parse well tests ──
        t_dates, t_oil, t_wtr = [], [], []
        if has_tests:
            for r in tr:
                t_dates.append(r[0]); t_oil.append(r[1] or 0); t_wtr.append(r[2] or 0)

        # ── Determine what to plot based on well purpose ──
        is_producer = purp == "PROD"
        is_injector = purp == "INJ"

        if is_producer:
            # 2 subplots: top = oil/gross/tests, bottom = water cut + flowline temp
            ax1 = self.fig.add_subplot(211)
            ax2 = self.fig.add_subplot(212, sharex=ax1)

            # ── Top panel: Oil, Gross, Well Tests ──
            lines = []
            if any(v > 0 for v in m_oil):
                l, = ax1.plot(m_dates, m_oil, "-", color="#27ae60", lw=1.2, alpha=0.8, label="Oil (BOPD)")
                lines.append(l)
            if any(v > 0 for v in m_gross):
                l, = ax1.plot(m_dates, m_gross, "-", color="#2980b9", lw=1.0, alpha=0.5, label="Gross (BFPD)")
                lines.append(l)
            if has_tests and any(v > 0 for v in t_oil):
                l = ax1.scatter(t_dates, t_oil, marker="o", s=20, color="#e74c3c",
                               zorder=5, label="Well Test Oil")
                lines.append(l)

            # Mark abandon date
            if adate:
                ax1.axvline(x=adate, color=self.ACCENT, ls="--", lw=1.5, alpha=0.7, label="P&A Date")
                ax2.axvline(x=adate, color=self.ACCENT, ls="--", lw=1.5, alpha=0.7)

            ax1.set_ylabel("Rate (bbl/d)", fontsize=9)
            ax1.legend(fontsize=7, loc="upper right", framealpha=0.85, ncol=2)
            ax1.tick_params(labelsize=8); ax1.grid(True, alpha=0.2)

            # ── Bottom panel: Water Cut + Flowline Temp ──
            wc_clean = [(d, v) for d, v in zip(m_dates, m_wc) if v is not None and 0 <= v <= 100]
            temp_clean = [(d, v) for d, v in zip(m_dates, m_temp) if v is not None and 80 <= v <= 400]

            lines2 = []
            if wc_clean:
                wd, wv = zip(*wc_clean)
                l, = ax2.plot(wd, wv, "-", color="#8e44ad", lw=1.0, alpha=0.7, label="Water Cut %")
                lines2.append(l)
                ax2.set_ylabel("Water Cut (%)", fontsize=9, color="#8e44ad")
                ax2.set_ylim(0, 105)

            if temp_clean:
                ax2b = ax2.twinx()
                td, tv = zip(*temp_clean)
                l, = ax2b.plot(td, tv, "-", color="#e67e22", lw=1.0, alpha=0.7, label="Flowline Temp (°F)")
                lines2.append(l)
                ax2b.set_ylabel("Flowline Temp (°F)", fontsize=9, color="#e67e22")
                ax2b.tick_params(labelsize=8, labelcolor="#e67e22")

            if lines2:
                ax2.legend(lines2, [l.get_label() for l in lines2],
                          fontsize=7, loc="upper right", framealpha=0.85)
            ax2.tick_params(labelsize=8); ax2.grid(True, alpha=0.2)

            all_dates = m_dates + t_dates
            self._fmt_x(ax2, all_dates)

        elif is_injector:
            # Single panel: steam + water injection monthly rates
            ax1 = self.fig.add_subplot(111)
            lines = []
            if matl == "Steam" or any(v > 0 for v in m_stm):
                l, = ax1.plot(m_dates, m_stm, "-", color="#e67e22", lw=1.2, label="Steam Inj (BSPD)")
                ax1.fill_between(m_dates, m_stm, alpha=0.1, color="#e67e22")
                lines.append(l)
            if matl == "Water" or any(v > 0 for v in m_wtr_inj):
                l, = ax1.plot(m_dates, m_wtr_inj, "-", color="#2980b9", lw=1.2, label="Water Inj (BWPD)")
                ax1.fill_between(m_dates, m_wtr_inj, alpha=0.1, color="#2980b9")
                lines.append(l)

            if adate:
                ax1.axvline(x=adate, color=self.ACCENT, ls="--", lw=1.5, alpha=0.7, label="P&A Date")

            ax1.set_ylabel("Injection Rate (bbl/d)", fontsize=9)
            if lines:
                ax1.legend(fontsize=8, loc="upper right", framealpha=0.85)
            ax1.tick_params(labelsize=8); ax1.grid(True, alpha=0.2)
            self._fmt_x(ax1, m_dates)

        else:
            # Generic: just oil + gross
            ax1 = self.fig.add_subplot(111)
            if any(v > 0 for v in m_oil):
                ax1.plot(m_dates, m_oil, "-", color="#27ae60", lw=1.2, label="Oil (BOPD)")
            if any(v > 0 for v in m_gross):
                ax1.plot(m_dates, m_gross, "-", color="#2980b9", lw=1.0, alpha=0.5, label="Gross (BFPD)")
            if adate:
                ax1.axvline(x=adate, color=self.ACCENT, ls="--", lw=1.5, alpha=0.7, label="P&A Date")
            ax1.set_ylabel("Rate (bbl/d)", fontsize=9)
            ax1.legend(fontsize=8, loc="upper right", framealpha=0.85)
            ax1.tick_params(labelsize=8); ax1.grid(True, alpha=0.2)
            self._fmt_x(ax1, m_dates)

        # ── Title ──
        t = f"{name}"
        if fld: t += f"  ({fld})"
        t += f"  —  {'Producer' if is_producer else 'Injector' if is_injector else purp}"
        if adate:
            t += f"  |  P&A: {adate:%Y-%m-%d}"
        ax1.set_title(t, fontsize=11, fontweight="bold", pad=10)

        self.fig.tight_layout()
        self.canvas.draw()

        info_parts = [f"{name}"]
        if has_prod: info_parts.append(f"{len(mr)} monthly pts")
        if has_tests: info_parts.append(f"{len(tr)} tests")
        self.chart_lbl.config(text=" — ".join(info_parts), fg=self.ACCENT)
        self.notes_btn.config(state="normal" if self._current_well_fac_id else "disabled")

    def _fmt_x(self, ax, dates):
        if not dates: return
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        span = (max(dates) - min(dates)).days if len(dates) > 1 else 30
        if   span > 3600: ax.xaxis.set_major_locator(mdates.YearLocator(2))
        elif span > 1800: ax.xaxis.set_major_locator(mdates.YearLocator())
        elif span > 720:  ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        elif span > 360:  ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        elif span > 120:  ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        self.fig.autofmt_xdate(rotation=45)

    # ── WRA Notes toggle ─────────────────────────────────────────────────────
    def _toggle_notes(self):
        if self.notes_visible:
            self.notes_frame.pack_forget()
            self.notes_visible = False
            self.notes_btn.config(text="Show WRA Notes")
        else:
            if self._current_well_fac_id:
                self.notes_text.delete("1.0", "end")
                self.notes_text.insert("end", "Loading notes ...\n")
                self.notes_frame.pack(fill="x", padx=6, pady=(0,6))
                self.notes_visible = True
                self.notes_btn.config(text="Hide WRA Notes")
                threading.Thread(target=self._load_notes_bg, daemon=True).start()

    def _load_notes_bg(self):
        try:
            _, rows = run_query(SQL_WRA_NOTES, {"well_fac_id": self._current_well_fac_id})
            self.root.after(0, self._show_notes, rows)
        except Exception as e:
            self.root.after(0, lambda: self.notes_text.delete("1.0", "end"))
            self.root.after(0, lambda: self.notes_text.insert("end", f"Error: {e}"))

    def _show_notes(self, rows):
        self.notes_text.delete("1.0", "end")
        if not rows:
            self.notes_text.insert("end", f"No WRA notes found for {self._current_well_name}.")
            return
        self.notes_text.insert("end", f"WRA Notes for {self._current_well_name} ({len(rows)} entries):\n")
        self.notes_text.insert("end", "=" * 80 + "\n")
        for dt, txt in rows:
            dt_str = dt.strftime("%Y-%m-%d") if dt else "Unknown"
            self.notes_text.insert("end", f"\n[{dt_str}]\n")
            self.notes_text.insert("end", f"{txt}\n")
            self.notes_text.insert("end", "-" * 60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()