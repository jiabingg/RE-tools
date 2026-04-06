"""
Well Dossier Desktop App
========================
Tabs:
  1. Overview           — Well metadata from cmpl_dmn + wlbr_dmn
  2. Drilling/Completion — Casing, perforations, formation tops, directional survey
  3. Production          — Monthly production history + well tests
  4. Operations          — Workovers (DSS) + off-reason/downtime history
  5. WRA Notes           — Engineer comments (well_notes_cmnt_tb)
  6. Visualizations      — Production charts (oil/gas/water rates, volumes, cumulative)
  7. Production Eng      — Daily surveillance, pump fillage, rod loads, pumping unit
  8. Reservoir Eng       — Cumulative, coordinates, pattern wells, zonal allocation, segments

Requirements:   pip install oracledb matplotlib
Run:            python well_dossier.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, csv, sys, textwrap
from datetime import datetime
from collections import defaultdict

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
    import matplotlib.ticker as mticker
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


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
    if isinstance(val, datetime): return val.strftime("%Y-%m-%d %H:%M")
    if isinstance(val, float):
        return f"{int(val):,}" if val == int(val) else f"{val:,.2f}"
    return str(val)

def fmt_num(v):
    if v is None: return ""
    try: n = float(v)
    except: return str(v)
    if abs(n) >= 1e6: return f"{n/1e6:.1f}M"
    if abs(n) >= 1e3: return f"{n/1e3:.1f}K"
    return f"{int(round(n)):,}"


# ─────────────────────────────────────────────────────────────────────────────
# SQL QUERIES — parameterized by cmpl_fac_id or well_fac_id
# ─────────────────────────────────────────────────────────────────────────────

def sql_overview(api):
    return f"""SELECT cd.cmpl_nme, cd.cmpl_fac_id, cd.well_fac_id, cd.cmpl_dmn_key,
    cd.well_api_nbr, cd.engr_strg_nme, cd.rsvr_engr_strg_nme, cd.strg_nme,
    cd.opnl_fld, cd.fncl_fld_nme, cd.prdu_nme, cd.area_nme, cd.sub_area_nme,
    cd.prim_purp_type_cde, cd.prim_matl_desc, cd.actv_indc, cd.in_svc_indc,
    cd.init_prod_dte, cd.cmpl_state_type_cde,
    wd.total_dpth_qty AS TD_MD, wd.total_dpth_tvd_qty AS TD_TVD,
    wd.plug_back_dpth_qty AS PBK_MD, wd.wlbr_incl_type_desc AS INCLINATION
FROM dwrptg.cmpl_dmn cd
LEFT JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
WHERE cd.well_api_nbr LIKE '%{api}%' AND cd.actv_indc = 'Y'
FETCH FIRST 5 ROWS ONLY"""

def sql_casing(fac):
    return f"""SELECT wa.wlbr_asbly_cls_desc AS ASSEMBLY_CLASS,
    ws.item_desc AS ITEM, ws.sttr_top_md_qty AS TOP_MD, ws.sttr_btm_md_qty AS BTM_MD,
    ws.nmnl_size_qty AS SIZE_IN, ws.wt_qty AS WEIGHT_PPF,
    ws.grd_octg_cde AS GRADE, ws.thd_prlf_octg_desc AS THREAD,
    ws.top_lthsg_unit_nme AS TOP_FM, ws.btm_lthsg_unit_nme AS BTM_FM
FROM dwrptg.wlbr_asbly_sttr_fact ws
JOIN dwrptg.wlbr_asbly_dmn wa ON ws.wlbr_asbly_dmn_key = wa.wlbr_asbly_dmn_key
JOIN dwrptg.wlbr_dmn wd ON wa.wlbr_fac_id = wd.wlbr_fac_id
JOIN dwrptg.cmpl_dmn cd ON cd.well_fac_id = wd.well_fac_id
WHERE cd.cmpl_fac_id = {fac} AND wa.pull_dttm IS NULL
ORDER BY ws.sttr_top_md_qty"""

def sql_perforations(fac):
    return f"""SELECT awo.ACTL_OPG_NTVL_TYPE_DESC AS PERF_TYPE,
    awo.WLBR_OPG_NTVL_STAT_DESC AS STATUS,
    awo.OPG_TOP_MD_QTY AS TOP_MD, awo.OPG_BTM_MD_QTY AS BTM_MD,
    ROUND(awo.OPG_BTM_MD_QTY - awo.OPG_TOP_MD_QTY, 1) AS INTERVAL_FT,
    awo.OPG_TOP_TVD_QTY AS TOP_TVD, awo.OPG_BTM_TVD_QTY AS BTM_TVD,
    awo.SHTS_PER_FT_QTY AS SPF, awo.HOLE_SIZE_QTY AS HOLE_SIZE,
    awo.EFTV_DTTM AS PERF_DATE
FROM dwrptg.actl_wlbr_opg_ntvl_dmn awo
JOIN dwrptg.wlbr_dmn wd ON awo.wlbr_fac_id = wd.wlbr_fac_id
JOIN dwrptg.cmpl_dmn cd ON cd.well_fac_id = wd.well_fac_id
WHERE cd.cmpl_fac_id = {fac}
ORDER BY awo.OPG_TOP_MD_QTY"""

def sql_formations(fac):
    return f"""SELECT mp.mrkr_nme AS MARKER, mp.md_qty AS MD_FT, mp.tvd_qty AS TVD_FT
FROM dwrptg.wlbr_mrkr_pick_dmn mp
JOIN dwrptg.wlbr_dmn wd ON mp.wlbr_fac_id = wd.wlbr_fac_id
JOIN dwrptg.cmpl_dmn cd ON cd.well_fac_id = wd.well_fac_id
WHERE cd.cmpl_fac_id = {fac} AND mp.term_dttm IS NULL
ORDER BY mp.md_qty"""

def sql_directional(wfac):
    return f"""SELECT D.MD_QTY AS MD, D.TVD_QTY AS TVD,
    D.INCL_ANGL_QTY AS INCLINATION, D.AZMH_QTY AS AZIMUTH,
    D.XCRD AS X, D.YCRD AS Y
FROM dwrptg.dsvy_pt_dmn D
WHERE D.wlbr_fac_id IN (SELECT wlbr_fac_id FROM dwrptg.wlbr_dmn WHERE well_fac_id = {wfac})
ORDER BY D.MD_QTY"""

def sql_monthly_prod(fac):
    return f"""SELECT TRUNC(cmf.eftv_dttm, 'MM') AS MONTH,
    SUM(cmf.aloc_oil_prod_vol_qty) AS OIL_BBL,
    SUM(cmf.aloc_gros_prod_vol_qty) AS GROSS_BBL,
    SUM(cmf.aloc_wtr_prod_vol_qty) AS WATER_BBL,
    SUM(cmf.aloc_gas_prod_vol_qty) AS GAS_MCF,
    AVG(cmf.aloc_oil_prod_dly_rte_qty) AS OIL_BOPD,
    AVG(cmf.aloc_gros_prod_dly_rte_qty) AS GROSS_BFPD,
    AVG(cmf.aloc_wtr_prod_dly_rte_qty) AS WATER_BWPD,
    SUM(cmf.aloc_stm_inj_vol_qty) AS STEAM_INJ_BBL,
    SUM(cmf.aloc_wtr_inj_vol_qty) AS WATER_INJ_BBL
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.cmpl_fac_id = {fac}
GROUP BY TRUNC(cmf.eftv_dttm, 'MM')
ORDER BY MONTH"""

def sql_well_tests(fac):
    return f"""SELECT f.prod_msmt_strt_dttm AS TEST_DATE,
    f.bopd_qty AS BOPD, f.bwpd_qty AS BWPD, f.bgpd_qty AS BGPD, f.test_temp_qty AS TEMP
FROM dwrptg.cmpl_prod_tst_fact f
JOIN dwrptg.cmpl_prod_tst_dmn d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
WHERE f.cmpl_fac_id = {fac} AND d.use_for_aloc_indc = 'Y'
ORDER BY f.prod_msmt_strt_dttm DESC FETCH FIRST 100 ROWS ONLY"""

def sql_workovers(fac):
    return f"""SELECT wo.JOBTYPE, wo.STARTDATE, wo.ENDDATE
FROM dss.dss_work_over wo WHERE wo.PID = {fac}
ORDER BY wo.STARTDATE DESC FETCH FIRST 50 ROWS ONLY"""

def sql_off_reasons(fac):
    return f"""SELECT cof.off_rsn_type_cde AS CODE, cof.off_rsn_type_desc AS DESCRIPTION,
    cof.off_rsn_eftv_dttm AS START_DATE, cof.off_rsn_term_dttm AS END_DATE,
    ROUND(NVL(cof.off_rsn_term_dttm, SYSDATE) - cof.off_rsn_eftv_dttm, 1) AS DAYS_DOWN
FROM dwrptg.cmpl_off_rsn_fact cof WHERE cof.cmpl_fac_id = {fac}
ORDER BY cof.off_rsn_eftv_dttm DESC FETCH FIRST 100 ROWS ONLY"""

def sql_wra_notes(wfac):
    return f"""SELECT wnc.well_notes_cmnt_dte AS NOTE_DATE, wnc.well_notes_cmnt_txt AS NOTE_TEXT
FROM dwrptg.well_notes_cmnt_tb wnc WHERE wnc.well_fac_id = {wfac}
ORDER BY wnc.well_notes_cmnt_dte DESC FETCH FIRST 200 ROWS ONLY"""

# Production Engineering
def sql_daily_prod(fac):
    return f"""SELECT TRUNC(cdf.eftv_dttm) AS PROD_DATE,
    ROUND(AVG(cdf.aloc_oil_prod_vol_qty), 2) AS OIL_BBL,
    ROUND(AVG(cdf.flow_line_temp_qty), 1) AS FLOWLINE_TEMP,
    ROUND(AVG(cdf.wlhd_csg_prsr_qty), 1) AS CSG_PRSR,
    ROUND(AVG(cdf.wlhd_tbg_prsr_qty), 1) AS TBG_PRSR,
    ROUND(AVG(cdf.prod_uptm_secs_qty) / 3600, 1) AS UPTIME_HRS
FROM dwrptg.cmpl_dly_fact cdf
JOIN dwrptg.cmpl_dmn cd ON cdf.cmpl_fac_id = cd.cmpl_fac_id
WHERE cd.cmpl_fac_id = {fac} AND cd.actv_indc = 'Y'
  AND cdf.eftv_dttm >= ADD_MONTHS(SYSDATE, -6)
GROUP BY TRUNC(cdf.eftv_dttm) ORDER BY PROD_DATE DESC
FETCH FIRST 180 ROWS ONLY"""

def sql_fillage(fac):
    return f"""SELECT TRUNC(fdv.eftv_dttm) AS READING_DATE,
    ROUND(CAST(fdv.valu_txt AS NUMBER), 1) AS PUMP_FILLAGE_PCT
FROM dwrptg.fac_dyn_parm_valu fdv
WHERE fdv.fac_id = {fac} AND fdv.parm_type_id = 3657
  AND fdv.eftv_dttm >= ADD_MONTHS(SYSDATE, -3)
ORDER BY fdv.eftv_dttm DESC FETCH FIRST 90 ROWS ONLY"""

def sql_rod_loads(fac):
    return f"""SELECT TRUNC(fdv.eftv_dttm) AS READING_DATE,
    MAX(CASE WHEN fdv.parm_type_id = 4121 THEN ROUND(CAST(fdv.valu_txt AS NUMBER), 0) END) AS MAX_ROD_LOAD,
    MAX(CASE WHEN fdv.parm_type_id = 4122 THEN ROUND(CAST(fdv.valu_txt AS NUMBER), 0) END) AS MIN_ROD_LOAD
FROM dwrptg.fac_dyn_parm_valu fdv
WHERE fdv.fac_id = {fac} AND fdv.parm_type_id IN (4121, 4122)
  AND fdv.eftv_dttm >= ADD_MONTHS(SYSDATE, -3)
GROUP BY TRUNC(fdv.eftv_dttm) ORDER BY READING_DATE DESC
FETCH FIRST 90 ROWS ONLY"""

def sql_pumping_unit(fac):
    return f"""SELECT * FROM dwrptg.pmpg_unit_data
WHERE cmpl_fac_id = {fac} FETCH FIRST 5 ROWS ONLY"""

def sql_current_off(fac):
    return f"""SELECT cof.off_rsn_type_cde AS CODE, cof.off_rsn_type_desc AS DESCRIPTION,
    cof.off_rsn_eftv_dttm AS DOWN_SINCE,
    ROUND(SYSDATE - cof.off_rsn_eftv_dttm, 1) AS DAYS_DOWN
FROM dwrptg.cmpl_off_rsn_fact cof
WHERE cof.cmpl_fac_id = {fac} AND cof.off_rsn_term_dttm IS NULL"""

# Reservoir Engineering
def sql_cum_production(fac):
    return f"""SELECT cm.name AS WELL, cm.init_prod_dte AS INIT_PROD,
    cmf.eftv_dttm AS MONTH,
    cmf.aloc_cum_oil_prod_vol_qty AS CUM_OIL_BBL,
    cmf.aloc_oil_prod_dly_rte_qty AS OIL_BOPD,
    cmf.aloc_gros_prod_dly_rte_qty AS GROSS_BFPD,
    cmf.mon_since_init_prod_qty AS MONTHS_ON
FROM dss.dss_completion_master cm
JOIN dss.dss_completion_mnly_fact cmf ON cm.pid = cmf.pid
WHERE cm.pid = {fac}
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -60)
ORDER BY cmf.eftv_dttm DESC FETCH FIRST 60 ROWS ONLY"""

def sql_coordinates(fac):
    return f"""SELECT cm.name AS WELL, cm.pid,
    cm.topx AS HEEL_X, cm.topy AS HEEL_Y,
    cm.bottomx AS TOE_X, cm.bottomy AS TOE_Y,
    cm.surfacex AS SURF_X, cm.surfacey AS SURF_Y,
    cm.top_md_qty AS HEEL_MD
FROM dss.dss_completion_master cm WHERE cm.pid = {fac}"""

def sql_nearby_wells(fac):
    return f"""SELECT cd.cmpl_nme AS WELL, cd.cmpl_fac_id AS FAC_ID,
    cd.prim_purp_type_cde AS PURPOSE, cd.actv_indc AS ACTIVE,
    cd.in_svc_indc AS IN_SVC, cd.engr_strg_nme AS ENG_STRATEGY
FROM dwrptg.cmpl_dmn cd
WHERE cd.engr_strg_nme = (SELECT engr_strg_nme FROM dwrptg.cmpl_dmn
  WHERE cmpl_fac_id = {fac} AND actv_indc = 'Y' FETCH FIRST 1 ROW ONLY)
  AND cd.actv_indc = 'Y'
ORDER BY cd.prim_purp_type_cde, cd.cmpl_nme"""

def sql_segments(fac):
    return f"""SELECT rsd.rsvr_sgmt_nme AS SEGMENT, cd.cmpl_nme AS WELL,
    cd.prim_purp_type_cde AS PURPOSE
FROM dwrptg.cmpl_rsvr_sgmt_mbr rsm
JOIN dwrptg.cmpl_dmn cd ON cd.cmpl_fac_id = rsm.cmpl_fac_id
JOIN dwrptg.rsvr_sgmt_dmn rsd ON rsm.rsvr_sgmt_id = rsd.rsvr_sgmt_id
WHERE cd.engr_strg_nme = (SELECT engr_strg_nme FROM dwrptg.cmpl_dmn
  WHERE cmpl_fac_id = {fac} AND actv_indc = 'Y' FETCH FIRST 1 ROW ONLY)
  AND cd.actv_indc = 'Y'
ORDER BY rsd.rsvr_sgmt_nme, cd.prim_purp_type_cde, cd.cmpl_nme"""

def sql_zonal(fac):
    return f"""SELECT gd.lthsg_unit_nme AS ZONE,
    gf.eftv_dttm AS MONTH,
    ROUND(gf.aloc_oil_prod_dly_rate_qty, 1) AS OIL_RATE,
    ROUND(gf.aloc_stm_inj_dly_rate_qty, 1) AS STEAM_INJ_RATE,
    ROUND(gf.aloc_wtr_inj_dly_rate_qty, 1) AS WATER_INJ_RATE
FROM dwrptg.cmpl_gntl_mnly_fact gf
JOIN dwrptg.gntl_dmn gd ON gd.gntl_id = gf.gntl_id
WHERE gf.cmpl_fac_id = {fac}
  AND gf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
ORDER BY gf.eftv_dttm DESC, gd.lthsg_unit_nme
FETCH FIRST 200 ROWS ONLY"""

def sql_pattern_prod(fac):
    return f"""SELECT TRUNC(cmf.eftv_dttm, 'MM') AS MONTH,
    cd.cmpl_nme AS WELL,
    SUM(cmf.aloc_oil_prod_vol_qty) AS OIL_BBL,
    SUM(cmf.aloc_gros_prod_vol_qty) AS GROSS_BBL,
    SUM(cmf.aloc_wtr_prod_vol_qty) AS WATER_BBL
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.engr_strg_nme = (SELECT engr_strg_nme FROM dwrptg.cmpl_dmn
  WHERE cmpl_fac_id = {fac} AND actv_indc = 'Y' FETCH FIRST 1 ROW ONLY)
  AND cd.prim_purp_type_cde = 'PROD' AND cd.actv_indc = 'Y'
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -24)
GROUP BY TRUNC(cmf.eftv_dttm, 'MM'), cd.cmpl_nme
ORDER BY MONTH DESC, cd.cmpl_nme FETCH FIRST 500 ROWS ONLY"""


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
        w = (col_widths or {}).get(c, max(80, len(c) * 9))
        tree.column(c, width=w, anchor="w")
    for i, row in enumerate(rows):
        tree.insert("", "end",
                    values=[i + 1] + [fmt(v) for v in row],
                    tags=("even" if i % 2 == 0 else "odd",))

def _sort_tree(tree, col, rev):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    try:
        data.sort(key=lambda t: float(t[0].replace(",", "")), reverse=rev)
    except:
        data.sort(key=lambda t: t[0], reverse=rev)
    for i, (_, k) in enumerate(data):
        tree.move(k, "", i)
        tree.item(k, tags=("even" if i % 2 == 0 else "odd",))
        tree.set(k, "#", i + 1)
    tree.heading(col, command=lambda: _sort_tree(tree, col, not rev))

def copy_tree_to_clipboard(root, tree):
    """Copy all treeview data to clipboard as tab-separated text."""
    children = tree.get_children()
    if not children:
        messagebox.showinfo("Empty", "No data to copy.")
        return
    cols = [c for c in tree["columns"] if c != "#"]
    lines = ["\t".join(cols)]
    for ch in children:
        vals = tree.item(ch, "values")[1:]  # skip row number
        lines.append("\t".join(str(v) for v in vals))
    text = "\n".join(lines)
    root.clipboard_clear()
    root.clipboard_append(text)
    messagebox.showinfo("Copied", f"{len(children)} rows copied to clipboard.")

def export_tree(tree, title="export"):
    ch = tree.get_children()
    if not ch:
        messagebox.showinfo("No Data", "Nothing to export.")
        return
    path = filedialog.asksaveasfilename(
        defaultextension=".csv", filetypes=[("CSV", "*.csv")],
        initialfile=f"{title}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    if not path: return
    cols = [c for c in tree["columns"] if c != "#"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols)
        for c in ch:
            w.writerow(tree.item(c, "values")[1:])
    messagebox.showinfo("Saved", f"{len(ch)} rows → {path}")

def make_tree(parent):
    """Create a treeview with scrollbars inside parent frame."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════
class WellDossierApp:
    BG = "#f4f6f8"; ACCENT = "#1a5276"; PANEL = "#ffffff"; BORDER = "#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("Well Dossier — CRC Oracle Data Warehouse")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 650)

        # State
        self.cmpl_fac_id = None
        self.well_fac_id = None
        self.well_name = None
        self.well_api = None
        self.overview_rows = []
        self.prod_data = []  # cached monthly production for charts

        self._style()
        self._build_ui()
        self._statusbar()
        self._set_status("Enter an API number and click Search.")

    # ── Styling ──────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(); s.theme_use("clam")
        self.root.configure(bg=self.BG)
        for name, kw in {
            "TFrame": dict(background=self.BG),
            "TLabel": dict(background=self.BG, font=("Segoe UI", 10)),
            "TButton": dict(font=("Segoe UI", 10)),
            "TNotebook": dict(background=self.BG),
            "TNotebook.Tab": dict(padding=[14, 6], font=("Segoe UI", 10)),
            "Header.TLabel": dict(font=("Segoe UI", 14, "bold"),
                                  foreground=self.ACCENT, background=self.BG),
            "Sub.TLabel": dict(font=("Segoe UI", 9), foreground="#666",
                               background=self.BG),
            "Info.TLabel": dict(font=("Segoe UI", 10), background="#e8f0fe",
                                foreground="#1a5276"),
            "Status.TLabel": dict(font=("Segoe UI", 9), background="#dde4ea",
                                  padding=(8, 4)),
            "Accent.TButton": dict(font=("Segoe UI", 11, "bold"), padding=[18, 6]),
            "Treeview": dict(font=("Consolas", 9), rowheight=24),
            "Treeview.Heading": dict(font=("Segoe UI", 9, "bold"),
                                     foreground="white", background=self.ACCENT),
        }.items():
            s.configure(name, **kw)
        s.map("Treeview.Heading", background=[("active", "#1a6b9c")])
        s.map("Treeview", background=[("selected", "#d4e6f1")])

    # ── Build UI ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header with search
        hdr = ttk.Frame(self.root, padding=(12, 8)); hdr.pack(fill="x")
        ttk.Label(hdr, text="Well Dossier", style="Header.TLabel").pack(side="left")
        ttk.Label(hdr, text="CRC Oracle Data Warehouse",
                  style="Sub.TLabel").pack(side="left", padx=15)

        # Search bar
        sf = ttk.Frame(hdr); sf.pack(side="right")
        ttk.Label(sf, text="API #:").pack(side="left", padx=(0, 4))
        self.api_var = tk.StringVar(value="0411104823")
        self.api_entry = ttk.Entry(sf, textvariable=self.api_var, width=18,
                                    font=("Consolas", 11))
        self.api_entry.pack(side="left", padx=(0, 6))
        self.api_entry.bind("<Return>", lambda e: self._on_search())
        self.search_btn = ttk.Button(sf, text="  Search  ",
                                      style="Accent.TButton",
                                      command=self._on_search)
        self.search_btn.pack(side="left")

        # Well info bar (hidden until search)
        self.info_frame = ttk.Frame(self.root, padding=(12, 4))
        self.info_lbl = ttk.Label(self.info_frame, text="", style="Info.TLabel",
                                   padding=(10, 4))
        self.info_lbl.pack(fill="x")

        # Notebook (tabs)
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self.tabs = {}
        tab_defs = [
            ("overview", "  1. Overview  "),
            ("drilling", "  2. Drilling/Completion  "),
            ("production", "  3. Production  "),
            ("operations", "  4. Operations  "),
            ("wra", "  5. WRA Notes  "),
            ("viz", "  6. Visualizations  "),
            ("prodeng", "  7. Production Eng  "),
            ("reseng", "  8. Reservoir Eng  "),
        ]
        for tid, label in tab_defs:
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=label)
            self.tabs[tid] = frame

    def _statusbar(self):
        self.sb = ttk.Label(self.root, text="", style="Status.TLabel", anchor="w")
        self.sb.pack(fill="x", side="bottom")

    def _set_status(self, msg):
        self.sb.config(text=msg)
        self.root.update_idletasks()

    # ── Search handler ───────────────────────────────────────────────────────
    def _on_search(self):
        api = self.api_var.get().strip().replace("-", "")
        if not api:
            messagebox.showwarning("No API", "Enter an API number.")
            return
        self.search_btn.config(state="disabled")
        self._set_status(f"Searching for API {api} ...")
        threading.Thread(target=self._search_bg, args=(api,), daemon=True).start()

    def _search_bg(self, api):
        try:
            cols, rows = run_query(sql_overview(api))
            if not rows:
                self.root.after(0, lambda: messagebox.showinfo(
                    "Not Found", f"No active completion found for API {api}"))
                self.root.after(0, lambda: self._set_status("Not found."))
                return
            self.overview_cols = cols
            self.overview_rows = rows
            r = rows[0]
            self.well_name = str(r[0])
            self.cmpl_fac_id = int(r[1])
            self.well_fac_id = int(r[2])
            self.well_api = str(r[4])
            self.prod_data = []  # reset cached production
            self.root.after(0, self._display_overview)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.root.after(0, lambda: self._set_status(f"Error: {str(e)[:80]}"))
        finally:
            self.root.after(0, lambda: self.search_btn.config(state="normal"))

    def _display_overview(self):
        r = self.overview_rows[0]
        field = str(r[8] or "")
        purpose = str(r[13] or "")
        strategy = str(r[5] or "")
        active = "Active" if str(r[15]) == "Y" else "Inactive"
        info = (f"{self.well_name}  |  API: {self.well_api}  |  "
                f"Field: {field}  |  Type: {purpose}  |  "
                f"Strategy: {strategy}  |  Status: {active}  |  "
                f"cmpl_fac_id: {self.cmpl_fac_id}  |  well_fac_id: {self.well_fac_id}")
        self.info_lbl.config(text=info)
        self.info_frame.pack(fill="x", before=self.nb)
        self._set_status(f"Loaded {self.well_name} ({self.well_api}). "
                         f"Click tabs to load data sections.")
        self._build_overview_tab()
        self._build_drilling_tab()
        self._build_production_tab()
        self._build_operations_tab()
        self._build_wra_tab()
        self._build_viz_tab()
        self._build_prodeng_tab()
        self._build_reseng_tab()
        self.nb.select(0)

    # ══════════════════════════════════════════════════════════════════════════
    # QUERY PANEL — reusable pattern for each data section
    # ══════════════════════════════════════════════════════════════════════════
    def _make_query_section(self, parent, label, sql_fn, extra_buttons=None):
        """Create a labeled section with Load / Copy / Export buttons + treeview.
        Returns (frame, tree, load_button) so caller can customize."""
        lf = ttk.LabelFrame(parent, text=f"  {label}  ", padding=5)
        lf.pack(fill="both", expand=True, padx=6, pady=4)

        # Button bar
        bar = ttk.Frame(lf); bar.pack(fill="x", pady=(0, 4))

        tree_frame = ttk.Frame(lf)
        tree_frame.pack(fill="both", expand=True)
        tree = make_tree(tree_frame)

        count_lbl = ttk.Label(bar, text="", style="Sub.TLabel")
        count_lbl.pack(side="right", padx=8)

        def _do_load():
            load_btn.config(state="disabled")
            self._set_status(f"Loading {label} ...")
            def _bg():
                try:
                    sql = sql_fn()
                    cols, rows = run_query(sql)
                    self.root.after(0, lambda: populate_tree(tree, cols, rows))
                    self.root.after(0, lambda: count_lbl.config(
                        text=f"{len(rows)} rows"))
                    self.root.after(0, lambda: self._set_status(
                        f"{label}: {len(rows)} rows loaded."))
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror(
                        f"{label} Error", str(e)))
                    self.root.after(0, lambda: self._set_status(
                        f"{label} failed: {str(e)[:60]}"))
                finally:
                    self.root.after(0, lambda: load_btn.config(state="normal"))
            threading.Thread(target=_bg, daemon=True).start()

        load_btn = ttk.Button(bar, text="▶ Load", command=_do_load)
        load_btn.pack(side="left", padx=2)

        ttk.Button(bar, text="📋 Copy",
                   command=lambda: copy_tree_to_clipboard(self.root, tree)
                   ).pack(side="left", padx=2)

        ttk.Button(bar, text="💾 Export CSV",
                   command=lambda: export_tree(tree, label.replace(" ", "_"))
                   ).pack(side="left", padx=2)

        if extra_buttons:
            for btn_text, btn_cmd in extra_buttons:
                ttk.Button(bar, text=btn_text, command=btn_cmd).pack(
                    side="left", padx=2)

        return lf, tree, load_btn

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1: OVERVIEW
    # ══════════════════════════════════════════════════════════════════════════
    def _build_overview_tab(self):
        tab = self.tabs["overview"]
        for w in tab.winfo_children(): w.destroy()

        # Key-value display
        lf = ttk.LabelFrame(tab, text="  Well Metadata  ", padding=8)
        lf.pack(fill="x", padx=6, pady=4)

        cols = self.overview_cols
        row = self.overview_rows[0]
        grid_frame = ttk.Frame(lf)
        grid_frame.pack(fill="x")

        for i, (col, val) in enumerate(zip(cols, row)):
            r_idx = i // 3
            c_idx = (i % 3) * 2
            tk.Label(grid_frame, text=col.replace("_", " ") + ":",
                     font=("Segoe UI", 9, "bold"), fg=self.ACCENT,
                     bg=self.BG, anchor="e").grid(
                row=r_idx, column=c_idx, sticky="e", padx=(8, 4), pady=2)
            tk.Label(grid_frame, text=fmt(val),
                     font=("Consolas", 10), bg=self.BG, anchor="w").grid(
                row=r_idx, column=c_idx + 1, sticky="w", padx=(0, 16), pady=2)

        # Copy overview to clipboard
        def _copy_overview():
            lines = [f"{c}: {fmt(v)}" for c, v in zip(cols, row)]
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(lines))
            messagebox.showinfo("Copied", "Well overview copied to clipboard.")

        btn_bar = ttk.Frame(lf); btn_bar.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_bar, text="📋 Copy Overview", command=_copy_overview
                   ).pack(side="left", padx=4)

        # Current off-reason
        self._make_query_section(tab, "Current Off-Reason (if down)",
            lambda: sql_current_off(self.cmpl_fac_id))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2: DRILLING & COMPLETION
    # ══════════════════════════════════════════════════════════════════════════
    def _build_drilling_tab(self):
        tab = self.tabs["drilling"]
        for w in tab.winfo_children(): w.destroy()

        # Use a canvas + scrollbar for many sections
        canvas = tk.Canvas(tab, bg=self.BG, highlightthickness=0)
        vsb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>",
                          lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._make_query_section(scroll_frame, "Casing & Tubing (Current Assembly)",
            lambda: sql_casing(self.cmpl_fac_id))

        self._make_query_section(scroll_frame, "Perforations / Slots",
            lambda: sql_perforations(self.cmpl_fac_id))

        self._make_query_section(scroll_frame, "Formation Tops (Marker Picks)",
            lambda: sql_formations(self.cmpl_fac_id))

        self._make_query_section(scroll_frame, "Directional Survey",
            lambda: sql_directional(self.well_fac_id))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3: PRODUCTION
    # ══════════════════════════════════════════════════════════════════════════
    def _build_production_tab(self):
        tab = self.tabs["production"]
        for w in tab.winfo_children(): w.destroy()

        # Monthly production with chart loading
        lf, tree, _ = self._make_query_section(tab, "Monthly Production (Full History)",
            lambda: sql_monthly_prod(self.cmpl_fac_id))

        self._make_query_section(tab, "Well Tests (Allocated)",
            lambda: sql_well_tests(self.cmpl_fac_id))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 4: OPERATIONS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_operations_tab(self):
        tab = self.tabs["operations"]
        for w in tab.winfo_children(): w.destroy()

        self._make_query_section(tab, "Workovers (DSS)",
            lambda: sql_workovers(self.cmpl_fac_id))

        self._make_query_section(tab, "Off-Reason / Downtime History",
            lambda: sql_off_reasons(self.cmpl_fac_id))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 5: WRA NOTES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_wra_tab(self):
        tab = self.tabs["wra"]
        for w in tab.winfo_children(): w.destroy()

        lf = ttk.LabelFrame(tab, text="  WRA Engineer Notes  ", padding=5)
        lf.pack(fill="both", expand=True, padx=6, pady=4)

        bar = ttk.Frame(lf); bar.pack(fill="x", pady=(0, 4))
        count_lbl = ttk.Label(bar, text="", style="Sub.TLabel")
        count_lbl.pack(side="right", padx=8)

        # Text widget for notes (better than treeview for long text)
        txt_frame = ttk.Frame(lf)
        txt_frame.pack(fill="both", expand=True)
        self.wra_text = tk.Text(txt_frame, wrap="word", font=("Consolas", 9),
                                bg="white", fg="#333", bd=1, relief="solid",
                                padx=8, pady=8)
        txt_sb = ttk.Scrollbar(txt_frame, orient="vertical",
                                command=self.wra_text.yview)
        self.wra_text.configure(yscrollcommand=txt_sb.set)
        self.wra_text.pack(side="left", fill="both", expand=True)
        txt_sb.pack(side="right", fill="y")

        # Tag styles for the text widget
        self.wra_text.tag_configure("date", font=("Segoe UI", 10, "bold"),
                                     foreground=self.ACCENT, spacing1=12)
        self.wra_text.tag_configure("body", font=("Consolas", 9),
                                     foreground="#333", spacing1=2, lmargin1=10,
                                     lmargin2=10)
        self.wra_text.tag_configure("sep", foreground="#ccc")

        def _load_wra():
            load_btn.config(state="disabled")
            self._set_status("Loading WRA notes ...")
            def _bg():
                try:
                    cols, rows = run_query(sql_wra_notes(self.well_fac_id))
                    self.root.after(0, lambda: _populate_wra(rows))
                    self.root.after(0, lambda: count_lbl.config(
                        text=f"{len(rows)} notes"))
                    self.root.after(0, lambda: self._set_status(
                        f"WRA Notes: {len(rows)} entries loaded."))
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror(
                        "WRA Error", str(e)))
                finally:
                    self.root.after(0, lambda: load_btn.config(state="normal"))
            threading.Thread(target=_bg, daemon=True).start()

        def _populate_wra(rows):
            self.wra_text.config(state="normal")
            self.wra_text.delete("1.0", "end")
            for row in rows:
                dt = fmt(row[0])
                txt = str(row[1] or "").strip()
                self.wra_text.insert("end", f"── {dt} ", "date")
                self.wra_text.insert("end", "─" * 60 + "\n", "sep")
                self.wra_text.insert("end", txt + "\n\n", "body")
            self.wra_text.config(state="disabled")
            self._wra_rows = rows  # cache for copy

        def _copy_wra():
            if not hasattr(self, '_wra_rows') or not self._wra_rows:
                messagebox.showinfo("Empty", "Load WRA notes first.")
                return
            lines = []
            for r in self._wra_rows:
                lines.append(f"DATE: {fmt(r[0])}")
                lines.append(str(r[1] or "").strip())
                lines.append("")
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(lines))
            messagebox.showinfo("Copied",
                f"{len(self._wra_rows)} notes copied to clipboard.")

        load_btn = ttk.Button(bar, text="▶ Load", command=_load_wra)
        load_btn.pack(side="left", padx=2)
        ttk.Button(bar, text="📋 Copy All Notes", command=_copy_wra
                   ).pack(side="left", padx=2)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 6: VISUALIZATIONS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_viz_tab(self):
        tab = self.tabs["viz"]
        for w in tab.winfo_children(): w.destroy()

        if not HAS_MPL:
            ttk.Label(tab, text="matplotlib not installed. Run: pip install matplotlib",
                      font=("Segoe UI", 12)).pack(pady=40)
            return

        bar = ttk.Frame(tab); bar.pack(fill="x", padx=6, pady=4)

        self.viz_frame = ttk.Frame(tab)
        self.viz_frame.pack(fill="both", expand=True, padx=6)

        def _load_charts():
            chart_btn.config(state="disabled")
            self._set_status("Loading production data for charts ...")
            def _bg():
                try:
                    cols, rows = run_query(sql_monthly_prod(self.cmpl_fac_id))
                    self.prod_data = rows
                    self.prod_cols = cols
                    self.root.after(0, self._draw_charts)
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror(
                        "Chart Error", str(e)))
                finally:
                    self.root.after(0, lambda: chart_btn.config(state="normal"))
            threading.Thread(target=_bg, daemon=True).start()

        chart_btn = ttk.Button(bar, text="▶ Load & Draw Charts",
                                style="Accent.TButton", command=_load_charts)
        chart_btn.pack(side="left", padx=4)
        ttk.Label(bar, text="(Fetches full monthly production history)",
                  style="Sub.TLabel").pack(side="left", padx=8)

    def _draw_charts(self):
        for w in self.viz_frame.winfo_children(): w.destroy()
        data = self.prod_data
        if not data:
            ttk.Label(self.viz_frame, text="No production data.",
                      font=("Segoe UI", 12)).pack(pady=40)
            return

        # Parse data: MONTH(0), OIL_BBL(1), GROSS_BBL(2), WATER_BBL(3),
        #             GAS_MCF(4), OIL_BOPD(5), GROSS_BFPD(6), WATER_BWPD(7),
        #             STEAM_INJ_BBL(8), WATER_INJ_BBL(9)
        dates = [r[0] for r in data]
        oil_rate = [float(r[5] or 0) for r in data]
        gross_rate = [float(r[6] or 0) for r in data]
        water_rate = [float(r[7] or 0) for r in data]
        oil_vol = [float(r[1] or 0) for r in data]
        water_vol = [float(r[3] or 0) for r in data]
        gas_vol = [float(r[4] or 0) for r in data]

        # Cumulative
        import numpy as np
        cum_oil = np.cumsum(oil_vol)
        cum_water = np.cumsum(water_vol)
        cum_gas = np.cumsum(gas_vol)

        # Create scrollable canvas for multiple charts
        canvas = tk.Canvas(self.viz_frame, bg=self.BG, highlightthickness=0)
        vsb = ttk.Scrollbar(self.viz_frame, orient="vertical",
                             command=canvas.yview)
        scroll = ttk.Frame(canvas)
        scroll.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # Chart 1: Rates
        fig1 = Figure(figsize=(11, 3.5), dpi=100, facecolor="white")
        ax1 = fig1.add_subplot(111)
        ax1.plot(dates, oil_rate, color="#2d6a4f", lw=2, label="Oil (BOPD)")
        ax1.plot(dates, gross_rate, color="#3b82f6", lw=1.2, alpha=0.6,
                 label="Gross (BFPD)")
        ax1.plot(dates, water_rate, color="#60a5fa", lw=1, linestyle="--",
                 alpha=0.5, label="Water (BWPD)")
        ax1.set_title(f"{self.well_name} — Monthly Production Rates",
                      fontsize=11, fontweight="bold", color=self.ACCENT)
        ax1.set_ylabel("Rate (B/D)"); ax1.legend(fontsize=8)
        ax1.grid(axis="y", alpha=0.3, linestyle="--")
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _: fmt_num(x)))
        self._fmt_x(ax1, dates); fig1.tight_layout()
        f1 = tk.Frame(scroll); f1.pack(fill="x", pady=4)
        c1 = FigureCanvasTkAgg(fig1, f1); c1.draw()
        NavigationToolbar2Tk(c1, f1).update()
        c1.get_tk_widget().pack(fill="x")

        # Chart 2: Volumes
        fig2 = Figure(figsize=(11, 3.5), dpi=100, facecolor="white")
        ax2 = fig2.add_subplot(111)
        ax2.fill_between(dates, oil_vol, alpha=0.3, color="#2d6a4f")
        ax2.plot(dates, oil_vol, color="#2d6a4f", lw=1.5, label="Oil (BBL)")
        ax2.plot(dates, water_vol, color="#3b82f6", lw=1, label="Water (BBL)")
        ax2.set_title(f"{self.well_name} — Monthly Volumes",
                      fontsize=11, fontweight="bold", color=self.ACCENT)
        ax2.set_ylabel("Volume (BBL)"); ax2.legend(fontsize=8)
        ax2.grid(axis="y", alpha=0.3, linestyle="--")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _: fmt_num(x)))
        self._fmt_x(ax2, dates); fig2.tight_layout()
        f2 = tk.Frame(scroll); f2.pack(fill="x", pady=4)
        c2 = FigureCanvasTkAgg(fig2, f2); c2.draw()
        NavigationToolbar2Tk(c2, f2).update()
        c2.get_tk_widget().pack(fill="x")

        # Chart 3: Cumulative
        fig3 = Figure(figsize=(11, 3.5), dpi=100, facecolor="white")
        ax3 = fig3.add_subplot(111)
        ax3.plot(dates, cum_oil, color="#2d6a4f", lw=2.5, label="Cum Oil")
        ax3.plot(dates, cum_water, color="#60a5fa", lw=1.5, label="Cum Water")
        ax3.set_title(f"{self.well_name} — Cumulative Production",
                      fontsize=11, fontweight="bold", color=self.ACCENT)
        ax3.set_ylabel("Cumulative (BBL)"); ax3.legend(fontsize=8)
        ax3.grid(axis="y", alpha=0.3, linestyle="--")
        ax3.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _: fmt_num(x)))
        self._fmt_x(ax3, dates); fig3.tight_layout()
        f3 = tk.Frame(scroll); f3.pack(fill="x", pady=4)
        c3 = FigureCanvasTkAgg(fig3, f3); c3.draw()
        NavigationToolbar2Tk(c3, f3).update()
        c3.get_tk_widget().pack(fill="x")

        # Chart 4: Gas
        fig4 = Figure(figsize=(11, 3), dpi=100, facecolor="white")
        ax4 = fig4.add_subplot(111)
        ax4.plot(dates, gas_vol, color="#dc2626", lw=1.5, label="Gas (MCF)")
        ax4.fill_between(dates, gas_vol, alpha=0.15, color="#dc2626")
        ax4.set_title(f"{self.well_name} — Monthly Gas",
                      fontsize=11, fontweight="bold", color=self.ACCENT)
        ax4.set_ylabel("Gas (MCF)"); ax4.legend(fontsize=8)
        ax4.grid(axis="y", alpha=0.3, linestyle="--")
        ax4.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _: fmt_num(x)))
        self._fmt_x(ax4, dates); fig4.tight_layout()
        f4 = tk.Frame(scroll); f4.pack(fill="x", pady=4)
        c4 = FigureCanvasTkAgg(fig4, f4); c4.draw()
        NavigationToolbar2Tk(c4, f4).update()
        c4.get_tk_widget().pack(fill="x")

        self._set_status(f"Charts drawn: {len(data)} months of data.")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 7: PRODUCTION ENGINEERING
    # ══════════════════════════════════════════════════════════════════════════
    def _build_prodeng_tab(self):
        tab = self.tabs["prodeng"]
        for w in tab.winfo_children(): w.destroy()

        canvas = tk.Canvas(tab, bg=self.BG, highlightthickness=0)
        vsb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        scroll = ttk.Frame(canvas)
        scroll.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._make_query_section(scroll,
            "Daily Production + Pressures (Last 6 Mo)",
            lambda: sql_daily_prod(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Pump Fillage — XSPOC (Last 3 Mo)",
            lambda: sql_fillage(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Rod Loads — Max/Min (Last 3 Mo)",
            lambda: sql_rod_loads(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Pumping Unit Specs",
            lambda: sql_pumping_unit(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Recent Well Tests",
            lambda: sql_well_tests(self.cmpl_fac_id))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 8: RESERVOIR ENGINEERING
    # ══════════════════════════════════════════════════════════════════════════
    def _build_reseng_tab(self):
        tab = self.tabs["reseng"]
        for w in tab.winfo_children(): w.destroy()

        canvas = tk.Canvas(tab, bg=self.BG, highlightthickness=0)
        vsb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        scroll = ttk.Frame(canvas)
        scroll.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._make_query_section(scroll,
            "Cumulative Production (DSS — Last 5 Years)",
            lambda: sql_cum_production(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Bottomhole Coordinates (DSS)",
            lambda: sql_coordinates(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Pattern Wells (Same Eng Strategy)",
            lambda: sql_nearby_wells(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Reservoir Segment Membership",
            lambda: sql_segments(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Zonal Allocation (Last 12 Months)",
            lambda: sql_zonal(self.cmpl_fac_id))

        self._make_query_section(scroll,
            "Pattern Production — All Producers (Last 24 Mo)",
            lambda: sql_pattern_prod(self.cmpl_fac_id))

    # ── Chart formatting helper ──────────────────────────────────────────────
    def _fmt_x(self, ax, dates):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        span = (max(dates) - min(dates)).days if len(dates) > 1 else 30
        if span > 1800: ax.xaxis.set_major_locator(mdates.YearLocator(2))
        elif span > 720: ax.xaxis.set_major_locator(mdates.YearLocator())
        elif span > 360: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        else: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        ax.tick_params(axis="x", rotation=45, labelsize=8)


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    WellDossierApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()