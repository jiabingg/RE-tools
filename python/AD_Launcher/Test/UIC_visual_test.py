#Help: UIC Project Dashboard - Well List, Inj & Prod Charts, Cumulative
"""
UIC Project Dashboard (v10 - Auto-save Production Unit assignments)
=======================================================================
Tabs:
  1. Executive Summary - portfolio charts and filtered project table
  2. Project Metadata - project-level metadata and six-month volumes for QC
  3. Settings / Overrides - auto-save field-to-production-unit mapping and manual dashboard adjustments
  4. Water Disposal   - water disposal projects ranked by recent disposal rate
  5. Well List        - wells for selected UIC project(s) or manual API list
  6. Injection Chart  - avg daily rates + well selector
  7. Production Chart - avg daily rates + well selector
  8. Cumulative Chart - running totals + table + well selector
  9. Well Map         - State Plane XY plot by purpose/status

Requirements:
    pip install oracledb matplotlib numpy

Run:
    python uic_dashboard_executive_with_production_units.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import csv
import sys
import re
import os
import json
from datetime import datetime
from collections import defaultdict

# -----------------------------------------------------------------------------
# Database connection settings
# -----------------------------------------------------------------------------
# NOTE: For production, avoid hardcoding credentials. Prefer environment
# variables, a keyring, or a login prompt.
TNS_ALIAS = "ODW"
DB_USERNAME = "rptguser"
DB_PASSWORD = "allusers"

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uic_dashboard_settings.json")

try:
    import oracledb
except ImportError:
    sys.exit("ERROR: oracledb not installed. Run: pip install oracledb")

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


# -----------------------------------------------------------------------------
# DB helpers
# -----------------------------------------------------------------------------
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
    cur.close()
    conn.close()
    return cols, rows


def fmt(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, float):
        return f"{int(val):,}" if val == int(val) else f"{val:,.1f}"
    return str(val)


def fmt_num(v):
    if v is None:
        return ""
    try:
        n = float(v)
    except Exception:
        return str(v)
    if abs(n) >= 1e9:
        return f"{n / 1e9:.1f}B"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{int(round(n)):,}"


def sql_in_list(values):
    """
    Simple quoted IN-list helper for this dashboard pattern.
    For a hardened production version, replace dynamic IN-lists with bind variables.
    """
    safe = []
    for v in values:
        s = str(v).replace("'", "''")
        safe.append(f"'{s}'")
    return ", ".join(safe)


# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------
SQL_PROJECTS = """
SELECT p.UIC_PROJ_CDE,
       p.UIC_PROJ_DESC,
       p.FLUID_TYPE_DESC,
       p.RCVY_TYPE_DESC,
       p.FLD_NME,
       p.MAX_WELL_CNT,
       p.MAX_BPD_INJ_VOL,
       p.STAT_TYPE_DESC AS CURRENT_STATUS,
       (SELECT COUNT(*)
          FROM dwrptg.UIC_PROJ_WELL_DMN w
         WHERE w.UIC_PROJ_CDE = p.UIC_PROJ_CDE) AS WELL_COUNT
FROM dwrptg.UIC_PROJ_DMN p
ORDER BY p.UIC_PROJ_CDE
"""


SQL_MANAGER_PROJECTS = """
WITH project_wells AS (
    SELECT DISTINCT
           wpd.UIC_PROJ_CDE,
           cd.well_fac_id,
           cd.cmpl_dmn_key,
           cd.prim_purp_type_cde,
           cd.actv_indc,
           cd.in_svc_indc
    FROM dwrptg.UIC_PROJ_WELL_DMN wpd
    JOIN dwrptg.cmpl_dmn cd
      ON wpd.WELL_FAC_ID = cd.well_fac_id
     AND cd.actv_indc = 'Y'
),
well_counts AS (
    SELECT
        UIC_PROJ_CDE,
        COUNT(*) AS WELL_COUNT,
        SUM(CASE WHEN actv_indc = 'Y' THEN 1 ELSE 0 END) AS ACTIVE_WELL_COUNT,
        SUM(CASE WHEN in_svc_indc = 'Y' THEN 1 ELSE 0 END) AS IN_SERVICE_WELL_COUNT,
        SUM(CASE WHEN prim_purp_type_cde = 'INJ' THEN 1 ELSE 0 END) AS INJ_WELL_COUNT,
        SUM(CASE WHEN prim_purp_type_cde = 'PROD' THEN 1 ELSE 0 END) AS PROD_WELL_COUNT,
        SUM(CASE WHEN prim_purp_type_cde = 'OBSN' THEN 1 ELSE 0 END) AS OBSN_WELL_COUNT
    FROM project_wells
    GROUP BY UIC_PROJ_CDE
),
monthly_activity AS (
    SELECT
        pw.UIC_PROJ_CDE,
        TRUNC(cmf.eftv_dttm, 'MM') AS MONTH_DT,
        SUM(NVL(cmf.aloc_wtr_inj_dly_rte_qty, 0)) AS WTR_INJ_BPD,
        SUM(NVL(cmf.aloc_wtr_inj_vol_qty, 0))     AS WTR_INJ_BBL,
        SUM(NVL(cmf.aloc_stm_inj_dly_rte_qty, 0)) AS STM_INJ_BPD,
        SUM(NVL(cmf.aloc_stm_inj_vol_qty, 0))     AS STM_INJ_BBL,
        SUM(NVL(cmf.aloc_gas_inj_dly_rte_qty, 0)) AS GAS_INJ_MCFD,
        SUM(NVL(cmf.aloc_gas_inj_vol_qty, 0))     AS GAS_INJ_VOL,
        SUM(NVL(cmf.aloc_oil_prod_dly_rte_qty, 0)) AS OIL_BOPD
    FROM project_wells pw
    JOIN dwrptg.cmpl_mnly_fact cmf
      ON pw.cmpl_dmn_key = cmf.cmpl_dmn_key
    WHERE cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -5)
    GROUP BY pw.UIC_PROJ_CDE, TRUNC(cmf.eftv_dttm, 'MM')
),
sixmo_activity AS (
    SELECT
        UIC_PROJ_CDE,
        AVG(WTR_INJ_BPD) AS AVG_WTR_INJ_BPD_6MO,
        SUM(WTR_INJ_BBL) AS TOTAL_WTR_INJ_BBL_6MO,
        AVG(STM_INJ_BPD) AS AVG_STEAM_INJ_BPD_6MO,
        SUM(STM_INJ_BBL) AS TOTAL_STEAM_INJ_BBL_6MO,
        AVG(GAS_INJ_MCFD) AS AVG_GAS_INJ_MCFD_6MO,
        SUM(GAS_INJ_VOL) AS TOTAL_GAS_INJ_VOL_6MO,
        AVG(OIL_BOPD) AS AVG_OIL_BOPD_6MO,
        MAX(MONTH_DT) AS LAST_ACTIVITY_MONTH
    FROM monthly_activity
    GROUP BY UIC_PROJ_CDE
)
SELECT
    p.UIC_PROJ_CDE,
    p.UIC_PROJ_DESC,
    p.FLUID_TYPE_DESC,
    p.RCVY_TYPE_DESC,
    p.FLD_NME,
    p.STAT_TYPE_DESC AS CURRENT_STATUS,
    p.MAX_WELL_CNT,
    p.MAX_BPD_INJ_VOL,

    NVL(wc.WELL_COUNT, 0) AS WELL_COUNT,
    NVL(wc.ACTIVE_WELL_COUNT, 0) AS ACTIVE_WELL_COUNT,
    NVL(wc.IN_SERVICE_WELL_COUNT, 0) AS IN_SERVICE_WELL_COUNT,
    NVL(wc.INJ_WELL_COUNT, 0) AS INJ_WELL_COUNT,
    NVL(wc.PROD_WELL_COUNT, 0) AS PROD_WELL_COUNT,
    NVL(wc.OBSN_WELL_COUNT, 0) AS OBSN_WELL_COUNT,

    NVL(sa.AVG_WTR_INJ_BPD_6MO, 0) AS AVG_WTR_INJ_BPD_6MO,
    NVL(sa.TOTAL_WTR_INJ_BBL_6MO, 0) AS TOTAL_WTR_INJ_BBL_6MO,
    NVL(sa.AVG_STEAM_INJ_BPD_6MO, 0) AS AVG_STEAM_INJ_BPD_6MO,
    NVL(sa.AVG_GAS_INJ_MCFD_6MO, 0) AS AVG_GAS_INJ_MCFD_6MO,
    NVL(sa.AVG_OIL_BOPD_6MO, 0) AS AVG_OIL_BOPD_6MO,
    sa.LAST_ACTIVITY_MONTH,
    NVL(sa.TOTAL_STEAM_INJ_BBL_6MO, 0) AS TOTAL_STEAM_INJ_BBL_6MO,
    NVL(sa.TOTAL_GAS_INJ_VOL_6MO, 0) AS TOTAL_GAS_INJ_VOL_6MO
FROM dwrptg.UIC_PROJ_DMN p
LEFT JOIN well_counts wc
  ON p.UIC_PROJ_CDE = wc.UIC_PROJ_CDE
LEFT JOIN sixmo_activity sa
  ON p.UIC_PROJ_CDE = sa.UIC_PROJ_CDE
ORDER BY NVL(sa.AVG_WTR_INJ_BPD_6MO, 0) DESC, p.UIC_PROJ_CDE
"""


def sql_wells(proj_codes):
    in_list = sql_in_list(proj_codes)
    return f"""
SELECT DISTINCT cd.cmpl_nme AS WELL_NME,
       cd.well_api_nbr AS WELL_API_NBR,
       cd.opnl_fld AS FLD_NME,
       cd.prim_purp_type_cde AS PRIM_PURP_TYPE_CDE,
       cd.engr_strg_nme AS ENGR_STRG_NME,
       cd.actv_indc AS ACTV_INDC,
       cd.in_svc_indc AS IN_SVC_INDC,
       wpd.UIC_PROJ_CDE,
       fl.XCRD,
       fl.YCRD
FROM dwrptg.UIC_PROJ_WELL_DMN wpd
JOIN dwrptg.cmpl_dmn cd
  ON wpd.WELL_FAC_ID = cd.well_fac_id
 AND cd.actv_indc = 'Y'
LEFT JOIN dwrptg.fac_lctn_dmn fl
  ON cd.well_fac_id = fl.fac_id
WHERE wpd.UIC_PROJ_CDE IN ({in_list})
ORDER BY wpd.UIC_PROJ_CDE, cd.prim_purp_type_cde, cd.cmpl_nme
"""


def sql_production_by_well(proj_codes):
    in_list = sql_in_list(proj_codes)
    return f"""
SELECT cd.cmpl_nme AS WELL_NME,
       cd.well_api_nbr AS WELL_API_NBR,
       TRUNC(cmf.eftv_dttm, 'MM') AS MONTH_DT,
       cmf.aloc_oil_prod_vol_qty AS OIL_VOL,
       cmf.aloc_wtr_prod_vol_qty AS WATER_VOL,
       cmf.aloc_gas_prod_vol_qty AS GAS_VOL,
       cmf.aloc_gros_prod_vol_qty AS GROSS_VOL,
       cmf.aloc_stm_inj_vol_qty AS STEAM_INJ_VOL,
       cmf.aloc_wtr_inj_vol_qty AS WATER_INJ_VOL,
       cmf.aloc_gas_inj_vol_qty AS GAS_INJ_VOL,
       cmf.aloc_oil_prod_dly_rte_qty AS OIL_RATE,
       cmf.aloc_wtr_prod_dly_rte_qty AS WATER_RATE,
       cmf.aloc_gas_prod_dly_rte_qty AS GAS_RATE,
       cmf.aloc_gros_prod_dly_rte_qty AS GROSS_RATE,
       cmf.aloc_stm_inj_dly_rte_qty AS STEAM_INJ_RATE,
       cmf.aloc_wtr_inj_dly_rte_qty AS WATER_INJ_RATE,
       cmf.aloc_gas_inj_dly_rte_qty AS GAS_INJ_RATE
FROM dwrptg.UIC_PROJ_WELL_DMN wpd
JOIN dwrptg.cmpl_dmn cd
  ON wpd.WELL_FAC_ID = cd.well_fac_id
 AND cd.actv_indc = 'Y'
JOIN dwrptg.cmpl_mnly_fact cmf
  ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE wpd.UIC_PROJ_CDE IN ({in_list})
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -120)
ORDER BY cd.cmpl_nme, TRUNC(cmf.eftv_dttm, 'MM')
"""


def sql_wells_by_api(api_list):
    in_list = sql_in_list(api_list)
    return f"""
SELECT DISTINCT cd.cmpl_nme AS WELL_NME,
       cd.well_api_nbr AS WELL_API_NBR,
       cd.opnl_fld AS FLD_NME,
       cd.prim_purp_type_cde AS PRIM_PURP_TYPE_CDE,
       cd.engr_strg_nme AS ENGR_STRG_NME,
       cd.actv_indc AS ACTV_INDC,
       cd.in_svc_indc AS IN_SVC_INDC,
       'MANUAL' AS UIC_PROJ_CDE,
       fl.XCRD,
       fl.YCRD
FROM dwrptg.cmpl_dmn cd
LEFT JOIN dwrptg.fac_lctn_dmn fl
  ON cd.well_fac_id = fl.fac_id
WHERE cd.well_api_nbr IN ({in_list})
  AND cd.actv_indc = 'Y'
ORDER BY cd.prim_purp_type_cde, cd.cmpl_nme
"""


def sql_production_by_well_api(api_list):
    in_list = sql_in_list(api_list)
    return f"""
SELECT cd.cmpl_nme AS WELL_NME,
       cd.well_api_nbr AS WELL_API_NBR,
       TRUNC(cmf.eftv_dttm, 'MM') AS MONTH_DT,
       cmf.aloc_oil_prod_vol_qty AS OIL_VOL,
       cmf.aloc_wtr_prod_vol_qty AS WATER_VOL,
       cmf.aloc_gas_prod_vol_qty AS GAS_VOL,
       cmf.aloc_gros_prod_vol_qty AS GROSS_VOL,
       cmf.aloc_stm_inj_vol_qty AS STEAM_INJ_VOL,
       cmf.aloc_wtr_inj_vol_qty AS WATER_INJ_VOL,
       cmf.aloc_gas_inj_vol_qty AS GAS_INJ_VOL,
       cmf.aloc_oil_prod_dly_rte_qty AS OIL_RATE,
       cmf.aloc_wtr_prod_dly_rte_qty AS WATER_RATE,
       cmf.aloc_gas_prod_dly_rte_qty AS GAS_RATE,
       cmf.aloc_gros_prod_dly_rte_qty AS GROSS_RATE,
       cmf.aloc_stm_inj_dly_rte_qty AS STEAM_INJ_RATE,
       cmf.aloc_wtr_inj_dly_rte_qty AS WATER_INJ_RATE,
       cmf.aloc_gas_inj_dly_rte_qty AS GAS_INJ_RATE
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf
  ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.well_api_nbr IN ({in_list})
  AND cd.actv_indc = 'Y'
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -120)
ORDER BY cd.cmpl_nme, TRUNC(cmf.eftv_dttm, 'MM')
"""


# -----------------------------------------------------------------------------
# Treeview helpers
# -----------------------------------------------------------------------------
def populate_tree(tree, columns, rows, col_widths=None):
    tree.delete(*tree.get_children())
    dcols = ["#"] + list(columns)
    tree["columns"] = dcols
    tree["show"] = "headings"

    tree.heading("#", text="#", anchor="center")
    tree.column("#", width=45, anchor="center", stretch=False)

    for c in columns:
        tree.heading(c, text=c, anchor="w", command=lambda col=c: _sort_tree(tree, col, False))
        tree.column(c, width=(col_widths or {}).get(c, max(80, len(c) * 9)), anchor="w")

    for i, row in enumerate(rows):
        tree.insert("", "end", values=[i + 1] + [fmt(v) for v in row], tags=("even" if i % 2 == 0 else "odd",))


def _sort_tree(tree, col, rev):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    try:
        data.sort(key=lambda t: float(str(t[0]).replace(",", "")), reverse=rev)
    except Exception:
        data.sort(key=lambda t: t[0], reverse=rev)

    for i, (_, k) in enumerate(data):
        tree.move(k, "", i)
        tree.item(k, tags=("even" if i % 2 == 0 else "odd",))
        tree.set(k, "#", i + 1)

    tree.heading(col, command=lambda: _sort_tree(tree, col, not rev))


def export_tree(tree, title="export"):
    ch = tree.get_children()
    if not ch:
        messagebox.showinfo("No Data", "Nothing to export.")
        return

    path = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV", "*.csv")],
        initialfile=f"{title}_{datetime.now():%Y%m%d_%H%M%S}.csv",
    )
    if not path:
        return

    cols = [c for c in tree["columns"] if c != "#"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in ch:
            w.writerow(tree.item(c, "values")[1:])

    messagebox.showinfo("Saved", f"{len(ch)} rows -> {path}")


COLORS = {
    "oil": "#2d6a4f",
    "water_prod": "#2563eb",
    "gas_prod": "#dc2626",
    "steam_inj": "#ea580c",
    "water_inj": "#3b82f6",
    "gas_inj": "#ef4444",
    "cum_oil": "#2d6a4f",
    "cum_water": "#60a5fa",
    "cum_steam": "#fb923c",
    "cum_winj": "#3b82f6",
    "cum_ginj": "#ef4444",
    "prod_active": "#059669",
    "inj_active": "#d97706",
    "obsn": "#7c3aed",
    "idle": "#9ca3af",
}


# -----------------------------------------------------------------------------
# Main App
# -----------------------------------------------------------------------------
class App:
    BG = "#f4f6f8"
    ACCENT = "#1a5276"
    PANEL = "#ffffff"
    BORDER = "#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("UIC Project Dashboard - CRC Oracle Data Warehouse")
        self.root.geometry("1550x940")
        self.root.minsize(1180, 720)

        self.projects_rows = []
        self.all_proj_items = []

        self.manager_cols = []
        self.manager_rows = []

        self.well_cols = []
        self.well_rows = []

        self.prod_well_cols = []
        self.prod_well_rows = []

        self.selected_codes = []
        self.well_list_for_charts = []

        self.production_unit_values = ["Unassigned", "Elk Hills", "Belridge", "Wilmington"]
        self.field_unit_map = {}
        self.manual_overrides = {}
        self.override_entry_vars = {}
        self.field_search_var = None
        self.field_unit_list = None
        self.field_unit_tree = None
        self.selected_field_for_unit = None
        self.settings_status_label = None
        self._load_dashboard_settings()

        self._style()
        self._build_ui()
        self._statusbar()

        self._set_status("Connecting to database ...")
        self.root.after(100, self._load_projects)

    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        self.root.configure(bg=self.BG)

        styles = {
            "TFrame": dict(background=self.BG),
            "TLabel": dict(background=self.BG, font=("Segoe UI", 10)),
            "TButton": dict(font=("Segoe UI", 10)),
            "TNotebook": dict(background=self.BG),
            "TNotebook.Tab": dict(padding=[14, 6], font=("Segoe UI", 10)),
            "Header.TLabel": dict(font=("Segoe UI", 14, "bold"), foreground=self.ACCENT, background=self.BG),
            "Sub.TLabel": dict(font=("Segoe UI", 9), foreground="#666", background=self.BG),
            "Status.TLabel": dict(font=("Segoe UI", 9), background="#dde4ea", padding=(8, 4)),
            "Accent.TButton": dict(font=("Segoe UI", 11, "bold"), padding=[18, 6]),
            "Treeview": dict(font=("Consolas", 9), rowheight=24),
            "Treeview.Heading": dict(font=("Segoe UI", 9, "bold"), foreground="white", background=self.ACCENT),
        }

        for name, kw in styles.items():
            s.configure(name, **kw)

        s.map("Treeview.Heading", background=[("active", "#1a6b9c")])
        s.map("Treeview", background=[("selected", "#d4e6f1")])

    def _build_ui(self):
        hdr = ttk.Frame(self.root, padding=(12, 8))
        hdr.pack(fill="x")

        ttk.Label(hdr, text="UIC Project Dashboard", style="Header.TLabel").pack(side="left")
        ttk.Label(
            hdr,
            text="Underground Injection Control - Executive, Disposal, Surveillance & Reporting",
            style="Sub.TLabel",
        ).pack(side="left", padx=15)

        pw = ttk.PanedWindow(self.root, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        # LEFT - project picker
        left = ttk.Frame(pw)
        pw.add(left, weight=1)

        lf = ttk.LabelFrame(left, text=" Select UIC Project(s) ", padding=5)
        lf.pack(fill="both", expand=True)

        sf = ttk.Frame(lf)
        sf.pack(fill="x", pady=(0, 5))

        ttk.Label(sf, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._filter_projects)
        ttk.Entry(sf, textvariable=self.search_var, width=28).pack(side="left", padx=5, fill="x", expand=True)

        lb_frame = ttk.Frame(lf)
        lb_frame.pack(fill="both", expand=True)

        sb = ttk.Scrollbar(lb_frame, orient="vertical")
        self.proj_lb = tk.Listbox(
            lb_frame,
            selectmode="extended",
            font=("Consolas", 9),
            yscrollcommand=sb.set,
            activestyle="none",
            selectbackground=self.ACCENT,
            selectforeground="white",
        )
        sb.config(command=self.proj_lb.yview)
        self.proj_lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.proj_lb.bind(
            "<<ListboxSelect>>",
            lambda e: self.sel_lbl.config(text=f"Selected: {len(self.proj_lb.curselection())} project(s)"),
        )

        self.sel_lbl = ttk.Label(lf, text="Selected: 0", font=("Segoe UI", 9))
        self.sel_lbl.pack(fill="x", pady=(5, 0))

        self.load_btn = ttk.Button(
            lf,
            text="Load Project Data",
            style="Accent.TButton",
            command=self._on_load,
        )
        self.load_btn.pack(fill="x", pady=(5, 0))

        sep = ttk.Separator(lf, orient="horizontal")
        sep.pack(fill="x", pady=(10, 5))

        api_lf = ttk.LabelFrame(lf, text=" Manual API Override ", padding=4)
        api_lf.pack(fill="x", pady=(0, 0))

        ttk.Label(
            api_lf,
            text="Paste APIs (one per line or comma-separated):",
            font=("Segoe UI", 8),
            foreground="#666",
        ).pack(anchor="w")

        api_txt_frame = ttk.Frame(api_lf)
        api_txt_frame.pack(fill="x", pady=(2, 4))

        self.api_text = tk.Text(
            api_txt_frame,
            height=5,
            width=28,
            font=("Consolas", 8),
            wrap="word",
            bd=1,
            relief="solid",
        )
        api_sb = ttk.Scrollbar(api_txt_frame, orient="vertical", command=self.api_text.yview)
        self.api_text.configure(yscrollcommand=api_sb.set)
        self.api_text.pack(side="left", fill="x", expand=True)
        api_sb.pack(side="right", fill="y")

        self.api_load_btn = ttk.Button(api_lf, text="Load by API List", command=self._on_load_by_api)
        self.api_load_btn.pack(fill="x")

        # RIGHT - notebook
        right = ttk.Frame(pw)
        pw.add(right, weight=4)

        self.cards_frame = ttk.Frame(right)
        self.cards_frame.pack(fill="x", pady=(0, 5))

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

        self.tab_mgr = ttk.Frame(self.nb)
        self.tab_meta = ttk.Frame(self.nb)
        self.tab_settings = ttk.Frame(self.nb)
        self.tab_wtr_disp = ttk.Frame(self.nb)
        self.tab_wells = ttk.Frame(self.nb)
        self.tab_inj = ttk.Frame(self.nb)
        self.tab_prod = ttk.Frame(self.nb)
        self.tab_cum = ttk.Frame(self.nb)
        self.tab_map = ttk.Frame(self.nb)

        self.nb.add(self.tab_mgr, text="  Executive Summary  ")
        self.nb.add(self.tab_meta, text="  Project Metadata  ")
        self.nb.add(self.tab_settings, text="  Settings / Overrides  ")
        self.nb.add(self.tab_wtr_disp, text="  Water Disposal  ")
        self.nb.add(self.tab_wells, text="  Well List  ")
        self.nb.add(self.tab_inj, text="  Injection  ")
        self.nb.add(self.tab_prod, text="  Production  ")
        self.nb.add(self.tab_cum, text="  Cumulative  ")
        self.nb.add(self.tab_map, text="  Well Map  ")

    def _statusbar(self):
        self.sb = ttk.Label(self.root, text="", style="Status.TLabel", anchor="w")
        self.sb.pack(fill="x", side="bottom")

    def _set_status(self, msg):
        self.sb.config(text=msg)
        self.root.update_idletasks()

    # -------------------------------------------------------------------------
    # Load project metadata and executive summary data
    # -------------------------------------------------------------------------
    def _load_projects(self):
        threading.Thread(target=self._load_projects_bg, daemon=True).start()

    def _load_projects_bg(self):
        try:
            cols, rows = run_query(SQL_PROJECTS)
            self.projects_rows = rows

            mcols, mrows = run_query(SQL_MANAGER_PROJECTS)
            self.manager_cols = mcols
            self.manager_rows = mrows

            self.root.after(0, self._populate_projects)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Database Error", f"Cannot connect:\n{e}"))
            self.root.after(0, lambda: self._set_status(f"Connection failed: {str(e)[:80]}"))

    def _populate_projects(self):
        self.all_proj_items = []
        self.proj_lb.delete(0, "end")

        for row in self.projects_rows:
            code = str(row[0] or "").strip()
            desc = str(row[1] or "")[:38]
            status = str(row[7] or "")[:12]
            wc = str(row[8] or 0)
            display = f"{code:<14} {desc:<40} {status:<13} ({wc} wells)"
            self.all_proj_items.append((code, display))
            self.proj_lb.insert("end", display)

        self._ensure_field_unit_map()
        self._build_executive_summary_tab()
        self._build_project_metadata_tab()
        self._build_settings_tab()
        self._build_water_disposal_tab()

        self._set_status(f"Loaded {len(self.projects_rows)} projects. Select and click Load.")

    def _filter_projects(self, *a):
        term = self.search_var.get().lower()
        self.proj_lb.delete(0, "end")
        for code, display in self.all_proj_items:
            if term in display.lower():
                self.proj_lb.insert("end", display)

    def _get_selected_codes(self):
        vis = self.proj_lb.get(0, "end")
        return [vis[i][:14].strip() for i in self.proj_lb.curselection()]

    # -------------------------------------------------------------------------
    # Dashboard settings: Production Unit mapping + manual overrides
    # -------------------------------------------------------------------------
    def _load_dashboard_settings(self):
        """Load field production-unit assignments and manual override values."""
        self.field_unit_map = {}
        self.manual_overrides = {}
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.field_unit_map = dict(data.get("field_unit_map", {}) or {})
                self.manual_overrides = dict(data.get("manual_overrides", {}) or {})
        except Exception as e:
            print(f"Could not load dashboard settings: {e}")
            self.field_unit_map = {}
            self.manual_overrides = {}

    def _save_dashboard_settings(self, show_message=False):
        """Save dashboard settings next to the script as JSON."""
        data = {
            "field_unit_map": self.field_unit_map,
            "manual_overrides": self.manual_overrides,
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            if show_message:
                messagebox.showinfo("Saved", f"Dashboard settings saved to:\n{SETTINGS_FILE}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Could not save dashboard settings:\n{e}")

    def _default_production_unit_for_field(self, field):
        """Conservative defaults; anything uncertain stays Unassigned for user QC."""
        f = str(field or "").upper()
        if "ELK" in f:
            return "Elk Hills"
        if "BELRIDGE" in f:
            return "Belridge"
        if "WILMINGTON" in f:
            return "Wilmington"
        return "Unassigned"

    def _ensure_field_unit_map(self):
        """Make sure every field in the loaded project metadata has an assignment row."""
        changed = False
        for r in self.manager_rows:
            field = str(r[4] or "Unknown")
            if field not in self.field_unit_map:
                self.field_unit_map[field] = self._default_production_unit_for_field(field)
                changed = True
        if changed:
            self._save_dashboard_settings(show_message=False)

    def _production_unit_for_field(self, field):
        field = str(field or "Unknown")
        return self.field_unit_map.get(field, self._default_production_unit_for_field(field))

    def _production_unit_for_row(self, row):
        return self._production_unit_for_field(row[4])

    def _exec_group_mode(self):
        try:
            return self.exec_group_var.get()
        except Exception:
            return "Field"

    def _chart_group_for_row(self, row):
        if self._exec_group_mode() == "Production Unit":
            return self._production_unit_for_row(row)
        return str(row[4] or "Unknown")

    def _chart_group_label(self):
        return "Production Unit" if self._exec_group_mode() == "Production Unit" else "Field"

    def _override_value(self, key, calculated_value):
        val = self.manual_overrides.get(key, None)
        if val in (None, ""):
            return calculated_value
        try:
            return float(val)
        except Exception:
            return calculated_value

    def _has_override(self, key):
        return self.manual_overrides.get(key, None) not in (None, "")

    def _override_suffix(self, key):
        return " *" if self._has_override(key) else ""

    def _apply_volume_overrides_to_chart_data(self, categories, data):
        """
        If a manual category total exists, scale the chart stacks so the plotted
        category total matches the executive-card override. If no database volume
        exists for an overridden category, place the value under Manual Adjustment.
        """
        key_map = {
            "Water Disposal": "water_disposal_bpd",
            "Water Injection": "water_injection_bpd",
            "Steam Injection": "steam_injection_bwed",
            "Gas Disposal": "gas_disposal_mcfd",
        }
        adjusted = {cat: defaultdict(float, data.get(cat, {})) for cat in categories}
        for cat in categories:
            key = key_map.get(cat)
            if not key or not self._has_override(key):
                continue
            target = self._override_value(key, sum(adjusted[cat].values()))
            current = sum(adjusted[cat].values())
            if current > 0:
                factor = target / current
                for group in list(adjusted[cat].keys()):
                    adjusted[cat][group] *= factor
            elif target > 0:
                adjusted[cat]["Manual Adjustment"] += target
        return adjusted

    def _build_settings_tab(self):
        for w in self.tab_settings.winfo_children():
            w.destroy()

        top = ttk.Frame(self.tab_settings, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Production Unit Mapping / Manual Dashboard Adjustments", style="Header.TLabel").pack(side="left")
        self.settings_status_label = ttk.Label(top, text="Production Unit assignments auto-save", style="Sub.TLabel")
        self.settings_status_label.pack(side="right")

        note = ttk.Label(
            self.tab_settings,
            text=(
                "Assign each field to a Production Unit; changes save automatically as soon as you choose a value. "
                "Then use the Executive Summary 'Group Charts By' filter. Manual adjustments override only the final Executive Summary card totals and volume chart totals; "
                "the Project Metadata / QC tab continues to show database-calculated values."
            ),
            style="Sub.TLabel",
        )
        note.pack(fill="x", padx=10, pady=(0, 6))

        manual = ttk.LabelFrame(self.tab_settings, text=" Manual Executive Summary Adjustments ", padding=8)
        manual.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(manual, text="Leave blank to use the database-calculated value.", style="Sub.TLabel").grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 4))

        fields = [
            ("water_disposal_bpd", "Water Disposal BPD"),
            ("water_injection_bpd", "Water Injection BPD"),
            ("steam_injection_bwed", "Steam Injection BWE/d"),
            ("gas_disposal_mcfd", "Gas Disposal MCFD"),
            ("total_liquid_bpd", "Total Liquid/Steam BPD"),
        ]
        self.override_entry_vars = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(manual, text=label + ":").grid(row=1, column=i * 2, sticky="e", padx=(0, 4), pady=3)
            val = self.manual_overrides.get(key, "")
            self.override_entry_vars[key] = tk.StringVar(value="" if val in (None, "") else str(val))
            ttk.Entry(manual, textvariable=self.override_entry_vars[key], width=14).grid(row=1, column=i * 2 + 1, sticky="w", padx=(0, 10), pady=3)

        ttk.Button(manual, text="Apply / Save Adjustments", command=lambda: self._save_settings_from_ui(show_message=True)).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(manual, text="Clear Manual Adjustments", command=self._clear_manual_overrides).grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))

        mapping = ttk.LabelFrame(self.tab_settings, text=" Field to Production Unit Assignment ", padding=8)
        mapping.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(mapping)
        left.pack(side="left", fill="both", expand=False, padx=(0, 8))
        ttk.Label(left, text="Search Field:").pack(anchor="w")
        self.field_search_var = tk.StringVar()
        self.field_search_var.trace_add("write", lambda *a: self._populate_field_unit_list())
        ttk.Entry(left, textvariable=self.field_search_var, width=28).pack(fill="x", pady=(0, 5))

        lb_frame = ttk.Frame(left)
        lb_frame.pack(fill="both", expand=True)
        lb_sb = ttk.Scrollbar(lb_frame, orient="vertical")
        self.field_unit_list = tk.Listbox(
            lb_frame,
            selectmode="browse",
            width=32,
            height=18,
            font=("Consolas", 9),
            yscrollcommand=lb_sb.set,
            exportselection=False,
        )
        lb_sb.config(command=self.field_unit_list.yview)
        self.field_unit_list.pack(side="left", fill="both", expand=True)
        lb_sb.pack(side="right", fill="y")
        self.field_unit_list.bind("<<ListboxSelect>>", lambda e: self._on_field_unit_select())

        assign = ttk.Frame(mapping)
        assign.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(assign, text="Selected Field:").pack(anchor="w")
        self.selected_field_label = ttk.Label(assign, text="None", style="Sub.TLabel", wraplength=220)
        self.selected_field_label.pack(anchor="w", pady=(0, 8))
        ttk.Label(assign, text="Production Unit:").pack(anchor="w")
        self.unit_assign_var = tk.StringVar(value="Unassigned")
        self.unit_assign_cb = ttk.Combobox(
            assign,
            textvariable=self.unit_assign_var,
            values=self.production_unit_values,
            width=22,
            state="readonly",
        )
        self.unit_assign_cb.pack(anchor="w", pady=(0, 3))
        self.unit_assign_cb.bind("<<ComboboxSelected>>", lambda e: self._auto_save_unit_assignment())

        ttk.Label(
            assign,
            text="Select a field, then choose a unit. The assignment saves automatically.",
            style="Sub.TLabel",
            wraplength=230,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Button(assign, text="Auto Assign Obvious Fields", command=self._auto_assign_obvious_units).pack(anchor="w", fill="x", pady=(0, 5))
        ttk.Button(assign, text="Refresh Executive Charts", command=self._refresh_executive_summary).pack(anchor="w", fill="x")

        table_frame = ttk.Frame(mapping)
        table_frame.pack(side="left", fill="both", expand=True)
        self.field_unit_tree = self._mktree(table_frame)
        self._populate_field_unit_list()
        self._populate_field_unit_tree()

    def _save_settings_from_ui(self, show_message=False):
        overrides = {}
        for key, var in getattr(self, "override_entry_vars", {}).items():
            raw = var.get().strip()
            if not raw:
                overrides[key] = ""
                continue
            try:
                overrides[key] = float(raw.replace(",", ""))
            except Exception:
                messagebox.showerror("Invalid Override", f"{raw!r} is not a valid number for {key}.")
                return
        if overrides:
            self.manual_overrides = overrides
        self._save_dashboard_settings(show_message=show_message)
        try:
            self._refresh_executive_summary()
            self._filter_project_metadata_table()
        except Exception:
            pass

    def _clear_manual_overrides(self):
        self.manual_overrides = {k: "" for k in ["water_disposal_bpd", "water_injection_bpd", "steam_injection_bwed", "gas_disposal_mcfd", "total_liquid_bpd"]}
        for key, var in getattr(self, "override_entry_vars", {}).items():
            var.set("")
        self._save_dashboard_settings(show_message=False)
        self._refresh_executive_summary()

    def _all_fields_for_mapping(self):
        return sorted(set(str(r[4] or "Unknown") for r in self.manager_rows))

    def _populate_field_unit_list(self):
        if self.field_unit_list is None:
            return
        term = self.field_search_var.get().lower() if self.field_search_var is not None else ""
        self.field_unit_list.delete(0, "end")
        for field in self._all_fields_for_mapping():
            if term and term not in field.lower():
                continue
            unit = self._production_unit_for_field(field)
            self.field_unit_list.insert("end", f"{field:<28}  ->  {unit}")

    def _field_from_listbox_display(self, display):
        return str(display).split("  ->  ")[0].strip()

    def _on_field_unit_select(self):
        sel = self.field_unit_list.curselection()
        if not sel:
            return
        display = self.field_unit_list.get(sel[0])
        field = self._field_from_listbox_display(display)
        self.selected_field_for_unit = field
        self.selected_field_label.config(text=field)
        self.unit_assign_var.set(self._production_unit_for_field(field))

    def _auto_save_unit_assignment(self):
        field = self.selected_field_for_unit
        if not field:
            return

        unit = self.unit_assign_var.get()
        self.field_unit_map[field] = unit
        self._save_dashboard_settings(show_message=False)

        self._populate_field_unit_list()
        self._populate_field_unit_tree()
        self._select_field_in_unit_list(field)

        try:
            self._refresh_executive_summary()
            self._filter_project_metadata_table()
        except Exception:
            pass

        msg = f"Saved: {field} -> {unit}"
        self._set_status(msg)
        if self.settings_status_label is not None:
            self.settings_status_label.config(text=msg)

    def _apply_unit_to_selected_field(self):
        # Backward-compatible alias. The UI now auto-saves from the dropdown.
        self._auto_save_unit_assignment()

    def _select_field_in_unit_list(self, field):
        if self.field_unit_list is None:
            return
        for i in range(self.field_unit_list.size()):
            display = self.field_unit_list.get(i)
            if self._field_from_listbox_display(display) == field:
                self.field_unit_list.selection_clear(0, "end")
                self.field_unit_list.selection_set(i)
                self.field_unit_list.see(i)
                break

    def _auto_assign_obvious_units(self):
        changed = 0
        for field in self._all_fields_for_mapping():
            default = self._default_production_unit_for_field(field)
            if default != "Unassigned" and self.field_unit_map.get(field) != default:
                self.field_unit_map[field] = default
                changed += 1
        self._save_dashboard_settings(show_message=False)
        self._populate_field_unit_list()
        self._populate_field_unit_tree()
        try:
            self._refresh_executive_summary()
            self._filter_project_metadata_table()
        except Exception:
            pass
        msg = f"Auto-assigned {changed} field(s). Settings saved."
        self._set_status(msg)
        if self.settings_status_label is not None:
            self.settings_status_label.config(text=msg)

    def _populate_field_unit_tree(self):
        if self.field_unit_tree is None:
            return
        rows = [(field, self._production_unit_for_field(field)) for field in self._all_fields_for_mapping()]
        populate_tree(self.field_unit_tree, ["FLD_NME", "PRODUCTION_UNIT"], rows, {"FLD_NME": 220, "PRODUCTION_UNIT": 160})

    # -------------------------------------------------------------------------
    # Executive Summary and Water Disposal dashboard helpers
    # -------------------------------------------------------------------------
    def _num(self, v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def _val(self, row, idx, default=None):
        try:
            return row[idx]
        except Exception:
            return default

    def _is_active_status(self, status):
        s = str(status or "").upper()
        if any(x in s for x in ["INACTIVE", "SUSP", "SHUT", "RESCIND", "CANCEL", "ABANDON"]):
            return False
        return any(x in s for x in ["ACTIVE", "APPROVED", "OPERATING", "AUTHORIZED"])

    def _is_suspended_status(self, status):
        s = str(status or "").upper()
        return any(x in s for x in ["SUSP", "SHUT", "INACTIVE", "RESCIND", "CANCEL"])

    def _status_matches_filter(self, status, selected):
        if selected == "ALL":
            return True
        if selected == "ACTIVE":
            return self._is_active_status(status)
        if selected == "SUSPENDED":
            return self._is_suspended_status(status)
        if selected == "OTHER":
            return not self._is_active_status(status) and not self._is_suspended_status(status)
        if selected.startswith("STATUS: "):
            exact_status = selected.replace("STATUS: ", "", 1)
            return str(status or "") == exact_status
        return True

    def _status_group(self, status):
        if self._is_active_status(status):
            return "ACTIVE"
        if self._is_suspended_status(status):
            return "SUSPENDED"
        return "OTHER"

    def _project_type_matches_filter(self, row, selected):
        if selected in ("ALL", "", None):
            return True
        return self._project_type(row) == selected

    def _available_project_type_values(self):
        types = sorted(set(self._project_type(r) for r in self.manager_rows))
        preferred = [
            "Steamflood",
            "Waterflood",
            "Cyclic Steam",
            "Water Disposal",
            "Dual Steam/Waterflood",
            "Gas Disposal",
            "Other",
        ]
        ordered = [t for t in preferred if t in types] + [t for t in types if t not in preferred]
        return ["ALL"] + ordered

    def _project_type(self, row):
        """
        Manager SQL row layout:
          0 UIC_PROJ_CDE
          1 UIC_PROJ_DESC
          2 FLUID_TYPE_DESC
          3 RCVY_TYPE_DESC
          4 FLD_NME
          5 CURRENT_STATUS
        """
        text = " ".join(str(row[i] or "").upper() for i in [1, 2, 3])

        if "GAS" in text and ("DISP" in text or "DISPOSAL" in text):
            return "Gas Disposal"
        if "WATER" in text and ("DISP" in text or "DISPOSAL" in text):
            return "Water Disposal"
        if ("DUAL" in text and "STEAM" in text and "WATER" in text) or ("STEAM" in text and "WATERFLOOD" in text):
            return "Dual Steam/Waterflood"
        if "CYCLIC" in text or "CSS" in text:
            return "Cyclic Steam"
        if "STEAM" in text or "STEAMFLOOD" in text or "THERMAL" in text:
            return "Steamflood"
        if "WATERFLOOD" in text or "WATER FLOOD" in text:
            return "Waterflood"
        return "Other"

    def _is_water_disposal_project(self, row):
        return self._project_type(row) == "Water Disposal"

    def _well_count_for_exec_filter(self, row):
        """
        Row index reference from SQL_MANAGER_PROJECTS:
          8  WELL_COUNT
          10 IN_SERVICE_WELL_COUNT
          11 INJ_WELL_COUNT
          12 PROD_WELL_COUNT
          13 OBSN_WELL_COUNT
        """
        selected = getattr(self, "exec_well_type_var", tk.StringVar(value="ALL WELLS")).get()

        total = self._num(row[8])
        in_service = self._num(row[10])
        inj = self._num(row[11])
        prod = self._num(row[12])
        obsn = self._num(row[13])
        other = max(total - inj - prod - obsn, 0)
        not_in_service = max(total - in_service, 0)

        if selected == "ALL WELLS":
            return total
        if selected == "INJECTOR":
            return inj
        if selected == "PRODUCER":
            return prod
        if selected == "OBSERVATION":
            return obsn
        if selected == "OTHER":
            return other
        if selected == "IN SERVICE":
            return in_service
        if selected == "NOT IN SERVICE":
            return not_in_service
        return total

    def _get_executive_filtered_rows(self):
        """
        Return all projects that match the Executive Summary filters.

        Important QC behavior:
        - Do NOT drop projects with zero selected wells.
        - Well counts remain zero where applicable.
        - This keeps Executive Summary project counts aligned with Project Metadata / QC.
        """
        status_filter = getattr(self, "exec_status_var", tk.StringVar(value="ALL")).get()
        project_type_filter = getattr(self, "exec_project_type_var", tk.StringVar(value="ALL")).get()

        rows = []
        for r in self.manager_rows:
            if not self._status_matches_filter(r[5], status_filter):
                continue
            if not self._project_type_matches_filter(r, project_type_filter):
                continue
            rows.append(r)
        return rows

    def _make_card(self, parent, label, value, color=None):
        c = tk.Frame(parent, bg="white", bd=1, relief="solid")
        c.pack(side="left", padx=4, pady=2, fill="x", expand=True)

        tk.Label(c, text=label, font=("Segoe UI", 9), fg="#6b7280", bg="white").pack(
            anchor="w", padx=10, pady=(6, 0)
        )
        tk.Label(c, text=value, font=("Segoe UI", 18, "bold"), fg=color or self.ACCENT, bg="white").pack(
            anchor="w", padx=10, pady=(0, 6)
        )

    def _build_executive_summary_tab(self):
        for w in self.tab_mgr.winfo_children():
            w.destroy()

        old_status = "ALL"
        old_project_type = "ALL"
        old_well_type = "ALL WELLS"
        old_group_by = "Field"
        old_bar_size_by = "Well Count"
        try:
            old_status = self.exec_status_var.get()
        except Exception:
            pass
        try:
            old_project_type = self.exec_project_type_var.get()
        except Exception:
            pass
        try:
            old_well_type = self.exec_well_type_var.get()
        except Exception:
            pass
        try:
            old_group_by = self.exec_group_var.get()
        except Exception:
            pass
        try:
            old_bar_size_by = self.exec_bar_size_var.get()
        except Exception:
            pass

        top = ttk.Frame(self.tab_mgr, padding=(8, 6))
        top.pack(fill="x")

        ttk.Label(top, text="Executive Summary", style="Header.TLabel").pack(side="left")
        ttk.Button(top, text="Refresh", command=self._refresh_manager_tabs).pack(side="right")

        filter_bar = ttk.Frame(self.tab_mgr)
        filter_bar.pack(fill="x", padx=8, pady=(0, 6))

        ttk.Label(filter_bar, text="Project Status:").pack(side="left")
        exact_statuses = sorted(set(str(r[5] or "") for r in self.manager_rows if r[5]))
        status_values = ["ALL", "ACTIVE", "SUSPENDED", "OTHER"] + [f"STATUS: {s}" for s in exact_statuses]
        if old_status not in status_values:
            old_status = "ALL"
        self.exec_status_var = tk.StringVar(value=old_status)
        status_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.exec_status_var,
            values=status_values,
            width=28,
            state="readonly",
        )
        status_cb.pack(side="left", padx=(5, 18))
        status_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_executive_summary())

        ttk.Label(filter_bar, text="Project Type:").pack(side="left")
        project_type_values = self._available_project_type_values()
        if old_project_type not in project_type_values:
            old_project_type = "ALL"
        self.exec_project_type_var = tk.StringVar(value=old_project_type)
        type_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.exec_project_type_var,
            values=project_type_values,
            width=24,
            state="readonly",
        )
        type_cb.pack(side="left", padx=(5, 18))
        type_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_executive_summary())

        ttk.Label(filter_bar, text="Well Type:").pack(side="left")
        well_type_values = [
            "ALL WELLS",
            "INJECTOR",
            "PRODUCER",
            "OBSERVATION",
            "OTHER",
            "IN SERVICE",
            "NOT IN SERVICE",
        ]
        if old_well_type not in well_type_values:
            old_well_type = "ALL WELLS"
        self.exec_well_type_var = tk.StringVar(value=old_well_type)
        well_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.exec_well_type_var,
            values=well_type_values,
            width=18,
            state="readonly",
        )
        well_cb.pack(side="left", padx=(5, 18))
        well_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_executive_summary())

        ttk.Label(filter_bar, text="Group Charts By:").pack(side="left")
        if old_group_by not in ["Field", "Production Unit"]:
            old_group_by = "Field"
        self.exec_group_var = tk.StringVar(value=old_group_by)
        group_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.exec_group_var,
            values=["Field", "Production Unit"],
            width=18,
            state="readonly",
        )
        group_cb.pack(side="left", padx=(5, 18))
        group_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_executive_summary())

        ttk.Label(filter_bar, text="Bar Size By:").pack(side="left")
        bar_size_values = ["Well Count", "Project Count"]
        if old_bar_size_by not in bar_size_values:
            old_bar_size_by = "Well Count"
        self.exec_bar_size_var = tk.StringVar(value=old_bar_size_by)
        bar_size_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.exec_bar_size_var,
            values=bar_size_values,
            width=15,
            state="readonly",
        )
        bar_size_cb.pack(side="left", padx=5)
        bar_size_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_executive_summary())

        self.exec_cards_frame = ttk.Frame(self.tab_mgr)
        self.exec_cards_frame.pack(fill="x", padx=8, pady=(0, 6))

        self.exec_chart_nb = ttk.Notebook(self.tab_mgr)
        self.exec_chart_nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.exec_field_chart_frame = ttk.Frame(self.exec_chart_nb)
        self.exec_type_chart_frame = ttk.Frame(self.exec_chart_nb)
        self.exec_volume_chart_frame = ttk.Frame(self.exec_chart_nb)
        self.exec_table_frame = ttk.Frame(self.exec_chart_nb)

        self.exec_chart_nb.add(self.exec_field_chart_frame, text="  By Field / PU  ")
        self.exec_chart_nb.add(self.exec_type_chart_frame, text="  By Project Type  ")
        self.exec_chart_nb.add(self.exec_volume_chart_frame, text="  Volume  ")
        self.exec_chart_nb.add(self.exec_table_frame, text="  Project Table  ")

        self._refresh_executive_summary()

    def _refresh_executive_summary(self):
        rows = self._get_executive_filtered_rows()

        for frame in [
            self.exec_cards_frame,
            self.exec_field_chart_frame,
            self.exec_type_chart_frame,
            self.exec_volume_chart_frame,
            self.exec_table_frame,
        ]:
            for w in frame.winfo_children():
                w.destroy()

        total_projects = len(rows)
        selected_wells = sum(self._well_count_for_exec_filter(r) for r in rows)
        active_projects = sum(1 for r in rows if self._is_active_status(r[5]))
        suspended_projects = sum(1 for r in rows if self._is_suspended_status(r[5]))

        calc_water_disp = sum(self._num(r[14]) for r in rows if self._project_type(r) == "Water Disposal")
        calc_water_inj = sum(self._num(r[14]) for r in rows if self._project_type(r) in ["Waterflood", "Dual Steam/Waterflood"])
        calc_steam_inj = sum(self._num(r[16]) for r in rows if self._project_type(r) in ["Steamflood", "Cyclic Steam", "Dual Steam/Waterflood"])
        calc_gas_disp = sum(self._num(r[17]) for r in rows if self._project_type(r) == "Gas Disposal")

        water_disp = self._override_value("water_disposal_bpd", calc_water_disp)
        water_inj = self._override_value("water_injection_bpd", calc_water_inj)
        steam_inj = self._override_value("steam_injection_bwed", calc_steam_inj)
        gas_disp = self._override_value("gas_disposal_mcfd", calc_gas_disp)
        total_liquid = self._override_value("total_liquid_bpd", water_disp + water_inj + steam_inj)

        self._make_card(self.exec_cards_frame, "Projects", f"{total_projects:,}", self.ACCENT)
        self._make_card(self.exec_cards_frame, "Selected Wells", f"{int(selected_wells):,}", "#7c3aed")
        self._make_card(self.exec_cards_frame, "Active Projects", f"{active_projects:,}", "#059669")
        self._make_card(self.exec_cards_frame, "Suspended Projects", f"{suspended_projects:,}", "#dc2626")
        self._make_card(self.exec_cards_frame, "Total Liquid/Steam" + self._override_suffix("total_liquid_bpd"), f"{fmt_num(total_liquid)} BPD/BWE", "#6d28d9")
        self._make_card(self.exec_cards_frame, "Water Disposal" + self._override_suffix("water_disposal_bpd"), f"{fmt_num(water_disp)} BPD", "#2563eb")
        self._make_card(self.exec_cards_frame, "Water Injection" + self._override_suffix("water_injection_bpd"), f"{fmt_num(water_inj)} BPD", "#0ea5e9")
        self._make_card(self.exec_cards_frame, "Steam Injection" + self._override_suffix("steam_injection_bwed"), f"{fmt_num(steam_inj)} BWE/d", "#ea580c")
        self._make_card(self.exec_cards_frame, "Gas Disposal" + self._override_suffix("gas_disposal_mcfd"), f"{fmt_num(gas_disp)} MCFD", "#ef4444")

        if not HAS_MPL:
            for frame in [self.exec_field_chart_frame, self.exec_type_chart_frame, self.exec_volume_chart_frame]:
                ttk.Label(frame, text="Matplotlib is required for charts. Run: pip install matplotlib", font=("Segoe UI", 12)).pack(pady=40)
        else:
            self._draw_count_chart_by_field(rows)
            self._draw_count_chart_by_project_type(rows)
            self._draw_volume_chart_by_field(rows)

        self._draw_executive_project_table(rows)

    def _summarize_by_field(self, rows):
        data = defaultdict(lambda: {"projects": set(), "wells": 0.0})
        for r in rows:
            field = self._chart_group_for_row(r)
            wc = self._well_count_for_exec_filter(r)

            # Count the project even when wc is zero, so QC project counts match
            # the Project Metadata / QC tab. The well contribution remains zero.
            data[field]["projects"].add(r[0])
            data[field]["wells"] += wc

        output = []
        for field, vals in data.items():
            output.append((field, len(vals["projects"]), vals["wells"]))
        output.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return output

    def _summarize_by_project_type(self, rows):
        data = defaultdict(lambda: {"projects": set(), "wells": 0.0})
        for r in rows:
            ptype = self._project_type(r)
            wc = self._well_count_for_exec_filter(r)

            # Count the project even when wc is zero, so the label reflects
            # the full filtered project population.
            data[ptype]["projects"].add(r[0])
            data[ptype]["wells"] += wc

        output = []
        for ptype, vals in data.items():
            output.append((ptype, len(vals["projects"]), vals["wells"]))
        output.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return output

    def _project_type_color(self, project_type):
        colors = {
            "Steamflood": "#c0392b",
            "Waterflood": "#2c7fb8",
            "Cyclic Steam": "#e67e22",
            "Water Disposal": "#5d6d7e",
            "Dual Steam/Waterflood": "#8e44ad",
            "Gas Disposal": "#27ae60",
            "Other": "#95a5a6",
        }
        return colors.get(project_type, "#95a5a6")

    def _field_color_map(self, fields):
        palette = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173",
            "#3182bd", "#31a354", "#756bb1", "#636363", "#e6550d",
        ]
        return {field: palette[i % len(palette)] for i, field in enumerate(fields)}

    def _draw_horizontal_count_chart(self, frame, items, group_label, color_mode="field"):
        if not items:
            ttk.Label(frame, text="No data for selected filters.", font=("Segoe UI", 18)).pack(pady=40)
            return

        bar_size_by = getattr(self, "exec_bar_size_var", tk.StringVar(value="Well Count")).get()
        use_project_count = bar_size_by == "Project Count"

        # Items are tuples: (label, project_count, well_count).
        # The bars are sized by the selected metric, but the title only says what is displayed.
        if use_project_count:
            items = sorted(items, key=lambda x: (x[1], x[2]), reverse=True)
            xlabel = "Number of Projects"
            chart_title = f"UIC Project Count by {group_label}"
        else:
            items = sorted(items, key=lambda x: (x[2], x[1]), reverse=True)
            xlabel = "Number of Wells"
            chart_title = f"UIC Well Count by {group_label}"

        top_n = 15
        items = items[:top_n]
        labels = [x[0] for x in items]
        project_counts = [int(x[1]) for x in items]
        well_counts = [int(x[2]) for x in items]
        bar_values = project_counts if use_project_count else well_counts

        max_bar = max(bar_values) if bar_values else 1
        if max_bar <= 0:
            max_bar = 1

        # Larger chart/font sizing for readability.
        label_font = 14
        axis_font = 15
        title_font = 20
        value_font = 14
        tick_font = 13

        fig_height = max(6.0, min(11.0, 0.68 * len(items) + 2.2))
        fig = Figure(figsize=(12.5, fig_height), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)
        y = list(range(len(items)))

        if color_mode == "project_type":
            colors = [self._project_type_color(label) for label in labels]
        else:
            color_map = self._field_color_map(labels)
            colors = [color_map[label] for label in labels]

        ax.barh(y, bar_values, color=colors, alpha=0.95)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=label_font)
        ax.invert_yaxis()
        ax.set_xlabel(xlabel, fontsize=axis_font)
        ax.set_title(chart_title, fontsize=title_font, fontweight="bold", color=self.ACCENT, pad=14)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.tick_params(axis="x", labelsize=tick_font)
        ax.grid(axis="x", alpha=0.25, linestyle="-")
        ax.set_axisbelow(True)
        ax.set_xlim(0, max_bar * 1.48)

        for i, (label, projects, wells) in enumerate(items):
            bar_value = bar_values[i]
            if use_project_count:
                label_text = f"{int(projects):,} projects  ({int(wells):,} wells)"
            else:
                label_text = f"{int(wells):,} wells  ({int(projects):,} projects)"

            ax.text(
                bar_value + max_bar * 0.018,
                i,
                label_text,
                va="center",
                fontsize=value_font,
                color="#222",
            )

        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, frame)
        canvas.draw()
        NavigationToolbar2Tk(canvas, frame).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def _draw_count_chart_by_field(self, rows):
        items = self._summarize_by_field(rows)
        group_label = self._chart_group_label()
        self._draw_horizontal_count_chart(
            self.exec_field_chart_frame,
            items,
            group_label,
            color_mode="field",
        )

    def _draw_count_chart_by_project_type(self, rows):
        items = self._summarize_by_project_type(rows)
        self._draw_horizontal_count_chart(
            self.exec_type_chart_frame,
            items,
            "Project Type",
            color_mode="project_type",
        )

    def _volume_data_by_category_and_field(self, rows):
        categories = ["Water Disposal", "Water Injection", "Steam Injection", "Gas Disposal"]
        data = {cat: defaultdict(float) for cat in categories}

        for r in rows:
            ptype = self._project_type(r)
            field = self._chart_group_for_row(r)
            water_rate = self._num(r[14])
            steam_rate = self._num(r[16])
            gas_rate = self._num(r[17])

            if ptype == "Water Disposal" and water_rate > 0:
                data["Water Disposal"][field] += water_rate
            if ptype in ["Waterflood", "Dual Steam/Waterflood"] and water_rate > 0:
                data["Water Injection"][field] += water_rate
            if ptype in ["Steamflood", "Cyclic Steam", "Dual Steam/Waterflood"] and steam_rate > 0:
                data["Steam Injection"][field] += steam_rate
            if ptype == "Gas Disposal" and gas_rate > 0:
                data["Gas Disposal"][field] += gas_rate

        return categories, data

    def _draw_volume_chart_by_field(self, rows):
        categories, data = self._volume_data_by_category_and_field(rows)
        data = self._apply_volume_overrides_to_chart_data(categories, data)
        group_label = self._chart_group_label()

        field_totals = defaultdict(float)
        for cat in categories:
            for field, val in data[cat].items():
                field_totals[field] += val

        if not field_totals:
            ttk.Label(
                self.exec_volume_chart_frame,
                text="No recent injection/disposal volume for selected filters.",
                font=("Segoe UI", 18),
            ).pack(pady=40)
            return

        top_fields = [f for f, _ in sorted(field_totals.items(), key=lambda x: x[1], reverse=True)[:10]]
        fields_to_plot = list(top_fields)
        other_total = sum(val for field, val in field_totals.items() if field not in top_fields)
        if other_total > 0:
            fields_to_plot.append("Other")

        plot_data = {field: [0.0] * len(categories) for field in fields_to_plot}
        for i, cat in enumerate(categories):
            for field, val in data[cat].items():
                if field in top_fields:
                    plot_data[field][i] += val
                else:
                    plot_data["Other"][i] += val

        color_map = self._field_color_map(fields_to_plot)
        fig = Figure(figsize=(12.5, 7.5), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)

        x = list(range(len(categories)))
        bottom = [0.0] * len(categories)

        for field in fields_to_plot:
            vals = plot_data[field]
            ax.bar(x, vals, bottom=bottom, label=field, color=color_map[field], alpha=0.95)
            bottom = [bottom[i] + vals[i] for i in range(len(categories))]

        max_total = max(bottom) if bottom else 1
        if max_total <= 0:
            max_total = 1

        for i, total in enumerate(bottom):
            if total > 0:
                ax.text(i, total + max_total * 0.02, fmt_num(total), ha="center", va="bottom", fontsize=14, fontweight="bold")

        ax.set_xticks(x)
        ax.set_xticklabels(categories, rotation=10, ha="right", fontsize=14)
        ax.set_ylabel("6-Month Average Daily Rate", fontsize=15)
        ax.set_title(f"UIC Injection / Disposal Volume by Category and {group_label}", fontsize=20, fontweight="bold", color=self.ACCENT, pad=14)
        ax.tick_params(axis="y", labelsize=13)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: fmt_num(y)))
        ax.grid(axis="y", alpha=0.25, linestyle="--")
        ax.set_axisbelow(True)

        ax.text(
            0.01,
            0.98,
            "Water rates = BPD/BWE-d equivalent; gas disposal = MCFD. Values use six-month average daily rate. Asterisk cards use manual override.",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=12,
            color="#555",
        )

        ax.legend(title=group_label, fontsize=12, title_fontsize=14, loc="upper left", bbox_to_anchor=(1.01, 1.0), framealpha=0.9)
        fig.tight_layout(rect=[0, 0, 0.82, 1])

        canvas = FigureCanvasTkAgg(fig, self.exec_volume_chart_frame)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.exec_volume_chart_frame).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def _draw_executive_project_table(self, rows):
        fbar = ttk.Frame(self.exec_table_frame)
        fbar.pack(fill="x", padx=4, pady=(4, 5))

        ttk.Label(fbar, text=f"{len(rows)} projects after filters", style="Sub.TLabel").pack(side="left")
        ttk.Button(fbar, text="Export CSV", command=lambda: export_tree(self.exec_tree, "uic_executive_summary")).pack(side="right")

        frm = ttk.Frame(self.exec_table_frame)
        frm.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.exec_tree = self._mktree(frm)

        cols = [
            "UIC_PROJ_CDE",
            "PROJECT_TYPE",
            "UIC_PROJ_DESC",
            "FLD_NME",
            "PRODUCTION_UNIT",
            "CURRENT_STATUS",
            "SELECTED_WELL_COUNT",
            "TOTAL_WELL_COUNT",
            "INJ_WELLS",
            "PROD_WELLS",
            "OBSN_WELLS",
            "AVG_WTR_INJ_BPD_6MO",
            "AVG_STEAM_BPD_6MO",
            "AVG_GAS_MCFD_6MO",
            "LAST_ACTIVITY_MONTH",
        ]

        display_rows = []
        for r in rows:
            display_rows.append(
                (
                    r[0],
                    self._project_type(r),
                    r[1],
                    r[4],
                    self._production_unit_for_row(r),
                    r[5],
                    self._well_count_for_exec_filter(r),
                    r[8],
                    r[11],
                    r[12],
                    r[13],
                    r[14],
                    r[16],
                    r[17],
                    r[19],
                )
            )

        display_rows.sort(key=lambda x: self._num(x[6]), reverse=True)

        populate_tree(
            self.exec_tree,
            cols,
            display_rows,
            {
                "UIC_PROJ_CDE": 110,
                "PROJECT_TYPE": 150,
                "UIC_PROJ_DESC": 260,
                "FLD_NME": 130,
                "PRODUCTION_UNIT": 135,
                "CURRENT_STATUS": 130,
                "SELECTED_WELL_COUNT": 150,
                "TOTAL_WELL_COUNT": 130,
                "INJ_WELLS": 90,
                "PROD_WELLS": 90,
                "OBSN_WELLS": 90,
                "AVG_WTR_INJ_BPD_6MO": 160,
                "AVG_STEAM_BPD_6MO": 150,
                "AVG_GAS_MCFD_6MO": 150,
                "LAST_ACTIVITY_MONTH": 130,
            },
        )


    # -------------------------------------------------------------------------
    # Project Metadata tab for QC
    # -------------------------------------------------------------------------
    def _build_project_metadata_tab(self):
        for w in self.tab_meta.winfo_children():
            w.destroy()

        old_status = "ALL"
        old_project_type = "ALL"
        old_search = ""
        try:
            old_status = self.meta_status_var.get()
        except Exception:
            pass
        try:
            old_project_type = self.meta_project_type_var.get()
        except Exception:
            pass
        try:
            old_search = self.meta_search_var.get()
        except Exception:
            pass

        top = ttk.Frame(self.tab_meta, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Project Metadata / QC", style="Header.TLabel").pack(side="left")
        ttk.Button(top, text="Refresh", command=self._refresh_manager_tabs).pack(side="right")

        filter_bar = ttk.Frame(self.tab_meta)
        filter_bar.pack(fill="x", padx=8, pady=(0, 6))

        ttk.Label(filter_bar, text="Search:").pack(side="left")
        self.meta_search_var = tk.StringVar(value=old_search)
        self.meta_search_var.trace_add("write", lambda *a: self._filter_project_metadata_table())
        ttk.Entry(filter_bar, textvariable=self.meta_search_var, width=24).pack(side="left", padx=(5, 14))

        ttk.Label(filter_bar, text="Project Status:").pack(side="left")
        exact_statuses = sorted(set(str(r[5] or "") for r in self.manager_rows if r[5]))
        status_values = ["ALL", "ACTIVE", "SUSPENDED", "OTHER"] + [f"STATUS: {s}" for s in exact_statuses]
        if old_status not in status_values:
            old_status = "ALL"
        self.meta_status_var = tk.StringVar(value=old_status)
        status_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.meta_status_var,
            values=status_values,
            width=26,
            state="readonly",
        )
        status_cb.pack(side="left", padx=(5, 14))
        status_cb.bind("<<ComboboxSelected>>", lambda e: self._filter_project_metadata_table())

        ttk.Label(filter_bar, text="Project Type:").pack(side="left")
        project_type_values = self._available_project_type_values()
        if old_project_type not in project_type_values:
            old_project_type = "ALL"
        self.meta_project_type_var = tk.StringVar(value=old_project_type)
        type_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.meta_project_type_var,
            values=project_type_values,
            width=24,
            state="readonly",
        )
        type_cb.pack(side="left", padx=(5, 14))
        type_cb.bind("<<ComboboxSelected>>", lambda e: self._filter_project_metadata_table())

        ttk.Button(
            filter_bar,
            text="Export CSV",
            command=lambda: export_tree(self.meta_tree, "uic_project_metadata_qc"),
        ).pack(side="right")

        self.meta_cards_frame = ttk.Frame(self.tab_meta)
        self.meta_cards_frame.pack(fill="x", padx=8, pady=(0, 6))

        note = ttk.Label(
            self.tab_meta,
            text=(
                "Use STATUS_GROUP to QC the active/suspended count. The group is derived from the status text: "
                "ACTIVE includes ACTIVE/APPROVED/OPERATING/AUTHORIZED; SUSPENDED includes SUSP/SHUT/INACTIVE/RESCIND/CANCEL. "
                "Six-month volumes use current month plus previous five months from monthly allocation data. Production Unit is user-assigned in Settings / Overrides."
            ),
            style="Sub.TLabel",
        )
        note.pack(fill="x", padx=10, pady=(0, 4))

        frm = ttk.Frame(self.tab_meta)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.meta_tree = self._mktree(frm)

        self._filter_project_metadata_table()

    def _metadata_filtered_rows(self):
        term = getattr(self, "meta_search_var", tk.StringVar(value="")).get().lower()
        status_filter = getattr(self, "meta_status_var", tk.StringVar(value="ALL")).get()
        project_type_filter = getattr(self, "meta_project_type_var", tk.StringVar(value="ALL")).get()

        rows = []
        for r in self.manager_rows:
            if not self._status_matches_filter(r[5], status_filter):
                continue
            if not self._project_type_matches_filter(r, project_type_filter):
                continue
            haystack_values = [
                r[0], self._project_type(r), r[1], r[2], r[3], r[4], self._production_unit_for_row(r), r[5], self._status_group(r[5])
            ]
            haystack = " ".join(str(v or "").lower() for v in haystack_values)
            if term and term not in haystack:
                continue
            rows.append(r)
        rows.sort(key=lambda r: (self._status_group(r[5]), self._project_type(r), str(r[4] or ""), str(r[0] or "")))
        return rows

    def _metadata_cols(self):
        return [
            "UIC_PROJ_CDE",
            "PROJECT_TYPE",
            "STATUS_GROUP",
            "CURRENT_STATUS",
            "UIC_PROJ_DESC",
            "FLD_NME",
            "PRODUCTION_UNIT",
            "FLUID_TYPE_DESC",
            "RCVY_TYPE_DESC",
            "MAX_WELL_CNT",
            "MAX_BPD_INJ_VOL",
            "TOTAL_WELLS",
            "ACTIVE_WELLS",
            "IN_SERVICE_WELLS",
            "INJ_WELLS",
            "PROD_WELLS",
            "OBSN_WELLS",
            "RAW_AVG_WTR_INJ_BPD_6MO",
            "RAW_TOTAL_WTR_INJ_BBL_6MO",
            "WATER_DISP_BPD_6MO",
            "WATER_DISP_BBL_6MO",
            "WATERFLOOD_WTR_INJ_BPD_6MO",
            "WATERFLOOD_WTR_INJ_BBL_6MO",
            "AVG_STEAM_INJ_BPD_6MO",
            "TOTAL_STEAM_INJ_BWE_6MO",
            "GAS_DISP_MCFD_6MO",
            "GAS_DISP_MCF_6MO",
            "AVG_OIL_BOPD_6MO",
            "LAST_ACTIVITY_MONTH",
        ]

    def _metadata_col_widths(self):
        return {
            "UIC_PROJ_CDE": 110,
            "PROJECT_TYPE": 150,
            "STATUS_GROUP": 105,
            "CURRENT_STATUS": 135,
            "UIC_PROJ_DESC": 260,
            "FLD_NME": 130,
            "PRODUCTION_UNIT": 135,
            "FLUID_TYPE_DESC": 120,
            "RCVY_TYPE_DESC": 145,
            "MAX_WELL_CNT": 105,
            "MAX_BPD_INJ_VOL": 125,
            "TOTAL_WELLS": 105,
            "ACTIVE_WELLS": 105,
            "IN_SERVICE_WELLS": 130,
            "INJ_WELLS": 90,
            "PROD_WELLS": 90,
            "OBSN_WELLS": 90,
            "RAW_AVG_WTR_INJ_BPD_6MO": 190,
            "RAW_TOTAL_WTR_INJ_BBL_6MO": 200,
            "WATER_DISP_BPD_6MO": 160,
            "WATER_DISP_BBL_6MO": 165,
            "WATERFLOOD_WTR_INJ_BPD_6MO": 220,
            "WATERFLOOD_WTR_INJ_BBL_6MO": 225,
            "AVG_STEAM_INJ_BPD_6MO": 175,
            "TOTAL_STEAM_INJ_BWE_6MO": 190,
            "GAS_DISP_MCFD_6MO": 165,
            "GAS_DISP_MCF_6MO": 160,
            "AVG_OIL_BOPD_6MO": 145,
            "LAST_ACTIVITY_MONTH": 135,
        }

    def _metadata_display_rows(self, rows):
        display_rows = []
        for r in rows:
            ptype = self._project_type(r)
            raw_wtr_bpd = self._num(r[14])
            raw_wtr_bbl = self._num(r[15])
            steam_bpd = self._num(r[16])
            gas_mcf_d = self._num(r[17])
            oil_bopd = self._num(r[18])
            last_activity = r[19]
            steam_bbl = self._num(self._val(r, 20, 0))
            gas_vol = self._num(self._val(r, 21, 0))

            water_disp_bpd = raw_wtr_bpd if ptype == "Water Disposal" else 0
            water_disp_bbl = raw_wtr_bbl if ptype == "Water Disposal" else 0
            waterflood_bpd = raw_wtr_bpd if ptype in ["Waterflood", "Dual Steam/Waterflood"] else 0
            waterflood_bbl = raw_wtr_bbl if ptype in ["Waterflood", "Dual Steam/Waterflood"] else 0
            gas_disp_mcf_d = gas_mcf_d if ptype == "Gas Disposal" else 0
            gas_disp_vol = gas_vol if ptype == "Gas Disposal" else 0

            display_rows.append((
                r[0],
                ptype,
                self._status_group(r[5]),
                r[5],
                r[1],
                r[4],
                self._production_unit_for_row(r),
                r[2],
                r[3],
                r[6],
                r[7],
                r[8],
                r[9],
                r[10],
                r[11],
                r[12],
                r[13],
                raw_wtr_bpd,
                raw_wtr_bbl,
                water_disp_bpd,
                water_disp_bbl,
                waterflood_bpd,
                waterflood_bbl,
                steam_bpd,
                steam_bbl,
                gas_disp_mcf_d,
                gas_disp_vol,
                oil_bopd,
                last_activity,
            ))
        return display_rows

    def _filter_project_metadata_table(self):
        rows = self._metadata_filtered_rows()

        for w in self.meta_cards_frame.winfo_children():
            w.destroy()

        active_projects = sum(1 for r in rows if self._is_active_status(r[5]))
        suspended_projects = sum(1 for r in rows if self._is_suspended_status(r[5]))
        other_projects = len(rows) - active_projects - suspended_projects
        total_wells = sum(self._num(r[8]) for r in rows)
        water_disp_bpd = sum(self._num(r[14]) for r in rows if self._project_type(r) == "Water Disposal")
        steam_bbl = sum(self._num(self._val(r, 20, 0)) for r in rows)

        self._make_card(self.meta_cards_frame, "Filtered Projects", f"{len(rows):,}", self.ACCENT)
        self._make_card(self.meta_cards_frame, "Active by Rule", f"{active_projects:,}", "#059669")
        self._make_card(self.meta_cards_frame, "Suspended by Rule", f"{suspended_projects:,}", "#dc2626")
        self._make_card(self.meta_cards_frame, "Other Status", f"{other_projects:,}", "#6b7280")
        self._make_card(self.meta_cards_frame, "Total Wells", f"{int(total_wells):,}", "#7c3aed")
        self._make_card(self.meta_cards_frame, "Water Disposal", f"{fmt_num(water_disp_bpd)} BPD", "#2563eb")
        self._make_card(self.meta_cards_frame, "Steam Vol 6-Mo", f"{fmt_num(steam_bbl)} BWE", "#ea580c")

        populate_tree(
            self.meta_tree,
            self._metadata_cols(),
            self._metadata_display_rows(rows),
            self._metadata_col_widths(),
        )

    def _water_disposal_cols(self):
        return [
            "UIC_PROJ_CDE",
            "UIC_PROJ_DESC",
            "FLD_NME",
            "CURRENT_STATUS",
            "WELL_COUNT",
            "IN_SERVICE_WELLS",
            "INJ_WELLS",
            "AVG_WTR_DISP_BPD_6MO",
            "TOTAL_WTR_DISP_BBL_6MO",
            "LAST_ACTIVITY_MONTH",
        ]

    def _water_disposal_col_widths(self):
        return {
            "UIC_PROJ_CDE": 110,
            "UIC_PROJ_DESC": 280,
            "FLD_NME": 130,
            "CURRENT_STATUS": 130,
            "WELL_COUNT": 90,
            "IN_SERVICE_WELLS": 120,
            "INJ_WELLS": 90,
            "AVG_WTR_DISP_BPD_6MO": 170,
            "TOTAL_WTR_DISP_BBL_6MO": 180,
            "LAST_ACTIVITY_MONTH": 130,
        }

    def _water_disposal_display_rows(self, rows):
        display_rows = []
        for r in rows:
            display_rows.append((r[0], r[1], r[4], r[5], r[8], r[10], r[11], r[14], r[15], r[19]))
        return display_rows

    def _build_water_disposal_tab(self):
        for w in self.tab_wtr_disp.winfo_children():
            w.destroy()

        rows = [r for r in self.manager_rows if self._is_water_disposal_project(r)]
        rows = sorted(rows, key=lambda r: self._num(r[14]), reverse=True)

        top = ttk.Frame(self.tab_wtr_disp, padding=(8, 6))
        top.pack(fill="x")

        ttk.Label(top, text="Water Disposal Project Dashboard", style="Header.TLabel").pack(side="left")
        ttk.Button(top, text="Refresh", command=self._refresh_manager_tabs).pack(side="right")

        cards = ttk.Frame(self.tab_wtr_disp)
        cards.pack(fill="x", padx=8, pady=(0, 6))

        active_projects = sum(1 for r in rows if self._is_active_status(r[5]))
        suspended_projects = sum(1 for r in rows if self._is_suspended_status(r[5]))
        total_avg_wtr_bpd = sum(self._num(r[14]) for r in rows)
        total_wtr_bbl_6mo = sum(self._num(r[15]) for r in rows)
        active_wells = sum(self._num(r[9]) for r in rows)
        in_service_wells = sum(self._num(r[10]) for r in rows)

        self._make_card(cards, "Water Disposal Projects", f"{len(rows):,}", self.ACCENT)
        self._make_card(cards, "Active Projects", f"{active_projects:,}", "#059669")
        self._make_card(cards, "Suspended Projects", f"{suspended_projects:,}", "#dc2626")
        self._make_card(cards, "6-Mo Avg Disposal", f"{fmt_num(total_avg_wtr_bpd)} BPD", "#d97706")
        self._make_card(cards, "6-Mo Total Disposed", f"{fmt_num(total_wtr_bbl_6mo)} bbl", "#ea580c")
        self._make_card(cards, "Active Wells", f"{int(active_wells):,}", "#7c3aed")
        self._make_card(cards, "In-Service Wells", f"{int(in_service_wells):,}", "#2563eb")

        ttk.Label(
            self.tab_wtr_disp,
            text=(
                "Water disposal projects are identified from project description, fluid type, and recovery type. "
                "Table is sorted by six-month average water disposal BPD."
            ),
            style="Sub.TLabel",
        ).pack(fill="x", padx=10, pady=(0, 4))

        fbar = ttk.Frame(self.tab_wtr_disp)
        fbar.pack(fill="x", padx=8, pady=(0, 5))

        ttk.Label(fbar, text="Search:").pack(side="left")
        self.wtr_disp_search_var = tk.StringVar()
        self.wtr_disp_search_var.trace_add("write", lambda *a: self._filter_water_disposal_table())
        ttk.Entry(fbar, textvariable=self.wtr_disp_search_var, width=28).pack(side="left", padx=5)

        ttk.Label(fbar, text="Status:").pack(side="left", padx=(12, 0))
        self.wtr_disp_status_var = tk.StringVar(value="ALL")
        status_cb = ttk.Combobox(
            fbar,
            textvariable=self.wtr_disp_status_var,
            values=["ALL", "ACTIVE", "SUSPENDED"],
            width=16,
            state="readonly",
        )
        status_cb.pack(side="left", padx=5)
        status_cb.bind("<<ComboboxSelected>>", lambda e: self._filter_water_disposal_table())

        ttk.Button(fbar, text="Export CSV", command=lambda: export_tree(self.wtr_disp_tree, "uic_water_disposal_projects")).pack(side="right")

        self.wtr_disp_count_lbl = ttk.Label(fbar, text=f"{len(rows)} water disposal projects", style="Sub.TLabel")
        self.wtr_disp_count_lbl.pack(side="right", padx=10)

        frm = ttk.Frame(self.tab_wtr_disp)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.wtr_disp_tree = self._mktree(frm)

        populate_tree(
            self.wtr_disp_tree,
            self._water_disposal_cols(),
            self._water_disposal_display_rows(rows),
            self._water_disposal_col_widths(),
        )

    def _filter_water_disposal_table(self):
        term = self.wtr_disp_search_var.get().lower()
        status_filter = self.wtr_disp_status_var.get()
        rows = [r for r in self.manager_rows if self._is_water_disposal_project(r)]

        filtered = []
        for r in rows:
            haystack = " ".join(str(v or "").lower() for v in r)
            if term and term not in haystack:
                continue
            if status_filter == "ACTIVE" and not self._is_active_status(r[5]):
                continue
            if status_filter == "SUSPENDED" and not self._is_suspended_status(r[5]):
                continue
            filtered.append(r)

        filtered = sorted(filtered, key=lambda r: self._num(r[14]), reverse=True)

        populate_tree(
            self.wtr_disp_tree,
            self._water_disposal_cols(),
            self._water_disposal_display_rows(filtered),
            self._water_disposal_col_widths(),
        )
        self.wtr_disp_count_lbl.config(text=f"{len(filtered)} water disposal projects")

    def _refresh_manager_tabs(self):
        self._set_status("Refreshing executive dashboard ...")

        def bg():
            try:
                mcols, mrows = run_query(SQL_MANAGER_PROJECTS)
                self.manager_cols = mcols
                self.manager_rows = mrows
                self.root.after(0, self._ensure_field_unit_map)
                self.root.after(0, self._build_executive_summary_tab)
                self.root.after(0, self._build_project_metadata_tab)
                self.root.after(0, self._build_settings_tab)
                self.root.after(0, self._build_water_disposal_tab)
                self.root.after(0, lambda: self._set_status("Executive dashboard refreshed."))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Refresh Error", str(e)))
                self.root.after(0, lambda: self._set_status("Executive dashboard refresh failed."))

        threading.Thread(target=bg, daemon=True).start()

    # -------------------------------------------------------------------------
    # Load selected project/well-level data
    # -------------------------------------------------------------------------
    def _on_load(self):
        codes = self._get_selected_codes()
        if not codes:
            messagebox.showwarning("No Selection", "Select at least one project.")
            return

        self.selected_codes = codes
        self.load_btn.config(state="disabled")
        self._set_status(f"Loading data for {len(codes)} project(s) ...")
        threading.Thread(target=self._load_bg, args=(codes,), daemon=True).start()

    def _load_bg(self, codes):
        try:
            self.root.after(0, lambda: self._set_status("Querying wells ..."))
            wc, wr = run_query(sql_wells(codes))
            self.well_cols = wc
            self.well_rows = wr

            self.root.after(0, lambda: self._set_status("Querying per-well production and injection ..."))
            pc, pr = run_query(sql_production_by_well(codes))
            self.prod_well_cols = pc
            self.prod_well_rows = pr

            seen = set()
            wl = []
            for r in pr:
                key = (r[0], r[1])
                if key not in seen:
                    seen.add(key)
                    wl.append(key)
            wl.sort()
            self.well_list_for_charts = wl

            self.root.after(0, self._display_results)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Query Error", str(e)))
            self.root.after(0, lambda: self._set_status("Query failed."))
        finally:
            self.root.after(0, lambda: self.load_btn.config(state="normal"))

    def _on_load_by_api(self):
        raw = self.api_text.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning("No APIs", "Paste at least one API number into the text box.")
            return

        tokens = re.split(r"[,\s\n\r\t]+", raw)
        apis = [t.strip() for t in tokens if t.strip()]

        if not apis:
            messagebox.showwarning("No APIs", "Could not parse any API numbers from the input.")
            return

        cleaned = []
        for a in apis:
            a = a.replace("-", "").replace(".", "")
            if a.isdigit():
                if len(a) == 9:
                    a = "0" + a
                cleaned.append(a)
            else:
                cleaned.append(a)

        self.selected_codes = ["MANUAL"]
        self.api_load_btn.config(state="disabled")
        self.load_btn.config(state="disabled")
        self._set_status(f"Loading data for {len(cleaned)} manually entered API(s) ...")
        threading.Thread(target=self._load_by_api_bg, args=(cleaned,), daemon=True).start()

    def _load_by_api_bg(self, apis):
        try:
            self.root.after(0, lambda: self._set_status(f"Querying well details for {len(apis)} API(s) ..."))
            wc, wr = run_query(sql_wells_by_api(apis))
            self.well_cols = wc
            self.well_rows = wr

            self.root.after(0, lambda: self._set_status(f"Querying per-well production and injection for {len(apis)} API(s) ..."))
            pc, pr = run_query(sql_production_by_well_api(apis))
            self.prod_well_cols = pc
            self.prod_well_rows = pr

            seen = set()
            wl = []
            for r in pr:
                key = (r[0], r[1])
                if key not in seen:
                    seen.add(key)
                    wl.append(key)
            wl.sort()
            self.well_list_for_charts = wl

            self.root.after(0, self._display_results)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Query Error", str(e)))
            self.root.after(0, lambda: self._set_status("Query failed."))
        finally:
            self.root.after(0, lambda: self.api_load_btn.config(state="normal"))
            self.root.after(0, lambda: self.load_btn.config(state="normal"))

    def _display_results(self):
        self._update_cards()
        self._build_well_table()

        if HAS_MPL:
            for fn in [self._build_inj_tab, self._build_prod_tab, self._build_cum_tab, self._build_map]:
                try:
                    fn()
                except Exception as e:
                    print(f"Error building {fn.__name__}: {e}")

        codes_str = ", ".join(self.selected_codes[:5])
        if len(self.selected_codes) > 5:
            codes_str += f" +{len(self.selected_codes) - 5} more"
        self._set_status(f"Loaded {len(self.well_rows)} wells, {len(self.prod_well_rows)} well-months | {codes_str}")

    # -------------------------------------------------------------------------
    # Well-level data aggregation
    # -------------------------------------------------------------------------
    def _aggregate_data(self, selected_wells):
        """
        Aggregate prod_well_rows for the given well names.

        Query columns:
          0 WELL_NME, 1 API, 2 MONTH_DT,
          3 OIL_VOL, 4 WATER_VOL, 5 GAS_VOL, 6 GROSS_VOL,
          7 STEAM_INJ_VOL, 8 WATER_INJ_VOL, 9 GAS_INJ_VOL,
          10 OIL_RATE, 11 WATER_RATE, 12 GAS_RATE, 13 GROSS_RATE,
          14 STEAM_INJ_RATE, 15 WATER_INJ_RATE, 16 GAS_INJ_RATE

        Returns:
          (MONTH_DT, OIL_VOL, WATER_VOL, GAS_VOL, GROSS_VOL,
           STEAM_INJ_VOL, WATER_INJ_VOL, GAS_INJ_VOL,
           OIL_RATE, WATER_RATE, GAS_RATE, GROSS_RATE,
           STEAM_INJ_RATE, WATER_INJ_RATE, GAS_INJ_RATE)
        """
        num_val_cols = 14
        monthly = defaultdict(lambda: [0.0] * num_val_cols)

        for r in self.prod_well_rows:
            wn = r[0]
            if selected_wells is not None and wn not in selected_wells:
                continue
            dt = r[2]
            for i in range(num_val_cols):
                monthly[dt][i] += float(r[3 + i] or 0)

        result = []
        for dt in sorted(monthly.keys()):
            result.append((dt,) + tuple(monthly[dt]))
        return result

    # -------------------------------------------------------------------------
    # Well-level summary cards
    # -------------------------------------------------------------------------
    def _update_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()

        total = len(self.well_rows)
        prods = sum(1 for r in self.well_rows if r[3] == "PROD")
        injs = sum(1 for r in self.well_rows if r[3] == "INJ")
        insvc = sum(1 for r in self.well_rows if r[6] == "Y")
        flds = len(set(r[2] for r in self.well_rows if r[2]))

        for label, val, clr in [
            ("Loaded Wells", total, self.ACCENT),
            ("Producers", prods, "#059669"),
            ("Injectors", injs, "#d97706"),
            ("In Service", insvc, "#22c55e"),
            ("Fields", flds, "#7c3aed"),
        ]:
            c = tk.Frame(self.cards_frame, bg="white", bd=1, relief="solid")
            c.pack(side="left", padx=4, pady=2, fill="x", expand=True)
            tk.Label(c, text=label, font=("Segoe UI", 9), fg="#6b7280", bg="white").pack(
                anchor="w", padx=10, pady=(6, 0)
            )
            tk.Label(c, text=str(val), font=("Segoe UI", 20, "bold"), fg=clr, bg="white").pack(
                anchor="w", padx=10, pady=(0, 6)
            )

    # -------------------------------------------------------------------------
    # Tab 3: Well List
    # -------------------------------------------------------------------------
    def _build_well_table(self):
        for w in self.tab_wells.winfo_children():
            w.destroy()

        fbar = ttk.Frame(self.tab_wells)
        fbar.pack(fill="x", pady=(5, 5), padx=8)

        ttk.Label(fbar, text="Search:").pack(side="left")
        self.wsv = tk.StringVar()
        self.wsv.trace_add("write", self._filt_wt)
        ttk.Entry(fbar, textvariable=self.wsv, width=20).pack(side="left", padx=5)

        ttk.Label(fbar, text="Purpose:").pack(side="left", padx=(10, 0))
        self.wpv = tk.StringVar(value="ALL")
        cb = ttk.Combobox(fbar, textvariable=self.wpv, values=["ALL", "PROD", "INJ", "OBSN"], width=8, state="readonly")
        cb.pack(side="left", padx=5)
        cb.bind("<<ComboboxSelected>>", self._filt_wt)

        ttk.Button(fbar, text="Export CSV", command=lambda: export_tree(self.wt, "uic_wells")).pack(side="right")
        self.wlbl = ttk.Label(fbar, text=f"{len(self.well_rows)} wells", style="Sub.TLabel")
        self.wlbl.pack(side="right", padx=10)

        frm = ttk.Frame(self.tab_wells)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.wt = self._mktree(frm)
        dc = self.well_cols[:8]
        dr = [r[:8] for r in self.well_rows]
        populate_tree(
            self.wt,
            dc,
            dr,
            {
                "WELL_NME": 150,
                "WELL_API_NBR": 110,
                "FLD_NME": 100,
                "PRIM_PURP_TYPE_CDE": 80,
                "ENGR_STRG_NME": 140,
                "ACTV_INDC": 55,
                "IN_SVC_INDC": 55,
                "UIC_PROJ_CDE": 110,
            },
        )

    def _filt_wt(self, *a):
        term = self.wsv.get().lower()
        purp = self.wpv.get()
        dc = self.well_cols[:8]
        filtered = []

        for r in self.well_rows:
            if purp != "ALL" and str(r[3]) != purp:
                continue
            if term and term not in " ".join(str(v or "").lower() for v in r[:8]):
                continue
            filtered.append(r[:8])

        populate_tree(
            self.wt,
            dc,
            filtered,
            {
                "WELL_NME": 150,
                "WELL_API_NBR": 110,
                "FLD_NME": 100,
                "PRIM_PURP_TYPE_CDE": 80,
                "ENGR_STRG_NME": 140,
                "ACTV_INDC": 55,
                "IN_SVC_INDC": 55,
                "UIC_PROJ_CDE": 110,
            },
        )
        self.wlbl.config(text=f"{len(filtered)} wells")

    # -------------------------------------------------------------------------
    # Shared well selector panel
    # -------------------------------------------------------------------------
    def _make_well_selector(self, parent, on_change_callback):
        panel = tk.Frame(parent, bg=self.PANEL, bd=0, highlightbackground=self.BORDER, highlightthickness=1)
        panel.pack(side="left", fill="y", padx=(0, 6), pady=0)

        tk.Label(panel, text="WELLS", font=("Segoe UI", 9, "bold"), fg=self.ACCENT, bg=self.PANEL).pack(
            anchor="w", padx=8, pady=(6, 2)
        )

        count_lbl = tk.Label(panel, text=f"{len(self.well_list_for_charts)} wells", font=("Segoe UI", 8), fg="#888", bg=self.PANEL)
        count_lbl.pack(anchor="w", padx=8)

        lb_frame = tk.Frame(panel, bg=self.PANEL)
        lb_frame.pack(fill="both", expand=True, padx=6, pady=(4, 6))

        lb_sb = ttk.Scrollbar(lb_frame, orient="vertical")
        lb = tk.Listbox(
            lb_frame,
            selectmode="browse",
            width=28,
            exportselection=False,
            font=("Consolas", 8),
            bg="white",
            fg="#333",
            selectbackground="#d4e6f1",
            selectforeground="#1a5276",
            bd=1,
            relief="solid",
            highlightthickness=0,
            activestyle="none",
            yscrollcommand=lb_sb.set,
        )
        lb_sb.config(command=lb.yview)
        lb.pack(side="left", fill="both", expand=True)
        lb_sb.pack(side="right", fill="y")

        lb.insert("end", "** ALL WELLS **")
        for wn, api in self.well_list_for_charts:
            lb.insert("end", f"{wn}  ({api})")

        lb.selection_set(0)
        lb.bind("<<ListboxSelect>>", lambda e: on_change_callback())
        return lb

    def _get_selected_well_set(self, lb):
        sel = lb.curselection()
        if not sel or sel[0] == 0:
            return None
        idx = sel[0] - 1
        if 0 <= idx < len(self.well_list_for_charts):
            return {self.well_list_for_charts[idx][0]}
        return None

    def _get_chart_title_suffix(self, lb):
        sel = lb.curselection()
        if not sel or sel[0] == 0:
            return "(All Wells)"
        idx = sel[0] - 1
        if 0 <= idx < len(self.well_list_for_charts):
            return f"({self.well_list_for_charts[idx][0]})"
        return "(All Wells)"

    # -------------------------------------------------------------------------
    # Tab 4: Injection
    # -------------------------------------------------------------------------
    def _build_inj_tab(self):
        for w in self.tab_inj.winfo_children():
            w.destroy()

        if not self.prod_well_rows:
            ttk.Label(self.tab_inj, text="No data.", font=("Segoe UI", 12)).pack(pady=40)
            return

        outer = ttk.Frame(self.tab_inj)
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        self.inj_lb = self._make_well_selector(outer, self._refresh_inj_chart)
        self.inj_chart_frame = tk.Frame(outer, bg=self.PANEL)
        self.inj_chart_frame.pack(side="left", fill="both", expand=True)
        self._refresh_inj_chart()

    def _refresh_inj_chart(self):
        for w in self.inj_chart_frame.winfo_children():
            w.destroy()

        wells = self._get_selected_well_set(self.inj_lb)
        data = self._aggregate_data(wells)

        if not data:
            tk.Label(self.inj_chart_frame, text="No data for selection.", bg=self.PANEL).pack(pady=40)
            return

        suffix = self._get_chart_title_suffix(self.inj_lb)
        dates = [r[0] for r in data]
        stm = [r[12] for r in data]
        winj = [r[13] for r in data]
        ginj = [r[14] for r in data]
        wprod = [r[9] for r in data]

        fig = Figure(figsize=(10, 5), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)
        lines = []

        if any(v > 0 for v in stm):
            l, = ax.plot(dates, stm, color=COLORS["steam_inj"], lw=1.8, label="Steam Inj (BWE/d)")
            lines.append(l)
        if any(v > 0 for v in winj):
            l, = ax.plot(dates, winj, color=COLORS["water_inj"], lw=1.8, label="Water Inj / Disposal (BWPD)")
            lines.append(l)
        if any(v > 0 for v in ginj):
            l, = ax.plot(dates, ginj, color=COLORS["gas_inj"], lw=1.5, linestyle="--", label="Gas Inj (MCFD)")
            lines.append(l)

        ax.set_ylabel("Avg Daily Injection Rate", fontsize=10, color=self.ACCENT)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: fmt_num(x)))

        if any(v > 0 for v in wprod):
            ax2 = ax.twinx()
            l, = ax2.plot(dates, wprod, color=COLORS["water_prod"], lw=1.5, linestyle="-.", alpha=0.8, label="Water Prod (BWPD)")
            lines.append(l)
            ax2.set_ylabel("Water Prod (BWPD)", fontsize=10, color=COLORS["water_prod"])
            ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: fmt_num(x)))
            ax2.tick_params(labelsize=8, labelcolor=COLORS["water_prod"])

        ax.set_title(f"Avg Daily Injection / Disposal Rates {suffix}", fontsize=11, fontweight="bold", color=self.ACCENT, pad=10)
        if lines:
            ax.legend(lines, [l.get_label() for l in lines], fontsize=8, loc="upper left", framealpha=0.9)

        self._fmt_x(ax, dates)
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.set_axisbelow(True)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.inj_chart_frame)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.inj_chart_frame).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # -------------------------------------------------------------------------
    # Tab 5: Production
    # -------------------------------------------------------------------------
    def _build_prod_tab(self):
        for w in self.tab_prod.winfo_children():
            w.destroy()

        if not self.prod_well_rows:
            ttk.Label(self.tab_prod, text="No data.", font=("Segoe UI", 12)).pack(pady=40)
            return

        outer = ttk.Frame(self.tab_prod)
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        self.prod_lb = self._make_well_selector(outer, self._refresh_prod_chart)
        self.prod_chart_frame = tk.Frame(outer, bg=self.PANEL)
        self.prod_chart_frame.pack(side="left", fill="both", expand=True)
        self._refresh_prod_chart()

    def _refresh_prod_chart(self):
        for w in self.prod_chart_frame.winfo_children():
            w.destroy()

        wells = self._get_selected_well_set(self.prod_lb)
        data = self._aggregate_data(wells)

        if not data:
            tk.Label(self.prod_chart_frame, text="No data for selection.", bg=self.PANEL).pack(pady=40)
            return

        suffix = self._get_chart_title_suffix(self.prod_lb)
        dates = [r[0] for r in data]
        oil = [r[8] for r in data]
        gas = [r[10] for r in data]

        fig = Figure(figsize=(10, 5), dpi=100, facecolor="white")
        ax1 = fig.add_subplot(111)
        lines = []

        if any(v > 0 for v in oil):
            ax1.fill_between(dates, oil, alpha=0.10, color=COLORS["oil"])
            l, = ax1.plot(dates, oil, color=COLORS["oil"], lw=2.2, label="Oil (BOPD)")
            lines.append(l)

        ax1.set_ylabel("Oil Production (BOPD)", fontsize=10, color=COLORS["oil"])
        ax1.tick_params(axis="y", labelsize=9, labelcolor=COLORS["oil"])
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: fmt_num(x)))

        if any(v > 0 for v in gas):
            ax2 = ax1.twinx()
            l, = ax2.plot(dates, gas, color=COLORS["gas_prod"], lw=1.5, linestyle="--", label="Gas (MCFD)")
            lines.append(l)
            ax2.set_ylabel("Gas Production (MCFD)", fontsize=10, color=COLORS["gas_prod"])
            ax2.tick_params(axis="y", labelsize=9, labelcolor=COLORS["gas_prod"])
            ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: fmt_num(x)))

        ax1.set_title(f"Avg Daily Oil and Gas Production {suffix}", fontsize=11, fontweight="bold", color=self.ACCENT, pad=10)
        if lines:
            ax1.legend(lines, [l.get_label() for l in lines], fontsize=9, loc="upper right", framealpha=0.9)

        self._fmt_x(ax1, dates)
        ax1.grid(axis="y", alpha=0.3, linestyle="--")
        ax1.set_axisbelow(True)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.prod_chart_frame)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.prod_chart_frame).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # -------------------------------------------------------------------------
    # Tab 6: Cumulative
    # -------------------------------------------------------------------------
    def _build_cum_tab(self):
        for w in self.tab_cum.winfo_children():
            w.destroy()

        if not self.prod_well_rows:
            ttk.Label(self.tab_cum, text="No data.", font=("Segoe UI", 12)).pack(pady=40)
            return

        outer = ttk.Frame(self.tab_cum)
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        self.cum_lb = self._make_well_selector(outer, self._refresh_cum_chart)
        self.cum_chart_frame = tk.Frame(outer, bg=self.PANEL)
        self.cum_chart_frame.pack(side="left", fill="both", expand=True)
        self._refresh_cum_chart()

    def _refresh_cum_chart(self):
        for w in self.cum_chart_frame.winfo_children():
            w.destroy()

        wells = self._get_selected_well_set(self.cum_lb)
        data = self._aggregate_data(wells)

        if not data:
            tk.Label(self.cum_chart_frame, text="No data for selection.", bg=self.PANEL).pack(pady=40)
            return

        suffix = self._get_chart_title_suffix(self.cum_lb)
        import numpy as np

        dates = [r[0] for r in data]
        oil = [r[1] for r in data]
        water = [r[2] for r in data]
        gas = [r[3] for r in data]
        steam = [r[5] for r in data]
        winj = [r[6] for r in data]
        ginj = [r[7] for r in data]

        co = np.cumsum(oil)
        cw = np.cumsum(water)
        cg = np.cumsum(gas)
        cs = np.cumsum(steam)
        cwi = np.cumsum(winj)
        cgi = np.cumsum(ginj)

        cf = tk.Frame(self.cum_chart_frame, bg=self.PANEL)
        cf.pack(fill="both", expand=True)

        fig = Figure(figsize=(10, 3.5), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)
        ax.plot(dates, co, color=COLORS["cum_oil"], lw=2.5, label="Cum Oil")
        ax.plot(dates, cw, color=COLORS["cum_water"], lw=1.8, label="Cum Produced Water")
        ax.plot(dates, cs, color=COLORS["cum_steam"], lw=2, linestyle="--", label="Cum Steam Inj")
        ax.plot(dates, cwi, color=COLORS["cum_winj"], lw=1.5, linestyle="--", label="Cum Water Inj / Disposal")
        ax.plot(dates, cgi, color=COLORS["cum_ginj"], lw=1.5, linestyle=":", label="Cum Gas Inj")
        ax.set_title(f"Cumulative {suffix}", fontsize=11, fontweight="bold", color=self.ACCENT, pad=8)
        ax.set_ylabel("Cumulative volume", fontsize=9)
        ax.legend(fontsize=7, loc="upper left", framealpha=0.9)

        self._fmt_x(ax, dates)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: fmt_num(x)))
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.set_axisbelow(True)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, cf)
        canvas.draw()
        NavigationToolbar2Tk(canvas, cf).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        tk.Label(
            self.cum_chart_frame,
            text="Cumulative Summary",
            font=("Segoe UI", 9, "bold"),
            bg=self.PANEL,
            fg=self.ACCENT,
        ).pack(anchor="w", padx=6, pady=(4, 1))

        tf = tk.Frame(self.cum_chart_frame, bg=self.PANEL)
        tf.pack(fill="x", padx=4, pady=(0, 4))

        ct = ttk.Treeview(tf, height=6, selectmode="extended")
        cc = [
            "MONTH",
            "OIL",
            "WATER",
            "GAS",
            "STM_INJ",
            "WTR_INJ",
            "GAS_INJ",
            "CUM_OIL",
            "CUM_WTR",
            "CUM_STM",
            "CUM_WINJ",
            "CUM_GINJ",
        ]
        ct["columns"] = cc
        ct["show"] = "headings"

        for c in cc:
            ct.heading(c, text=c, anchor="w")
            ct.column(c, width=85, anchor="e")

        vs = ttk.Scrollbar(tf, orient="vertical", command=ct.yview)
        hs = ttk.Scrollbar(tf, orient="horizontal", command=ct.xview)
        ct.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        ct.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)

        ct.tag_configure("even", background="#f0f4f8")
        ct.tag_configure("odd", background="white")
        ct.tag_configure("total", background="#e0e7ff", font=("Consolas", 9, "bold"))

        for i in range(len(dates)):
            dt = dates[i].strftime("%Y-%m") if hasattr(dates[i], "strftime") else str(dates[i])[:7]
            ct.insert(
                "",
                "end",
                values=[
                    dt,
                    f"{oil[i]:,.0f}",
                    f"{water[i]:,.0f}",
                    f"{gas[i]:,.0f}",
                    f"{steam[i]:,.0f}",
                    f"{winj[i]:,.0f}",
                    f"{ginj[i]:,.0f}",
                    f"{co[i]:,.0f}",
                    f"{cw[i]:,.0f}",
                    f"{cs[i]:,.0f}",
                    f"{cwi[i]:,.0f}",
                    f"{cgi[i]:,.0f}",
                ],
                tags=("even" if i % 2 == 0 else "odd",),
            )

        ct.insert(
            "",
            "end",
            values=[
                "TOTAL",
                f"{sum(oil):,.0f}",
                f"{sum(water):,.0f}",
                f"{sum(gas):,.0f}",
                f"{sum(steam):,.0f}",
                f"{sum(winj):,.0f}",
                f"{sum(ginj):,.0f}",
                f"{co[-1]:,.0f}",
                f"{cw[-1]:,.0f}",
                f"{cs[-1]:,.0f}",
                f"{cwi[-1]:,.0f}",
                f"{cgi[-1]:,.0f}",
            ],
            tags=("total",),
        )

        kids = ct.get_children()
        if kids:
            ct.see(kids[-1])

    # -------------------------------------------------------------------------
    # Tab 7: Well Map
    # -------------------------------------------------------------------------
    def _build_map(self):
        for w in self.tab_map.winfo_children():
            w.destroy()

        mw = []
        for r in self.well_rows:
            try:
                x = float(r[8])
                y = float(r[9])
                if x > 0 and y > 0:
                    mw.append(r)
            except Exception:
                continue

        if not mw:
            ttk.Label(self.tab_map, text="No coordinate data.", font=("Segoe UI", 12)).pack(pady=40)
            return

        fig = Figure(figsize=(12, 7), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)

        groups = {
            ("PROD", "Y"): dict(marker="o", color=COLORS["prod_active"], label="Producer (in svc)", s=30, alpha=0.85),
            ("PROD", "N"): dict(marker="o", color=COLORS["idle"], label="Producer (out)", s=18, alpha=0.4),
            ("INJ", "Y"): dict(marker="^", color=COLORS["inj_active"], label="Injector (in svc)", s=35, alpha=0.85),
            ("INJ", "N"): dict(marker="^", color=COLORS["idle"], label="Injector (out)", s=18, alpha=0.4),
            ("OBSN", "Y"): dict(marker="s", color=COLORS["obsn"], label="Observation", s=25, alpha=0.85),
        }

        plotted = set()
        for r in mw:
            p = str(r[3] or "")
            iv = "Y" if str(r[6]) == "Y" else "N"
            x = float(r[8])
            y = float(r[9])
            k = (p, iv)
            st = groups.get(k, dict(marker="D", color="#9ca3af", label="Other", s=18, alpha=0.5))
            lb = st["label"] if k not in plotted else None
            plotted.add(k)
            ax.scatter(
                x,
                y,
                marker=st["marker"],
                c=st["color"],
                s=st["s"],
                alpha=st["alpha"],
                label=lb,
                edgecolors="white",
                linewidths=0.5,
                zorder=3,
            )

        ax.set_title("Well Locations (State Plane)", fontsize=12, fontweight="bold", color=self.ACCENT, pad=12)
        ax.set_xlabel("Easting (ft)")
        ax.set_ylabel("Northing (ft)")
        ax.legend(fontsize=8, loc="best", framealpha=0.9, markerscale=1.5)
        ax.grid(True, alpha=0.2, linestyle="--")
        ax.set_axisbelow(True)
        ax.set_aspect("equal", adjustable="datalim")
        ax.tick_params(labelsize=8)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.tab_map)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.tab_map).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # -------------------------------------------------------------------------
    # Generic UI helpers
    # -------------------------------------------------------------------------
    def _fmt_x(self, ax, dates):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        span = (max(dates) - min(dates)).days if len(dates) > 1 else 30

        if span > 1800:
            ax.xaxis.set_major_locator(mdates.YearLocator(2))
        elif span > 720:
            ax.xaxis.set_major_locator(mdates.YearLocator())
        elif span > 360:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        else:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))

        ax.tick_params(axis="x", rotation=45, labelsize=8)

    def _mktree(self, parent):
        tree = ttk.Treeview(parent, selectmode="extended")
        vs = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree.tag_configure("even", background="#f0f4f8")
        tree.tag_configure("odd", background="white")
        return tree


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()