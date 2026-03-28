"""
UIC Project Dashboard  (v2)
============================
Tabs:
  1. Well List        — wells for selected UIC project(s)
  2. Injection Chart  — stacked bar: steam, water, gas injection
  3. Production Chart  — oil, water, gas production over time
  4. Cumulative Chart  — running totals for prod & injection
  5. Well Map          — State Plane XY plot by purpose/status

Data Sources (all DWRPTG schema — accessible to rptguser):
  - dwrptg.UIC_PROJ_DMN          — project master (pre-joined lookups)
  - dwrptg.UIC_PROJ_WELL_DMN     — well membership (pre-joined attrs)
  - dwrptg.cmpl_dmn              — completion attributes
  - dwrptg.cmpl_mnly_fact        — monthly production & injection
  - dwrptg.fac_lctn_dmn          — surface coordinates

Requirements:   pip install oracledb matplotlib
Run:            python uic_dashboard.py
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
    import matplotlib.ticker as mticker
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


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
    """Format large numbers for chart axes."""
    if v is None:
        return ""
    try:
        n = float(v)
    except (ValueError, TypeError):
        return str(v)
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{int(round(n)):,}"


# ─────────────────────────────────────────────────────────────────────────────
# SQL — Project list (uses DWRPTG view — accessible to rptguser)
# ─────────────────────────────────────────────────────────────────────────────
SQL_PROJECTS = """
SELECT p.UIC_PROJ_CDE,
       p.UIC_PROJ_DESC,
       p.FLUID_TYPE_DESC,
       p.RCVY_TYPE_DESC,
       p.FLD_NME,
       p.MAX_WELL_CNT,
       p.MAX_BPD_INJ_VOL,
       p.STAT_TYPE_DESC  AS CURRENT_STATUS,
       (SELECT COUNT(*)
        FROM dwrptg.UIC_PROJ_WELL_DMN w
        WHERE w.UIC_PROJ_CDE = p.UIC_PROJ_CDE) AS WELL_COUNT
FROM dwrptg.UIC_PROJ_DMN p
ORDER BY p.UIC_PROJ_CDE
"""

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Well list for selected project(s)
# ─────────────────────────────────────────────────────────────────────────────
def sql_wells(proj_codes):
    in_list = ", ".join(f"'{c}'" for c in proj_codes)
    return f"""
SELECT DISTINCT
       cd.cmpl_nme              AS WELL_NME,
       cd.well_api_nbr          AS WELL_API_NBR,
       cd.opnl_fld              AS FLD_NME,
       cd.prim_purp_type_cde    AS PRIM_PURP_TYPE_CDE,
       cd.engr_strg_nme         AS ENGR_STRG_NME,
       cd.actv_indc             AS ACTV_INDC,
       cd.in_svc_indc           AS IN_SVC_INDC,
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

# ─────────────────────────────────────────────────────────────────────────────
# SQL — Monthly volumes + avg daily rates for selected project(s)
# ─────────────────────────────────────────────────────────────────────────────
def sql_production(proj_codes):
    in_list = ", ".join(f"'{c}'" for c in proj_codes)
    return f"""
SELECT TRUNC(cmf.eftv_dttm, 'MM')          AS MONTH_DT,
       SUM(cmf.aloc_oil_prod_vol_qty)       AS OIL_PROD,
       SUM(cmf.aloc_wtr_prod_vol_qty)       AS WATER_PROD,
       SUM(cmf.aloc_gas_prod_vol_qty)       AS GAS_PROD,
       SUM(cmf.aloc_gros_prod_vol_qty)      AS GROSS_PROD,
       SUM(cmf.aloc_stm_inj_vol_qty)        AS STEAM_INJ,
       SUM(cmf.aloc_wtr_inj_vol_qty)        AS WATER_INJ,
       SUM(cmf.aloc_gas_inj_vol_qty)        AS GAS_INJ,
       SUM(cmf.aloc_oil_prod_dly_rte_qty)   AS OIL_RATE,
       SUM(cmf.aloc_wtr_prod_dly_rte_qty)   AS WATER_RATE,
       SUM(cmf.aloc_gas_prod_dly_rte_qty)   AS GAS_RATE,
       SUM(cmf.aloc_gros_prod_dly_rte_qty)  AS GROSS_RATE,
       SUM(cmf.aloc_stm_inj_dly_rte_qty)    AS STEAM_INJ_RATE,
       SUM(cmf.aloc_wtr_inj_dly_rte_qty)    AS WATER_INJ_RATE,
       SUM(cmf.aloc_gas_inj_dly_rte_qty)    AS GAS_INJ_RATE
FROM dwrptg.UIC_PROJ_WELL_DMN wpd
JOIN dwrptg.cmpl_dmn cd
     ON wpd.WELL_FAC_ID = cd.well_fac_id
     AND cd.actv_indc = 'Y'
JOIN dwrptg.cmpl_mnly_fact cmf
     ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE wpd.UIC_PROJ_CDE IN ({in_list})
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -120)
GROUP BY TRUNC(cmf.eftv_dttm, 'MM')
ORDER BY TRUNC(cmf.eftv_dttm, 'MM')
"""


# ─────────────────────────────────────────────────────────────────────────────
# Treeview helpers
# ─────────────────────────────────────────────────────────────────────────────
def populate_tree(tree, columns, rows, col_widths=None):
    tree.delete(*tree.get_children())
    dcols = ["#"] + list(columns)
    tree["columns"] = dcols
    tree["show"] = "headings"
    tree.heading("#", text="#", anchor="center")
    tree.column("#", width=45, anchor="center", stretch=False)
    for c in columns:
        tree.heading(c, text=c, anchor="w",
                     command=lambda col=c: _sort_tree(tree, col, False))
        tree.column(c, width=(col_widths or {}).get(c, max(80, len(c) * 9)),
                    anchor="w")
    for i, row in enumerate(rows):
        tree.insert("", "end", values=[i + 1] + [fmt(v) for v in row],
                    tags=("even" if i % 2 == 0 else "odd",))


def _sort_tree(tree, col, rev):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    try:
        data.sort(key=lambda t: float(t[0].replace(",", "")), reverse=rev)
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
        defaultextension=".csv", filetypes=[("CSV", "*.csv")],
        initialfile=f"{title}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    if not path:
        return
    cols = [c for c in tree["columns"] if c != "#"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in ch:
            w.writerow(tree.item(c, "values")[1:])
    messagebox.showinfo("Saved", f"{len(ch)} rows -> {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Color palette
# ─────────────────────────────────────────────────────────────────────────────
COLORS = {
    "oil":        "#2d6a4f",
    "water_prod": "#2563eb",
    "gas_prod":   "#dc2626",
    "steam_inj":  "#ea580c",
    "water_inj":  "#3b82f6",
    "gas_inj":    "#ef4444",
    "cum_oil":    "#2d6a4f",
    "cum_water":  "#60a5fa",
    "cum_steam":  "#fb923c",
    "cum_winj":   "#3b82f6",
    "cum_ginj":   "#ef4444",
    "prod_active": "#059669",
    "inj_active":  "#d97706",
    "obsn":        "#7c3aed",
    "idle":        "#9ca3af",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Application
# ═══════════════════════════════════════════════════════════════════════════════
class App:
    BG     = "#f4f6f8"
    ACCENT = "#1a5276"
    PANEL  = "#ffffff"
    BORDER = "#d5dde5"

    def __init__(self, root):
        self.root = root
        self.root.title("UIC Project Dashboard — CRC Oracle Data Warehouse")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 650)

        self.projects_cols = []
        self.projects_rows = []
        self.all_proj_items = []
        self.well_cols = []
        self.well_rows = []
        self.prod_cols = []
        self.prod_rows = []
        self.selected_codes = []

        self._style()
        self._build_ui()
        self._statusbar()
        self._set_status("Connecting to database ...")
        self.root.after(100, self._load_projects)

    # ── Style ────────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        self.root.configure(bg=self.BG)
        cfg = {
            "TFrame":           dict(background=self.BG),
            "TLabel":           dict(background=self.BG, font=("Segoe UI", 10)),
            "TButton":          dict(font=("Segoe UI", 10)),
            "TNotebook":        dict(background=self.BG),
            "TNotebook.Tab":    dict(padding=[14, 6], font=("Segoe UI", 10)),
            "Header.TLabel":    dict(font=("Segoe UI", 14, "bold"),
                                     foreground=self.ACCENT, background=self.BG),
            "Sub.TLabel":       dict(font=("Segoe UI", 9), foreground="#666",
                                     background=self.BG),
            "Status.TLabel":    dict(font=("Segoe UI", 9), background="#dde4ea",
                                     padding=(8, 4)),
            "Accent.TButton":   dict(font=("Segoe UI", 11, "bold"), padding=[18, 6]),
            "Treeview":         dict(font=("Consolas", 9), rowheight=24),
            "Treeview.Heading": dict(font=("Segoe UI", 9, "bold"),
                                     foreground="white", background=self.ACCENT),
        }
        for name, kw in cfg.items():
            s.configure(name, **kw)
        s.map("Treeview.Heading", background=[("active", "#1a6b9c")])
        s.map("Treeview", background=[("selected", "#d4e6f1")])

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = ttk.Frame(self.root, padding=(12, 8))
        hdr.pack(fill="x")
        ttk.Label(hdr, text="UIC Project Dashboard",
                  style="Header.TLabel").pack(side="left")
        ttk.Label(hdr, text="Underground Injection Control — Surveillance & Reporting",
                  style="Sub.TLabel").pack(side="left", padx=15)

        pw = ttk.PanedWindow(self.root, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        # ─── LEFT: Project picker ────────────────────────────────────────────
        left = ttk.Frame(pw)
        pw.add(left, weight=1)

        lf = ttk.LabelFrame(left, text=" Select UIC Project(s) ", padding=5)
        lf.pack(fill="both", expand=True)

        sf = ttk.Frame(lf)
        sf.pack(fill="x", pady=(0, 5))
        ttk.Label(sf, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._filter_projects)
        ttk.Entry(sf, textvariable=self.search_var, width=28).pack(
            side="left", padx=5, fill="x", expand=True)

        lb_frame = ttk.Frame(lf)
        lb_frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical")
        self.proj_lb = tk.Listbox(
            lb_frame, selectmode="extended", font=("Consolas", 9),
            yscrollcommand=sb.set, activestyle="none",
            selectbackground=self.ACCENT, selectforeground="white")
        sb.config(command=self.proj_lb.yview)
        self.proj_lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.proj_lb.bind("<<ListboxSelect>>", self._on_selection_change)

        self.sel_lbl = ttk.Label(lf, text="Selected: 0 project(s)",
                                  font=("Segoe UI", 9))
        self.sel_lbl.pack(fill="x", pady=(5, 0))

        self.load_btn = ttk.Button(lf, text="  ▶  Load Project Data  ",
                                    style="Accent.TButton",
                                    command=self._on_load_data)
        self.load_btn.pack(fill="x", pady=(5, 0))

        # ─── RIGHT: Notebook ─────────────────────────────────────────────────
        right = ttk.Frame(pw)
        pw.add(right, weight=4)

        self.cards_frame = ttk.Frame(right)
        self.cards_frame.pack(fill="x", pady=(0, 5))

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

        self.tab_wells = ttk.Frame(self.nb)
        self.tab_inj   = ttk.Frame(self.nb)
        self.tab_prod  = ttk.Frame(self.nb)
        self.tab_cum   = ttk.Frame(self.nb)
        self.tab_map   = ttk.Frame(self.nb)

        self.nb.add(self.tab_wells, text="  Well List  ")
        self.nb.add(self.tab_inj,   text="  Injection  ")
        self.nb.add(self.tab_prod,  text="  Production  ")
        self.nb.add(self.tab_cum,   text="  Cumulative  ")
        self.nb.add(self.tab_map,   text="  Well Map  ")

    def _statusbar(self):
        self.sb = ttk.Label(self.root, text="", style="Status.TLabel", anchor="w")
        self.sb.pack(fill="x", side="bottom")

    def _set_status(self, msg):
        self.sb.config(text=msg)
        self.root.update_idletasks()

    # ── Load project list ────────────────────────────────────────────────────
    def _load_projects(self):
        threading.Thread(target=self._load_projects_bg, daemon=True).start()

    def _load_projects_bg(self):
        try:
            cols, rows = run_query(SQL_PROJECTS)
            self.projects_cols = cols
            self.projects_rows = rows
            self.root.after(0, self._populate_project_list)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror(
                "Database Error", f"Cannot connect to database:\n{e}"))
            self.root.after(0, lambda: self._set_status("Connection failed."))

    def _populate_project_list(self):
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
        self._set_status(
            f"Loaded {len(self.projects_rows)} UIC projects.  "
            f"Select project(s) and click Load.")

    def _filter_projects(self, *args):
        term = self.search_var.get().lower()
        self.proj_lb.delete(0, "end")
        for code, display in self.all_proj_items:
            if term in display.lower():
                self.proj_lb.insert("end", display)

    def _on_selection_change(self, event=None):
        sel = self.proj_lb.curselection()
        self.sel_lbl.config(text=f"Selected: {len(sel)} project(s)")

    def _get_selected_codes(self):
        sel_indices = self.proj_lb.curselection()
        visible = self.proj_lb.get(0, "end")
        codes = []
        for idx in sel_indices:
            code = visible[idx][:14].strip()
            codes.append(code)
        return codes

    # ── Load data ────────────────────────────────────────────────────────────
    def _on_load_data(self):
        codes = self._get_selected_codes()
        if not codes:
            messagebox.showwarning("No Selection",
                                   "Please select at least one project.")
            return
        self.selected_codes = codes
        self.load_btn.config(state="disabled")
        self._set_status(f"Loading data for {len(codes)} project(s) ...")
        threading.Thread(target=self._load_data_bg, args=(codes,),
                         daemon=True).start()

    def _load_data_bg(self, codes):
        try:
            self.root.after(0, lambda: self._set_status("Querying wells ..."))
            wc, wr = run_query(sql_wells(codes))
            self.well_cols = wc
            self.well_rows = wr

            self.root.after(0, lambda: self._set_status(
                "Querying production & injection ..."))
            pc, pr = run_query(sql_production(codes))
            self.prod_cols = pc
            self.prod_rows = pr

            self.root.after(0, self._display_results)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Query Error", str(e)))
            self.root.after(0, lambda: self._set_status("Query failed."))
        finally:
            self.root.after(0, lambda: self.load_btn.config(state="normal"))

    # ── Display results ──────────────────────────────────────────────────────
    def _display_results(self):
        self._update_summary_cards()
        self._build_well_table()
        if HAS_MPL:
            self._build_injection_chart()
            self._build_production_chart()
            self._build_cumulative_chart()
            self._build_well_map()

        codes_str = ", ".join(self.selected_codes[:5])
        if len(self.selected_codes) > 5:
            codes_str += f" +{len(self.selected_codes) - 5} more"
        self._set_status(
            f"Loaded {len(self.well_rows)} wells, "
            f"{len(self.prod_rows)} months  |  Projects: {codes_str}")

    # ── Summary cards ────────────────────────────────────────────────────────
    def _update_summary_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()

        total = len(self.well_rows)
        prods = sum(1 for r in self.well_rows if r[3] == "PROD")
        injs  = sum(1 for r in self.well_rows if r[3] == "INJ")
        insvc = sum(1 for r in self.well_rows if r[6] == "Y")
        flds  = len(set(r[2] for r in self.well_rows if r[2]))

        cards = [
            ("Total Wells", total, self.ACCENT),
            ("Producers",   prods, "#059669"),
            ("Injectors",   injs,  "#d97706"),
            ("In Service",  insvc, "#22c55e"),
            ("Fields",      flds,  "#7c3aed"),
        ]
        for label, value, color in cards:
            card = tk.Frame(self.cards_frame, bg="white", bd=1,
                            relief="solid", highlightthickness=0)
            card.pack(side="left", padx=4, pady=2, fill="x", expand=True)
            tk.Label(card, text=label, font=("Segoe UI", 9),
                     fg="#6b7280", bg="white").pack(anchor="w", padx=10, pady=(6, 0))
            tk.Label(card, text=str(value), font=("Segoe UI", 20, "bold"),
                     fg=color, bg="white").pack(anchor="w", padx=10, pady=(0, 6))

    # ── Tab 1: Well Table ────────────────────────────────────────────────────
    def _build_well_table(self):
        for w in self.tab_wells.winfo_children():
            w.destroy()

        fbar = ttk.Frame(self.tab_wells)
        fbar.pack(fill="x", pady=(5, 5), padx=8)

        ttk.Label(fbar, text="Search:").pack(side="left")
        self.well_search_var = tk.StringVar()
        self.well_search_var.trace_add("write", self._filter_well_table)
        ttk.Entry(fbar, textvariable=self.well_search_var, width=20).pack(
            side="left", padx=5)

        ttk.Label(fbar, text="Purpose:").pack(side="left", padx=(10, 0))
        self.well_purp_var = tk.StringVar(value="ALL")
        cb = ttk.Combobox(fbar, textvariable=self.well_purp_var,
                           values=["ALL", "PROD", "INJ", "OBSN"],
                           width=8, state="readonly")
        cb.pack(side="left", padx=5)
        cb.bind("<<ComboboxSelected>>", self._filter_well_table)

        ttk.Button(fbar, text="Export CSV",
                   command=lambda: export_tree(self.well_tree, "uic_wells")).pack(
            side="right")
        self.well_count_lbl = ttk.Label(
            fbar, text=f"{len(self.well_rows)} wells", style="Sub.TLabel")
        self.well_count_lbl.pack(side="right", padx=10)

        frm = ttk.Frame(self.tab_wells)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.well_tree = self._make_tree(frm)

        disp_cols = self.well_cols[:8]
        disp_rows = [r[:8] for r in self.well_rows]
        widths = {"WELL_NME": 150, "WELL_API_NBR": 110, "FLD_NME": 100,
                  "PRIM_PURP_TYPE_CDE": 80, "ENGR_STRG_NME": 140,
                  "ACTV_INDC": 55, "IN_SVC_INDC": 55, "UIC_PROJ_CDE": 110}
        populate_tree(self.well_tree, disp_cols, disp_rows, widths)

    def _filter_well_table(self, *args):
        term = self.well_search_var.get().lower()
        purp = self.well_purp_var.get()
        disp_cols = self.well_cols[:8]
        filtered = []
        for r in self.well_rows:
            if purp != "ALL" and str(r[3]) != purp:
                continue
            if term:
                row_text = " ".join(str(v or "").lower() for v in r[:8])
                if term not in row_text:
                    continue
            filtered.append(r[:8])
        populate_tree(self.well_tree, disp_cols, filtered,
                      {"WELL_NME": 150, "WELL_API_NBR": 110, "FLD_NME": 100,
                       "PRIM_PURP_TYPE_CDE": 80, "ENGR_STRG_NME": 140,
                       "ACTV_INDC": 55, "IN_SVC_INDC": 55, "UIC_PROJ_CDE": 110})
        self.well_count_lbl.config(text=f"{len(filtered)} wells")

    # ── Tab 2: Injection Chart ───────────────────────────────────────────────
    def _build_injection_chart(self):
        for w in self.tab_inj.winfo_children():
            w.destroy()
        if not self.prod_rows:
            ttk.Label(self.tab_inj, text="No injection data.",
                      font=("Segoe UI", 12)).pack(pady=40)
            return

        dates, steam, water_inj, gas_inj, water_prod = [], [], [], [], []
        for r in self.prod_rows:
            dates.append(r[0])
            steam.append(float(r[12] or 0))       # STEAM_INJ_RATE (dly avg)
            water_inj.append(float(r[13] or 0))   # WATER_INJ_RATE (dly avg)
            gas_inj.append(float(r[14] or 0))     # GAS_INJ_RATE (dly avg)
            water_prod.append(float(r[9] or 0))   # WATER_RATE (dly avg)

        fig = Figure(figsize=(12, 5), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)

        lines = []
        if any(v > 0 for v in steam):
            l, = ax.plot(dates, steam, color=COLORS["steam_inj"], lw=1.8,
                         label="Steam Injection (BWE/d)")
            lines.append(l)
        if any(v > 0 for v in water_inj):
            l, = ax.plot(dates, water_inj, color=COLORS["water_inj"], lw=1.8,
                         label="Water Injection (BWPD)")
            lines.append(l)
        if any(v > 0 for v in gas_inj):
            l, = ax.plot(dates, gas_inj, color=COLORS["gas_inj"], lw=1.5,
                         linestyle="--", label="Gas Injection (MCFD)")
            lines.append(l)

        ax.set_ylabel("Avg Daily Injection Rate", fontsize=10,
                       color=self.ACCENT)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: fmt_num(x)))

        # Secondary Y axis: water production daily rate
        if any(v > 0 for v in water_prod):
            ax2 = ax.twinx()
            l, = ax2.plot(dates, water_prod, color=COLORS["water_prod"],
                          lw=1.5, linestyle="-.", alpha=0.8,
                          label="Water Production (BWPD)")
            lines.append(l)
            ax2.set_ylabel("Water Production (BWPD)", fontsize=10,
                           color=COLORS["water_prod"])
            ax2.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda x, _: fmt_num(x)))
            ax2.tick_params(labelsize=8, labelcolor=COLORS["water_prod"])

        ax.set_title("Avg Daily Injection Rates & Water Production", fontsize=12,
                     fontweight="bold", color=self.ACCENT, pad=12)
        ax.legend(lines, [l.get_label() for l in lines],
                  fontsize=8, loc="upper left", framealpha=0.9)

        # Show all available data on x-axis
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        span_days = (max(dates) - min(dates)).days if len(dates) > 1 else 30
        if span_days > 1800:
            ax.xaxis.set_major_locator(mdates.YearLocator(2))
        elif span_days > 720:
            ax.xaxis.set_major_locator(mdates.YearLocator())
        elif span_days > 360:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        else:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.set_axisbelow(True)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.tab_inj)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.tab_inj).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # ── Tab 3: Production Chart ──────────────────────────────────────────────
    def _build_production_chart(self):
        for w in self.tab_prod.winfo_children():
            w.destroy()
        if not self.prod_rows:
            ttk.Label(self.tab_prod, text="No production data.",
                      font=("Segoe UI", 12)).pack(pady=40)
            return

        dates, oil, gas = [], [], []
        for r in self.prod_rows:
            dates.append(r[0])
            oil.append(float(r[8] or 0))    # OIL_RATE (dly avg BOPD)
            gas.append(float(r[10] or 0))   # GAS_RATE (dly avg MCFD)

        fig = Figure(figsize=(12, 5), dpi=100, facecolor="white")
        ax1 = fig.add_subplot(111)

        lines = []
        # Oil on left Y-axis
        if any(v > 0 for v in oil):
            ax1.fill_between(dates, oil, alpha=0.10, color=COLORS["oil"])
            l, = ax1.plot(dates, oil, color=COLORS["oil"], lw=2.2,
                          label="Oil Production (BOPD)")
            lines.append(l)
        ax1.set_ylabel("Oil Production (BOPD)", fontsize=10, color=COLORS["oil"])
        ax1.tick_params(axis="y", labelsize=9, labelcolor=COLORS["oil"])
        ax1.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: fmt_num(x)))

        # Gas on right Y-axis
        if any(v > 0 for v in gas):
            ax2 = ax1.twinx()
            l, = ax2.plot(dates, gas, color=COLORS["gas_prod"], lw=1.5,
                          linestyle="--", label="Gas Production (MCFD)")
            lines.append(l)
            ax2.set_ylabel("Gas Production (MCFD)", fontsize=10,
                           color=COLORS["gas_prod"])
            ax2.tick_params(axis="y", labelsize=9, labelcolor=COLORS["gas_prod"])
            ax2.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda x, _: fmt_num(x)))

        ax1.set_title("Avg Daily Oil & Gas Production Rates", fontsize=12,
                      fontweight="bold", color=self.ACCENT, pad=12)
        ax1.legend(lines, [l.get_label() for l in lines],
                   fontsize=9, loc="upper right", framealpha=0.9)

        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        span_days = (max(dates) - min(dates)).days if len(dates) > 1 else 30
        if span_days > 1800:
            ax1.xaxis.set_major_locator(mdates.YearLocator(2))
        elif span_days > 720:
            ax1.xaxis.set_major_locator(mdates.YearLocator())
        elif span_days > 360:
            ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        else:
            ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        ax1.tick_params(axis="x", rotation=45, labelsize=8)
        ax1.grid(axis="y", alpha=0.3, linestyle="--")
        ax1.set_axisbelow(True)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.tab_prod)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.tab_prod).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # ── Tab 4: Cumulative Chart ──────────────────────────────────────────────
    def _build_cumulative_chart(self):
        for w in self.tab_cum.winfo_children():
            w.destroy()
        if not self.prod_rows:
            ttk.Label(self.tab_cum, text="No data.",
                      font=("Segoe UI", 12)).pack(pady=40)
            return

        import numpy as np

        dates = [r[0] for r in self.prod_rows]
        oil   = [float(r[1] or 0) for r in self.prod_rows]
        water = [float(r[2] or 0) for r in self.prod_rows]
        gas   = [float(r[3] or 0) for r in self.prod_rows]
        steam = [float(r[5] or 0) for r in self.prod_rows]
        winj  = [float(r[6] or 0) for r in self.prod_rows]
        ginj  = [float(r[7] or 0) for r in self.prod_rows]

        cum_oil   = np.cumsum(oil)
        cum_water = np.cumsum(water)
        cum_gas   = np.cumsum(gas)
        cum_steam = np.cumsum(steam)
        cum_winj  = np.cumsum(winj)
        cum_ginj  = np.cumsum(ginj)

        # ── Chart (top) ─────────────────────────────────────────────────────
        chart_frame = ttk.Frame(self.tab_cum)
        chart_frame.pack(fill="both", expand=True, padx=5, pady=(5, 0))

        fig = Figure(figsize=(12, 4), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)

        ax.plot(dates, cum_oil, color=COLORS["cum_oil"], lw=2.5,
                label="Cum Oil Prod")
        ax.plot(dates, cum_water, color=COLORS["cum_water"], lw=1.8,
                label="Cum Water Prod")
        ax.plot(dates, cum_steam, color=COLORS["cum_steam"], lw=2,
                linestyle="--", label="Cum Steam Inj")
        ax.plot(dates, cum_winj, color=COLORS["cum_winj"], lw=1.5,
                linestyle="--", label="Cum Water Inj")
        ax.plot(dates, cum_ginj, color=COLORS["cum_ginj"], lw=1.5,
                linestyle=":", label="Cum Gas Inj")

        ax.set_title("Cumulative Production & Injection", fontsize=12,
                     fontweight="bold", color=self.ACCENT, pad=10)
        ax.set_ylabel("Cumulative Volume (bbl)", fontsize=10)
        ax.legend(fontsize=8, loc="upper left", framealpha=0.9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: fmt_num(x)))
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.set_axisbelow(True)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, chart_frame)
        canvas.draw()
        NavigationToolbar2Tk(canvas, chart_frame).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # ── Summary Table (bottom) ──────────────────────────────────────────
        tbl_lbl = ttk.Label(self.tab_cum,
                             text="Cumulative Summary (last 10 years of data)",
                             font=("Segoe UI", 10, "bold"))
        tbl_lbl.pack(anchor="w", padx=10, pady=(8, 2))

        tbl_frame = ttk.Frame(self.tab_cum)
        tbl_frame.pack(fill="x", padx=8, pady=(0, 8))

        cum_tree = ttk.Treeview(tbl_frame, height=8, selectmode="extended")
        cum_cols = ["MONTH", "OIL_PROD", "WATER_PROD", "GAS_PROD",
                    "STEAM_INJ", "WATER_INJ", "GAS_INJ",
                    "CUM_OIL", "CUM_WATER", "CUM_STEAM", "CUM_WINJ", "CUM_GINJ"]
        cum_headers = ["Month", "Oil Prod", "Water Prod", "Gas Prod",
                       "Steam Inj", "Water Inj", "Gas Inj",
                       "Cum Oil", "Cum Water", "Cum Steam", "Cum WInj", "Cum GInj"]
        cum_widths = [85, 85, 90, 80, 85, 85, 80, 95, 95, 95, 95, 95]

        cum_tree["columns"] = cum_cols
        cum_tree["show"] = "headings"
        for col, hdr, w in zip(cum_cols, cum_headers, cum_widths):
            cum_tree.heading(col, text=hdr, anchor="w")
            cum_tree.column(col, width=w, anchor="e")

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=cum_tree.yview)
        hsb = ttk.Scrollbar(tbl_frame, orient="horizontal", command=cum_tree.xview)
        cum_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        cum_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tbl_frame.columnconfigure(0, weight=1)
        tbl_frame.rowconfigure(0, weight=1)

        cum_tree.tag_configure("even", background="#f0f4f8")
        cum_tree.tag_configure("odd", background="white")
        cum_tree.tag_configure("total", background="#e0e7ff",
                                font=("Consolas", 9, "bold"))

        for i in range(len(dates)):
            dt_str = dates[i].strftime("%Y-%m") if hasattr(dates[i], "strftime") else str(dates[i])[:7]
            vals = [
                dt_str,
                f"{oil[i]:,.0f}", f"{water[i]:,.0f}", f"{gas[i]:,.0f}",
                f"{steam[i]:,.0f}", f"{winj[i]:,.0f}", f"{ginj[i]:,.0f}",
                f"{cum_oil[i]:,.0f}", f"{cum_water[i]:,.0f}",
                f"{cum_steam[i]:,.0f}", f"{cum_winj[i]:,.0f}",
                f"{cum_ginj[i]:,.0f}",
            ]
            tag = "even" if i % 2 == 0 else "odd"
            cum_tree.insert("", "end", values=vals, tags=(tag,))

        # Total row
        cum_tree.insert("", "end", values=[
            "TOTAL",
            f"{sum(oil):,.0f}", f"{sum(water):,.0f}", f"{sum(gas):,.0f}",
            f"{sum(steam):,.0f}", f"{sum(winj):,.0f}", f"{sum(ginj):,.0f}",
            f"{cum_oil[-1]:,.0f}", f"{cum_water[-1]:,.0f}",
            f"{cum_steam[-1]:,.0f}", f"{cum_winj[-1]:,.0f}",
            f"{cum_ginj[-1]:,.0f}",
        ], tags=("total",))

        # Scroll to bottom to show most recent
        children = cum_tree.get_children()
        if children:
            cum_tree.see(children[-1])

    # ── Tab 5: Well Map ──────────────────────────────────────────────────────
    def _build_well_map(self):
        for w in self.tab_map.winfo_children():
            w.destroy()

        map_wells = []
        for r in self.well_rows:
            try:
                x = float(r[8])
                y = float(r[9])
                if x > 0 and y > 0:
                    map_wells.append(r)
            except (TypeError, ValueError, IndexError):
                continue

        if not map_wells:
            ttk.Label(self.tab_map, text="No coordinate data available.",
                      font=("Segoe UI", 12)).pack(pady=40)
            return

        fig = Figure(figsize=(12, 7), dpi=100, facecolor="white")
        ax = fig.add_subplot(111)

        groups = {
            ("PROD", "Y"):  dict(marker="o", color=COLORS["prod_active"],
                                 label="Producer (in svc)", s=30, alpha=0.85),
            ("PROD", "N"):  dict(marker="o", color=COLORS["idle"],
                                 label="Producer (out of svc)", s=18, alpha=0.4),
            ("INJ", "Y"):   dict(marker="^", color=COLORS["inj_active"],
                                 label="Injector (in svc)", s=35, alpha=0.85),
            ("INJ", "N"):   dict(marker="^", color=COLORS["idle"],
                                 label="Injector (out of svc)", s=18, alpha=0.4),
            ("OBSN", "Y"):  dict(marker="s", color=COLORS["obsn"],
                                 label="Observation", s=25, alpha=0.85),
        }

        plotted = set()
        for r in map_wells:
            purp = str(r[3] or "")
            insvc = "Y" if str(r[6]) == "Y" else "N"
            x, y = float(r[8]), float(r[9])
            key = (purp, insvc)
            style = groups.get(key, dict(marker="D", color="#9ca3af",
                                         label="Other", s=18, alpha=0.5))
            lbl = style["label"] if key not in plotted else None
            plotted.add(key)
            ax.scatter(x, y, marker=style["marker"], c=style["color"],
                       s=style["s"], alpha=style["alpha"], label=lbl,
                       edgecolors="white", linewidths=0.5, zorder=3)

        ax.set_title("Well Locations (State Plane Coordinates)", fontsize=12,
                     fontweight="bold", color=self.ACCENT, pad=12)
        ax.set_xlabel("Easting (ft)", fontsize=10)
        ax.set_ylabel("Northing (ft)", fontsize=10)
        ax.legend(fontsize=8, loc="best", framealpha=0.9, markerscale=1.5)
        ax.grid(True, alpha=0.2, linestyle="--")
        ax.set_axisbelow(True)
        ax.set_aspect("equal", adjustable="datalim")
        ax.tick_params(labelsize=8)
        ax.xaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.tab_map)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.tab_map).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # ── shared tree helper ───────────────────────────────────────────────────
    def _make_tree(self, parent):
        tree = ttk.Treeview(parent, selectmode="extended")
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree.tag_configure("even", background="#f0f4f8")
        tree.tag_configure("odd", background="white")
        return tree


# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()