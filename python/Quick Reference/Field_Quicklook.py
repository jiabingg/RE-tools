#Help: One-Screen Field Snapshot
"""
Field Quicklook — One-Screen Field Snapshot
=============================================
Select a field from a dropdown and get:
  Tab 1: Summary KPIs (total oil, steam, water, SOR, active counts)
  Tab 2: Top 15 & Bottom 15 producers by oil rate
  Tab 3: Wells currently down — grouped by reason (table + pie chart)
  Tab 4: Idle wells (>6 months with no production)
  Tab 5: Engineering Strategy Breakdown (oil/steam/WC by pattern)
  Tab 6: Monthly Field Trend (12-month stacked chart)

Connects to CRC Oracle Data Warehouse (ODW) via oracledb.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
from datetime import datetime

# ---------------------------------------------------------------------------
# Oracle connection
# ---------------------------------------------------------------------------
try:
    import oracledb
    try:
        oracledb.init_oracle_client()
    except Exception:
        pass
except ImportError:
    messagebox.showerror("Missing Package", "oracledb is required.\npip install oracledb")
    raise SystemExit

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

DB_USER = "rptguser"
DB_PASS = "allusers"
DB_DSN = "ODW"

FIELDS = [
    "San Ardo",
    "Coalinga",
    "Belridge",
    "Elk Hills",
    "Midway Sunset",
    "Lost Hills",
    "Ventura",
]


def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)


# ============================================================================
# SQL QUERIES
# ============================================================================

# Latest-month field summary — producers
SQL_FIELD_SUMMARY_PROD = """
SELECT
    COUNT(DISTINCT cd.cmpl_fac_id) AS active_producers,
    ROUND(SUM(cmf.aloc_oil_prod_dly_rte_qty), 0) AS total_oil_bopd,
    ROUND(SUM(cmf.aloc_gros_prod_dly_rte_qty), 0) AS total_gross_bfpd,
    ROUND(SUM(cmf.aloc_wtr_prod_dly_rte_qty), 0) AS total_water_bwpd,
    ROUND(
        SUM(cmf.aloc_wtr_prod_dly_rte_qty) /
        NULLIF(SUM(cmf.aloc_gros_prod_dly_rte_qty), 0) * 100, 1
    ) AS field_wc_pct,
    MAX(cmf.eftv_dttm) AS data_month
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.opnl_fld = :field_name
  AND cd.actv_indc = 'Y'
  AND cd.prim_purp_type_cde = 'PROD'
  AND cmf.eftv_dttm = (
      SELECT MAX(cmf2.eftv_dttm)
      FROM dwrptg.cmpl_mnly_fact cmf2
      JOIN dwrptg.cmpl_dmn cd2 ON cd2.cmpl_dmn_key = cmf2.cmpl_dmn_key
      WHERE cd2.opnl_fld = :field_name
        AND cd2.actv_indc = 'Y'
        AND cd2.prim_purp_type_cde = 'PROD'
        AND cmf2.aloc_oil_prod_dly_rte_qty > 0
  )
"""

# Latest-month field summary — injectors
SQL_FIELD_SUMMARY_INJ = """
SELECT
    COUNT(DISTINCT cd.cmpl_fac_id) AS active_injectors,
    ROUND(SUM(cmf.aloc_cnts_stm_inj_dly_rte_qty), 0) AS total_steam_bspd,
    ROUND(SUM(cmf.aloc_wtr_inj_dly_rte_qty), 0) AS total_water_inj_bwpd
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.opnl_fld = :field_name
  AND cd.actv_indc = 'Y'
  AND cd.prim_purp_type_cde = 'INJ'
  AND cmf.eftv_dttm = (
      SELECT MAX(cmf2.eftv_dttm)
      FROM dwrptg.cmpl_mnly_fact cmf2
      JOIN dwrptg.cmpl_dmn cd2 ON cd2.cmpl_dmn_key = cmf2.cmpl_dmn_key
      WHERE cd2.opnl_fld = :field_name
        AND cd2.actv_indc = 'Y'
        AND cd2.prim_purp_type_cde = 'INJ'
  )
"""

# Top 15 + Bottom 15 producers (latest month with production)
SQL_TOP_BOTTOM = """
WITH ranked AS (
    SELECT
        cd.cmpl_nme,
        cd.engr_strg_nme,
        ROUND(cmf.aloc_oil_prod_dly_rte_qty, 1) AS oil_bopd,
        ROUND(cmf.aloc_gros_prod_dly_rte_qty, 1) AS gross_bfpd,
        ROUND(CASE WHEN NVL(cmf.aloc_gros_prod_dly_rte_qty, 0) > 0
              THEN cmf.aloc_wtr_prod_dly_rte_qty / cmf.aloc_gros_prod_dly_rte_qty * 100
              ELSE NULL END, 1) AS wc_pct,
        ROW_NUMBER() OVER (ORDER BY cmf.aloc_oil_prod_dly_rte_qty DESC) AS rank_top,
        ROW_NUMBER() OVER (ORDER BY cmf.aloc_oil_prod_dly_rte_qty ASC) AS rank_bot
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
    WHERE cd.opnl_fld = :field_name
      AND cd.actv_indc = 'Y'
      AND cd.prim_purp_type_cde = 'PROD'
      AND cmf.aloc_oil_prod_dly_rte_qty > 0
      AND cmf.eftv_dttm = (
          SELECT MAX(cmf2.eftv_dttm)
          FROM dwrptg.cmpl_mnly_fact cmf2
          JOIN dwrptg.cmpl_dmn cd2 ON cd2.cmpl_dmn_key = cmf2.cmpl_dmn_key
          WHERE cd2.opnl_fld = :field_name
            AND cd2.actv_indc = 'Y'
            AND cd2.prim_purp_type_cde = 'PROD'
            AND cmf2.aloc_oil_prod_dly_rte_qty > 0
      )
)
SELECT cmpl_nme, engr_strg_nme, oil_bopd, gross_bfpd, wc_pct, rank_top, rank_bot
FROM ranked
WHERE rank_top <= 15 OR rank_bot <= 15
ORDER BY oil_bopd DESC
"""

# Wells currently down by reason
SQL_WELLS_DOWN = """
SELECT
    cd.cmpl_nme,
    cd.engr_strg_nme,
    cd.prim_purp_type_cde AS purpose,
    cof.off_rsn_type_cde,
    cof.off_rsn_type_desc,
    cof.off_rsn_eftv_dttm,
    ROUND(SYSDATE - cof.off_rsn_eftv_dttm, 0) AS days_down
FROM dwrptg.cmpl_off_rsn_fact cof
JOIN dwrptg.cmpl_dmn cd ON cof.cmpl_fac_id = cd.cmpl_fac_id
WHERE cd.opnl_fld = :field_name
  AND cd.actv_indc = 'Y'
  AND cof.off_rsn_term_dttm IS NULL
ORDER BY days_down DESC
"""

# Idle wells — active completions with zero production in last 6 months
SQL_IDLE_WELLS = """
SELECT
    cd.cmpl_nme,
    cd.engr_strg_nme,
    cd.prim_purp_type_cde AS purpose,
    cd.in_svc_indc,
    MAX(cmf.eftv_dttm) AS last_prod_month,
    ROUND(MONTHS_BETWEEN(SYSDATE, MAX(cmf.eftv_dttm)), 0) AS months_idle
FROM dwrptg.cmpl_dmn cd
LEFT JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
    AND cmf.aloc_oil_prod_dly_rte_qty > 0
WHERE cd.opnl_fld = :field_name
  AND cd.actv_indc = 'Y'
  AND cd.prim_purp_type_cde = 'PROD'
GROUP BY cd.cmpl_nme, cd.engr_strg_nme, cd.prim_purp_type_cde, cd.in_svc_indc
HAVING MAX(cmf.eftv_dttm) < ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -6)
    OR MAX(cmf.eftv_dttm) IS NULL
ORDER BY months_idle DESC NULLS FIRST
"""

# Engineering strategy breakdown (latest month)
SQL_ENGR_STRATEGY = """
SELECT
    cd.engr_strg_nme,
    COUNT(DISTINCT CASE WHEN cd.prim_purp_type_cde = 'PROD' THEN cd.cmpl_fac_id END) AS producers,
    COUNT(DISTINCT CASE WHEN cd.prim_purp_type_cde = 'INJ' THEN cd.cmpl_fac_id END) AS injectors,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
              THEN cmf.aloc_oil_prod_dly_rte_qty ELSE 0 END), 0) AS oil_bopd,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
              THEN cmf.aloc_gros_prod_dly_rte_qty ELSE 0 END), 0) AS gross_bfpd,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'INJ'
              THEN cmf.aloc_cnts_stm_inj_dly_rte_qty ELSE 0 END), 0) AS steam_bspd,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'INJ'
              THEN cmf.aloc_wtr_inj_dly_rte_qty ELSE 0 END), 0) AS water_inj_bwpd,
    ROUND(
        SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
            THEN cmf.aloc_wtr_prod_dly_rte_qty ELSE 0 END) /
        NULLIF(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
            THEN cmf.aloc_gros_prod_dly_rte_qty ELSE 0 END), 0) * 100, 1
    ) AS wc_pct,
    ROUND(
        SUM(CASE WHEN cd.prim_purp_type_cde = 'INJ'
            THEN cmf.aloc_cnts_stm_inj_dly_rte_qty ELSE 0 END) /
        NULLIF(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
            THEN cmf.aloc_oil_prod_dly_rte_qty ELSE 0 END), 0), 1
    ) AS instantaneous_sor
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.opnl_fld = :field_name
  AND cd.actv_indc = 'Y'
  AND cd.prim_purp_type_cde IN ('PROD', 'INJ')
  AND cmf.eftv_dttm = (
      SELECT MAX(cmf2.eftv_dttm)
      FROM dwrptg.cmpl_mnly_fact cmf2
      JOIN dwrptg.cmpl_dmn cd2 ON cd2.cmpl_dmn_key = cmf2.cmpl_dmn_key
      WHERE cd2.opnl_fld = :field_name AND cd2.actv_indc = 'Y'
  )
GROUP BY cd.engr_strg_nme
ORDER BY oil_bopd DESC
"""

# Monthly field trend (12 months)
SQL_MONTHLY_TREND = """
SELECT
    cmf.eftv_dttm AS month,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
              THEN cmf.aloc_oil_prod_dly_rte_qty ELSE 0 END), 0) AS oil_bopd,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
              THEN cmf.aloc_gros_prod_dly_rte_qty ELSE 0 END), 0) AS gross_bfpd,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'INJ'
              THEN cmf.aloc_cnts_stm_inj_dly_rte_qty ELSE 0 END), 0) AS steam_bspd,
    ROUND(SUM(CASE WHEN cd.prim_purp_type_cde = 'INJ'
              THEN cmf.aloc_wtr_inj_dly_rte_qty ELSE 0 END), 0) AS water_inj_bwpd,
    ROUND(
        SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
            THEN cmf.aloc_wtr_prod_dly_rte_qty ELSE 0 END) /
        NULLIF(SUM(CASE WHEN cd.prim_purp_type_cde = 'PROD'
            THEN cmf.aloc_gros_prod_dly_rte_qty ELSE 0 END), 0) * 100, 1
    ) AS wc_pct,
    COUNT(DISTINCT CASE WHEN cd.prim_purp_type_cde = 'PROD'
          AND cmf.aloc_oil_prod_dly_rte_qty > 0 THEN cd.cmpl_fac_id END) AS active_producers,
    COUNT(DISTINCT CASE WHEN cd.prim_purp_type_cde = 'INJ'
          AND cmf.aloc_cnts_stm_inj_dly_rte_qty > 0 THEN cd.cmpl_fac_id END) AS active_injectors
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.cmpl_mnly_fact cmf ON cd.cmpl_dmn_key = cmf.cmpl_dmn_key
WHERE cd.opnl_fld = :field_name
  AND cd.actv_indc = 'Y'
  AND cd.prim_purp_type_cde IN ('PROD', 'INJ')
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
GROUP BY cmf.eftv_dttm
ORDER BY cmf.eftv_dttm
"""


# ============================================================================
# APPLICATION
# ============================================================================

class FieldQuicklookApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Field Quicklook — One-Screen Field Snapshot")
        self.root.geometry("1300x850")
        self.root.minsize(1050, 650)

        self._build_ui()

    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self.root, padding=5)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Field:").pack(side=tk.LEFT, padx=(0, 5))
        self.field_var = tk.StringVar()
        self.field_combo = ttk.Combobox(top, textvariable=self.field_var,
                                         values=FIELDS, state="readonly", width=18,
                                         font=("Consolas", 11))
        self.field_combo.pack(side=tk.LEFT, padx=(0, 5))
        self.field_combo.current(0)

        self.load_btn = ttk.Button(top, text="Load", command=self._on_load)
        self.load_btn.pack(side=tk.LEFT, padx=(0, 15))

        self.status_var = tk.StringVar(value="Select a field and click Load")
        ttk.Label(top, textvariable=self.status_var, foreground="gray").pack(side=tk.LEFT)

        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tab_kpi = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_kpi, text="  KPI Summary  ")

        self.tab_topbot = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_topbot, text="  Top / Bottom Producers  ")

        self.tab_down = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_down, text="  Wells Down  ")

        self.tab_idle = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_idle, text="  Idle Wells (>6 mo)  ")

        self.tab_engr = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_engr, text="  Engr Strategy Breakdown  ")

        self.tab_trend = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_trend, text="  Monthly Trend (12 mo)  ")

    # ---- LOAD DISPATCH -----------------------------------------------------

    def _on_load(self):
        field = self.field_var.get().strip()
        if not field:
            messagebox.showwarning("Input", "Select a field.")
            return
        self.load_btn.config(state=tk.DISABLED)
        self.status_var.set(f"Loading {field}...")
        threading.Thread(target=self._load_all, args=(field,), daemon=True).start()

    def _load_all(self, field):
        try:
            conn = get_connection()
            cur = conn.cursor()
            params = {"field_name": field}

            cur.execute(SQL_FIELD_SUMMARY_PROD, params)
            row_prod = cur.fetchone()
            cols_prod = [d[0] for d in cur.description]

            cur.execute(SQL_FIELD_SUMMARY_INJ, params)
            row_inj = cur.fetchone()
            cols_inj = [d[0] for d in cur.description]

            cur.execute(SQL_TOP_BOTTOM, params)
            cols_tb = [d[0] for d in cur.description]
            rows_tb = cur.fetchall()

            cur.execute(SQL_WELLS_DOWN, params)
            cols_dn = [d[0] for d in cur.description]
            rows_dn = cur.fetchall()

            cur.execute(SQL_IDLE_WELLS, params)
            cols_idle = [d[0] for d in cur.description]
            rows_idle = cur.fetchall()

            cur.execute(SQL_ENGR_STRATEGY, params)
            cols_engr = [d[0] for d in cur.description]
            rows_engr = cur.fetchall()

            cur.execute(SQL_MONTHLY_TREND, params)
            cols_trend = [d[0] for d in cur.description]
            rows_trend = cur.fetchall()

            conn.close()

            self.root.after(0, lambda: self._populate_all(
                field,
                dict(zip(cols_prod, row_prod)) if row_prod else {},
                dict(zip(cols_inj, row_inj)) if row_inj else {},
                cols_tb, rows_tb,
                cols_dn, rows_dn,
                cols_idle, rows_idle,
                cols_engr, rows_engr,
                cols_trend, rows_trend,
            ))

        except Exception as e:
            self.root.after(0, lambda: self._show_error(str(e)))

    def _show_error(self, msg):
        self.status_var.set("Error — see popup")
        self.load_btn.config(state=tk.NORMAL)
        messagebox.showerror("Database Error", msg)

    def _populate_all(self, field, prod_kpi, inj_kpi,
                      cols_tb, rows_tb,
                      cols_dn, rows_dn,
                      cols_idle, rows_idle,
                      cols_engr, rows_engr,
                      cols_trend, rows_trend):

        data_month = prod_kpi.get("DATA_MONTH", "")
        if isinstance(data_month, datetime):
            data_month = data_month.strftime("%b %Y")
        self.status_var.set(f"{field}  —  Data month: {data_month}")
        self.load_btn.config(state=tk.NORMAL)

        self._populate_kpi(field, prod_kpi, inj_kpi)
        self._populate_topbot(cols_tb, rows_tb)
        self._populate_down(cols_dn, rows_dn)
        self._populate_idle(cols_idle, rows_idle)
        self._populate_engr(cols_engr, rows_engr)
        self._populate_trend(field, cols_trend, rows_trend)

    # ---- TAB 1: KPI Summary -----------------------------------------------

    def _populate_kpi(self, field, prod, inj):
        for w in self.tab_kpi.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_kpi, padding=20)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"{field} — Field Quicklook",
                  font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=4,
                                                       sticky=tk.W, pady=(0, 15))

        total_oil = prod.get("TOTAL_OIL_BOPD", 0) or 0
        total_steam = inj.get("TOTAL_STEAM_BSPD", 0) or 0
        sor = round(total_steam / total_oil, 1) if total_oil > 0 else 0

        kpis = [
            ("Active Producers", self._fmt_int(prod.get("ACTIVE_PRODUCERS")), "#2ECC71"),
            ("Active Injectors", self._fmt_int(inj.get("ACTIVE_INJECTORS")), "#3498DB"),
            ("", "", ""),  # spacer
            ("Total Oil (BOPD)", self._fmt_int(total_oil), "#27AE60"),
            ("Total Gross (BFPD)", self._fmt_int(prod.get("TOTAL_GROSS_BFPD")), "#2980B9"),
            ("Total Water Prod (BWPD)", self._fmt_int(prod.get("TOTAL_WATER_BWPD")), "#E67E22"),
            ("Field Water Cut (%)", f"{prod.get('FIELD_WC_PCT', 0) or 0:.1f}", "#E74C3C"),
            ("", "", ""),
            ("Total Steam Inj (BSPD)", self._fmt_int(total_steam), "#C0392B"),
            ("Total Water Inj (BWPD)", self._fmt_int(inj.get("TOTAL_WATER_INJ_BWPD")), "#2980B9"),
            ("Instantaneous SOR", f"{sor:.1f}", "#8E44AD"),
        ]

        row_idx = 1
        for label, value, color in kpis:
            if label == "":
                ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
                    row=row_idx, column=0, columnspan=4, sticky="ew", pady=8)
                row_idx += 1
                continue

            ttk.Label(frm, text=label, font=("Segoe UI", 12)).grid(
                row=row_idx, column=0, sticky=tk.W, padx=(0, 20), pady=4)
            ttk.Label(frm, text=value, font=("Consolas", 14, "bold"),
                      foreground=color).grid(
                row=row_idx, column=1, sticky=tk.W, pady=4)
            row_idx += 1

        # SOR benchmark note
        if total_steam > 0:
            if sor < 4:
                note = "SOR < 4 — Excellent thermal efficiency"
            elif sor < 6:
                note = "SOR 4–6 — Good, typical mature steamflood"
            elif sor < 10:
                note = "SOR 6–10 — Marginal, review conformance"
            else:
                note = "SOR > 10 — Very high, investigate channeling"
            ttk.Label(frm, text=note, font=("Segoe UI", 10, "italic"),
                      foreground="#7F8C8D").grid(
                row=row_idx, column=0, columnspan=4, sticky=tk.W, pady=(10, 0))

    # ---- TAB 2: Top / Bottom Producers ------------------------------------

    def _populate_topbot(self, cols, rows):
        for w in self.tab_topbot.winfo_children():
            w.destroy()

        if not rows:
            ttk.Label(self.tab_topbot, text="No production data found.",
                      font=("Segoe UI", 11)).pack(padx=20, pady=20)
            return

        top15 = [r for r in rows if r[5] <= 15]
        bot15 = [r for r in rows if r[6] <= 15 and r[5] > 15]  # avoid overlap

        display_cols = ["CMPL_NME", "ENGR_STRG_NME", "OIL_BOPD", "GROSS_BFPD", "WC_PCT"]

        # Top producers
        ttk.Label(self.tab_topbot, text="Top 15 Producers by Oil Rate",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 2))
        t1 = self._make_treeview(self.tab_topbot, display_cols,
                                  [r[:5] for r in top15], height=10)
        t1.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        # Bottom producers
        ttk.Label(self.tab_topbot, text="Bottom 15 Producers by Oil Rate",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(5, 2))
        t2 = self._make_treeview(self.tab_topbot, display_cols,
                                  [r[:5] for r in bot15], height=10)
        t2.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    # ---- TAB 3: Wells Down -------------------------------------------------

    def _populate_down(self, cols, rows):
        for w in self.tab_down.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_down, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Wells Currently Down  ({len(rows)} total)",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))

        # Summary by reason (for pie chart)
        reason_counts = {}
        for r in rows:
            reason = r[3] or "UNKNOWN"  # off_rsn_type_cde
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        # Split: chart left, table right
        split = ttk.PanedWindow(frm, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)

        # Pie chart
        if HAS_MPL and reason_counts:
            chart_frm = ttk.Frame(split)
            split.add(chart_frm, weight=1)

            fig = Figure(figsize=(5, 4), dpi=100)
            ax = fig.add_subplot(111)
            labels = list(reason_counts.keys())
            sizes = list(reason_counts.values())
            colors_pie = [
                "#E74C3C", "#3498DB", "#2ECC71", "#F39C12", "#9B59B6",
                "#1ABC9C", "#E67E22", "#34495E", "#95A5A6", "#D35400",
                "#C0392B", "#2980B9", "#27AE60", "#8E44AD", "#16A085",
            ]
            ax.pie(sizes, labels=labels, autopct='%1.0f%%', startangle=90,
                   colors=colors_pie[:len(labels)], textprops={"fontsize": 8})
            ax.set_title("Down by Reason Category", fontsize=10)
            fig.tight_layout()

            canvas = FigureCanvasTkAgg(fig, chart_frm)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        elif reason_counts:
            summary_frm = ttk.Frame(split)
            split.add(summary_frm, weight=1)
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
                ttk.Label(summary_frm, text=f"  {reason}: {count}",
                          font=("Consolas", 10)).pack(anchor=tk.W)

        # Table
        table_frm = ttk.Frame(split)
        split.add(table_frm, weight=2)
        tree = self._make_treeview(table_frm, cols, rows, height=22)
        tree.pack(fill=tk.BOTH, expand=True)

    # ---- TAB 4: Idle Wells -------------------------------------------------

    def _populate_idle(self, cols, rows):
        for w in self.tab_idle.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_idle, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Idle Producers — No Oil in >6 Months  ({len(rows)} wells)",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))

        tree = self._make_treeview(frm, cols, rows, height=25)
        tree.pack(fill=tk.BOTH, expand=True)

    # ---- TAB 5: Engineering Strategy Breakdown -----------------------------

    def _populate_engr(self, cols, rows):
        for w in self.tab_engr.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_engr, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Engineering Strategy Breakdown  ({len(rows)} strategies)",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))

        tree = self._make_treeview(frm, cols, rows, height=25)
        tree.pack(fill=tk.BOTH, expand=True)

    # ---- TAB 6: Monthly Trend Chart ----------------------------------------

    def _populate_trend(self, field, cols, rows):
        for w in self.tab_trend.winfo_children():
            w.destroy()

        if not HAS_MPL:
            ttk.Label(self.tab_trend, text="matplotlib not installed — chart unavailable",
                      font=("Segoe UI", 11)).pack(padx=20, pady=20)
            return

        if not rows:
            ttk.Label(self.tab_trend, text="No monthly data found",
                      font=("Segoe UI", 11)).pack(padx=20, pady=20)
            return

        dates = [r[0] for r in rows]
        oil = [float(r[1] or 0) for r in rows]
        gross = [float(r[2] or 0) for r in rows]
        steam = [float(r[3] or 0) for r in rows]
        water_inj = [float(r[4] or 0) for r in rows]
        wc = [float(r[5]) if r[5] is not None else None for r in rows]
        n_prod = [int(r[6] or 0) for r in rows]
        n_inj = [int(r[7] or 0) for r in rows]

        fig = Figure(figsize=(13, 8), dpi=100)

        # Panel 1: Oil & Gross
        ax1 = fig.add_subplot(221)
        ax1.fill_between(dates, gross, alpha=0.25, color="#3498DB", label="Gross")
        ax1.plot(dates, oil, color="#2ECC71", linewidth=2, label="Oil")
        ax1.set_ylabel("BOPD / BFPD")
        ax1.set_title(f"{field} — Production (12 mo)")
        ax1.legend(fontsize=8)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

        # Panel 2: Injection
        ax2 = fig.add_subplot(222)
        ax2.bar(dates, steam, width=25, color="#E74C3C", alpha=0.8, label="Steam")
        ax2.bar(dates, water_inj, width=25, bottom=steam, color="#3498DB", alpha=0.7, label="Water Inj")
        ax2.set_ylabel("Inj Rate (bbl/day)")
        ax2.set_title("Injection")
        ax2.legend(fontsize=8)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

        # Panel 3: Water Cut
        ax3 = fig.add_subplot(223)
        wc_d = [d for d, w in zip(dates, wc) if w is not None]
        wc_v = [w for w in wc if w is not None]
        if wc_v:
            ax3.plot(wc_d, wc_v, color="#E67E22", linewidth=2, marker="o", markersize=4)
        ax3.set_ylabel("Water Cut (%)")
        ax3.set_ylim(0, 105)
        ax3.set_title("Water Cut")
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

        # Panel 4: Active well counts
        ax4 = fig.add_subplot(224)
        ax4.plot(dates, n_prod, color="#2ECC71", linewidth=2, marker="s", markersize=4, label="Producers")
        ax4.plot(dates, n_inj, color="#E74C3C", linewidth=2, marker="^", markersize=4, label="Injectors")
        ax4.set_ylabel("Well Count")
        ax4.set_title("Active Well Counts")
        ax4.legend(fontsize=8)
        ax4.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        ax4.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

        fig.autofmt_xdate()
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.tab_trend)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar = NavigationToolbar2Tk(canvas, self.tab_trend)
        toolbar.update()

    # ---- HELPERS -----------------------------------------------------------

    def _make_treeview(self, parent, columns, rows, height=15):
        container = ttk.Frame(parent)

        display_cols = [c.replace("_", " ").title() for c in columns]
        tree = ttk.Treeview(container, columns=columns, show="headings", height=height)

        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=tree.yview)
        hsb = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col, disp in zip(columns, display_cols):
            tree.heading(col, text=disp)
            tree.column(col, width=120, minwidth=60)

        for row in rows:
            values = []
            for v in row:
                if v is None:
                    values.append("")
                elif isinstance(v, datetime):
                    values.append(v.strftime("%Y-%m-%d"))
                else:
                    values.append(str(v))
            tree.insert("", tk.END, values=values)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        return container

    @staticmethod
    def _fmt_int(val):
        if val is None:
            return "—"
        try:
            return f"{int(val):,}"
        except (ValueError, TypeError):
            return str(val)


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = FieldQuicklookApp(root)
    root.mainloop()