#Help: Temperature Survey Data
"""
Temperature Survey Viewer  v2.0
================================
4-tab application for temperature survey analysis:
  Tab 1 — Survey Overview: well list (left ~50%) + all-surveys overlay chart (right ~50%)
  Tab 2 — Detailed Measurement: well/survey dropdowns -> single-survey chart + data table
  Tab 3 — Initial Temperature: zone avg/min/max from FIRST survey per well
  Tab 4 — Current Temperature: zone avg/min/max from LAST survey per well

Usage:  python temp_survey_viewer.py
Requires: oracledb, matplotlib
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
import csv
import os
import re
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

# ── Database credentials ──────────────────────────────────────────────────────
DB_USER = "rptguser"
DB_PASS = "allusers"
DB_TNS  = "ODW"

SURVEY_COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
]


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_date(dt):
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    return str(dt)[:10] if dt else ""


def _safe(v, fmt=".1f"):
    if v is None:
        return ""
    return f"{v:{fmt}}"


def _calc_zone_stats(pts, top, bot):
    """Return (avg, min, max, count) of temperatures between top and bot MD."""
    filtered = [p[2] for p in pts
                if p[0] is not None and p[2] is not None
                and p[0] >= top and p[0] <= bot]
    if not filtered:
        return (None, None, None, 0)
    return (sum(filtered) / len(filtered), min(filtered), max(filtered), len(filtered))


# ══════════════════════════════════════════════════════════════════════════════
#  Data layer
# ══════════════════════════════════════════════════════════════════════════════

def get_connection():
    try:
        oracledb.init_oracle_client()
    except Exception:
        pass
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_TNS)


def query_survey_headers(conn, api_list):
    """Return ALL TS/OFOT surveys per API, newest-first."""
    if not api_list:
        return {}
    ph = ", ".join(f":a{i}" for i in range(len(api_list)))
    bv = {f"a{i}": api for i, api in enumerate(api_list)}
    sql = f"""
        SELECT cd.cmpl_nme, cd.well_api_nbr, cd.cmpl_fac_id, cd.well_fac_id,
               lcd.log_curv_type_mnmn_txt, lcd.lggg_pass_strt_dttm,
               lcd.top_md_qty, lcd.base_md_qty,
               lcd.curv_min_valu, lcd.curv_max_valu,
               lcd.tot_curv_smpl, lcd.log_curv_dmn_key
        FROM dwrptg.log_curv_dmn lcd
        JOIN dwrptg.cmpl_dmn cd ON lcd.cmpl_fac_id = cd.cmpl_fac_id
        WHERE cd.well_api_nbr IN ({ph})
          AND cd.actv_indc = 'Y'
          AND lcd.log_curv_type_mnmn_txt IN ('TS', 'OFOT')
        ORDER BY cd.well_api_nbr, lcd.lggg_pass_strt_dttm DESC
    """
    cur = conn.cursor()
    cur.execute(sql, bv)
    rows = cur.fetchall()
    cur.close()
    result = {}
    for r in rows:
        api = r[1]
        # tuple: 0=cmpl_nme 1=cmpl_fac_id 2=well_fac_id 3=code 4=date
        #        5=top 6=tag 7=min 8=max 9=npts 10=key
        entry = (r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11])
        result.setdefault(api, []).append(entry)
    return result


def query_survey_depth_data(conn, keys):
    """Pull foot-by-foot data for log_curv_dmn_key list."""
    if not keys:
        return {}
    ph = ", ".join(f":k{i}" for i in range(len(keys)))
    bv = {f"k{i}": int(k) for i, k in enumerate(keys)}
    sql = f"""
        SELECT lmf.log_curv_dmn_key, lmf.md_qty, lmf.tvd_qty,
               lmf.msd_qty, lmf.lthsg_unit_nme
        FROM dwrptg.log_curv_msmt_fact lmf
        WHERE lmf.log_curv_dmn_key IN ({ph})
        ORDER BY lmf.log_curv_dmn_key, lmf.md_qty
    """
    cur = conn.cursor()
    cur.execute(sql, bv)
    rows = cur.fetchall()
    cur.close()
    result = {}
    for key, md, tvd, temp, zone in rows:
        result.setdefault(key, []).append((md, tvd, temp, zone or ""))
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Application
# ══════════════════════════════════════════════════════════════════════════════

class TempSurveyApp(tk.Tk):

    # Header tuple indices
    H_CMPL = 0; H_FAC = 1; H_WFAC = 2; H_CODE = 3; H_DATE = 4
    H_TOP = 5;  H_TAG = 6; H_MIN = 7;  H_MAX = 8;  H_NPTS = 9; H_KEY = 10

    def __init__(self):
        super().__init__()
        self.title("Temperature Survey Viewer")
        self.geometry("1400x850")
        self.minsize(1000, 650)

        self.survey_headers = {}
        self.depth_data = {}
        self.api_list = []

        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────
    #  TOP-LEVEL UI
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Input bar ─────────────────────────────────────────────────────
        top = tk.Frame(self, padx=10, pady=6)
        top.pack(fill=tk.X)
        tk.Label(top, text="Enter 10-digit API numbers (comma / space / newline separated):",
                 font=("Segoe UI", 10)).pack(anchor=tk.W)
        row = tk.Frame(top)
        row.pack(fill=tk.X, pady=(4, 0))
        self.api_text = scrolledtext.ScrolledText(row, height=3, width=80,
                                                   font=("Consolas", 10))
        self.api_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        bf = tk.Frame(row)
        bf.pack(side=tk.LEFT, padx=(8, 0))
        self.load_btn = tk.Button(bf, text="Load Surveys", width=14,
                                   command=self._on_load,
                                   font=("Segoe UI", 10, "bold"))
        self.load_btn.pack(pady=(0, 4))
        tk.Button(bf, text="Clear", width=14, command=self._on_clear,
                  font=("Segoe UI", 9)).pack()

        # ── Status + progress ─────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = tk.Label(self, textvariable=self.status_var, anchor=tk.W,
                                    relief=tk.SUNKEN, padx=6, font=("Segoe UI", 9))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.progress = ttk.Progressbar(self, mode="indeterminate")

        # ── Notebook ──────────────────────────────────────────────────────
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 4))

        self._build_tab1_overview()
        self._build_tab2_detail()
        self._build_tab3_initial()
        self._build_tab4_current()

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 1 — Survey Overview
    # ══════════════════════════════════════════════════════════════════════

    def _build_tab1_overview(self):
        frm = tk.Frame(self.nb)
        self.nb.add(frm, text="  Survey Overview  ")

        pw = ttk.PanedWindow(frm, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)

        # ── LEFT: well list ───────────────────────────────────────────────
        left = tk.Frame(pw)
        pw.add(left, weight=1)

        hdr = tk.Frame(left)
        hdr.pack(fill=tk.X, padx=4, pady=(4, 2))
        tk.Label(hdr, text="Wells", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(hdr, text="Export CSV", width=10,
                  command=self._t1_export).pack(side=tk.RIGHT)

        cols = ("well", "api", "surveys", "first", "last")
        self.t1_tree = ttk.Treeview(left, columns=cols, show="headings",
                                     selectmode="browse")
        for cid, hd, w in [("well", "Well", 130), ("api", "API", 105),
                           ("surveys", "# Surveys", 80),
                           ("first", "First Survey", 100), ("last", "Last Survey", 100)]:
            self.t1_tree.heading(cid, text=hd)
            self.t1_tree.column(cid, width=w,
                                anchor=tk.W if cid == "well" else tk.CENTER)
        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.t1_tree.yview)
        self.t1_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.t1_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self.t1_tree.bind("<<TreeviewSelect>>", self._t1_on_select)

        # ── RIGHT: overlay chart ──────────────────────────────────────────
        right = tk.Frame(pw)
        pw.add(right, weight=1)

        self.t1_fig = Figure(figsize=(6, 5), dpi=100)
        self.t1_ax = self.t1_fig.add_subplot(111)
        self.t1_canvas = FigureCanvasTkAgg(self.t1_fig, master=right)
        self.t1_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.t1_toolbar = NavigationToolbar2Tk(self.t1_canvas, right)
        self.t1_toolbar.update()

    def _t1_populate(self):
        self.t1_tree.delete(*self.t1_tree.get_children())
        for api in self.api_list:
            entries = self.survey_headers.get(api, [])
            if not entries:
                self.t1_tree.insert("", tk.END, iid=api,
                                     values=("— no surveys —", api, 0, "", ""))
                continue
            cmpl = entries[0][self.H_CMPL]
            n = len(entries)
            first_dt = _fmt_date(entries[-1][self.H_DATE])  # oldest (list newest-first)
            last_dt  = _fmt_date(entries[0][self.H_DATE])
            self.t1_tree.insert("", tk.END, iid=api,
                                 values=(cmpl, api, n, first_dt, last_dt))
        # auto-select first well that has surveys
        for api in self.api_list:
            if self.survey_headers.get(api):
                self.t1_tree.selection_set(api)
                self.t1_tree.focus(api)
                self._t1_draw(api)
                break

    def _t1_on_select(self, event=None):
        sel = self.t1_tree.selection()
        if sel:
            self._t1_draw(sel[0])

    def _t1_draw(self, api):
        entries = self.survey_headers.get(api, [])
        ax = self.t1_ax
        ax.clear()
        if not entries:
            ax.set_title(f"No surveys for API {api}", fontsize=11)
            self.t1_canvas.draw()
            return
        cmpl = entries[0][self.H_CMPL]
        ax.set_title(f"{cmpl}  ({api}) — {len(entries)} Survey(s)",
                      fontsize=12, fontweight="bold")
        ax.set_xlabel("Temperature (°F)", fontsize=10)
        ax.set_ylabel("Measured Depth (ft)", fontsize=10)
        ax.invert_yaxis()
        ax.grid(True, alpha=0.3)
        for i, e in enumerate(entries):
            pts = self.depth_data.get(e[self.H_KEY], [])
            if not pts:
                continue
            ax.plot([p[2] for p in pts], [p[0] for p in pts],
                    color=SURVEY_COLORS[i % len(SURVEY_COLORS)], linewidth=1.0,
                    label=f"{_fmt_date(e[self.H_DATE])} ({e[self.H_CODE]})")
        ax.legend(fontsize=7, loc="lower right")
        self.t1_fig.tight_layout()
        self.t1_canvas.draw()

    def _t1_export(self):
        if not self.survey_headers:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="survey_overview.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Well", "API", "Survey Date", "Type", "Top MD",
                         "Tag MD", "Min Temp F", "Max Temp F", "Samples"])
            for api in self.api_list:
                for e in self.survey_headers.get(api, []):
                    w.writerow([e[self.H_CMPL], api, _fmt_date(e[self.H_DATE]),
                                e[self.H_CODE], e[self.H_TOP], e[self.H_TAG],
                                e[self.H_MIN], e[self.H_MAX], e[self.H_NPTS]])
        self.status_var.set(f"Exported to {os.path.basename(path)}")

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 2 — Detailed Measurement
    # ══════════════════════════════════════════════════════════════════════

    def _build_tab2_detail(self):
        frm = tk.Frame(self.nb)
        self.nb.add(frm, text="  Detailed Measurement  ")

        # ── selectors ─────────────────────────────────────────────────────
        sel = tk.Frame(frm, padx=8, pady=6)
        sel.pack(fill=tk.X)
        tk.Label(sel, text="Well:", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.t2_well_var = tk.StringVar()
        self.t2_well_cb = ttk.Combobox(sel, textvariable=self.t2_well_var,
                                        state="readonly", width=40,
                                        font=("Consolas", 10))
        self.t2_well_cb.pack(side=tk.LEFT, padx=(4, 16))
        self.t2_well_cb.bind("<<ComboboxSelected>>", self._t2_well_changed)

        tk.Label(sel, text="Survey:", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.t2_surv_var = tk.StringVar()
        self.t2_surv_cb = ttk.Combobox(sel, textvariable=self.t2_surv_var,
                                        state="readonly", width=36,
                                        font=("Consolas", 10))
        self.t2_surv_cb.pack(side=tk.LEFT, padx=(4, 16))
        self.t2_surv_cb.bind("<<ComboboxSelected>>", self._t2_surv_changed)

        tk.Button(sel, text="Copy to Clipboard", width=16,
                  command=self._t2_copy).pack(side=tk.RIGHT, padx=(0, 6))
        tk.Button(sel, text="Export CSV", width=10,
                  command=self._t2_export).pack(side=tk.RIGHT)

        # ── paned: chart top / table bottom ───────────────────────────────
        pw = ttk.PanedWindow(frm, orient=tk.VERTICAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        # chart
        chart_f = tk.Frame(pw)
        pw.add(chart_f, weight=1)
        self.t2_fig = Figure(figsize=(8, 3), dpi=100)
        self.t2_ax = self.t2_fig.add_subplot(111)
        self.t2_canvas = FigureCanvasTkAgg(self.t2_fig, master=chart_f)
        self.t2_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.t2_toolbar = NavigationToolbar2Tk(self.t2_canvas, chart_f)
        self.t2_toolbar.update()

        # table
        table_f = tk.Frame(pw)
        pw.add(table_f, weight=1)

        self.t2_info_var = tk.StringVar(value="Select a well and survey above")
        tk.Label(table_f, textvariable=self.t2_info_var,
                 font=("Segoe UI", 9), anchor=tk.W).pack(fill=tk.X, padx=4, pady=(4, 2))

        cols = ("row_num", "md", "tvd", "temp_f", "zone")
        self.t2_tree = ttk.Treeview(table_f, columns=cols, show="headings")
        for cid, hd, w in [("row_num", "#", 50), ("md", "MD (ft)", 90),
                           ("tvd", "TVD (ft)", 90), ("temp_f", "Temp (°F)", 110),
                           ("zone", "Zone", 180)]:
            self.t2_tree.heading(cid, text=hd)
            self.t2_tree.column(cid, width=w,
                                anchor=tk.W if cid == "zone" else tk.CENTER)
        vsb = ttk.Scrollbar(table_f, orient=tk.VERTICAL, command=self.t2_tree.yview)
        self.t2_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.t2_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # internal state
        self._t2_map = {}
        self._t2_api_map = {}
        self._t2_key = None
        self._t2_cmpl = None
        self._t2_date = None

    # ── Tab 2 callbacks ───────────────────────────────────────────────────

    def _t2_refresh(self):
        items = []
        self._t2_api_map = {}
        for api in self.api_list:
            es = self.survey_headers.get(api, [])
            if not es:
                continue
            lbl = f"{es[0][self.H_CMPL]}  ({api})"
            items.append(lbl)
            self._t2_api_map[lbl] = api
        self.t2_well_cb["values"] = items
        if items:
            self.t2_well_cb.current(0)
            self._t2_well_changed()
        else:
            self.t2_well_cb.set("")
            self.t2_surv_cb["values"] = []
            self.t2_surv_cb.set("")

    def _t2_well_changed(self, event=None):
        api = self._t2_api_map.get(self.t2_well_var.get())
        if not api:
            return
        entries = self.survey_headers.get(api, [])
        items = []
        self._t2_map = {}
        for e in entries:
            k = e[self.H_KEY]
            n = len(self.depth_data.get(k, []))
            d = f"{_fmt_date(e[self.H_DATE])}  ({e[self.H_CODE]})  —  {n} pts"
            items.append(d)
            self._t2_map[d] = (e, k)
        self.t2_surv_cb["values"] = items
        if items:
            self.t2_surv_cb.current(0)
            self._t2_surv_changed()
        else:
            self.t2_surv_cb.set("")
            self._t2_clear()

    def _t2_surv_changed(self, event=None):
        m = self._t2_map.get(self.t2_surv_var.get())
        if not m:
            return
        entry, key = m
        cmpl = entry[self.H_CMPL]
        code = entry[self.H_CODE]
        ds = _fmt_date(entry[self.H_DATE])
        self._t2_key = key
        self._t2_cmpl = cmpl
        self._t2_date = ds
        pts = self.depth_data.get(key, [])

        # ── chart ─────────────────────────────────────────────────────────
        ax = self.t2_ax
        ax.clear()
        ax.set_title(f"{cmpl} — {ds} ({code})", fontsize=11, fontweight="bold")
        ax.set_xlabel("Temperature (°F)", fontsize=10)
        ax.set_ylabel("Measured Depth (ft)", fontsize=10)
        ax.invert_yaxis()
        ax.grid(True, alpha=0.3)
        if pts:
            depths = [p[0] for p in pts]
            temps  = [p[2] for p in pts]
            ax.plot(temps, depths, color="#4363d8", linewidth=1.0)
            ax.fill_betweenx(depths, temps, min(temps), alpha=0.08, color="#4363d8")
            mn_t, mx_t = min(temps), max(temps)
            mn_i, mx_i = temps.index(mn_t), temps.index(mx_t)
            ax.plot(mn_t, depths[mn_i], "v", color="#3cb44b", markersize=8,
                    label=f"Min {mn_t:.1f}°F @ {depths[mn_i]:.0f} ft")
            ax.plot(mx_t, depths[mx_i], "^", color="#e6194b", markersize=8,
                    label=f"Max {mx_t:.1f}°F @ {depths[mx_i]:.0f} ft")
            ax.legend(fontsize=8, loc="lower right")
        self.t2_fig.tight_layout()
        self.t2_canvas.draw()

        # ── table ─────────────────────────────────────────────────────────
        self.t2_tree.delete(*self.t2_tree.get_children())
        for i, (md, tvd, temp, zone) in enumerate(pts, 1):
            self.t2_tree.insert("", tk.END, values=(
                i, _safe(md), _safe(tvd), _safe(temp, ".2f"), zone or ""))

        self.t2_info_var.set(
            f"{cmpl} | {ds} ({code}) | {len(pts)} depth points | "
            f"Top MD: {_safe(entry[self.H_TOP], '.0f')} ft | "
            f"Tag MD: {_safe(entry[self.H_TAG], '.0f')} ft | "
            f"Min: {_safe(entry[self.H_MIN])}°F | Max: {_safe(entry[self.H_MAX])}°F")

    def _t2_clear(self):
        self.t2_ax.clear()
        self.t2_ax.set_title("No survey selected", fontsize=11)
        self.t2_canvas.draw()
        self.t2_tree.delete(*self.t2_tree.get_children())
        self.t2_info_var.set("Select a well and survey above")
        self._t2_key = None

    def _t2_export(self):
        if self._t2_key is None:
            messagebox.showinfo("No Data", "Select a well and survey first.")
            return
        sd = (self._t2_date or "").replace("-", "")
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"temp_detail_{self._t2_cmpl}_{sd}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        pts = self.depth_data.get(self._t2_key, [])
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["#", "Well", "Survey Date", "MD_ft", "TVD_ft", "Temp_F", "Zone"])
            for i, (md, tvd, temp, zone) in enumerate(pts, 1):
                w.writerow([i, self._t2_cmpl, self._t2_date, md, tvd, temp, zone])
        self.status_var.set(f"Exported {len(pts)} rows to {os.path.basename(path)}")

    def _t2_copy(self):
        if self._t2_key is None:
            messagebox.showinfo("No Data", "Select a well and survey first.")
            return
        pts = self.depth_data.get(self._t2_key, [])
        lines = ["#\tMD_ft\tTVD_ft\tTemp_F\tZone"]
        for i, (md, tvd, temp, zone) in enumerate(pts, 1):
            lines.append(f"{i}\t{_safe(md)}\t{_safe(tvd)}\t"
                         f"{_safe(temp, '.2f')}\t{zone or ''}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status_var.set(f"Copied {len(pts)} rows to clipboard")

    # ══════════════════════════════════════════════════════════════════════
    #  TAB 3 & TAB 4 — Initial / Current Temperature
    #  Per-well editable zone top/bottom (double-click Zone Top / Zone Bot).
    #  Survey Date filter (From / To) and sortable Survey Date column header.
    #  "Apply to All" pushes global depth entries to every well.
    # ══════════════════════════════════════════════════════════════════════

    _TT_COLS = [
        ("well",        "Well",             130, tk.W),
        ("api",         "API",              105, tk.CENTER),
        ("survey_date", "Survey Date \u25B2",  110, tk.CENTER),
        ("type",        "Type",              55, tk.CENTER),
        ("survey_top",  "Survey Top MD",     95, tk.CENTER),
        ("survey_tag",  "Survey Tag MD",     95, tk.CENTER),
        ("zone_top",    "Zone Top (ft)",     90, tk.CENTER),
        ("zone_bot",    "Zone Bot (ft)",     90, tk.CENTER),
        ("avg_temp",    "Avg Temp (\u00b0F)", 100, tk.CENTER),
        ("min_temp",    "Min Temp (\u00b0F)", 100, tk.CENTER),
        ("max_temp",    "Max Temp (\u00b0F)", 100, tk.CENTER),
        ("pts_used",    "# Pts Used",        80, tk.CENTER),
    ]

    def _build_temp_tab(self, title, which):
        frm = tk.Frame(self.nb)
        self.nb.add(frm, text=f"  {title}  ")

        # ── row 1: zone depth + apply ─────────────────────────────────────
        bar1 = tk.Frame(frm, padx=8, pady=6)
        bar1.pack(fill=tk.X)

        tk.Label(bar1, text="Default Zone Top (ft):",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)
        top_var = tk.StringVar()
        tk.Entry(bar1, textvariable=top_var, width=10,
                 font=("Consolas", 10)).pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(bar1, text="Default Zone Bot (ft):",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)
        bot_var = tk.StringVar()
        tk.Entry(bar1, textvariable=bot_var, width=10,
                 font=("Consolas", 10)).pack(side=tk.LEFT, padx=(4, 12))

        apply_btn = tk.Button(bar1, text="Apply to All", width=12,
                              font=("Segoe UI", 9))
        apply_btn.pack(side=tk.LEFT, padx=(4, 16))

        survey_label = "First (Oldest)" if which == "first" else "Last (Most Recent)"
        tk.Label(bar1, text=f"Using: {survey_label} survey per well",
                 font=("Segoe UI", 9, "italic"), fg="gray").pack(side=tk.LEFT)

        copy_btn = tk.Button(bar1, text="Copy to Clipboard", width=16)
        copy_btn.pack(side=tk.RIGHT, padx=(6, 0))
        exp_btn = tk.Button(bar1, text="Export CSV", width=10)
        exp_btn.pack(side=tk.RIGHT)

        # ── row 2: date filter + hint ─────────────────────────────────────
        bar2 = tk.Frame(frm, padx=8)
        bar2.pack(fill=tk.X, pady=(0, 4))

        tk.Label(bar2, text="Survey Date From:",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        date_from_var = tk.StringVar()
        tk.Entry(bar2, textvariable=date_from_var, width=12,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(bar2, text="To:",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        date_to_var = tk.StringVar()
        tk.Entry(bar2, textvariable=date_to_var, width=12,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(4, 8))

        filter_btn = tk.Button(bar2, text="Filter", width=8,
                                font=("Segoe UI", 9))
        filter_btn.pack(side=tk.LEFT, padx=(4, 4))
        clear_btn = tk.Button(bar2, text="Clear Filter", width=10,
                               font=("Segoe UI", 9))
        clear_btn.pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(bar2, text="(YYYY-MM-DD)   Double-click Zone Top / Bot to edit.   "
                 "Click Survey Date header to sort.",
                 font=("Segoe UI", 8, "italic"), fg="gray").pack(side=tk.LEFT)

        # ── treeview ──────────────────────────────────────────────────────
        cols = tuple(c[0] for c in self._TT_COLS)
        tree = ttk.Treeview(frm, columns=cols, show="headings")
        for cid, hd, w, anch in self._TT_COLS:
            tree.heading(cid, text=hd)
            tree.column(cid, width=w, anchor=anch)

        vsb = ttk.Scrollbar(frm, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        # state
        well_state = {}
        sort_asc = [True]  # mutable so closure can toggle

        state = {"tree": tree, "top_var": top_var, "bot_var": bot_var,
                 "which": which, "well_state": well_state,
                 "date_from_var": date_from_var, "date_to_var": date_to_var,
                 "detached": set(), "all_apis_ordered": [],
                 "sort_asc": sort_asc}

        # bindings
        tree.bind("<Double-1>", lambda e: self._tt_on_dblclick(e, state))
        tree.heading("survey_date",
                     command=lambda: self._tt_sort_by_date(state))

        apply_btn.config(command=lambda: self._tt_apply_all(state))
        filter_btn.config(command=lambda: self._tt_date_filter(state))
        clear_btn.config(command=lambda: self._tt_date_clear(state))
        exp_btn.config(command=lambda: self._export_temp(state, title))
        copy_btn.config(command=lambda: self._copy_temp(state))

        return state

    def _build_tab3_initial(self):
        self._t3 = self._build_temp_tab("Initial Temperature", "first")

    def _build_tab4_current(self):
        self._t4 = self._build_temp_tab("Current Temperature", "last")

    # ── Populate ──────────────────────────────────────────────────────────

    def _tt_populate(self, state):
        tree = state["tree"]
        which = state["which"]
        ws = state["well_state"]
        state["detached"].clear()
        state["all_apis_ordered"] = []
        tree.delete(*tree.get_children())

        for api in self.api_list:
            entries = self.survey_headers.get(api, [])
            if not entries:
                continue
            entry = entries[-1] if which == "first" else entries[0]
            pts = self.depth_data.get(entry[self.H_KEY], [])
            if not pts:
                continue
            if api not in ws:
                ws[api] = {"top": None, "bot": None}
            state["all_apis_ordered"].append(api)
            self._tt_insert_row(state, api, entry, pts)

    def _tt_row_vals(self, state, api, entry, pts):
        ws = state["well_state"]
        w = ws[api]
        all_mds = [p[0] for p in pts if p[0] is not None]
        if not all_mds:
            return None
        zt = w["top"] if w["top"] is not None else min(all_mds)
        zb = w["bot"] if w["bot"] is not None else max(all_mds)
        avg, mn, mx, cnt = _calc_zone_stats(pts, zt, zb)
        return (entry[self.H_CMPL], api,
                _fmt_date(entry[self.H_DATE]), entry[self.H_CODE],
                _safe(entry[self.H_TOP], ".0f"), _safe(entry[self.H_TAG], ".0f"),
                _safe(zt, ".0f"), _safe(zb, ".0f"),
                _safe(avg, ".2f"), _safe(mn, ".2f"), _safe(mx, ".2f"), cnt)

    def _tt_insert_row(self, state, api, entry, pts):
        tree = state["tree"]
        vals = self._tt_row_vals(state, api, entry, pts)
        if vals is None:
            return
        if tree.exists(api):
            tree.item(api, values=vals)
        else:
            tree.insert("", tk.END, iid=api, values=vals)

    def _tt_recalc_row(self, state, api):
        which = state["which"]
        entries = self.survey_headers.get(api, [])
        if not entries:
            return
        entry = entries[-1] if which == "first" else entries[0]
        pts = self.depth_data.get(entry[self.H_KEY], [])
        if pts:
            self._tt_insert_row(state, api, entry, pts)

    # ── Apply to All ──────────────────────────────────────────────────────

    def _tt_apply_all(self, state):
        ws = state["well_state"]
        try:
            g_top = float(state["top_var"].get())
        except (ValueError, TypeError):
            g_top = None
        try:
            g_bot = float(state["bot_var"].get())
        except (ValueError, TypeError):
            g_bot = None
        for api in ws:
            ws[api]["top"] = g_top
            ws[api]["bot"] = g_bot
            self._tt_recalc_row(state, api)

    # ── Date filter ───────────────────────────────────────────────────────

    def _tt_date_filter(self, state):
        """Hide rows whose survey date falls outside From/To range."""
        tree = state["tree"]
        detached = state["detached"]
        d_from = state["date_from_var"].get().strip()
        d_to   = state["date_to_var"].get().strip()

        # reattach everything first
        for api in state["all_apis_ordered"]:
            if api in detached:
                tree.reattach(api, "", "end")
                detached.discard(api)

        if not d_from and not d_to:
            self.status_var.set(f"Showing all {len(state['all_apis_ordered'])} wells")
            return

        for api in state["all_apis_ordered"]:
            if not tree.exists(api):
                continue
            vals = tree.item(api, "values")
            date_str = str(vals[2])  # survey_date column index 2
            hide = False
            if d_from and date_str < d_from:
                hide = True
            if d_to and date_str > d_to:
                hide = True
            if hide:
                tree.detach(api)
                detached.add(api)

        n_vis = len(tree.get_children())
        n_tot = len(state["all_apis_ordered"])
        self.status_var.set(f"Showing {n_vis} of {n_tot} wells (date filtered)")

    def _tt_date_clear(self, state):
        """Clear date filter and show all rows."""
        state["date_from_var"].set("")
        state["date_to_var"].set("")
        tree = state["tree"]
        detached = state["detached"]
        for api in state["all_apis_ordered"]:
            if api in detached:
                tree.reattach(api, "", "end")
                detached.discard(api)
        self.status_var.set(f"Showing all {len(state['all_apis_ordered'])} wells")

    # ── Sort by Survey Date ───────────────────────────────────────────────

    def _tt_sort_by_date(self, state):
        """Toggle sort on Survey Date column (asc/desc)."""
        tree = state["tree"]
        sort_asc = state["sort_asc"]

        # get all visible items with their date value
        items = []
        for iid in tree.get_children():
            vals = tree.item(iid, "values")
            items.append((iid, str(vals[2])))  # index 2 = survey_date

        items.sort(key=lambda x: x[1], reverse=not sort_asc[0])

        for idx, (iid, _) in enumerate(items):
            tree.move(iid, "", idx)

        # toggle direction for next click
        sort_asc[0] = not sort_asc[0]

        # update header arrow
        arrow = "\u25B2" if sort_asc[0] else "\u25BC"
        tree.heading("survey_date", text=f"Survey Date {arrow}")

    # ── Double-click for zone editing ─────────────────────────────────────

    def _tt_on_dblclick(self, event, state):
        tree = state["tree"]
        ws = state["well_state"]

        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id or not col_id:
            return

        col_idx = int(col_id.replace("#", "")) - 1
        col_ids = [c[0] for c in self._TT_COLS]
        if col_idx >= len(col_ids):
            return
        col_name = col_ids[col_idx]
        api = row_id

        if api not in ws:
            return
        if col_name not in ("zone_top", "zone_bot"):
            return

        bbox = tree.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox

        vals = tree.item(row_id, "values")
        cur_val = vals[col_idx] if col_idx < len(vals) else ""

        entry = tk.Entry(tree, font=("Consolas", 10), justify=tk.CENTER)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, cur_val)
        entry.select_range(0, tk.END)
        entry.focus_set()

        def _commit(evt=None):
            new_val = entry.get().strip()
            entry.destroy()
            try:
                parsed = float(new_val) if new_val else None
            except ValueError:
                return
            if col_name == "zone_top":
                ws[api]["top"] = parsed
            else:
                ws[api]["bot"] = parsed
            self._tt_recalc_row(state, api)

        def _cancel(evt=None):
            entry.destroy()

        entry.bind("<Return>", _commit)
        entry.bind("<Tab>", _commit)
        entry.bind("<FocusOut>", _commit)
        entry.bind("<Escape>", _cancel)

    # ── Export / Copy (visible rows only) ─────────────────────────────────

    def _export_temp(self, state, title):
        tree = state["tree"]
        visible = tree.get_children()
        if not visible:
            messagebox.showinfo("No Data", "No visible rows to export.")
            return
        safe = title.replace(" ", "_").lower()
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=f"{safe}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[1] for c in self._TT_COLS])
            for iid in visible:
                w.writerow(tree.item(iid, "values"))
        self.status_var.set(f"Exported {len(visible)} rows to {os.path.basename(path)}")

    def _copy_temp(self, state):
        tree = state["tree"]
        visible = tree.get_children()
        if not visible:
            messagebox.showinfo("No Data", "No visible rows to copy.")
            return
        hdr = "\t".join(c[1] for c in self._TT_COLS)
        lines = [hdr]
        for iid in visible:
            lines.append("\t".join(str(v) for v in tree.item(iid, "values")))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status_var.set(f"Copied {len(visible)} rows to clipboard")

    # ══════════════════════════════════════════════════════════════════════
    #  LOAD / CLEAR / PARSE
    # ══════════════════════════════════════════════════════════════════════

    def _parse_apis(self):
        raw = self.api_text.get("1.0", tk.END).strip()
        if not raw:
            return []
        tokens = re.split(r"[,\s\n\r]+", raw)
        apis = []
        for t in tokens:
            t = t.strip().replace("-", "")
            if not t:
                continue
            if len(t) == 9:
                t = "0" + t
            if len(t) == 10 and t.isdigit():
                apis.append(t)
            else:
                messagebox.showwarning("Invalid API",
                                       f"'{t}' is not a valid 10-digit API.")
        return list(dict.fromkeys(apis))

    def _on_clear(self):
        self.api_text.delete("1.0", tk.END)
        self.survey_headers.clear()
        self.depth_data.clear()
        self.api_list.clear()
        # tab 1
        self.t1_tree.delete(*self.t1_tree.get_children())
        self.t1_ax.clear()
        self.t1_canvas.draw()
        # tab 2
        self.t2_well_cb["values"] = []; self.t2_well_cb.set("")
        self.t2_surv_cb["values"] = []; self.t2_surv_cb.set("")
        self._t2_clear()
        # tab 3 / 4
        for st in (self._t3, self._t4):
            st["tree"].delete(*st["tree"].get_children())
            st["well_state"].clear()
            st["detached"].clear()
            st["all_apis_ordered"].clear()
            st["date_from_var"].set("")
            st["date_to_var"].set("")
        self.status_var.set("Cleared")

    def _on_load(self):
        apis = self._parse_apis()
        if not apis:
            messagebox.showinfo("No APIs", "Enter at least one valid 10-digit API.")
            return
        self.api_list = apis
        self.load_btn.config(state=tk.DISABLED)
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, before=self.status_bar)
        self.progress.start(15)
        self.status_var.set(f"Querying ODW for {len(apis)} API(s)...")
        threading.Thread(target=self._bg_load, daemon=True).start()

    def _bg_load(self):
        try:
            conn = get_connection()

            self.after(0, lambda: self.status_var.set(
                "Step 1/2: Fetching survey headers..."))
            headers = query_survey_headers(conn, self.api_list)

            all_keys = [e[self.H_KEY] for es in headers.values() for e in es]
            self.after(0, lambda: self.status_var.set(
                f"Step 2/2: Fetching depth data for {len(all_keys)} survey(s)..."))

            depth = {}
            for i in range(0, len(all_keys), 50):
                chunk = query_survey_depth_data(conn, all_keys[i:i + 50])
                depth.update(chunk)

            conn.close()
            self.survey_headers = headers
            self.depth_data = depth
            self.after(0, self._on_data_ready)

        except Exception as exc:
            self.after(0, lambda: self._on_load_error(str(exc)))

    def _on_load_error(self, msg):
        self.progress.stop()
        self.progress.pack_forget()
        self.load_btn.config(state=tk.NORMAL)
        self.status_var.set("Error")
        messagebox.showerror("Database Error", msg)

    def _on_data_ready(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.load_btn.config(state=tk.NORMAL)

        n_with = sum(1 for a in self.api_list if self.survey_headers.get(a))
        self.status_var.set(
            f"Loaded: {n_with}/{len(self.api_list)} wells have temperature surveys.")

        self._t1_populate()
        self._t2_refresh()
        self._tt_populate(self._t3)
        self._tt_populate(self._t4)


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if oracledb is None:
        print("ERROR: 'oracledb' package required.  pip install oracledb")
        exit(1)
    if matplotlib is None:
        print("ERROR: 'matplotlib' package required.  pip install matplotlib")
        exit(1)
    app = TempSurveyApp()
    app.mainloop()