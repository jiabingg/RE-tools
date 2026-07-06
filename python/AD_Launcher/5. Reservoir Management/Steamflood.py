"""
Steamflood Manager — Lost Hills AE-LHH
======================================
Injector-centered pattern surveillance, opportunity identification, and
decision support for the AE-LHH continuous steamflood (Tulare + Etchegoin).

Pattern definition:
  * Each active injector with bottomhole coordinates and a current zone
    assignment defines one pattern (pattern key = injector).
  * Producers within PATTERN_RADIUS_FT (bottomhole XY) of the injector that
    are open in the same reservoir (TULARE or ETCHEGOIN) are members.
  * A producer belonging to N patterns contributes 1/N of its volumes to
    each pattern. Injector volumes are allocated 100% to its own pattern.

Data sources (ODW, dwrptg + dss):
  * cmpl_dmn                  — well inventory
  * dss.dss_completion_master — bottomhole XY (pid = cmpl_fac_id)
  * wlbr_cmpl_gntl_fpp_fact   — current zone membership (term_dttm IS NULL)
  * cmpl_mnly_fact            — 36-month rates + temps (_dly_rte_qty columns)

Tabs:
  1. Field Overview   — KPIs + 36-month field trend (oil / steam / SOR)
  2. Patterns         — one row per injector pattern with health flags
  3. Pattern Detail   — member table + pattern map + rate history
  4. Well Detail      — single-well 36-month history
  5. Opportunities    — rule-based action list (decision support)
"""

import csv
import io
import math
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict
from datetime import datetime

import oracledb

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ------------------------------------------------------------------ config
DB_USER = "rptguser"
DB_PASSWORD = "allusers"
DB_DSN = "ODW"

ENGR_STRG = "AE-LHH"          # engineering string in scope
PATTERN_RADIUS_FT = 300.0      # producer capture radius around each injector
HISTORY_MONTHS = 36            # months of monthly history to load

# Health / opportunity thresholds (tune as needed)
SOR_EXCELLENT = 4.0
SOR_GOOD = 6.0
SOR_HIGH = 10.0
DECLINE_FLAG_PCT = -20.0       # 3-mo avg vs prior 12-mo avg oil change
INJ_CHANGE_FLAG_PCT = 25.0     # injector last-month vs prior 3-mo avg
TEMP_DROP_FLAG_F = 30.0        # flowline temp drop, 3-mo vs prior 12-mo
MIN_OIL_FOR_FLAGS = 2.0        # ignore trend flags below this BOPD
MIN_STEAM_FOR_FLAGS = 50.0     # ignore injector flags below this BSPD

APP_TITLE = f"Steamflood Manager — Lost Hills {ENGR_STRG}"

# Try thick mode first (required in some environments); fall back to thin.
try:
    oracledb.init_oracle_client()
except Exception:
    pass


def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)


# ------------------------------------------------------------------ SQL
SQL_WELLS = """
SELECT
    cd.cmpl_fac_id,
    cd.cmpl_nme,
    cd.prim_purp_type_cde,
    cd.in_svc_indc,
    cd.cmpl_state_type_cde,
    cd.init_prod_dte,
    cm.bottomx,
    cm.bottomy
FROM dwrptg.cmpl_dmn cd
LEFT JOIN dss.dss_completion_master cm ON cd.cmpl_fac_id = cm.pid
WHERE cd.engr_strg_nme = :strg
  AND cd.actv_indc = 'Y'
"""

# Current zone membership -> reservoir (TULARE / ETCHEGOIN)
SQL_ZONES = """
SELECT DISTINCT
    cd.cmpl_fac_id,
    CASE
        WHEN gd.lthsg_unit_nme LIKE 'TUL%' THEN 'TULARE'
        WHEN gd.lthsg_unit_nme LIKE 'ETCH%'
          OR gd.lthsg_unit_nme IN ('D','E','F','G') THEN 'ETCHEGOIN'
        ELSE 'OTHER'
    END AS rsvr
FROM dwrptg.wlbr_cmpl_gntl_fpp_fact f
JOIN dwrptg.gntl_dmn gd ON gd.gntl_dmn_key = f.gntl_dmn_key
JOIN dwrptg.cmpl_dmn cd ON cd.cmpl_fac_id = f.cmpl_fac_id
WHERE cd.engr_strg_nme = :strg
  AND cd.actv_indc = 'Y'
  AND f.term_dttm IS NULL
"""

SQL_MONTHLY = f"""
SELECT
    cmf.cmpl_fac_id,
    cmf.eftv_dttm,
    NVL(cmf.aloc_oil_prod_dly_rte_qty, 0)      AS oil_bopd,
    NVL(cmf.aloc_gros_prod_dly_rte_qty, 0)     AS gross_bfpd,
    NVL(cmf.aloc_wtr_prod_dly_rte_qty, 0)      AS water_bwpd,
    NVL(cmf.aloc_cnts_stm_inj_dly_rte_qty, 0)  AS steam_bspd,
    NVL(cmf.aloc_cycl_stm_inj_dly_rte_qty, 0)  AS cycl_bspd,
    NVL(cmf.aloc_wtr_inj_dly_rte_qty, 0)       AS wtr_inj_bwpd,
    cmf.avg_flw_line_temp_qty                  AS fl_temp,
    cmf.avg_wlhd_tbg_prsr_qty                  AS whp
FROM dwrptg.cmpl_mnly_fact cmf
JOIN dwrptg.cmpl_dmn cd ON cmf.cmpl_fac_id = cd.cmpl_fac_id
WHERE cd.engr_strg_nme = :strg
  AND cd.actv_indc = 'Y'
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -{HISTORY_MONTHS})
"""


def _safe_query(cur, sql, binds, label, errors):
    """Run one query; collect (not raise) errors so one bad column can't
    kill the whole load."""
    try:
        cur.execute(sql, binds)
        return cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{label}: {exc}")
        return []


# ------------------------------------------------------------------ data
class Well:
    __slots__ = ("fac_id", "name", "purpose", "in_svc", "state",
                 "init_prod", "x", "y", "reservoirs", "n_patterns")

    def __init__(self, fac_id, name, purpose, in_svc, state, init_prod, x, y):
        self.fac_id = fac_id
        self.name = name
        self.purpose = purpose            # PROD / INJ / OBSN
        self.in_svc = in_svc
        self.state = state
        self.init_prod = init_prod
        self.x = x
        self.y = y
        self.reservoirs = set()           # {'TULARE', 'ETCHEGOIN'}
        self.n_patterns = 0               # producers only: membership count


class Pattern:
    __slots__ = ("inj", "reservoirs", "members")

    def __init__(self, inj):
        self.inj = inj                    # Well (injector)
        self.reservoirs = set(inj.reservoirs)
        self.members = []                 # [(producer Well, distance_ft)]


class DataStore:
    def __init__(self):
        self.wells = {}                   # fac_id -> Well
        self.by_name = {}                 # name -> Well
        self.months = []                  # sorted list of date objects
        self.monthly = defaultdict(dict)  # fac_id -> {month: row dict}
        self.patterns = {}                # inj fac_id -> Pattern
        self.load_errors = []
        self.loaded_at = None

    # ---------------- load
    def load_all(self, progress_cb=None):
        def report(pct, msg):
            if progress_cb:
                progress_cb(pct, msg)

        errors = self.load_errors = []
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.arraysize = 5000

            report(5, "Loading well inventory…")
            for (fac_id, name, purp, in_svc, state, init_prod,
                 bx, by) in _safe_query(cur, SQL_WELLS, {"strg": ENGR_STRG},
                                        "Well inventory", errors):
                w = Well(fac_id, name, purp, in_svc, state, init_prod, bx, by)
                self.wells[fac_id] = w
                self.by_name[name] = w

            report(25, "Loading zone membership…")
            for fac_id, rsvr in _safe_query(cur, SQL_ZONES,
                                            {"strg": ENGR_STRG},
                                            "Zone membership", errors):
                if rsvr in ("TULARE", "ETCHEGOIN") and fac_id in self.wells:
                    self.wells[fac_id].reservoirs.add(rsvr)

            report(45, f"Loading {HISTORY_MONTHS}-month production/injection…")
            months = set()
            for row in _safe_query(cur, SQL_MONTHLY, {"strg": ENGR_STRG},
                                   "Monthly rates", errors):
                (fac_id, dt, oil, gross, water, steam, cycl,
                 wtr_inj, fl_temp, whp) = row
                m = dt.date() if hasattr(dt, "date") else dt
                months.add(m)
                self.monthly[fac_id][m] = {
                    "oil": oil, "gross": gross, "water": water,
                    "steam": steam, "cycl": cycl, "wtr_inj": wtr_inj,
                    "fl_temp": fl_temp, "whp": whp,
                }
            self.months = sorted(months)

            report(85, "Building injector-centered patterns…")
            self.build_patterns()
            self.loaded_at = datetime.now()
            report(100, "Ready")
        finally:
            conn.close()

    # ---------------- pattern engine
    def build_patterns(self):
        self.patterns = {}
        producers = [w for w in self.wells.values()
                     if w.purpose == "PROD" and w.x is not None
                     and w.y is not None and w.reservoirs]
        injectors = [w for w in self.wells.values()
                     if w.purpose == "INJ" and w.x is not None
                     and w.y is not None and w.reservoirs]

        # Coarse spatial bucketing so 450 x 1400 stays instant.
        cell = PATTERN_RADIUS_FT
        grid = defaultdict(list)
        for p in producers:
            p.n_patterns = 0
            grid[(int(p.x // cell), int(p.y // cell))].append(p)

        r2 = PATTERN_RADIUS_FT * PATTERN_RADIUS_FT
        for inj in injectors:
            pat = Pattern(inj)
            gx, gy = int(inj.x // cell), int(inj.y // cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for p in grid.get((gx + dx, gy + dy), ()):
                        if not (p.reservoirs & inj.reservoirs):
                            continue  # must be open in the same reservoir
                        d2 = (p.x - inj.x) ** 2 + (p.y - inj.y) ** 2
                        if d2 <= r2:
                            pat.members.append((p, math.sqrt(d2)))
            pat.members.sort(key=lambda t: t[1])
            self.patterns[inj.fac_id] = pat

        # Producer -> number of patterns it belongs to (for 1/N split)
        for pat in self.patterns.values():
            for p, _ in pat.members:
                p.n_patterns += 1

    # ---------------- series helpers
    def well_series(self, fac_id, key):
        rows = self.monthly.get(fac_id, {})
        return [rows.get(m, {}).get(key) for m in self.months]

    def pattern_series(self, inj_fac_id):
        """Monthly allocated series for one pattern.
        Producer volumes weighted 1/N; injector steam 100%."""
        pat = self.patterns[inj_fac_id]
        out = {"oil": [], "gross": [], "water": [], "steam": []}
        inj_rows = self.monthly.get(inj_fac_id, {})
        for m in self.months:
            oil = gross = water = 0.0
            for p, _ in pat.members:
                r = self.monthly.get(p.fac_id, {}).get(m)
                if r:
                    w = 1.0 / p.n_patterns if p.n_patterns else 0.0
                    oil += (r["oil"] or 0) * w
                    gross += (r["gross"] or 0) * w
                    water += (r["water"] or 0) * w
            ir = inj_rows.get(m)
            steam = ((ir["steam"] or 0) + (ir["cycl"] or 0)) if ir else 0.0
            out["oil"].append(oil)
            out["gross"].append(gross)
            out["water"].append(water)
            out["steam"].append(steam)
        return out

    @staticmethod
    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    def pattern_summary(self, inj_fac_id):
        """One-row health summary for a pattern (latest 3 mo vs prior 12)."""
        pat = self.patterns[inj_fac_id]
        s = self.pattern_series(inj_fac_id)
        oil3 = self._avg(s["oil"][-3:]) or 0.0
        gross3 = self._avg(s["gross"][-3:]) or 0.0
        water3 = self._avg(s["water"][-3:]) or 0.0
        steam3 = self._avg(s["steam"][-3:]) or 0.0
        oil12 = self._avg(s["oil"][-15:-3])
        sor = steam3 / oil3 if oil3 > 0.1 else None
        wc = water3 / gross3 * 100 if gross3 > 0.1 else None
        trend = ((oil3 - oil12) / oil12 * 100
                 if oil12 and oil12 > MIN_OIL_FOR_FLAGS else None)

        if not pat.members:
            status = "NO PRODUCERS"
        elif steam3 < 1:
            status = "NO STEAM"
        elif sor is None:
            status = "NO OIL"
        elif sor < SOR_EXCELLENT:
            status = "EFFICIENT"
        elif sor < SOR_GOOD:
            status = "GOOD"
        elif sor < SOR_HIGH:
            status = "REVIEW"
        else:
            status = "HIGH SOR"

        return {
            "inj": pat.inj, "reservoir": "/".join(sorted(pat.reservoirs)),
            "n_prod": len(pat.members),
            "oil": oil3, "gross": gross3, "steam": steam3,
            "sor": sor, "wc": wc, "trend": trend, "status": status,
        }

    def field_series(self, reservoir=None):
        """Field-level monthly totals, optionally filtered to one reservoir.
        Reservoir filter uses well zone membership (unsplit volumes)."""
        oil = [0.0] * len(self.months)
        gross = [0.0] * len(self.months)
        steam = [0.0] * len(self.months)
        for w in self.wells.values():
            if reservoir and reservoir not in w.reservoirs:
                continue
            rows = self.monthly.get(w.fac_id)
            if not rows:
                continue
            for i, m in enumerate(self.months):
                r = rows.get(m)
                if not r:
                    continue
                oil[i] += r["oil"] or 0
                gross[i] += r["gross"] or 0
                steam[i] += (r["steam"] or 0) + (r["cycl"] or 0)
        return oil, gross, steam

    # ---------------- opportunity engine
    def opportunities(self):
        """Rule-based findings -> list of dicts sorted by severity."""
        finds = []
        summaries = {fid: self.pattern_summary(fid) for fid in self.patterns}

        # -- pattern-level rules
        for fid, sm in summaries.items():
            name = sm["inj"].name
            if sm["steam"] >= MIN_STEAM_FOR_FLAGS and not sm["n_prod"]:
                finds.append(dict(
                    sev=1, kind="Orphan injector", entity=name, level="Pattern",
                    detail=f"{sm['steam']:.0f} BSPD with no in-zone producers "
                           f"within {PATTERN_RADIUS_FT:.0f} ft",
                    action="Verify pattern intent; reallocate or shut in steam"))
            if sm["sor"] is not None and sm["sor"] > SOR_HIGH \
                    and sm["steam"] >= MIN_STEAM_FOR_FLAGS:
                finds.append(dict(
                    sev=1, kind="High SOR pattern", entity=name, level="Pattern",
                    detail=f"SOR {sm['sor']:.1f} "
                           f"({sm['steam']:.0f} BSPD / {sm['oil']:.1f} BOPD)",
                    action="Cut steam; run ITG profile survey; conformance review"))
            if sm["sor"] is not None and sm["sor"] < SOR_EXCELLENT \
                    and (sm["trend"] or 0) < DECLINE_FLAG_PCT:
                finds.append(dict(
                    sev=2, kind="Steam-up candidate", entity=name, level="Pattern",
                    detail=f"Efficient (SOR {sm['sor']:.1f}) but oil "
                           f"{sm['trend']:+.0f}% vs prior 12-mo",
                    action="Candidate to increase steam target"))
            if sm["wc"] is not None and sm["wc"] > 98 \
                    and sm["sor"] is not None and sm["sor"] > 8:
                finds.append(dict(
                    sev=2, kind="Watered-out pattern", entity=name, level="Pattern",
                    detail=f"WC {sm['wc']:.1f}%, SOR {sm['sor']:.1f}",
                    action="Consider steam reduction / heat-balance rate"))

        # -- producer-level rules
        supported = set()
        for pat in self.patterns.values():
            for p, _ in pat.members:
                supported.add(p.fac_id)

        for w in self.wells.values():
            if w.purpose != "PROD":
                continue
            oil3 = self._avg(self.well_series(w.fac_id, "oil")[-3:])
            if oil3 is None:
                continue
            oil12 = self._avg(self.well_series(w.fac_id, "oil")[-15:-3])
            # Unsupported producer
            if w.fac_id not in supported and oil3 > MIN_OIL_FOR_FLAGS \
                    and w.reservoirs:
                finds.append(dict(
                    sev=3, kind="Unsupported producer", entity=w.name,
                    level="Well",
                    detail=f"{oil3:.1f} BOPD, no in-zone injector within "
                           f"{PATTERN_RADIUS_FT:.0f} ft",
                    action="Cyclic steam candidate / pattern gap review"))
            # Sharp decline
            if oil12 and oil12 > MIN_OIL_FOR_FLAGS:
                chg = (oil3 - oil12) / oil12 * 100
                if chg < DECLINE_FLAG_PCT:
                    finds.append(dict(
                        sev=2, kind="Producer decline", entity=w.name,
                        level="Well",
                        detail=f"Oil {oil3:.1f} vs {oil12:.1f} BOPD "
                               f"({chg:+.0f}%)",
                        action="Check mechanical (WE) then offset injector "
                               "changes (1–6 mo lag)"))
            # Flowline temp drop
            ft3 = self._avg(self.well_series(w.fac_id, "fl_temp")[-3:])
            ft12 = self._avg(self.well_series(w.fac_id, "fl_temp")[-15:-3])
            if ft3 and ft12 and (ft12 - ft3) > TEMP_DROP_FLAG_F \
                    and oil3 > MIN_OIL_FOR_FLAGS:
                finds.append(dict(
                    sev=2, kind="Thermal support loss", entity=w.name,
                    level="Well",
                    detail=f"Flowline temp {ft3:.0f}°F vs {ft12:.0f}°F "
                           f"({ft3 - ft12:+.0f}°F)",
                    action="Check offset injector rates / profile; "
                           "cyclic candidate"))

        # -- injector-level rules: recent rate change
        for w in self.wells.values():
            if w.purpose != "INJ":
                continue
            steam = [((r or 0) + (c or 0)) if r is not None or c is not None
                     else None
                     for r, c in zip(self.well_series(w.fac_id, "steam"),
                                     self.well_series(w.fac_id, "cycl"))]
            if len(steam) < 5:
                continue
            last = steam[-1]
            prior = self._avg([v for v in steam[-4:-1] if v is not None])
            if last is None or not prior or prior < MIN_STEAM_FOR_FLAGS:
                continue
            chg = (last - prior) / prior * 100
            if abs(chg) > INJ_CHANGE_FLAG_PCT:
                direction = "cut" if chg < 0 else "increase"
                finds.append(dict(
                    sev=3, kind=f"Injector rate {direction}", entity=w.name,
                    level="Well",
                    detail=f"{last:.0f} vs {prior:.0f} BSPD ({chg:+.0f}%)",
                    action="Verify intentional (target change); watch offset "
                           "producers 1–6 mo"))

        finds.sort(key=lambda f: (f["sev"], f["kind"], f["entity"]))
        return finds


# ------------------------------------------------------------------ UI
BG = "#f0f2f5"
CARD = "#ffffff"
ACCENT = "#1f6fb2"
ACCENT2 = "#2e8b57"
WARN = "#c0392b"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
FONT_H = ("Segoe UI", 14, "bold")
FONT_KPI = ("Segoe UI", 20, "bold")

STATUS_COLORS = {
    "EFFICIENT": "#d5f5e3", "GOOD": "#eafaf1", "REVIEW": "#fdf2d0",
    "HIGH SOR": "#fadbd8", "NO PRODUCERS": "#e8e8e8",
    "NO STEAM": "#e8e8e8", "NO OIL": "#fadbd8",
}
SEV_COLORS = {1: "#fadbd8", 2: "#fdf2d0", 3: "#eaf2f8"}


def _sort_key(val):
    """(priority, numeric, text) — numbers sort numerically, blanks last."""
    if val is None or val == "":
        return (2, 0.0, "")
    s = str(val).replace(",", "").replace("%", "").strip()
    try:
        return (0, float(s), "")
    except ValueError:
        return (1, 0.0, str(val).lower())


class SortableTree(ttk.Treeview):
    def __init__(self, master, columns, widths, **kw):
        super().__init__(master, columns=columns, show="headings", **kw)
        for col, w in zip(columns, widths):
            self.heading(col, text=col,
                         command=lambda c=col: self._sort(c, False))
            anchor = "e" if w < 110 else "w"
            self.column(col, width=w, anchor=anchor, stretch=True)

    def _sort(self, col, reverse):
        rows = [(self.set(k, col), k) for k in self.get_children("")]
        rows.sort(key=lambda t: _sort_key(t[0]), reverse=reverse)
        for i, (_, k) in enumerate(rows):
            self.move(k, "", i)
        self.heading(col, command=lambda: self._sort(col, not reverse))

    def visible_rows(self):
        cols = self["columns"]
        yield cols
        for k in self.get_children(""):
            yield [self.set(k, c) for c in cols]


def add_export_bar(parent, tree, title):
    bar = tk.Frame(parent, bg=CARD)
    bar.pack(fill="x", padx=8, pady=(0, 6))

    def export_csv():
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=f"{title}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(tree.visible_rows())
        messagebox.showinfo("Export", f"Saved {path}")

    def copy_clip():
        buf = io.StringIO()
        for row in tree.visible_rows():
            buf.write("\t".join(str(v) for v in row) + "\n")
        parent.clipboard_clear()
        parent.clipboard_append(buf.getvalue())

    ttk.Button(bar, text="Export CSV", command=export_csv,
               style="Accent.TButton").pack(side="right", padx=(6, 0))
    ttk.Button(bar, text="Copy to Clipboard",
               command=copy_clip).pack(side="right")
    return bar


def fmt(v, nd=1):
    if v is None:
        return ""
    return f"{v:,.{nd}f}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1480x900")
        self.configure(bg=BG)
        self.data = DataStore()
        self._style()
        self._build_shell()
        self._start_load()

    # ---------- style
    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, font=FONT)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(16, 8), font=FONT_B)
        st.map("TNotebook.Tab",
               background=[("selected", CARD)],
               foreground=[("selected", ACCENT)])
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     rowheight=24, font=FONT)
        st.configure("Treeview.Heading", font=FONT_B)
        st.configure("Accent.TButton", background=ACCENT,
                     foreground="white", font=FONT_B, padding=(10, 5))
        st.map("Accent.TButton", background=[("active", "#155a91")])
        st.configure("Green.TButton", background=ACCENT2,
                     foreground="white", font=FONT_B, padding=(10, 5))
        st.map("Green.TButton", background=[("active", "#226b42")])
        st.configure("TCombobox", fieldbackground=CARD)
        st.configure("Horizontal.TProgressbar", background=ACCENT)

    # ---------- shell + load
    def _build_shell(self):
        self.load_frame = tk.Frame(self, bg=BG)
        self.load_frame.pack(expand=True)
        tk.Label(self.load_frame, text=APP_TITLE, font=FONT_H,
                 bg=BG, fg=ACCENT).pack(pady=(0, 12))
        self.pbar = ttk.Progressbar(self.load_frame, length=420,
                                    mode="determinate")
        self.pbar.pack()
        self.load_lbl = tk.Label(self.load_frame, text="Connecting…",
                                 bg=BG, font=FONT)
        self.load_lbl.pack(pady=6)

    def _start_load(self):
        def progress(pct, msg):
            self.after(0, lambda: (self.pbar.configure(value=pct),
                                   self.load_lbl.configure(text=msg)))

        def worker():
            try:
                self.data.load_all(progress)
                self.after(0, self._on_loaded)
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: messagebox.showerror(
                    "Load failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_loaded(self):
        if self.data.load_errors:
            messagebox.showwarning(
                "Partial load",
                "Some queries failed:\n\n" +
                "\n".join(self.data.load_errors))
        self.load_frame.destroy()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_overview = tk.Frame(self.nb, bg=BG)
        self.tab_patterns = tk.Frame(self.nb, bg=BG)
        self.tab_pattern_dt = tk.Frame(self.nb, bg=BG)
        self.tab_well = tk.Frame(self.nb, bg=BG)
        self.tab_opps = tk.Frame(self.nb, bg=BG)
        self.nb.add(self.tab_overview, text="  Field Overview  ")
        self.nb.add(self.tab_patterns, text="  Patterns  ")
        self.nb.add(self.tab_pattern_dt, text="  Pattern Detail  ")
        self.nb.add(self.tab_well, text="  Well Detail  ")
        self.nb.add(self.tab_opps, text="  Opportunities  ")
        self._build_overview()
        self._build_patterns()
        self._build_pattern_detail()
        self._build_well_detail()
        self._build_opportunities()

    # ================================================== Tab 1: Overview
    def _build_overview(self):
        top = tk.Frame(self.tab_overview, bg=BG)
        top.pack(fill="x", padx=6, pady=(6, 0))
        tk.Label(top, text="Reservoir:", bg=BG, font=FONT_B).pack(side="left")
        self.ov_rsvr = ttk.Combobox(
            top, values=["All", "TULARE", "ETCHEGOIN"], width=12,
            state="readonly")
        self.ov_rsvr.current(0)
        self.ov_rsvr.pack(side="left", padx=6)
        self.ov_rsvr.bind("<<ComboboxSelected>>", lambda e: self._refresh_ov())
        self.ov_asof = tk.Label(top, bg=BG, fg="#666")
        self.ov_asof.pack(side="right")

        self.kpi_frame = tk.Frame(self.tab_overview, bg=BG)
        self.kpi_frame.pack(fill="x", padx=6, pady=8)
        self.kpi_labels = {}
        for key, title in [("oil", "Oil (BOPD)"), ("gross", "Gross (BFPD)"),
                           ("steam", "Steam (BSPD)"), ("sor", "Field SOR"),
                           ("wells", "Producers / Injectors"),
                           ("patterns", "Patterns")]:
            card = tk.Frame(self.kpi_frame, bg=CARD, bd=0,
                            highlightbackground="#dcdfe3",
                            highlightthickness=1)
            card.pack(side="left", expand=True, fill="both", padx=4)
            tk.Label(card, text=title, bg=CARD, fg="#666",
                     font=FONT).pack(pady=(10, 0))
            lbl = tk.Label(card, text="—", bg=CARD, fg=ACCENT, font=FONT_KPI)
            lbl.pack(pady=(0, 10))
            self.kpi_labels[key] = lbl

        chart_card = tk.Frame(self.tab_overview, bg=CARD,
                              highlightbackground="#dcdfe3",
                              highlightthickness=1)
        chart_card.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.ov_fig = Figure(figsize=(10, 4.6), dpi=100, facecolor=CARD)
        self.ov_canvas = FigureCanvasTkAgg(self.ov_fig, master=chart_card)
        self.ov_canvas.get_tk_widget().pack(fill="both", expand=True,
                                            padx=6, pady=6)
        self._refresh_ov()

    def _refresh_ov(self):
        d = self.data
        rsvr = self.ov_rsvr.get()
        rsvr = None if rsvr == "All" else rsvr
        oil, gross, steam = d.field_series(rsvr)
        n3 = min(3, len(d.months))
        oil3 = sum(oil[-n3:]) / n3 if n3 else 0
        gross3 = sum(gross[-n3:]) / n3 if n3 else 0
        steam3 = sum(steam[-n3:]) / n3 if n3 else 0
        sor = steam3 / oil3 if oil3 > 0.1 else None
        n_prod = sum(1 for w in d.wells.values() if w.purpose == "PROD"
                     and (not rsvr or rsvr in w.reservoirs))
        n_inj = sum(1 for w in d.wells.values() if w.purpose == "INJ"
                    and (not rsvr or rsvr in w.reservoirs))
        n_pat = sum(1 for p in d.patterns.values()
                    if not rsvr or rsvr in p.reservoirs)
        self.kpi_labels["oil"].config(text=f"{oil3:,.0f}")
        self.kpi_labels["gross"].config(text=f"{gross3:,.0f}")
        self.kpi_labels["steam"].config(text=f"{steam3:,.0f}")
        self.kpi_labels["sor"].config(
            text=f"{sor:.1f}" if sor else "—",
            fg=(WARN if sor and sor > SOR_HIGH else ACCENT))
        self.kpi_labels["wells"].config(text=f"{n_prod} / {n_inj}")
        self.kpi_labels["patterns"].config(text=f"{n_pat}")
        if d.loaded_at:
            self.ov_asof.config(
                text=f"Loaded {d.loaded_at:%Y-%m-%d %H:%M} — "
                     f"{len(d.months)} months of history")

        self.ov_fig.clear()
        ax = self.ov_fig.add_subplot(111)
        ax2 = ax.twinx()
        x = d.months
        ax.plot(x, oil, color=ACCENT2, lw=2, label="Oil (BOPD)")
        ax.plot(x, steam, color=WARN, lw=2, label="Steam (BSPD)")
        sor_series = [s / o if o and o > 0.1 else None
                      for s, o in zip(steam, oil)]
        ax2.plot(x, sor_series, color=ACCENT, lw=1.6, ls="--", label="SOR")
        ax.set_ylabel("Rate (bbl/d)")
        ax2.set_ylabel("SOR", color=ACCENT)
        ax.set_title(f"{ENGR_STRG} — {rsvr or 'All reservoirs'}",
                     fontsize=11, fontweight="bold")
        ax.grid(alpha=0.25)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
        self.ov_fig.autofmt_xdate()
        self.ov_fig.tight_layout()
        self.ov_canvas.draw()

    # ================================================== Tab 2: Patterns
    def _build_patterns(self):
        top = tk.Frame(self.tab_patterns, bg=BG)
        top.pack(fill="x", padx=6, pady=6)
        tk.Label(top, text="Reservoir:", bg=BG, font=FONT_B).pack(side="left")
        self.pt_rsvr = ttk.Combobox(
            top, values=["All", "TULARE", "ETCHEGOIN"], width=12,
            state="readonly")
        self.pt_rsvr.current(0)
        self.pt_rsvr.pack(side="left", padx=6)
        tk.Label(top, text="Status:", bg=BG, font=FONT_B).pack(
            side="left", padx=(12, 0))
        self.pt_status = ttk.Combobox(
            top, values=["All", "EFFICIENT", "GOOD", "REVIEW", "HIGH SOR",
                         "NO PRODUCERS", "NO STEAM", "NO OIL"],
            width=14, state="readonly")
        self.pt_status.current(0)
        self.pt_status.pack(side="left", padx=6)
        for cb in (self.pt_rsvr, self.pt_status):
            cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_patterns())
        tk.Label(top, text="Double-click a row to open Pattern Detail",
                 bg=BG, fg="#666").pack(side="right")

        card = tk.Frame(self.tab_patterns, bg=CARD,
                        highlightbackground="#dcdfe3", highlightthickness=1)
        card.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        cols = ("Injector", "Reservoir", "Producers", "Oil BOPD",
                "Gross BFPD", "Steam BSPD", "SOR", "WC %",
                "Oil Trend %", "Status")
        widths = (140, 110, 80, 90, 95, 95, 70, 70, 90, 120)
        self.pt_tree = SortableTree(card, cols, widths, height=24)
        vs = ttk.Scrollbar(card, orient="vertical",
                           command=self.pt_tree.yview)
        self.pt_tree.configure(yscrollcommand=vs.set)
        self.pt_tree.pack(side="left", fill="both", expand=True,
                          padx=(8, 0), pady=8)
        vs.pack(side="left", fill="y", pady=8)
        for status, color in STATUS_COLORS.items():
            self.pt_tree.tag_configure(status, background=color)
        self.pt_tree.bind("<Double-1>", self._open_pattern_from_row)
        add_export_bar(self.tab_patterns, self.pt_tree,
                       f"{ENGR_STRG}_patterns")
        self._refresh_patterns()

    def _refresh_patterns(self):
        rsvr = self.pt_rsvr.get()
        status_f = self.pt_status.get()
        t = self.pt_tree
        t.delete(*t.get_children(""))
        self._pattern_summaries = {}
        for fid in self.data.patterns:
            sm = self.data.pattern_summary(fid)
            self._pattern_summaries[sm["inj"].name] = fid
            if rsvr != "All" and rsvr not in sm["reservoir"]:
                continue
            if status_f != "All" and sm["status"] != status_f:
                continue
            t.insert("", "end", tags=(sm["status"],), values=(
                sm["inj"].name, sm["reservoir"], sm["n_prod"],
                fmt(sm["oil"]), fmt(sm["gross"], 0), fmt(sm["steam"], 0),
                fmt(sm["sor"]), fmt(sm["wc"]), fmt(sm["trend"], 0),
                sm["status"]))

    def _open_pattern_from_row(self, _event):
        sel = self.pt_tree.selection()
        if not sel:
            return
        name = self.pt_tree.set(sel[0], "Injector")
        self.pd_combo.set(name)
        self._refresh_pattern_detail()
        self.nb.select(self.tab_pattern_dt)

    # ================================================== Tab 3: Pattern Detail
    def _build_pattern_detail(self):
        top = tk.Frame(self.tab_pattern_dt, bg=BG)
        top.pack(fill="x", padx=6, pady=6)
        tk.Label(top, text="Pattern (injector):", bg=BG,
                 font=FONT_B).pack(side="left")
        inj_names = sorted(p.inj.name for p in self.data.patterns.values())
        self.pd_combo = ttk.Combobox(top, values=inj_names, width=22)
        self.pd_combo.pack(side="left", padx=6)
        ttk.Button(top, text="Load", style="Accent.TButton",
                   command=self._refresh_pattern_detail).pack(side="left")
        self.pd_info = tk.Label(top, bg=BG, fg="#444", font=FONT)
        self.pd_info.pack(side="left", padx=16)

        body = tk.Frame(self.tab_pattern_dt, bg=BG)
        body.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        left = tk.Frame(body, bg=CARD, highlightbackground="#dcdfe3",
                        highlightthickness=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        cols = ("Well", "Type", "Dist ft", "Share", "Oil BOPD",
                "Gross BFPD", "Steam BSPD")
        widths = (140, 60, 70, 60, 85, 90, 90)
        self.pd_tree = SortableTree(left, cols, widths, height=18)
        vs = ttk.Scrollbar(left, orient="vertical", command=self.pd_tree.yview)
        self.pd_tree.configure(yscrollcommand=vs.set)
        self.pd_tree.pack(side="left", fill="both", expand=True,
                          padx=(8, 0), pady=8)
        vs.pack(side="left", fill="y", pady=8)
        self.pd_tree.bind("<Double-1>", self._open_well_from_pattern)

        map_card = tk.Frame(right, bg=CARD, highlightbackground="#dcdfe3",
                            highlightthickness=1)
        map_card.pack(fill="both", expand=True, pady=(0, 4))
        self.pd_map_fig = Figure(figsize=(5, 3), dpi=100, facecolor=CARD)
        self.pd_map = FigureCanvasTkAgg(self.pd_map_fig, master=map_card)
        self.pd_map.get_tk_widget().pack(fill="both", expand=True,
                                         padx=4, pady=4)

        hist_card = tk.Frame(right, bg=CARD, highlightbackground="#dcdfe3",
                             highlightthickness=1)
        hist_card.pack(fill="both", expand=True, pady=(4, 0))
        self.pd_hist_fig = Figure(figsize=(5, 3), dpi=100, facecolor=CARD)
        self.pd_hist = FigureCanvasTkAgg(self.pd_hist_fig, master=hist_card)
        self.pd_hist.get_tk_widget().pack(fill="both", expand=True,
                                          padx=4, pady=4)
        add_export_bar(self.tab_pattern_dt, self.pd_tree,
                       f"{ENGR_STRG}_pattern_members")
        if inj_names:
            self.pd_combo.set(inj_names[0])
            self._refresh_pattern_detail()

    def _open_well_from_pattern(self, _event):
        sel = self.pd_tree.selection()
        if not sel:
            return
        self.wd_entry.delete(0, "end")
        self.wd_entry.insert(0, self.pd_tree.set(sel[0], "Well"))
        self._refresh_well_detail()
        self.nb.select(self.tab_well)

    def _refresh_pattern_detail(self):
        name = self.pd_combo.get().strip()
        w = self.data.by_name.get(name)
        if not w or w.fac_id not in self.data.patterns:
            messagebox.showwarning("Pattern", f"No pattern for '{name}'")
            return
        pat = self.data.patterns[w.fac_id]
        sm = self.data.pattern_summary(w.fac_id)
        self.pd_info.config(
            text=f"{sm['reservoir']}  |  {sm['n_prod']} producers  |  "
                 f"SOR {fmt(sm['sor']) or '—'}  |  Status: {sm['status']}")

        t = self.pd_tree
        t.delete(*t.get_children(""))
        d3 = lambda fid, key: self.data._avg(  # noqa: E731
            self.data.well_series(fid, key)[-3:])
        inj_steam = ((d3(w.fac_id, "steam") or 0) +
                     (d3(w.fac_id, "cycl") or 0))
        t.insert("", "end", values=(w.name, "INJ", 0, "100%", "", "",
                                    fmt(inj_steam, 0)))
        for p, dist in pat.members:
            share = f"1/{p.n_patterns}" if p.n_patterns else "—"
            t.insert("", "end", values=(
                p.name, "PROD", f"{dist:.0f}", share,
                fmt(d3(p.fac_id, "oil")), fmt(d3(p.fac_id, "gross"), 0), ""))

        # ---- map
        self.pd_map_fig.clear()
        ax = self.pd_map_fig.add_subplot(111)
        theta = [i / 60 * 2 * math.pi for i in range(61)]
        ax.plot([w.x + PATTERN_RADIUS_FT * math.cos(a) for a in theta],
                [w.y + PATTERN_RADIUS_FT * math.sin(a) for a in theta],
                color="#bbb", lw=1, ls="--")
        # context wells within 2x radius
        for o in self.data.wells.values():
            if o.x is None or o.fac_id == w.fac_id:
                continue
            if abs(o.x - w.x) > 2 * PATTERN_RADIUS_FT or \
               abs(o.y - w.y) > 2 * PATTERN_RADIUS_FT:
                continue
            member = any(p.fac_id == o.fac_id for p, _ in pat.members)
            if o.purpose == "PROD":
                ax.scatter(o.x, o.y, c=ACCENT2 if member else "#b8d8c6",
                           s=45 if member else 22, zorder=3)
                if member:
                    ax.annotate(o.name, (o.x, o.y), fontsize=6,
                                xytext=(3, 3), textcoords="offset points")
            elif o.purpose == "INJ":
                ax.scatter(o.x, o.y, c="#e6a3a3", marker="^", s=30, zorder=2)
            else:
                ax.scatter(o.x, o.y, c="#999", marker="s", s=18, zorder=2)
        ax.scatter(w.x, w.y, c=WARN, marker="^", s=140, zorder=4,
                   label=w.name)
        ax.annotate(w.name, (w.x, w.y), fontsize=7, fontweight="bold",
                    xytext=(4, 4), textcoords="offset points")
        ax.set_aspect("equal")
        ax.set_title(f"Pattern map ({PATTERN_RADIUS_FT:.0f} ft radius)",
                     fontsize=9)
        ax.tick_params(labelsize=6)
        self.pd_map_fig.tight_layout()
        self.pd_map.draw()

        # ---- history
        self.pd_hist_fig.clear()
        ax = self.pd_hist_fig.add_subplot(111)
        ax2 = ax.twinx()
        s = self.data.pattern_series(w.fac_id)
        x = self.data.months
        ax.plot(x, s["oil"], color=ACCENT2, lw=1.8, label="Alloc oil")
        ax.plot(x, s["steam"], color=WARN, lw=1.8, label="Steam")
        sor = [st / o if o and o > 0.1 else None
               for st, o in zip(s["steam"], s["oil"])]
        ax2.plot(x, sor, color=ACCENT, ls="--", lw=1.4, label="SOR")
        ax.set_ylabel("bbl/d", fontsize=8)
        ax2.set_ylabel("SOR", fontsize=8, color=ACCENT)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=7)
        ax2.tick_params(labelsize=7)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")
        self.pd_hist_fig.autofmt_xdate()
        self.pd_hist_fig.tight_layout()
        self.pd_hist.draw()

    # ================================================== Tab 4: Well Detail
    def _build_well_detail(self):
        top = tk.Frame(self.tab_well, bg=BG)
        top.pack(fill="x", padx=6, pady=6)
        tk.Label(top, text="Well name:", bg=BG, font=FONT_B).pack(side="left")
        self.wd_entry = ttk.Combobox(
            top, values=sorted(self.data.by_name), width=24)
        self.wd_entry.pack(side="left", padx=6)
        self.wd_entry.bind("<Return>", lambda e: self._refresh_well_detail())
        ttk.Button(top, text="Load", style="Accent.TButton",
                   command=self._refresh_well_detail).pack(side="left")
        self.wd_info = tk.Label(top, bg=BG, fg="#444", font=FONT)
        self.wd_info.pack(side="left", padx=16)

        card = tk.Frame(self.tab_well, bg=CARD,
                        highlightbackground="#dcdfe3", highlightthickness=1)
        card.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.wd_fig = Figure(figsize=(10, 5.5), dpi=100, facecolor=CARD)
        self.wd_canvas = FigureCanvasTkAgg(self.wd_fig, master=card)
        self.wd_canvas.get_tk_widget().pack(fill="both", expand=True,
                                            padx=6, pady=6)

    def _refresh_well_detail(self):
        name = self.wd_entry.get().strip()
        w = self.data.by_name.get(name)
        if not w:
            messagebox.showwarning("Well", f"'{name}' not found in "
                                           f"{ENGR_STRG}")
            return
        n_pats = sum(1 for pat in self.data.patterns.values()
                     if any(p.fac_id == w.fac_id for p, _ in pat.members))
        self.wd_info.config(
            text=f"{w.purpose}  |  {'/'.join(sorted(w.reservoirs)) or '—'}"
                 f"  |  in service: {w.in_svc}  |  member of "
                 f"{n_pats} pattern(s)"
                 + (f"  |  own pattern: {len(self.data.patterns[w.fac_id].members)} producers"
                    if w.fac_id in self.data.patterns else ""))

        d, x = self.data, self.data.months
        self.wd_fig.clear()
        ax1 = self.wd_fig.add_subplot(211)
        ax1b = ax1.twinx()
        oil = d.well_series(w.fac_id, "oil")
        gross = d.well_series(w.fac_id, "gross")
        steam = [(a or 0) + (b or 0) for a, b in
                 zip(d.well_series(w.fac_id, "steam"),
                     d.well_series(w.fac_id, "cycl"))]
        water = d.well_series(w.fac_id, "water")
        if w.purpose == "INJ":
            ax1.plot(x, steam, color=WARN, lw=2, label="Steam (BSPD)")
        else:
            ax1.plot(x, gross, color="#888", lw=1.4, label="Gross (BFPD)")
            ax1.plot(x, oil, color=ACCENT2, lw=2, label="Oil (BOPD)")
            wc = [wt / g * 100 if g and g > 0.1 else None
                  for wt, g in zip(water, gross)]
            ax1b.plot(x, wc, color=ACCENT, ls="--", lw=1.3, label="WC %")
            ax1b.set_ylabel("WC %", fontsize=8, color=ACCENT)
            ax1b.set_ylim(0, 105)
        ax1.set_ylabel("bbl/d", fontsize=8)
        ax1.grid(alpha=0.25)
        ax1.set_title(f"{w.name} — 36-month history", fontsize=10,
                      fontweight="bold")
        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax1b.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")

        ax2 = self.wd_fig.add_subplot(212, sharex=ax1)
        ft = d.well_series(w.fac_id, "fl_temp")
        whp = d.well_series(w.fac_id, "whp")
        ax2.plot(x, ft, color="#d35400", lw=1.6, label="Flowline temp °F")
        ax2b = ax2.twinx()
        ax2b.plot(x, whp, color="#7d3c98", lw=1.3, ls="--", label="WHP psi")
        ax2.set_ylabel("°F", fontsize=8)
        ax2b.set_ylabel("psi", fontsize=8, color="#7d3c98")
        ax2.grid(alpha=0.25)
        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = ax2b.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")
        self.wd_fig.autofmt_xdate()
        self.wd_fig.tight_layout()
        self.wd_canvas.draw()

    # ================================================== Tab 5: Opportunities
    def _build_opportunities(self):
        top = tk.Frame(self.tab_opps, bg=BG)
        top.pack(fill="x", padx=6, pady=6)
        tk.Label(top, text="Type:", bg=BG, font=FONT_B).pack(side="left")
        self.op_kind = ttk.Combobox(top, values=["All"], width=24,
                                    state="readonly")
        self.op_kind.current(0)
        self.op_kind.pack(side="left", padx=6)
        self.op_kind.bind("<<ComboboxSelected>>",
                          lambda e: self._refresh_opps())
        ttk.Button(top, text="Recalculate", style="Green.TButton",
                   command=self._recalc_opps).pack(side="left", padx=6)
        self.op_count = tk.Label(top, bg=BG, fg="#444", font=FONT_B)
        self.op_count.pack(side="right")

        card = tk.Frame(self.tab_opps, bg=CARD,
                        highlightbackground="#dcdfe3", highlightthickness=1)
        card.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        cols = ("Sev", "Type", "Level", "Entity", "Detail",
                "Recommended action")
        widths = (45, 160, 70, 130, 380, 380)
        self.op_tree = SortableTree(card, cols, widths, height=26)
        vs = ttk.Scrollbar(card, orient="vertical", command=self.op_tree.yview)
        self.op_tree.configure(yscrollcommand=vs.set)
        self.op_tree.pack(side="left", fill="both", expand=True,
                          padx=(8, 0), pady=8)
        vs.pack(side="left", fill="y", pady=8)
        for sev, color in SEV_COLORS.items():
            self.op_tree.tag_configure(f"sev{sev}", background=color)
        self.op_tree.bind("<Double-1>", self._open_entity_from_opp)
        add_export_bar(self.tab_opps, self.op_tree,
                       f"{ENGR_STRG}_opportunities")
        self._recalc_opps()

    def _recalc_opps(self):
        self._opps = self.data.opportunities()
        kinds = ["All"] + sorted({f["kind"] for f in self._opps})
        self.op_kind.configure(values=kinds)
        if self.op_kind.get() not in kinds:
            self.op_kind.current(0)
        self._refresh_opps()

    def _refresh_opps(self):
        kind = self.op_kind.get()
        t = self.op_tree
        t.delete(*t.get_children(""))
        n = 0
        for f in self._opps:
            if kind != "All" and f["kind"] != kind:
                continue
            n += 1
            t.insert("", "end", tags=(f"sev{f['sev']}",), values=(
                f["sev"], f["kind"], f["level"], f["entity"],
                f["detail"], f["action"]))
        self.op_count.config(text=f"{n} finding(s)")

    def _open_entity_from_opp(self, _event):
        sel = self.op_tree.selection()
        if not sel:
            return
        name = self.op_tree.set(sel[0], "Entity")
        level = self.op_tree.set(sel[0], "Level")
        w = self.data.by_name.get(name)
        if level == "Pattern" and w and w.fac_id in self.data.patterns:
            self.pd_combo.set(name)
            self._refresh_pattern_detail()
            self.nb.select(self.tab_pattern_dt)
        elif w:
            self.wd_entry.set(name)
            self._refresh_well_detail()
            self.nb.select(self.tab_well)


if __name__ == "__main__":
    App().mainloop()