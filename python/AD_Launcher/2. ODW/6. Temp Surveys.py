#Help: Temperature Survey Data
"""
Temperature Survey Viewer  v3.0
================================
5-tab application for downhole temperature survey analysis.

  Tab 1 — Well Finder:        Seed APIs + distance → find nearby wells with surveys
  Tab 2 — Survey Overview:    Well list + overlay chart (all surveys per well)
  Tab 3 — Detailed Measurement: Single-survey chart + depth-by-depth data table
  Tab 4 — Initial Temperature:  Zone avg/min/max from FIRST survey per well
  Tab 5 — Current Temperature:  Zone avg/min/max from LAST survey per well

Usage:  python temp_survey_viewer.py
Requires: oracledb, matplotlib
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading, csv, os, re, math
from datetime import datetime

try:
    import oracledb
except ImportError:
    oracledb = None

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
except ImportError:
    matplotlib = None

# ── DB ────────────────────────────────────────────────────────────────────────
DB_USER, DB_PASS, DB_TNS = "rptguser", "allusers", "ODW"

COLORS = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4",
          "#42d4f4","#f032e6","#bfef45","#fabed4","#469990"]

# ── Theme colours ─────────────────────────────────────────────────────────────
BG       = "#f5f6fa"
CARD_BG  = "#ffffff"
ACCENT   = "#2563eb"
ACCENT2  = "#16a34a"
MUTED    = "#64748b"
FONT     = "Segoe UI"
MONO     = "Consolas"


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def _fd(dt):
    return dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else (str(dt)[:10] if dt else "")

def _sf(v, fmt=".1f"):
    return "" if v is None else f"{v:{fmt}}"

def _zone_stats(pts, top, bot):
    f = [p[2] for p in pts if p[0] is not None and p[2] is not None and top <= p[0] <= bot]
    return (sum(f)/len(f), min(f), max(f), len(f)) if f else (None, None, None, 0)

def _parse_api_text(raw):
    tokens = re.split(r"[,\s\n\r]+", raw.strip())
    apis = []
    for t in tokens:
        t = t.strip().replace("-", "")
        if not t: continue
        if len(t) == 9: t = "0" + t
        if len(t) == 10 and t.isdigit(): apis.append(t)
    return list(dict.fromkeys(apis))


# ══════════════════════════════════════════════════════════════════════════════
#  Data layer  (unchanged logic, condensed)
# ══════════════════════════════════════════════════════════════════════════════
def _conn():
    try: oracledb.init_oracle_client()
    except: pass
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_TNS)

def _q_headers(conn, apis):
    if not apis: return {}
    ph = ",".join(f":a{i}" for i in range(len(apis)))
    bv = {f"a{i}": a for i,a in enumerate(apis)}
    cur = conn.cursor()
    cur.execute(f"""
        SELECT cd.cmpl_nme, cd.well_api_nbr, cd.cmpl_fac_id, cd.well_fac_id,
               lcd.log_curv_type_mnmn_txt, lcd.lggg_pass_strt_dttm,
               lcd.top_md_qty, lcd.base_md_qty, lcd.curv_min_valu,
               lcd.curv_max_valu, lcd.tot_curv_smpl, lcd.log_curv_dmn_key
        FROM dwrptg.log_curv_dmn lcd
        JOIN dwrptg.cmpl_dmn cd ON lcd.cmpl_fac_id=cd.cmpl_fac_id
        WHERE cd.well_api_nbr IN ({ph}) AND cd.actv_indc='Y'
          AND lcd.log_curv_type_mnmn_txt IN ('TS','OFOT')
        ORDER BY cd.well_api_nbr, lcd.lggg_pass_strt_dttm DESC""", bv)
    rows = cur.fetchall(); cur.close()
    r = {}
    for x in rows:
        r.setdefault(x[1],[]).append((x[0],x[2],x[3],x[4],x[5],x[6],x[7],x[8],x[9],x[10],x[11]))
    return r

def _q_depth(conn, keys):
    if not keys: return {}
    ph = ",".join(f":k{i}" for i in range(len(keys)))
    bv = {f"k{i}": int(k) for i,k in enumerate(keys)}
    cur = conn.cursor()
    cur.execute(f"""SELECT lmf.log_curv_dmn_key,lmf.md_qty,lmf.tvd_qty,
        lmf.msd_qty,lmf.lthsg_unit_nme FROM dwrptg.log_curv_msmt_fact lmf
        WHERE lmf.log_curv_dmn_key IN ({ph}) ORDER BY lmf.log_curv_dmn_key,lmf.md_qty""", bv)
    rows = cur.fetchall(); cur.close()
    r = {}
    for k,md,tvd,t,z in rows: r.setdefault(k,[]).append((md,tvd,t,z or ""))
    return r

def _q_nearby(conn, seeds, dist):
    if not seeds: return []
    ph = ",".join(f":a{i}" for i in range(len(seeds)))
    bv = {f"a{i}": a for i,a in enumerate(seeds)}; bv["dist"]=dist
    cur = conn.cursor()
    cur.execute(f"""
        WITH seed AS (
            SELECT cd.cmpl_nme,cd.well_api_nbr,wd.total_dpth_xcrd_qty x,wd.total_dpth_ycrd_qty y
            FROM dwrptg.cmpl_dmn cd JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id=wd.well_fac_id
            WHERE cd.well_api_nbr IN ({ph}) AND cd.actv_indc='Y' AND wd.total_dpth_xcrd_qty IS NOT NULL),
        cand AS (
            SELECT cd.cmpl_nme,cd.well_api_nbr,cd.prim_purp_type_cde,
                   wd.total_dpth_xcrd_qty x,wd.total_dpth_ycrd_qty y,
                   COUNT(DISTINCT lcd.log_curv_dmn_key) n
            FROM dwrptg.cmpl_dmn cd JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id=wd.well_fac_id
            JOIN dwrptg.log_curv_dmn lcd ON lcd.cmpl_fac_id=cd.cmpl_fac_id
            WHERE cd.actv_indc='Y' AND wd.total_dpth_xcrd_qty IS NOT NULL
              AND lcd.log_curv_type_mnmn_txt IN ('TS','OFOT')
            GROUP BY cd.cmpl_nme,cd.well_api_nbr,cd.prim_purp_type_cde,
                     wd.total_dpth_xcrd_qty,wd.total_dpth_ycrd_qty)
        SELECT c.cmpl_nme,c.well_api_nbr,c.prim_purp_type_cde,c.x,c.y,c.n,
               s.cmpl_nme,s.well_api_nbr,
               ROUND(SQRT(POWER(c.x-s.x,2)+POWER(c.y-s.y,2)),0) d
        FROM cand c CROSS JOIN seed s
        WHERE SQRT(POWER(c.x-s.x,2)+POWER(c.y-s.y,2))<=:dist
        ORDER BY d,c.cmpl_nme""", bv)
    rows = cur.fetchall(); cur.close()
    seen = {}
    for r in rows:
        api = r[1]
        if api not in seen or r[8] < seen[api]["dist"]:
            seen[api] = dict(well=r[0],api=r[1],purp=r[2],x=r[3],y=r[4],
                             nsurv=r[5],seed=r[6],seed_api=r[7],dist=r[8])
    return sorted(seen.values(), key=lambda x: x["dist"])


# ══════════════════════════════════════════════════════════════════════════════
#  Application
# ══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    H = dict(CMPL=0,FAC=1,WFAC=2,CODE=3,DATE=4,TOP=5,TAG=6,MIN=7,MAX=8,NPTS=9,KEY=10)

    def __init__(self):
        super().__init__()
        self.title("Temperature Survey Viewer")
        self.geometry("1440x880")
        self.minsize(1050, 680)
        self.configure(bg=BG)

        self.hdrs = {}; self.ddata = {}; self.api_list = []
        self._apply_style()
        self._build()

    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, font=(FONT, 10))
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", padding=[14, 6], font=(FONT, 10))
        s.map("TNotebook.Tab",
              background=[("selected", CARD_BG), ("!selected", "#e2e8f0")],
              foreground=[("selected", ACCENT), ("!selected", "#475569")])
        s.configure("Treeview", rowheight=24, font=(FONT, 9))
        s.configure("Treeview.Heading", font=(FONT, 9, "bold"),
                    background="#e2e8f0", foreground="#1e293b")
        s.map("Treeview", background=[("selected", "#dbeafe")],
              foreground=[("selected", "#1e40af")])
        s.configure("Accent.TButton", font=(FONT, 10, "bold"),
                    foreground="white", background=ACCENT, padding=[12, 4])
        s.map("Accent.TButton",
              background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])
        s.configure("Green.TButton", font=(FONT, 10, "bold"),
                    foreground="white", background=ACCENT2, padding=[12, 4])
        s.map("Green.TButton",
              background=[("active", "#15803d"), ("disabled", "#94a3b8")])
        s.configure("Tool.TButton", font=(FONT, 9), padding=[8, 2])
        s.configure("Card.TFrame", background=CARD_BG, relief="solid", borderwidth=1)
        s.configure("Card.TLabelframe", background=CARD_BG)
        s.configure("Card.TLabelframe.Label", background=CARD_BG,
                    font=(FONT, 10, "bold"), foreground="#1e293b")
        s.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 8, "italic"))

    # ── Reusable widget helpers ───────────────────────────────────────────

    def _lbl(self, p, t=None, **kw):
        if t is not None:
            kw["text"] = t
        return ttk.Label(p, **kw)

    def _entry(self, p, var, w=10):
        return ttk.Entry(p, textvariable=var, width=w, font=(MONO, 10))

    def _btn(self, p, t, cmd, style="Tool.TButton", **kw):
        return ttk.Button(p, text=t, command=cmd, style=style, **kw)

    def _tree_with_scroll(self, parent, cols, col_cfg):
        container = ttk.Frame(parent)
        tree = ttk.Treeview(container, columns=cols, show="headings", selectmode="browse")
        for cid, hd, w, anch in col_cfg:
            tree.heading(cid, text=hd)
            tree.column(cid, width=w, anchor=anch, minwidth=40)
        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        return container, tree

    # ──────────────────────────────────────────────────────────────────────
    #  BUILD
    # ──────────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Top bar: API input + Load ─────────────────────────────────────
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill=tk.X)
        top.columnconfigure(1, weight=1)

        self._lbl(top, "API Numbers:  ", font=(FONT, 10, "bold")).grid(
            row=0, column=0, sticky=tk.W)
        self.api_text = tk.Text(top, height=2, width=60, font=(MONO, 10),
                                relief="solid", bd=1, highlightthickness=0)
        self.api_text.grid(row=0, column=1, sticky=tk.EW, padx=(0, 8))

        btn_box = ttk.Frame(top)
        btn_box.grid(row=0, column=2, sticky=tk.N)
        self.load_btn = self._btn(btn_box, "  Load Surveys  ", self._on_load,
                                   style="Accent.TButton")
        self.load_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btn_box, "Clear", self._on_clear).pack(side=tk.LEFT)

        # ── Status bar ────────────────────────────────────────────────────
        bot = ttk.Frame(self)
        bot.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Ready — start with Well Finder or paste APIs above")
        ttk.Label(bot, textvariable=self.status_var, font=(FONT, 9),
                  foreground=MUTED, padding=(8, 4)).pack(fill=tk.X)
        self.progress = ttk.Progressbar(self, mode="indeterminate")

        # ── Notebook ──────────────────────────────────────────────────────
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 4))

        self._build_t0()
        self._build_t1()
        self._build_t2()
        self._build_t3()
        self._build_t4()

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 0 — Well Finder
    # ══════════════════════════════════════════════════════════════════════

    def _build_t0(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  Well Finder  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        # ── Card: input controls ──────────────────────────────────────────
        card = ttk.LabelFrame(tab, text="  Search Parameters  ",
                               style="Card.TLabelframe", padding=12)
        card.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        card.columnconfigure(1, weight=1)

        self._lbl(card, "Seed APIs:", font=(FONT, 10)).grid(row=0, column=0, sticky=tk.NW, padx=(0, 8))
        self.t0_api = tk.Text(card, height=3, width=50, font=(MONO, 10),
                               relief="solid", bd=1, highlightthickness=0)
        self.t0_api.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12))
        self.t0_api.insert("1.0", "0402979733")

        right = ttk.Frame(card)
        right.grid(row=0, column=2, sticky=tk.N)

        r1 = ttk.Frame(right)
        r1.pack(fill=tk.X, pady=(0, 8))
        self._lbl(r1, "Radius (ft):").pack(side=tk.LEFT)
        self.t0_dist = tk.StringVar(value="500")
        self._entry(r1, self.t0_dist, w=8).pack(side=tk.LEFT, padx=(6, 0))

        r2 = ttk.Frame(right)
        r2.pack(fill=tk.X)
        self.t0_search_btn = self._btn(r2, "  Search  ", self._t0_search,
                                        style="Accent.TButton")
        self.t0_search_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._btn(r2, "Use as Input \u2192", self._t0_use,
                  style="Green.TButton").pack(side=tk.LEFT)

        # ── Info + export row ─────────────────────────────────────────────
        info_row = ttk.Frame(tab)
        info_row.grid(row=1, column=0, sticky=tk.EW, pady=(0, 4))
        self.t0_info = tk.StringVar(value="Enter seed APIs and a search radius, then click Search.")
        self._lbl(info_row, textvariable=self.t0_info, style="Muted.TLabel").pack(side=tk.LEFT)
        self._btn(info_row, "Copy", self._t0_copy).pack(side=tk.RIGHT, padx=(4, 0))
        self._btn(info_row, "Export CSV", self._t0_export).pack(side=tk.RIGHT)

        # ── Results table ─────────────────────────────────────────────────
        cfg = [("well","Well",130,tk.W),("api","API",105,tk.CENTER),
               ("purp","Purpose",65,tk.CENTER),("nsurv","# Surveys",80,tk.CENTER),
               ("dist","Distance (ft)",95,tk.CENTER),("seed","Nearest Seed",130,tk.W),
               ("seed_api","Seed API",105,tk.CENTER),
               ("x","BH X",95,tk.CENTER),("y","BH Y",95,tk.CENTER)]
        cont, self.t0_tree = self._tree_with_scroll(tab, [c[0] for c in cfg], cfg)
        cont.grid(row=2, column=0, sticky=tk.NSEW)

        self._t0_res = []

    # ── Tab 0 logic ───────────────────────────────────────────────────────

    def _t0_search(self):
        seeds = _parse_api_text(self.t0_api.get("1.0", tk.END))
        if not seeds: messagebox.showinfo("No APIs","Enter at least one seed API."); return
        try:
            d = float(self.t0_dist.get())
            assert d > 0
        except: messagebox.showwarning("Distance","Enter a positive number."); return
        self.t0_search_btn.state(["disabled"])
        self._show_progress()
        self.status_var.set(f"Searching within {d:.0f} ft of {len(seeds)} seed(s)...")
        def bg():
            try:
                c = _conn(); res = _q_nearby(c, seeds, d); c.close()
                self.after(0, lambda: self._t0_done(res, seeds, d))
            except Exception as e:
                self.after(0, lambda: self._err(e, self.t0_search_btn))
        threading.Thread(target=bg, daemon=True).start()

    def _t0_done(self, res, seeds, d):
        self._hide_progress(); self.t0_search_btn.state(["!disabled"])
        self._t0_res = res
        self.t0_tree.delete(*self.t0_tree.get_children())
        for r in res:
            self.t0_tree.insert("",tk.END, values=(
                r["well"],r["api"],r["purp"],r["nsurv"],
                f"{r['dist']:.0f}",r["seed"],r["seed_api"],
                f"{r['x']:.1f}",f"{r['y']:.1f}"))
        n = len(res)
        self.t0_info.set(f"Found {n} well(s) within {d:.0f} ft of {len(seeds)} seed(s).  "
                         f"Click 'Use as Input \u2192' to load into survey viewer.")
        self.status_var.set(f"Well Finder: {n} wells found")

    def _t0_use(self):
        if not self._t0_res: messagebox.showinfo("No Results","Run a search first."); return
        apis = list(dict.fromkeys(r["api"] for r in self._t0_res))
        self.api_text.delete("1.0",tk.END)
        self.api_text.insert("1.0", ", ".join(apis))
        self.nb.select(1); self._on_load()

    def _t0_export(self):
        if not self._t0_res: return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
            initialfile="well_finder.csv", filetypes=[("CSV","*.csv")])
        if not p: return
        with open(p,"w",newline="") as f:
            w=csv.writer(f)
            w.writerow(["Well","API","Purpose","# Surveys","Distance ft",
                         "Nearest Seed","Seed API","BH X","BH Y"])
            for r in self._t0_res:
                w.writerow([r["well"],r["api"],r["purp"],r["nsurv"],
                            r["dist"],r["seed"],r["seed_api"],r["x"],r["y"]])
        self.status_var.set(f"Exported to {os.path.basename(p)}")

    def _t0_copy(self):
        if not self._t0_res: return
        h="Well\tAPI\tPurpose\t# Surveys\tDist ft\tNearest Seed\tSeed API\tBH X\tBH Y"
        lines=[h]+[f"{r['well']}\t{r['api']}\t{r['purp']}\t{r['nsurv']}\t"
               f"{r['dist']:.0f}\t{r['seed']}\t{r['seed_api']}\t{r['x']:.1f}\t{r['y']:.1f}"
               for r in self._t0_res]
        self.clipboard_clear(); self.clipboard_append("\n".join(lines))
        self.status_var.set(f"Copied {len(self._t0_res)} rows")

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 1 — Survey Overview
    # ══════════════════════════════════════════════════════════════════════

    def _build_t1(self):
        tab = ttk.Frame(self.nb, padding=4)
        self.nb.add(tab, text="  Survey Overview  ")

        pw = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)

        # ── LEFT: well list (top) + survey checklist (bottom) ─────────────
        left = ttk.Frame(pw, padding=4)
        pw.add(left, weight=1)

        lpw = ttk.PanedWindow(left, orient=tk.VERTICAL)
        lpw.pack(fill=tk.BOTH, expand=True)

        # Well list pane
        well_pane = ttk.Frame(lpw)
        lpw.add(well_pane, weight=2)

        whdr = ttk.Frame(well_pane)
        whdr.pack(fill=tk.X, pady=(0, 4))
        self._lbl(whdr, "Wells", font=(FONT, 10, "bold")).pack(side=tk.LEFT)
        self._btn(whdr, "Export CSV", self._t1_export).pack(side=tk.RIGHT)

        wcfg = [("well","Well",130,tk.W),("api","API",105,tk.CENTER),
                ("n","# Surveys",80,tk.CENTER),("first","First",95,tk.CENTER),
                ("last","Last",95,tk.CENTER)]
        wcont, self.t1_tree = self._tree_with_scroll(well_pane, [c[0] for c in wcfg], wcfg)
        wcont.pack(fill=tk.BOTH, expand=True)
        self.t1_tree.bind("<<TreeviewSelect>>", self._t1_sel)

        # Survey checklist pane
        surv_pane = ttk.Frame(lpw)
        lpw.add(surv_pane, weight=1)

        shdr = ttk.Frame(surv_pane)
        shdr.pack(fill=tk.X, pady=(4, 4))
        self._lbl(shdr, "Surveys (click to toggle)", font=(FONT, 9, "bold")).pack(side=tk.LEFT)
        self._btn(shdr, "Select All", self._t1_selall).pack(side=tk.RIGHT, padx=(4, 0))
        self._btn(shdr, "Deselect All", self._t1_dselall).pack(side=tk.RIGHT)

        scfg = [("chk","\u2713",35,tk.CENTER),("date","Date",95,tk.CENTER),
                ("type","Type",50,tk.CENTER),("top","Top MD",75,tk.CENTER),
                ("tag","Tag MD",75,tk.CENTER),("mn","Min \u00b0F",70,tk.CENTER),
                ("mx","Max \u00b0F",70,tk.CENTER)]
        scont, self.t1_stree = self._tree_with_scroll(surv_pane, [c[0] for c in scfg], scfg)
        scont.pack(fill=tk.BOTH, expand=True)
        self.t1_stree.bind("<ButtonRelease-1>", self._t1_toggle)

        # track which surveys are checked: {log_curv_dmn_key: bool}
        self._t1_checked = {}
        self._t1_cur_api = None

        # ── RIGHT: chart ──────────────────────────────────────────────────
        right = ttk.Frame(pw, padding=4)
        pw.add(right, weight=1)
        self.t1_fig = Figure(figsize=(6,5), dpi=100, facecolor=CARD_BG)
        self.t1_ax = self.t1_fig.add_subplot(111)
        self.t1_canvas = FigureCanvasTkAgg(self.t1_fig, master=right)
        self.t1_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.t1_toolbar = NavigationToolbar2Tk(self.t1_canvas, right)
        self.t1_toolbar.update()

    def _t1_pop(self):
        self.t1_tree.delete(*self.t1_tree.get_children())
        self._t1_checked.clear()
        # initialise all surveys as checked
        for api in self.api_list:
            for e in self.hdrs.get(api, []):
                self._t1_checked[e[10]] = True

        for api in self.api_list:
            es = self.hdrs.get(api, [])
            if not es:
                self.t1_tree.insert("",tk.END,iid=api,
                    values=("\u2014 no surveys \u2014",api,0,"",""))
                continue
            self.t1_tree.insert("",tk.END,iid=api,values=(
                es[0][0], api, len(es), _fd(es[-1][4]), _fd(es[0][4])))
        for api in self.api_list:
            if self.hdrs.get(api):
                self.t1_tree.selection_set(api); self._t1_sel(); break

    def _t1_sel(self, e=None):
        s = self.t1_tree.selection()
        if not s: return
        api = s[0]
        self._t1_cur_api = api
        self._t1_pop_surveys(api)
        self._t1_draw(api)

    def _t1_pop_surveys(self, api):
        """Populate the survey checklist for the selected well."""
        self.t1_stree.delete(*self.t1_stree.get_children())
        es = self.hdrs.get(api, [])
        for e in es:
            key = e[10]
            chk = "\u2713" if self._t1_checked.get(key, True) else ""
            self.t1_stree.insert("", tk.END, iid=str(key), values=(
                chk, _fd(e[4]), e[3],
                _sf(e[5], ".0f"), _sf(e[6], ".0f"),
                _sf(e[7]), _sf(e[8])))

    def _t1_toggle(self, event=None):
        """Toggle check on clicked survey row and redraw chart."""
        row = self.t1_stree.identify_row(event.y)
        if not row: return
        key = int(row)
        self._t1_checked[key] = not self._t1_checked.get(key, True)
        # update the checkmark display
        chk = "\u2713" if self._t1_checked[key] else ""
        vals = list(self.t1_stree.item(row, "values"))
        vals[0] = chk
        self.t1_stree.item(row, values=vals)
        # redraw chart
        if self._t1_cur_api:
            self._t1_draw(self._t1_cur_api)

    def _t1_selall(self):
        if not self._t1_cur_api: return
        for e in self.hdrs.get(self._t1_cur_api, []):
            self._t1_checked[e[10]] = True
        self._t1_pop_surveys(self._t1_cur_api)
        self._t1_draw(self._t1_cur_api)

    def _t1_dselall(self):
        if not self._t1_cur_api: return
        for e in self.hdrs.get(self._t1_cur_api, []):
            self._t1_checked[e[10]] = False
        self._t1_pop_surveys(self._t1_cur_api)
        self._t1_draw(self._t1_cur_api)

    def _t1_draw(self, api):
        es = self.hdrs.get(api, []); ax = self.t1_ax; ax.clear()
        if not es:
            ax.set_title(f"No surveys \u2014 API {api}"); self.t1_canvas.draw(); return
        ax.set_title(f"{es[0][0]}  ({api})", fontsize=12, fontweight="bold")
        ax.set_xlabel("Temperature (\u00b0F)"); ax.set_ylabel("Measured Depth (ft)")
        ax.invert_yaxis(); ax.grid(True, alpha=0.3)
        plotted = 0
        for i, e in enumerate(es):
            key = e[10]
            if not self._t1_checked.get(key, True):
                continue
            pts = self.ddata.get(key, [])
            if pts:
                ax.plot([p[2] for p in pts],[p[0] for p in pts],
                        color=COLORS[i%len(COLORS)], lw=1,
                        label=f"{_fd(e[4])} ({e[3]})")
                plotted += 1
        if plotted:
            ax.legend(fontsize=7, loc="lower right")
        self.t1_fig.tight_layout(); self.t1_canvas.draw()

    def _t1_export(self):
        if not self.hdrs: return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
            initialfile="survey_overview.csv", filetypes=[("CSV","*.csv")])
        if not p: return
        with open(p,"w",newline="") as f:
            w=csv.writer(f)
            w.writerow(["Well","API","Date","Type","Top MD","Tag MD","Min F","Max F","Samples"])
            for api in self.api_list:
                for e in self.hdrs.get(api,[]):
                    w.writerow([e[0],api,_fd(e[4]),e[3],e[5],e[6],e[7],e[8],e[9]])
        self.status_var.set(f"Exported to {os.path.basename(p)}")

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 2 — Detailed Measurement
    # ══════════════════════════════════════════════════════════════════════

    def _build_t2(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  Detailed Measurement  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # ── Selector bar ──────────────────────────────────────────────────
        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))

        self._lbl(bar, "Well:", font=(FONT, 10)).pack(side=tk.LEFT)
        self.t2_wv = tk.StringVar()
        self.t2_wcb = ttk.Combobox(bar, textvariable=self.t2_wv, state="readonly",
                                    width=36, font=(MONO, 10))
        self.t2_wcb.pack(side=tk.LEFT, padx=(4, 16))
        self.t2_wcb.bind("<<ComboboxSelected>>", self._t2_wc)

        self._lbl(bar, "Survey:", font=(FONT, 10)).pack(side=tk.LEFT)
        self.t2_sv = tk.StringVar()
        self.t2_scb = ttk.Combobox(bar, textvariable=self.t2_sv, state="readonly",
                                    width=32, font=(MONO, 10))
        self.t2_scb.pack(side=tk.LEFT, padx=(4, 16))
        self.t2_scb.bind("<<ComboboxSelected>>", self._t2_sc)

        self._btn(bar, "Copy", self._t2_copy).pack(side=tk.RIGHT, padx=(4, 0))
        self._btn(bar, "Export CSV", self._t2_exp).pack(side=tk.RIGHT)

        # ── Paned: chart / table ──────────────────────────────────────────
        pw = ttk.PanedWindow(tab, orient=tk.VERTICAL)
        pw.grid(row=1, column=0, sticky=tk.NSEW)

        cf = ttk.Frame(pw)
        pw.add(cf, weight=1)
        self.t2_fig = Figure(figsize=(8,3), dpi=100, facecolor=CARD_BG)
        self.t2_ax = self.t2_fig.add_subplot(111)
        self.t2_canvas = FigureCanvasTkAgg(self.t2_fig, master=cf)
        self.t2_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.t2_tb = NavigationToolbar2Tk(self.t2_canvas, cf); self.t2_tb.update()

        tf = ttk.Frame(pw)
        pw.add(tf, weight=1)
        self.t2_info = tk.StringVar(value="Select a well and survey above")
        self._lbl(tf, textvariable=self.t2_info, style="Muted.TLabel").pack(
            fill=tk.X, padx=4, pady=(4, 2))
        cfg = [("#","#",50,tk.CENTER),("md","MD (ft)",90,tk.CENTER),
               ("tvd","TVD (ft)",90,tk.CENTER),("t","Temp (\u00b0F)",110,tk.CENTER),
               ("z","Zone",180,tk.W)]
        cont, self.t2_tree = self._tree_with_scroll(tf, [c[0] for c in cfg], cfg)
        cont.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self._t2m = {}; self._t2am = {}
        self._t2k = self._t2c = self._t2d = None

    def _t2_ref(self):
        items = []; self._t2am = {}
        for api in self.api_list:
            es = self.hdrs.get(api,[])
            if not es: continue
            l = f"{es[0][0]}  ({api})"; items.append(l); self._t2am[l] = api
        self.t2_wcb["values"] = items
        if items: self.t2_wcb.current(0); self._t2_wc()

    def _t2_wc(self, e=None):
        api = self._t2am.get(self.t2_wv.get())
        if not api: return
        items = []; self._t2m = {}
        for en in self.hdrs.get(api,[]):
            k = en[10]; n = len(self.ddata.get(k,[]))
            d = f"{_fd(en[4])}  ({en[3]})  \u2014  {n} pts"
            items.append(d); self._t2m[d] = (en, k)
        self.t2_scb["values"] = items
        if items: self.t2_scb.current(0); self._t2_sc()

    def _t2_sc(self, e=None):
        m = self._t2m.get(self.t2_sv.get())
        if not m: return
        en, k = m; c = en[0]; cd = en[3]; ds = _fd(en[4])
        self._t2k, self._t2c, self._t2d = k, c, ds
        pts = self.ddata.get(k, [])
        ax = self.t2_ax; ax.clear()
        ax.set_title(f"{c} \u2014 {ds} ({cd})", fontsize=11, fontweight="bold")
        ax.set_xlabel("Temperature (\u00b0F)"); ax.set_ylabel("Measured Depth (ft)")
        ax.invert_yaxis(); ax.grid(True, alpha=0.3)
        if pts:
            D = [p[0] for p in pts]; T = [p[2] for p in pts]
            ax.plot(T, D, color="#4363d8", lw=1)
            ax.fill_betweenx(D, T, min(T), alpha=0.07, color="#4363d8")
            mn, mx = min(T), max(T); mi, xi = T.index(mn), T.index(mx)
            ax.plot(mn, D[mi], "v", color="#3cb44b", ms=8,
                    label=f"Min {mn:.1f}\u00b0F @ {D[mi]:.0f} ft")
            ax.plot(mx, D[xi], "^", color="#e6194b", ms=8,
                    label=f"Max {mx:.1f}\u00b0F @ {D[xi]:.0f} ft")
            ax.legend(fontsize=8, loc="lower right")
        self.t2_fig.tight_layout(); self.t2_canvas.draw()
        self.t2_tree.delete(*self.t2_tree.get_children())
        for i, (md,tvd,t,z) in enumerate(pts, 1):
            self.t2_tree.insert("",tk.END, values=(i,_sf(md),_sf(tvd),_sf(t,".2f"),z))
        self.t2_info.set(f"{c} | {ds} ({cd}) | {len(pts)} pts | "
            f"Top: {_sf(en[5],'.0f')} ft | Tag: {_sf(en[6],'.0f')} ft | "
            f"Min: {_sf(en[7])}\u00b0F | Max: {_sf(en[8])}\u00b0F")

    def _t2_exp(self):
        if not self._t2k: messagebox.showinfo("No Data","Select a survey."); return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
            initialfile=f"detail_{self._t2c}_{(self._t2d or '').replace('-','')}.csv",
            filetypes=[("CSV","*.csv")])
        if not p: return
        pts = self.ddata.get(self._t2k,[])
        with open(p,"w",newline="") as f:
            w=csv.writer(f)
            w.writerow(["#","Well","Date","MD","TVD","Temp_F","Zone"])
            for i,(md,tvd,t,z) in enumerate(pts,1):
                w.writerow([i,self._t2c,self._t2d,md,tvd,t,z])
        self.status_var.set(f"Exported {len(pts)} rows")

    def _t2_copy(self):
        if not self._t2k: return
        pts = self.ddata.get(self._t2k,[])
        lines = ["#\tMD\tTVD\tTemp_F\tZone"]
        for i,(md,tvd,t,z) in enumerate(pts,1):
            lines.append(f"{i}\t{_sf(md)}\t{_sf(tvd)}\t{_sf(t,'.2f')}\t{z}")
        self.clipboard_clear(); self.clipboard_append("\n".join(lines))
        self.status_var.set(f"Copied {len(pts)} rows")

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 3 & 4 — Initial / Current Temperature
    # ══════════════════════════════════════════════════════════════════════

    _TT = [("well","Well",130,tk.W),("api","API",105,tk.CENTER),
           ("date","Survey Date \u25b2",110,tk.CENTER),("type","Type",55,tk.CENTER),
           ("stop","Survey Top",90,tk.CENTER),("stag","Survey Tag",90,tk.CENTER),
           ("zt","Zone Top",85,tk.CENTER),("zb","Zone Bot",85,tk.CENTER),
           ("avg","Avg \u00b0F",90,tk.CENTER),("mn","Min \u00b0F",90,tk.CENTER),
           ("mx","Max \u00b0F",90,tk.CENTER),("np","# Pts",70,tk.CENTER)]

    def _build_tt(self, title, which):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text=f"  {title}  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        # ── Controls card ─────────────────────────────────────────────────
        card = ttk.LabelFrame(tab, text="  Zone Depth & Date Filter  ",
                               style="Card.TLabelframe", padding=(12, 8))
        card.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))

        r1 = ttk.Frame(card); r1.pack(fill=tk.X, pady=(0, 6))
        self._lbl(r1, "Default Zone Top (ft):").pack(side=tk.LEFT)
        top_v = tk.StringVar()
        self._entry(r1, top_v, 10).pack(side=tk.LEFT, padx=(4, 20))
        self._lbl(r1, "Default Zone Bot (ft):").pack(side=tk.LEFT)
        bot_v = tk.StringVar()
        self._entry(r1, bot_v, 10).pack(side=tk.LEFT, padx=(4, 12))
        self._btn(r1, "Apply to All", lambda: self._tt_applyall(st),
                  style="Accent.TButton").pack(side=tk.LEFT, padx=(8, 0))

        lbl = "First (Oldest)" if which == "first" else "Last (Most Recent)"
        self._lbl(r1, f"  Using: {lbl} survey", style="Muted.TLabel").pack(
            side=tk.LEFT, padx=(16, 0))

        r2 = ttk.Frame(card); r2.pack(fill=tk.X)
        self._lbl(r2, "Date From:").pack(side=tk.LEFT)
        df_v = tk.StringVar()
        self._entry(r2, df_v, 12).pack(side=tk.LEFT, padx=(4, 12))
        self._lbl(r2, "To:").pack(side=tk.LEFT)
        dt_v = tk.StringVar()
        self._entry(r2, dt_v, 12).pack(side=tk.LEFT, padx=(4, 8))
        self._btn(r2, "Filter", lambda: self._tt_dfilt(st)).pack(side=tk.LEFT, padx=(4, 4))
        self._btn(r2, "Clear Filter", lambda: self._tt_dclear(st)).pack(side=tk.LEFT)
        self._lbl(r2, "(YYYY-MM-DD)   Dbl-click Zone Top/Bot to edit per well",
                  style="Muted.TLabel").pack(side=tk.LEFT, padx=(12, 0))

        self._btn(r2, "Copy", lambda: self._tt_copy(st)).pack(side=tk.RIGHT, padx=(4,0))
        self._btn(r2, "Export CSV", lambda: self._tt_exp(st, title)).pack(side=tk.RIGHT)

        # ── Table ─────────────────────────────────────────────────────────
        cont, tree = self._tree_with_scroll(tab, [c[0] for c in self._TT], self._TT)
        cont.grid(row=2, column=0, sticky=tk.NSEW)

        tree.heading("date", command=lambda: self._tt_sort(st))
        tree.bind("<Double-1>", lambda e: self._tt_dblclick(e, st))

        st = {"tree": tree, "tv": top_v, "bv": bot_v, "w": which,
              "ws": {}, "det": set(), "ord": [], "dfv": df_v, "dtv": dt_v,
              "asc": [True]}
        return st

    def _build_t3(self): self._t3 = self._build_tt("Initial Temperature", "first")
    def _build_t4(self): self._t4 = self._build_tt("Current Temperature", "last")

    def _tt_pop(self, st):
        tree, ws = st["tree"], st["ws"]
        st["det"].clear(); st["ord"] = []
        tree.delete(*tree.get_children())
        for api in self.api_list:
            es = self.hdrs.get(api,[])
            if not es: continue
            en = es[-1] if st["w"]=="first" else es[0]
            pts = self.ddata.get(en[10],[])
            if not pts: continue
            if api not in ws: ws[api] = {"top": None, "bot": None}
            st["ord"].append(api)
            self._tt_ins(st, api, en, pts)

    def _tt_vals(self, st, api, en, pts):
        w = st["ws"][api]
        mds = [p[0] for p in pts if p[0] is not None]
        if not mds: return None
        zt = w["top"] if w["top"] is not None else min(mds)
        zb = w["bot"] if w["bot"] is not None else max(mds)
        a, mn, mx, n = _zone_stats(pts, zt, zb)
        return (en[0],api,_fd(en[4]),en[3],_sf(en[5],".0f"),_sf(en[6],".0f"),
                _sf(zt,".0f"),_sf(zb,".0f"),_sf(a,".2f"),_sf(mn,".2f"),_sf(mx,".2f"),n)

    def _tt_ins(self, st, api, en, pts):
        tree = st["tree"]; v = self._tt_vals(st, api, en, pts)
        if v is None: return
        if tree.exists(api): tree.item(api, values=v)
        else: tree.insert("",tk.END,iid=api,values=v)

    def _tt_recalc(self, st, api):
        es = self.hdrs.get(api,[])
        if not es: return
        en = es[-1] if st["w"]=="first" else es[0]
        pts = self.ddata.get(en[10],[])
        if pts: self._tt_ins(st, api, en, pts)

    def _tt_applyall(self, st):
        try: gt = float(st["tv"].get())
        except: gt = None
        try: gb = float(st["bv"].get())
        except: gb = None
        for api in st["ws"]:
            st["ws"][api]["top"] = gt; st["ws"][api]["bot"] = gb
            self._tt_recalc(st, api)

    def _tt_dfilt(self, st):
        tree, det = st["tree"], st["det"]
        df, dt = st["dfv"].get().strip(), st["dtv"].get().strip()
        for api in st["ord"]:
            if api in det: tree.reattach(api,"","end"); det.discard(api)
        if not df and not dt:
            self.status_var.set(f"Showing all {len(st['ord'])} wells"); return
        for api in st["ord"]:
            if not tree.exists(api): continue
            d = str(tree.item(api,"values")[2])
            if (df and d < df) or (dt and d > dt): tree.detach(api); det.add(api)
        self.status_var.set(f"Showing {len(tree.get_children())} of {len(st['ord'])} wells")

    def _tt_dclear(self, st):
        st["dfv"].set(""); st["dtv"].set("")
        for api in st["ord"]:
            if api in st["det"]: st["tree"].reattach(api,"","end"); st["det"].discard(api)
        self.status_var.set(f"Showing all {len(st['ord'])} wells")

    def _tt_sort(self, st):
        tree, asc = st["tree"], st["asc"]
        items = [(iid, str(tree.item(iid,"values")[2])) for iid in tree.get_children()]
        items.sort(key=lambda x: x[1], reverse=not asc[0])
        for i,(iid,_) in enumerate(items): tree.move(iid,"",i)
        asc[0] = not asc[0]
        tree.heading("date", text=f"Survey Date {'\u25b2' if asc[0] else '\u25bc'}")

    def _tt_dblclick(self, event, st):
        tree, ws = st["tree"], st["ws"]
        rid, cid = tree.identify_row(event.y), tree.identify_column(event.x)
        if not rid or not cid: return
        ci = int(cid.replace("#",""))-1
        cols = [c[0] for c in self._TT]
        if ci >= len(cols): return
        cn = cols[ci]
        if rid not in ws or cn not in ("zt","zb"): return
        bb = tree.bbox(rid, cid)
        if not bb: return
        x, y, w, h = bb
        cur = tree.item(rid,"values")[ci] if ci < len(tree.item(rid,"values")) else ""
        ent = tk.Entry(tree, font=(MONO, 10), justify=tk.CENTER)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, cur); ent.select_range(0, tk.END); ent.focus_set()
        def commit(ev=None):
            v = ent.get().strip(); ent.destroy()
            try: p = float(v) if v else None
            except: return
            ws[rid]["top" if cn=="zt" else "bot"] = p
            self._tt_recalc(st, rid)
        ent.bind("<Return>", commit); ent.bind("<Tab>", commit)
        ent.bind("<FocusOut>", commit); ent.bind("<Escape>", lambda e: ent.destroy())

    def _tt_exp(self, st, title):
        tree = st["tree"]; vis = tree.get_children()
        if not vis: return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
            initialfile=f"{title.replace(' ','_').lower()}.csv",filetypes=[("CSV","*.csv")])
        if not p: return
        with open(p,"w",newline="") as f:
            w=csv.writer(f); w.writerow([c[1] for c in self._TT])
            for iid in vis: w.writerow(tree.item(iid,"values"))
        self.status_var.set(f"Exported {len(vis)} rows")

    def _tt_copy(self, st):
        tree = st["tree"]; vis = tree.get_children()
        if not vis: return
        lines = ["\t".join(c[1] for c in self._TT)]
        for iid in vis: lines.append("\t".join(str(v) for v in tree.item(iid,"values")))
        self.clipboard_clear(); self.clipboard_append("\n".join(lines))
        self.status_var.set(f"Copied {len(vis)} rows")

    # ══════════════════════════════════════════════════════════════════════
    #  LOAD / CLEAR
    # ══════════════════════════════════════════════════════════════════════

    def _show_progress(self):
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, before=self.progress.master.winfo_children()[-1])
        self.progress.start(15)

    def _hide_progress(self):
        self.progress.stop(); self.progress.pack_forget()

    def _err(self, exc, btn=None):
        self._hide_progress()
        if btn: btn.state(["!disabled"])
        if hasattr(self, 'load_btn'): self.load_btn.state(["!disabled"])
        self.status_var.set("Error")
        messagebox.showerror("Database Error", str(exc))

    def _on_clear(self):
        self.api_text.delete("1.0",tk.END)
        self.hdrs.clear(); self.ddata.clear(); self.api_list.clear()
        self.t1_tree.delete(*self.t1_tree.get_children())
        self.t1_stree.delete(*self.t1_stree.get_children())
        self._t1_checked.clear(); self._t1_cur_api = None
        self.t1_ax.clear(); self.t1_canvas.draw()
        self.t2_wcb["values"]=[]; self.t2_wcb.set("")
        self.t2_scb["values"]=[]; self.t2_scb.set("")
        self.t2_ax.clear(); self.t2_canvas.draw()
        self.t2_tree.delete(*self.t2_tree.get_children())
        self.t2_info.set("Select a well and survey above")
        self._t2k = None
        for st in (self._t3, self._t4):
            st["tree"].delete(*st["tree"].get_children())
            st["ws"].clear(); st["det"].clear(); st["ord"].clear()
            st["dfv"].set(""); st["dtv"].set("")
        self.status_var.set("Cleared")

    def _on_load(self):
        apis = _parse_api_text(self.api_text.get("1.0",tk.END))
        if not apis: messagebox.showinfo("No APIs","Enter at least one valid API."); return
        self.api_list = apis
        self.load_btn.state(["disabled"])
        self._show_progress()
        self.status_var.set(f"Querying ODW for {len(apis)} API(s)...")
        def bg():
            try:
                c = _conn()
                self.after(0, lambda: self.status_var.set("Fetching survey headers..."))
                h = _q_headers(c, self.api_list)
                keys = [e[10] for es in h.values() for e in es]
                self.after(0, lambda: self.status_var.set(f"Fetching depth data ({len(keys)} surveys)..."))
                d = {}
                for i in range(0,len(keys),50):
                    d.update(_q_depth(c, keys[i:i+50]))
                c.close(); self.hdrs=h; self.ddata=d
                self.after(0, self._loaded)
            except Exception as e:
                self.after(0, lambda: self._err(e))
        threading.Thread(target=bg, daemon=True).start()

    def _loaded(self):
        self._hide_progress(); self.load_btn.state(["!disabled"])
        n = sum(1 for a in self.api_list if self.hdrs.get(a))
        self.status_var.set(f"Loaded: {n}/{len(self.api_list)} wells have temperature surveys")
        self._t1_pop(); self._t2_ref()
        self._tt_pop(self._t3); self._tt_pop(self._t4)


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if not oracledb: print("ERROR: pip install oracledb"); exit(1)
    if not matplotlib: print("ERROR: pip install matplotlib"); exit(1)
    App().mainloop()