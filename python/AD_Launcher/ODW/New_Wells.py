"""
Recent Drilled Wells Performance Viewer  (v8)
===============================================
Tabs:
  0. Dashboard        — KPI cards, spud trend, IP distribution, field breakdown
  1. Well Inventory   — filterable by Field + Engr Strategy
  2. Well Tests       — filterable by Field + Engr Strategy
  3. Well Test Chart  — filterable by Field + Engr Strategy

Vintage dropdown: 2020 = wells spudded 1/1/2020 – 12/31/2020
Custom date range: user picks From / To dates manually
Auto-loads "Last 12 Months" on startup.

Requirements:   pip install oracledb matplotlib python-dateutil
Run:            python recent_wells_performance.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, csv, sys
from datetime import datetime
from collections import defaultdict

TNS_ALIAS = "ODW"; DB_USERNAME = "rptguser"; DB_PASSWORD = "allusers"

try: import oracledb
except ImportError: sys.exit("pip install oracledb")

try:
    import matplotlib; matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError: HAS_MPL = False

# All SQL queries use :spud_date and :spud_end (date range)
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
      AND wd.bore_start_dttm <  :spud_end
      AND cd.actv_indc = 'Y'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
)
SELECT cmpl_nme, well_api_nbr, opnl_fld, prim_purp_type_cde, prim_matl_desc,
       engr_strg_nme, cmpl_state_type_desc, cmpl_state_eftv_dttm,
       bore_start_dttm, init_prod_dte, init_inj_dte, cmpl_fac_id, cmpl_dmn_key
FROM base WHERE rn = 1
ORDER BY prim_purp_type_cde DESC, bore_start_dttm DESC
"""
INV_FLD = 2; INV_ENGR = 5

SQL_WELL_TESTS_LATEST = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld, cd.engr_strg_nme,
           cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date AND wd.bore_start_dttm < :spud_end
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'PROD'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
),
ranked AS (
    SELECT dd.cmpl_nme AS WELL_NME, dd.well_api_nbr AS WELL_API_NBR,
           dd.opnl_fld AS FLD_NME, dd.engr_strg_nme AS ENGR_STRG_NME,
           f.prod_msmt_strt_dttm AS TEST_DATE, f.bopd_qty AS OIL_BOPD,
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
WT_FLD = 2; WT_ENGR = 3

SQL_WELL_TESTS_PEAK = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date AND wd.bore_start_dttm < :spud_end
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'PROD'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
),
ranked AS (
    SELECT dd.cmpl_nme AS WELL_NME, dd.well_api_nbr AS WELL_API_NBR,
           f.prod_msmt_strt_dttm AS PEAK_TEST_DATE, f.bopd_qty AS PEAK_OIL_BOPD,
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
    WHERE wd.bore_start_dttm >= :spud_date AND wd.bore_start_dttm < :spud_end
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

SQL_INJECTORS_WITH_DATA = """
WITH dedup AS (
    SELECT cd.cmpl_nme, cd.well_api_nbr, cd.opnl_fld, cd.engr_strg_nme,
           cd.prim_purp_type_cde, cd.prim_matl_desc, cd.cmpl_fac_id,
           ROW_NUMBER() OVER (PARTITION BY cd.well_api_nbr
                              ORDER BY cd.cmpl_fac_id DESC) AS drn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE wd.bore_start_dttm >= :spud_date AND wd.bore_start_dttm < :spud_end
      AND cd.actv_indc = 'Y' AND cd.prim_purp_type_cde = 'INJ'
      AND NVL(cd.cmpl_state_type_desc, 'X') != 'Permanently Abandoned'
)
SELECT DISTINCT dd.cmpl_nme, dd.opnl_fld, dd.engr_strg_nme,
       dd.prim_purp_type_cde, dd.prim_matl_desc, dd.cmpl_fac_id, 0 AS peak_oil
FROM dedup dd
JOIN dwrptg.cmpl_dly_fact cdf ON dd.cmpl_fac_id = cdf.cmpl_fac_id
WHERE dd.drn = 1 AND cdf.eftv_dttm >= :spud_date
  AND (NVL(cdf.aloc_stm_inj_vol_qty,0) > 0 OR NVL(cdf.aloc_wtr_inj_vol_qty,0) > 0)
  AND ROWNUM <= 1000
ORDER BY dd.opnl_fld, dd.cmpl_nme
"""

SQL_PROD_WELL_TESTS = """
SELECT f.prod_msmt_strt_dttm, f.bopd_qty, f.gros_wtr_prod_vol_qty,
       ROUND(f.bopd_qty * NVL(f.prod_gas_oil_rat_qty,0)/1000, 2)
FROM dwrptg.cmpl_prod_tst_fact f
JOIN dwrptg.cmpl_prod_tst_dmn  d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
WHERE f.cmpl_fac_id = :cmpl_fac_id AND d.use_for_aloc_indc = 'Y'
  AND f.prod_msmt_strt_dttm >= :spud_date
ORDER BY f.prod_msmt_strt_dttm
"""

SQL_INJ_DAILY = """
SELECT cdf.eftv_dttm, ROUND(cdf.aloc_stm_inj_vol_qty,1),
       ROUND(cdf.aloc_wtr_inj_vol_qty,1)
FROM dwrptg.cmpl_dly_fact cdf
WHERE cdf.cmpl_fac_id = :cmpl_fac_id AND cdf.eftv_dttm >= :start_date
ORDER BY cdf.eftv_dttm
"""

# ─── DB ──────────────────────────────────────────────────────────────────────
def get_conn():
    try: oracledb.init_oracle_client()
    except: pass
    return oracledb.connect(user=DB_USERNAME, password=DB_PASSWORD, dsn=TNS_ALIAS)

def qry(sql, p=None):
    cn = get_conn(); cu = cn.cursor(); cu.execute(sql, p or {})
    c = [d[0] for d in cu.description]; r = cu.fetchall()
    cu.close(); cn.close(); return c, r

def fmt(v):
    if v is None: return ""
    if isinstance(v, datetime): return v.strftime("%Y-%m-%d")
    if isinstance(v, float): return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"
    return str(v)

# ─── Treeview ────────────────────────────────────────────────────────────────
def pop_tree(tree, cols, rows, cw=None):
    tree.delete(*tree.get_children())
    dc = ["#"]+list(cols); tree["columns"]=dc; tree["show"]="headings"
    tree.heading("#",text="#",anchor="center"); tree.column("#",width=45,anchor="center",stretch=False)
    for c in cols:
        tree.heading(c,text=c,anchor="w",command=lambda col=c: _st(tree,col,False))
        tree.column(c,width=(cw or {}).get(c,max(80,len(c)*9)),anchor="w")
    for i,row in enumerate(rows):
        tree.insert("","end",values=[i+1]+[fmt(v) for v in row],tags=("even" if i%2==0 else "odd",))

def _sk(v,r):
    if v=="": return (1,0,"")
    s=v.replace(",","")
    try: return (0,float(s),"")
    except: return (0,0,v)

def _st(tree,col,rev):
    d=[(tree.set(k,col),k) for k in tree.get_children("")]
    d.sort(key=lambda t:_sk(t[0],rev),reverse=rev)
    for i,(_,k) in enumerate(d):
        tree.move(k,"",i); tree.item(k,tags=("even" if i%2==0 else "odd",)); tree.set(k,"#",i+1)
    tree.heading(col,command=lambda:_st(tree,col,not rev))

def _swt(tree,col,rev):
    d=[(tree.set(k,col),k) for k in tree.get_children("")]
    d.sort(key=lambda t:_sk(t[0],rev),reverse=rev)
    for i,(_,k) in enumerate(d):
        tree.move(k,"",i); tree.item(k,tags=("even" if i%2==0 else "odd",))
    tree.heading(col,command=lambda:_swt(tree,col,not rev))

def exp_tree(tree,title="export"):
    ch=tree.get_children()
    if not ch: messagebox.showinfo("No Data","Nothing."); return
    path=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv")],
        initialfile=f"{title}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    if not path: return
    cols=[c for c in tree["columns"] if c!="#"]
    with open(path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(cols)
        for c in ch: w.writerow(tree.item(c,"values")[1:])
    messagebox.showinfo("Saved",f"{len(ch)} rows -> {path}")

def mk_filter(parent,fv,ev,cb):
    bar=ttk.Frame(parent,padding=(8,4)); bar.pack(fill="x")
    ttk.Label(bar,text="Field:").pack(side="left")
    fc=ttk.Combobox(bar,textvariable=fv,width=14,state="readonly")
    fc.pack(side="left",padx=(4,12)); fc.bind("<<ComboboxSelected>>",cb)
    ttk.Label(bar,text="Engr Strategy:").pack(side="left")
    ec=ttk.Combobox(bar,textvariable=ev,width=22,state="readonly")
    ec.pack(side="left",padx=(4,0)); ec.bind("<<ComboboxSelected>>",cb)
    return fc,ec

# ─── Date picker helper ──────────────────────────────────────────────────────
def make_date_row(parent, label_text, yr_var, mo_var, dy_var):
    """Create a Y-M-D date row with label. Returns the frame."""
    cur_yr = datetime.now().year
    f = ttk.Frame(parent); f.pack(side="left")
    ttk.Label(f, text=label_text).pack(side="left")
    ttk.Combobox(f, textvariable=yr_var, width=6,
                 values=[str(y) for y in range(2015, cur_yr+2)],
                 state="readonly").pack(side="left", padx=2)
    ttk.Label(f, text="-").pack(side="left")
    ttk.Combobox(f, textvariable=mo_var, width=4,
                 values=[f"{m:02d}" for m in range(1,13)],
                 state="readonly").pack(side="left", padx=2)
    ttk.Label(f, text="-").pack(side="left")
    ttk.Combobox(f, textvariable=dy_var, width=4,
                 values=[f"{d:02d}" for d in range(1,32)],
                 state="readonly").pack(side="left", padx=2)
    return f


# ─── App ─────────────────────────────────────────────────────────────────────
class App:
    BG="#f4f6f8"; ACC="#1a5276"; PNL="#ffffff"; BDR="#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("Recent Drilled Wells — Performance Viewer")
        self.root.geometry("1400x880"); self.root.minsize(1100,650)
        self.raw_ic=[]; self.raw_ir=[]; self.raw_wc=[]; self.raw_wr=[]
        self.cw=[]; self.spud=None; self.spud_end=None
        self._sty(); self._top(); self._nb()
        self._t0_dash(); self._t1_inv(); self._t2_wt(); self._t3_ch()
        self._sbar()
        self._set("Loading last 12 months ...")
        self.root.after(300, self._on_vintage)

    def _sty(self):
        s=ttk.Style(); s.theme_use("clam"); self.root.configure(bg=self.BG)
        for n,kw in [
            ("TFrame",dict(background=self.BG)),("TLabel",dict(background=self.BG,font=("Segoe UI",10))),
            ("TButton",dict(font=("Segoe UI",10))),("TNotebook",dict(background=self.BG)),
            ("TNotebook.Tab",dict(padding=[14,6],font=("Segoe UI",10))),
            ("Header.TLabel",dict(font=("Segoe UI",13,"bold"),foreground=self.ACC,background=self.BG)),
            ("Sub.TLabel",dict(font=("Segoe UI",9),foreground="#666",background=self.BG)),
            ("Status.TLabel",dict(font=("Segoe UI",9),background="#dde4ea",padding=(8,4))),
            ("Accent.TButton",dict(font=("Segoe UI",11,"bold"),padding=[18,6])),
            ("Treeview",dict(font=("Consolas",9),rowheight=24)),
            ("Treeview.Heading",dict(font=("Segoe UI",9,"bold"),foreground="white",background=self.ACC)),
        ]: s.configure(n,**kw)
        s.map("Treeview.Heading",background=[("active","#1a6b9c")])
        s.map("Treeview",background=[("selected","#d4e6f1")])

    # ── Top bar ──────────────────────────────────────────────────────────────
    def _top(self):
        b = ttk.Frame(self.root, padding=(12, 8)); b.pack(fill="x")
        ttk.Label(b, text="Recent Drilled Wells Performance",
                  style="Header.TLabel").pack(side="left")

        # Right side controls
        r = ttk.Frame(b); r.pack(side="right")

        # Row 1: Vintage quick-select
        row1 = ttk.Frame(r); row1.pack(fill="x")
        ttk.Label(row1, text="Vintage:").pack(side="left")
        cur_yr = datetime.now().year
        vintage_vals = ["Last 12 Months"] + [str(y) for y in range(cur_yr, 2015, -1)]
        self.vintage_var = tk.StringVar(value="Last 12 Months")
        vc = ttk.Combobox(row1, textvariable=self.vintage_var, width=14,
                          values=vintage_vals, state="readonly")
        vc.pack(side="left", padx=(4, 0))
        vc.bind("<<ComboboxSelected>>", self._on_vintage)

        # Separator
        ttk.Label(row1, text="    │  ", foreground="#ccc").pack(side="left")

        # From date
        self.fr_yr = tk.StringVar(value="2024")
        self.fr_mo = tk.StringVar(value="01")
        self.fr_dy = tk.StringVar(value="01")
        make_date_row(row1, " From: ", self.fr_yr, self.fr_mo, self.fr_dy)

        ttk.Label(row1, text="   ").pack(side="left")

        # To date
        self.to_yr = tk.StringVar(value=str(cur_yr))
        self.to_mo = tk.StringVar(value="12")
        self.to_dy = tk.StringVar(value="31")
        make_date_row(row1, " To: ", self.to_yr, self.to_mo, self.to_dy)

        ttk.Label(row1, text="  ").pack(side="left")
        self.pbtn = ttk.Button(row1, text="  Pull Data  ",
                               style="Accent.TButton", command=self._on_pull)
        self.pbtn.pack(side="left", padx=(8, 0))

    def _on_vintage(self, _=None):
        """Vintage: set from/to dates and auto-pull."""
        v = self.vintage_var.get()
        if v == "Last 12 Months":
            from dateutil.relativedelta import relativedelta
            ago = datetime.now() - relativedelta(months=12)
            self.fr_yr.set(str(ago.year)); self.fr_mo.set(f"{ago.month:02d}"); self.fr_dy.set(f"{ago.day:02d}")
            now = datetime.now()
            self.to_yr.set(str(now.year)); self.to_mo.set(f"{now.month:02d}"); self.to_dy.set(f"{now.day:02d}")
        else:
            yr = int(v)
            self.fr_yr.set(v); self.fr_mo.set("01"); self.fr_dy.set("01")
            self.to_yr.set(v); self.to_mo.set("12"); self.to_dy.set("31")
        self._on_pull()

    def _nb(self):
        self.nb=ttk.Notebook(self.root); self.nb.pack(fill="both",expand=True,padx=10,pady=(4,0))
        self.f0=ttk.Frame(self.nb); self.f1=ttk.Frame(self.nb)
        self.f2=ttk.Frame(self.nb); self.f3=ttk.Frame(self.nb)
        self.nb.add(self.f0,text="  Dashboard  "); self.nb.add(self.f1,text="  Well Inventory  ")
        self.nb.add(self.f2,text="  Well Tests  "); self.nb.add(self.f3,text="  Well Test Chart  ")

    # ── Tab 0: Dashboard ─────────────────────────────────────────────────────
    def _t0_dash(self):
        self.dash_frame=ttk.Frame(self.f0); self.dash_frame.pack(fill="both",expand=True,padx=8,pady=8)
        if HAS_MPL:
            self.dash_fig=Figure(figsize=(13,7),dpi=100,facecolor=self.BG)
            self.dash_canvas=FigureCanvasTkAgg(self.dash_fig,master=self.dash_frame)
            self.dash_canvas.get_tk_widget().pack(fill="both",expand=True)

    def _t1_inv(self):
        self.i_fv=tk.StringVar(value="All"); self.i_ev=tk.StringVar(value="All")
        self.i_fc,self.i_ec = mk_filter(self.f1,self.i_fv,self.i_ev,self._fi)
        top=ttk.Frame(self.f1,padding=(8,2)); top.pack(fill="x")
        self.i_lb=ttk.Label(top,text="No data.",style="Sub.TLabel"); self.i_lb.pack(side="left")
        ttk.Button(top,text="Export CSV",command=lambda:exp_tree(self.t1,"inventory")).pack(side="right")
        frm=ttk.Frame(self.f1); frm.pack(fill="both",expand=True,padx=8,pady=(0,8))
        self.t1=self._mt(frm)

    def _t2_wt(self):
        self.w_fv=tk.StringVar(value="All"); self.w_ev=tk.StringVar(value="All")
        self.w_fc,self.w_ec = mk_filter(self.f2,self.w_fv,self.w_ev,self._fw)
        top=ttk.Frame(self.f2,padding=(8,2)); top.pack(fill="x")
        self.w_lb=ttk.Label(top,text="No data.",style="Sub.TLabel"); self.w_lb.pack(side="left")
        ttk.Button(top,text="Export CSV",command=lambda:exp_tree(self.t2,"well_tests")).pack(side="right")
        frm=ttk.Frame(self.f2); frm.pack(fill="both",expand=True,padx=8,pady=(0,8))
        self.t2=self._mt(frm)

    def _t3_ch(self):
        o=ttk.Frame(self.f3); o.pack(fill="both",expand=True,padx=8,pady=8)
        o.columnconfigure(1,weight=1); o.rowconfigure(0,weight=1)
        left=tk.Frame(o,bg=self.PNL,highlightbackground=self.BDR,highlightthickness=1)
        left.grid(row=0,column=0,sticky="ns",padx=(0,8))
        fs=tk.Frame(left,bg=self.PNL); fs.pack(fill="x",padx=10,pady=(8,0))
        tk.Label(fs,text="FIELD",font=("Segoe UI",8,"bold"),fg=self.ACC,bg=self.PNL).pack(anchor="w")
        self.c_fv=tk.StringVar(value="All")
        self.c_fc=ttk.Combobox(fs,textvariable=self.c_fv,width=28,state="readonly")
        self.c_fc.pack(fill="x",pady=(2,0)); self.c_fc.bind("<<ComboboxSelected>>",self._cf)
        es=tk.Frame(left,bg=self.PNL); es.pack(fill="x",padx=10,pady=(4,0))
        tk.Label(es,text="ENGR STRATEGY",font=("Segoe UI",8,"bold"),fg=self.ACC,bg=self.PNL).pack(anchor="w")
        self.c_ev=tk.StringVar(value="All")
        self.c_ec=ttk.Combobox(es,textvariable=self.c_ev,width=28,state="readonly")
        self.c_ec.pack(fill="x",pady=(2,0)); self.c_ec.bind("<<ComboboxSelected>>",self._ce)
        tk.Frame(left,bg=self.BDR,height=1).pack(fill="x",padx=10,pady=6)
        wh=tk.Frame(left,bg=self.PNL); wh.pack(fill="x",padx=10)
        tk.Label(wh,text="SELECT WELL",font=("Segoe UI",8,"bold"),fg=self.ACC,bg=self.PNL).pack(side="left")
        self.wc_lb=tk.Label(wh,text="",font=("Segoe UI",8),fg="#888",bg=self.PNL); self.wc_lb.pack(side="right")
        tf=tk.Frame(left,bg=self.PNL); tf.pack(fill="both",expand=True,padx=10,pady=(4,8))
        self.wtr=ttk.Treeview(tf,columns=("WELL","TYPE","PEAK_OIL"),show="headings",selectmode="browse")
        self.wtr.heading("WELL",text="Well",command=lambda:_swt(self.wtr,"WELL",False))
        self.wtr.heading("TYPE",text="Type",command=lambda:_swt(self.wtr,"TYPE",False))
        self.wtr.heading("PEAK_OIL",text="Peak Oil",command=lambda:_swt(self.wtr,"PEAK_OIL",True))
        self.wtr.column("WELL",width=150); self.wtr.column("TYPE",width=72); self.wtr.column("PEAK_OIL",width=62,anchor="e")
        sb=ttk.Scrollbar(tf,orient="vertical",command=self.wtr.yview); self.wtr.configure(yscrollcommand=sb.set)
        self.wtr.pack(side="left",fill="both",expand=True); sb.pack(side="right",fill="y")
        self.wtr.tag_configure("even",background="#f0f4f8"); self.wtr.tag_configure("odd",background="white")
        self.wtr.bind("<<TreeviewSelect>>",self._ws)
        right=tk.Frame(o,bg=self.PNL,highlightbackground=self.BDR,highlightthickness=1)
        right.grid(row=0,column=1,sticky="nsew")
        ct=tk.Frame(right,bg=self.PNL); ct.pack(fill="x",padx=10,pady=(8,0))
        self.ch_lb=tk.Label(ct,text="Select a well",font=("Segoe UI",9),fg="#888",bg=self.PNL); self.ch_lb.pack(side="left")
        ca=tk.Frame(right,bg=self.PNL); ca.pack(fill="both",expand=True,padx=6,pady=(4,6))
        if HAS_MPL:
            self.fig=Figure(figsize=(10,5),dpi=100,facecolor=self.PNL)
            self.canvas=FigureCanvasTkAgg(self.fig,master=ca)
            self.canvas.get_tk_widget().pack(fill="both",expand=True)
            tb=tk.Frame(ca,bg=self.PNL); tb.pack(fill="x"); NavigationToolbar2Tk(self.canvas,tb).update()

    def _mt(self,p):
        t=ttk.Treeview(p,selectmode="extended")
        vs=ttk.Scrollbar(p,orient="vertical",command=t.yview); hs=ttk.Scrollbar(p,orient="horizontal",command=t.xview)
        t.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        t.grid(row=0,column=0,sticky="nsew"); vs.grid(row=0,column=1,sticky="ns"); hs.grid(row=1,column=0,sticky="ew")
        p.columnconfigure(0,weight=1); p.rowconfigure(0,weight=1)
        t.tag_configure("even",background="#f0f4f8"); t.tag_configure("odd",background="white"); return t

    def _sbar(self):
        self.sb=ttk.Label(self.root,text="",style="Status.TLabel",anchor="w"); self.sb.pack(fill="x",side="bottom")
    def _set(self,m): self.sb.config(text=m); self.root.update_idletasks()
    def _err(self,m): self._set("Query failed."); messagebox.showerror("Error",m)

    # ═════════════════════════════════════════════════════════════════════════
    # Pull
    # ═════════════════════════════════════════════════════════════════════════
    def _on_pull(self):
        try:
            self.spud = datetime(int(self.fr_yr.get()),int(self.fr_mo.get()),int(self.fr_dy.get()))
            self.spud_end = datetime(int(self.to_yr.get()),int(self.to_mo.get()),int(self.to_dy.get()))
            # Make end date inclusive (end of day)
            from dateutil.relativedelta import relativedelta
            self.spud_end = self.spud_end + relativedelta(days=1)
        except Exception:
            messagebox.showerror("Bad Date","Invalid date range."); return
        if self.spud_end <= self.spud:
            messagebox.showerror("Bad Range","'To' date must be after 'From' date."); return
        self.pbtn.config(state="disabled")
        self._set(f"Querying wells spudded {self.spud:%Y-%m-%d} to {(self.spud_end-relativedelta(days=1)):%Y-%m-%d} ...")
        threading.Thread(target=self._bg,daemon=True).start()

    def _bg(self):
        try:
            p = {"spud_date": self.spud, "spud_end": self.spud_end}
            ic,ir = qry(SQL_WELL_INVENTORY, p); self.raw_ic=ic; self.raw_ir=ir
            self.root.after(0,self._set,"Querying well tests ...")
            lc,lr = qry(SQL_WELL_TESTS_LATEST, p); pc,pr = qry(SQL_WELL_TESTS_PEAK, p)
            mc,mr = self._mtest(lc,lr,pc,pr); self.raw_wc=mc; self.raw_wr=mr
            self.root.after(0,self._set,"Finding wells with data ...")
            _,prod = qry(SQL_PRODUCERS_WITH_TESTS, p); _,inj = qry(SQL_INJECTORS_WITH_DATA, p)
            cw=[]
            for r in prod: cw.append((r[0],r[1] or "",r[2] or "",r[3] or "",r[4] or "",r[5],r[6] or 0))
            for r in inj:  cw.append((r[0],r[1] or "",r[2] or "",r[3] or "",r[4] or "",r[5],0))
            cw.sort(key=lambda t:(t[1],t[0])); self.cw=cw
            self.root.after(0,self._set,f"Loaded {len(ir)} well(s).")
            self.root.after(0,self._pop_all)
        except Exception as e: self.root.after(0,self._err,str(e))
        finally: self.root.after(0,lambda:self.pbtn.config(state="normal"))

    def _mtest(self,lc,lr,pc,pr):
        pm={}
        ni=pc.index("WELL_NME"); ndi=pc.index("PEAK_TEST_DATE"); noi=pc.index("PEAK_OIL_BOPD")
        for r in pr: pm[r[ni]]=(r[ndi],r[noi])
        mc=list(lc)+["PEAK_TEST_DATE","PEAK_OIL_BOPD"]
        mr=[list(r)+list(pm.get(r[0],(None,None))) for r in lr]
        mr.sort(key=lambda x: x[-1] if x[-1] is not None else -1,reverse=True)
        return mc,mr

    def _pop_all(self):
        fs=sorted(set(r[INV_FLD] or "" for r in self.raw_ir if r[INV_FLD]))
        es=sorted(set(r[INV_ENGR] or "" for r in self.raw_ir if r[INV_ENGR]))
        self.i_fc["values"]=["All"]+fs; self.i_ec["values"]=["All"]+es
        self.i_fv.set("All"); self.i_ev.set("All")
        wfs=sorted(set(r[WT_FLD] or "" for r in self.raw_wr if r[WT_FLD]))
        wes=sorted(set(r[WT_ENGR] or "" for r in self.raw_wr if r[WT_ENGR]))
        self.w_fc["values"]=["All"]+wfs; self.w_ec["values"]=["All"]+wes
        self.w_fv.set("All"); self.w_ev.set("All")
        cfs=sorted(set(f for _,f,_,_,_,_,_ in self.cw if f))
        ces=sorted(set(e for _,_,e,_,_,_,_ in self.cw if e))
        self.c_fc["values"]=["All"]+cfs; self.c_ec["values"]=["All"]+ces
        self.c_fv.set("All"); self.c_ev.set("All")
        self._ai(); self._aw(); self._rwt(); self._draw_dashboard()

    # ── Filters ──────────────────────────────────────────────────────────────
    def _fi(self,_=None):
        fld=self.i_fv.get()
        es=sorted(set(r[INV_ENGR] or "" for r in self.raw_ir if r[INV_ENGR] and (fld=="All" or r[INV_FLD]==fld)))
        self.i_ec["values"]=["All"]+es
        if self.i_ev.get() not in ["All"]+es: self.i_ev.set("All")
        self._ai()
    def _ai(self):
        fld=self.i_fv.get(); eng=self.i_ev.get()
        fr=[r[:11] for r in self.raw_ir if (fld=="All" or (r[INV_FLD] or "")==fld) and (eng=="All" or (r[INV_ENGR] or "")==eng)]
        w={"CMPL_NME":170,"WELL_API_NBR":110,"OPNL_FLD":100,"PRIM_PURP_TYPE_CDE":70,"PRIM_MATL_DESC":80,
           "ENGR_STRG_NME":170,"CMPL_STATE_TYPE_DESC":120,"CMPL_STATE_EFTV_DTTM":110,
           "BORE_START_DTTM":100,"INIT_PROD_DTE":100,"INIT_INJ_DTE":100}
        pop_tree(self.t1,self.raw_ic[:11],fr,w)
        self.i_lb.config(text=f"{len(fr)} well(s)  (P&A excluded, one per API)")
    def _fw(self,_=None):
        fld=self.w_fv.get()
        es=sorted(set(r[WT_ENGR] or "" for r in self.raw_wr if r[WT_ENGR] and (fld=="All" or r[WT_FLD]==fld)))
        self.w_ec["values"]=["All"]+es
        if self.w_ev.get() not in ["All"]+es: self.w_ev.set("All")
        self._aw()
    def _aw(self):
        fld=self.w_fv.get(); eng=self.w_ev.get()
        fr=[r for r in self.raw_wr if (fld=="All" or (r[WT_FLD] or "")==fld) and (eng=="All" or (r[WT_ENGR] or "")==eng)]
        w={"WELL_NME":170,"WELL_API_NBR":110,"FLD_NME":100,"ENGR_STRG_NME":160,"TEST_DATE":100,
           "OIL_BOPD":80,"WTR_BWPD":80,"GAS_MCFD":80,"WC_PCT":70,"PEAK_TEST_DATE":110,"PEAK_OIL_BOPD":100}
        pop_tree(self.t2,self.raw_wc,fr,w)
        self.w_lb.config(text=f"{len(fr)} producer(s)  (sorted by Peak Oil)")
    def _cf(self,_=None):
        fld=self.c_fv.get()
        es=sorted(set(e for _,f,e,_,_,_,_ in self.cw if e and (fld=="All" or f==fld)))
        self.c_ec["values"]=["All"]+es
        if self.c_ev.get() not in ["All"]+es: self.c_ev.set("All")
        self._rwt()
    def _ce(self,_=None): self._rwt()
    def _rwt(self):
        fld=self.c_fv.get(); eng=self.c_ev.get()
        self.wtr.delete(*self.wtr.get_children()); cnt=0
        for nm,f,eg,pu,mt,fi,pk in self.cw:
            if fld!="All" and f!=fld: continue
            if eng!="All" and eg!=eng: continue
            tp="PROD" if pu=="PROD" else f"INJ-{mt}" if pu=="INJ" else pu
            ps=fmt(pk) if pk and pk>0 else ""
            self.wtr.insert("","end",values=(nm,tp,ps),tags=("even" if cnt%2==0 else "odd",)); cnt+=1
        self.wc_lb.config(text=f"{cnt} well(s)")
        ch=self.wtr.get_children()
        if ch: self.wtr.selection_set(ch[0]); self.wtr.focus(ch[0]); self._ws()
        else:
            if HAS_MPL: self.fig.clear(); self.canvas.draw()
    def _gsi(self):
        sel=self.wtr.selection()
        if not sel: return None
        nm=self.wtr.item(sel[0],"values")[0]; fld=self.c_fv.get(); eng=self.c_ev.get()
        for n,f,eg,p,m,fi,pk in self.cw:
            if n==nm and (fld=="All" or f==fld) and (eng=="All" or eg==eng): return (n,f,p,m,fi,pk)
        return None
    def _ws(self,_=None):
        i=self._gsi()
        if not i or not HAS_MPL: return
        nm,fl,pu,mt,fi,pk=i; self.ch_lb.config(text=f"Loading {nm} ...",fg="#888")
        threading.Thread(target=self._cbg,args=(nm,fl,pu,mt,fi),daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # Dashboard
    # ═════════════════════════════════════════════════════════════════════════
    def _draw_dashboard(self):
        if not HAS_MPL: return
        self.dash_fig.clear()
        inv=self.raw_ir
        n_prod=sum(1 for r in inv if r[3]=="PROD")
        n_inj=sum(1 for r in inv if r[3]=="INJ")
        n_obsn=sum(1 for r in inv if r[3]=="OBSN")
        n_total=len(inv)
        peak_map={}
        for r in self.raw_wr:
            if r[-1] is not None and r[-1]>0: peak_map[r[0]]=r[-1]
        pv=list(peak_map.values())
        avg_ip=sum(pv)/len(pv) if pv else 0
        med_ip=sorted(pv)[len(pv)//2] if pv else 0
        max_ip=max(pv) if pv else 0
        s2p=[]
        for r in inv:
            if r[3]=="PROD" and r[9] and r[8]:
                days=(r[9]-r[8]).days
                if 0<=days<=365: s2p.append(days)
        avg_s2p=sum(s2p)/len(s2p) if s2p else 0
        monthly=defaultdict(lambda:{"PROD":0,"INJ":0,"OBSN":0})
        for r in inv:
            if r[8]: monthly[r[8].replace(day=1)][r[3] or "OTHER"]+=1
        ms=sorted(monthly.keys())
        by_fld=defaultdict(lambda:{"PROD":0,"INJ":0,"OBSN":0})
        for r in inv: by_fld[r[2] or "Unknown"][r[3] or "OTHER"]+=1
        ip_by_fld=defaultdict(list)
        for r in self.raw_wr:
            if r[-1] and r[-1]>0 and r[WT_FLD]: ip_by_fld[r[WT_FLD]].append(r[-1])

        # Date range label for dashboard title
        from dateutil.relativedelta import relativedelta
        end_disp = self.spud_end - relativedelta(days=1)
        range_str = f"{self.spud:%Y-%m-%d}  to  {end_disp:%Y-%m-%d}"

        gs=self.dash_fig.add_gridspec(2,3,hspace=0.35,wspace=0.3,left=0.06,right=0.97,top=0.90,bottom=0.08)

        # KPI cards
        ax=self.dash_fig.add_subplot(gs[0,0]); ax.axis("off")
        cards=[(f"{n_total}","Total Wells Drilled"),(f"{n_prod}","Producers"),
               (f"{n_inj}","Injectors"),(f"{n_obsn}","Observation"),
               (f"{avg_ip:.0f}","Avg Peak IP (BOPD)"),(f"{med_ip:.0f}","Median Peak IP"),
               (f"{max_ip:.0f}","Max Peak IP"),(f"{avg_s2p:.0f}","Avg Spud-to-Prod (days)")]
        yp=1.0
        for val,label in cards:
            ax.text(0.02,yp,val,fontsize=16,fontweight="bold",color=self.ACC,transform=ax.transAxes,va="top")
            ax.text(0.40,yp-0.01,label,fontsize=9,color="#666",transform=ax.transAxes,va="top"); yp-=0.125
        ax.set_title(f"KPIs  ({range_str})",fontsize=10,fontweight="bold",loc="left",pad=8)

        # Monthly spud trend
        ax2=self.dash_fig.add_subplot(gs[0,1])
        if ms:
            pv2=[monthly[m]["PROD"] for m in ms]; iv=[monthly[m]["INJ"] for m in ms]; ov=[monthly[m]["OBSN"] for m in ms]
            xl=[m.strftime("%b\n%y") for m in ms]; x=range(len(ms))
            ax2.bar(x,pv2,color="#27ae60",label="PROD",width=0.7)
            ax2.bar(x,iv,bottom=pv2,color="#2980b9",label="INJ",width=0.7)
            b2=[p+i for p,i in zip(pv2,iv)]; ax2.bar(x,ov,bottom=b2,color="#e67e22",label="OBSN",width=0.7)
            ax2.set_xticks(list(x)); ax2.set_xticklabels(xl,fontsize=7); ax2.legend(fontsize=7,loc="upper left")
        ax2.set_title("Monthly Spud Count",fontsize=10,fontweight="bold",loc="left",pad=8)
        ax2.tick_params(labelsize=8); ax2.grid(axis="y",alpha=0.3)

        # Wells by field
        ax3=self.dash_fig.add_subplot(gs[0,2])
        fn=sorted(by_fld.keys(),key=lambda f:sum(by_fld[f].values()),reverse=True)
        if fn:
            y=range(len(fn)); pv3=[by_fld[f]["PROD"] for f in fn]; iv3=[by_fld[f]["INJ"] for f in fn]; ov3=[by_fld[f]["OBSN"] for f in fn]
            ax3.barh(y,pv3,color="#27ae60",label="PROD",height=0.6)
            ax3.barh(y,iv3,left=pv3,color="#2980b9",label="INJ",height=0.6)
            lf=[p+i for p,i in zip(pv3,iv3)]; ax3.barh(y,ov3,left=lf,color="#e67e22",label="OBSN",height=0.6)
            ax3.set_yticks(list(y)); ax3.set_yticklabels(fn,fontsize=8); ax3.invert_yaxis()
            ax3.legend(fontsize=7,loc="lower right")
        ax3.set_title("Wells by Field",fontsize=10,fontweight="bold",loc="left",pad=8)
        ax3.tick_params(labelsize=8); ax3.grid(axis="x",alpha=0.3)

        # IP histogram
        ax4=self.dash_fig.add_subplot(gs[1,0:2])
        if pv:
            ax4.hist(pv,bins=25,color="#2980b9",edgecolor="white",alpha=0.85)
            ax4.axvline(avg_ip,color="#e74c3c",linestyle="--",lw=1.5,label=f"Avg: {avg_ip:.0f}")
            ax4.axvline(med_ip,color="#e67e22",linestyle="--",lw=1.5,label=f"Median: {med_ip:.0f}")
            ax4.legend(fontsize=8); ax4.set_xlabel("Peak Oil IP (BOPD)",fontsize=9); ax4.set_ylabel("Count",fontsize=9)
        ax4.set_title("Peak IP Distribution",fontsize=10,fontweight="bold",loc="left",pad=8)
        ax4.tick_params(labelsize=8); ax4.grid(axis="y",alpha=0.3)

        # Avg IP by field
        ax5=self.dash_fig.add_subplot(gs[1,2])
        fipn=sorted(ip_by_fld.keys())
        if fipn:
            avgs=[sum(ip_by_fld[f])/len(ip_by_fld[f]) for f in fipn]; cnts=[len(ip_by_fld[f]) for f in fipn]
            bars=ax5.bar(range(len(fipn)),avgs,color="#27ae60",edgecolor="white",width=0.6)
            ax5.set_xticks(range(len(fipn))); ax5.set_xticklabels(fipn,fontsize=7,rotation=30,ha="right")
            for i,(bb,cnt) in enumerate(zip(bars,cnts)):
                ax5.text(bb.get_x()+bb.get_width()/2,bb.get_height()+1,f"n={cnt}",ha="center",fontsize=7,color="#666")
        ax5.set_ylabel("Avg Peak IP (BOPD)",fontsize=9)
        ax5.set_title("Avg Peak IP by Field",fontsize=10,fontweight="bold",loc="left",pad=8)
        ax5.tick_params(labelsize=8); ax5.grid(axis="y",alpha=0.3)
        self.dash_canvas.draw()

    # ═════════════════════════════════════════════════════════════════════════
    # Chart
    # ═════════════════════════════════════════════════════════════════════════
    def _cbg(self,nm,fl,pu,mt,fi):
        try:
            if pu=="PROD":
                c,r=qry(SQL_PROD_WELL_TESTS,{"cmpl_fac_id":fi,"spud_date":self.spud})
                self.root.after(0,self._dp,c,r,nm,fl)
            elif pu=="INJ":
                c,r=qry(SQL_INJ_DAILY,{"cmpl_fac_id":fi,"start_date":self.spud})
                self.root.after(0,self._di,c,r,nm,fl,mt)
        except Exception as e: self.root.after(0,self._err,str(e))
    def _dp(self,cols,rows,nm,fl):
        self.fig.clear()
        if not rows: self.ch_lb.config(text=f"{nm}: no data.",fg="#c0392b"); self.canvas.draw(); return
        dt,oil,wtr,gas=[],[],[],[]
        for r in rows: dt.append(r[0]); oil.append(r[1] or 0); wtr.append(r[2] or 0); gas.append(r[3] or 0)
        ax=self.fig.add_subplot(111); ln=[]
        if any(v>0 for v in oil): l,=ax.plot(dt,oil,"o-",color="#27ae60",ms=5,lw=1.5,label="Oil (BOPD)"); ln.append(l)
        if any(v>0 for v in wtr): l,=ax.plot(dt,wtr,"s-",color="#2980b9",ms=4,lw=1.2,label="Water (BWPD)"); ln.append(l)
        ax.set_ylabel("Liquid Rate (bbl/d)",fontsize=9); ax.tick_params(labelsize=8); ax.grid(True,alpha=0.25)
        if any(v>0 for v in gas):
            a2=ax.twinx(); l,=a2.plot(dt,gas,"x--",color="#e74c3c",ms=4,lw=1.2,label="Gas (MCFD)"); ln.append(l)
            a2.set_ylabel("Gas (MCFD)",fontsize=9,color="#e74c3c"); a2.tick_params(labelsize=8,labelcolor="#e74c3c")
        ax.legend(ln,[l.get_label() for l in ln],fontsize=8,loc="upper left",framealpha=0.85)
        t=f"{nm}  —  Allocated Well Tests"; t+=f"  ({fl})" if fl else ""
        ax.set_title(t,fontsize=11,fontweight="bold",pad=10); self._fx(ax,dt)
        self.fig.tight_layout(); self.canvas.draw()
        self.ch_lb.config(text=f"{nm} — {len(rows)} tests",fg=self.ACC)
    def _di(self,cols,rows,nm,fl,mt):
        self.fig.clear()
        if not rows: self.ch_lb.config(text=f"{nm}: no data.",fg="#c0392b"); self.canvas.draw(); return
        dt,st,wt=[],[],[]
        for r in rows: dt.append(r[0]); st.append(r[1] or 0); wt.append(r[2] or 0)
        ax=self.fig.add_subplot(111)
        if mt=="Steam":
            ax.plot(dt,st,color="#e67e22",lw=0.9,alpha=0.85,label="Steam (bbl/d)")
            ax.fill_between(dt,st,alpha=0.15,color="#e67e22"); ax.set_ylabel("Steam Inj (bbl/d)",fontsize=9)
        elif mt=="Water":
            ax.plot(dt,wt,color="#2980b9",lw=0.9,alpha=0.85,label="Water (bbl/d)")
            ax.fill_between(dt,wt,alpha=0.15,color="#2980b9"); ax.set_ylabel("Water Inj (bbl/d)",fontsize=9)
        else:
            if any(v>0 for v in st): ax.plot(dt,st,color="#e67e22",lw=0.9,label="Steam")
            if any(v>0 for v in wt): ax.plot(dt,wt,color="#2980b9",lw=0.9,label="Water")
            ax.set_ylabel("Injection (bbl/d)",fontsize=9)
        ax.tick_params(labelsize=8); ax.grid(True,alpha=0.25); ax.legend(fontsize=8,loc="upper left")
        t=f"{nm}  —  Daily Injection"; t+=f"  ({fl})" if fl else ""
        ax.set_title(t,fontsize=11,fontweight="bold",pad=10); self._fx(ax,dt)
        self.fig.tight_layout(); self.canvas.draw()
        self.ch_lb.config(text=f"{nm} — {len(rows):,} pts",fg=self.ACC)
    def _fx(self,ax,dt):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        sp=(max(dt)-min(dt)).days if len(dt)>1 else 30
        if sp>720: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        elif sp>360: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        elif sp>120: ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        self.fig.autofmt_xdate(rotation=45)

def main():
    try: from dateutil.relativedelta import relativedelta
    except ImportError:
        import subprocess; subprocess.check_call([sys.executable,"-m","pip","install","python-dateutil","-q"])
    root=tk.Tk(); App(root); root.mainloop()

if __name__=="__main__": main()