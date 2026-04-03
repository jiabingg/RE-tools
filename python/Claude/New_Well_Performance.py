"""
EKPSPP Well Data Viewer v4
==========================
Two-step query: API10 → resolve API14 → pull data with exact PID match.

SETUP:
  1. pip install oracledb matplotlib
  2. EKPSPP.WORLD must be in tnsnames.ora
  3. Run:  python ekpspp_well_viewer.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import csv
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  EKPSPP Connection                                                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
DB_TNS_ALIAS = "EKPSPP.WORLD"
DB_USER      = "oxy_read"
DB_PASSWORD  = "oxy_read"
# ═════════════════════════════════════════════════════════════════════════════


def get_connection():
    try:
        import oracledb
        try:
            oracledb.init_oracle_client()
        except Exception:
            pass
        return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_TNS_ALIAS)
    except ImportError:
        import cx_Oracle
        return cx_Oracle.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_TNS_ALIAS)


# ---------------------------------------------------------------------------
# STEP 1: Resolve API10 → API14 list
# ---------------------------------------------------------------------------
SQL_RESOLVE_API14 = """
SELECT API_NO14, WELLCOMP_NAME, CURR_COMP_TYPE
FROM ODS.BI_WELLCOMP_V
WHERE {api10_filter}
ORDER BY WELLCOMP_NAME
"""

# ---------------------------------------------------------------------------
# STEP 2: Use exact PID = 'api14' for all queries (fast, index-friendly)
# ---------------------------------------------------------------------------
SQL_WELL_INFO = """
SELECT
    C.WELLCOMP_NAME, C.API_NO14,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    C.ORGLEV4_NAME AS AREA, C.ORGLEV5_NAME AS SUB_AREA,
    C.REG_NAME AS REGION, C.FIELD_NAME,
    C.CURR_COMP_TYPE, C.CURR_COMP_STATUS, C.STATUS_EFF_DATE,
    C.RESERVOIR_CD, C.CURR_METHOD_PROD AS LIFT_METHOD,
    C.WELLBORE_NAME, C.WELL_SPUD_DATE, C.COMPLETION_DATE,
    C.FIRST_PROD_DATE, C.BOTTOM_HOLE_TMD,
    W.SECTION, W.SURF_LATITUDE, W.SURF_LONGITUDE
FROM ODS.BI_WELLCOMP_V C
LEFT JOIN ODS.BI_WELL W ON SUBSTR(C.API_NO14, 1, 10) = W.API_NO10
WHERE C.API_NO14 IN ({api14_list})
ORDER BY C.WELLCOMP_NAME
"""

SQL_PRODUCTION = """
SELECT
    C.WELLCOMP_NAME,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    MV.PROD_INJ_DATE,
    MV.CDOIL_PROD AS BOPD, MV.CDWAT_PROD AS BWPD,
    MV.CDGAS_PROD AS MCFD, MV.CDN2_PROD AS N2_MCFD,
    MV.WCUT_PROD AS WATER_CUT_PCT, MV.GOR_PROD,
    MV.OIL_PROD AS MONTHLY_OIL_BBL, MV.WATER_PROD AS MONTHLY_WAT_BBL,
    MV.GAS_PROD AS MONTHLY_GAS_MCF, MV.DAYS_PROD, MV.ACTIVE_PROD
FROM DSS.MONTHLY_VOLUMES MV
JOIN ODS.BI_WELLCOMP_V C ON MV.PID = C.API_NO14
WHERE MV.PTYPE = 'COMP'
  AND (MV.OIL_PROD > 0 OR MV.WATER_PROD > 0 OR MV.GAS_PROD > 0)
  AND MV.PID IN ({api14_list})
ORDER BY C.WELLCOMP_NAME, MV.PROD_INJ_DATE DESC
"""

SQL_INJECTION = """
SELECT
    C.WELLCOMP_NAME,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    MV.PROD_INJ_DATE,
    MV.CDWAT_INJ AS WATER_INJ_BWIPD,
    MV.CDDISPWAT_INJ AS DISP_WAT_INJ_BWIPD,
    MV.CDGAS_INJ AS GAS_INJ_MCFIPD,
    MV.CDN2_INJ AS N2_INJ_MCFIPD,
    MV.CDSTEAM_INJ AS STEAM_INJ_BWED,
    MV.WATER_INJ AS MONTHLY_WAT_INJ_BBL,
    MV.DISP_WATER_INJ AS MONTHLY_DISP_WAT_BBL,
    MV.GAS_INJ AS MONTHLY_GAS_INJ_MCF,
    MV.STEAM_INJ AS MONTHLY_STEAM_BBL,
    MV.NITROGEN_INJ AS MONTHLY_N2_INJ_MCF,
    MV.DAYS_INJECT, MV.ACTIVE_WINJ, MV.ACTIVE_GINJ, MV.ACTIVE_WD
FROM DSS.MONTHLY_VOLUMES MV
JOIN ODS.BI_WELLCOMP_V C ON MV.PID = C.API_NO14
WHERE MV.PTYPE = 'COMP'
  AND MV.PID IN ({api14_list})
  AND (MV.WATER_INJ > 0 OR MV.DISP_WATER_INJ > 0
       OR MV.GAS_INJ > 0 OR MV.STEAM_INJ > 0
       OR MV.NITROGEN_INJ > 0 OR MV.CYCLIC_STEAM_INJ > 0)
ORDER BY C.WELLCOMP_NAME, MV.PROD_INJ_DATE DESC
"""

SQL_REGULATORY = """
SELECT
    C.WELLCOMP_NAME,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    C.CURR_COMP_TYPE, C.CURR_COMP_STATUS,
    U.TEST_TYPE, U.TEST_DATE, U.TEST_PSI, U.MASP,
    U.PASS_OR_FAIL, U.NEXT_SURVEY, U.NEXT_SAPT,
    U.VARIANCE, U.COMMENTS,
    MW.UIC_PROJECT_NUMBER, MW.UIC_FLAG
FROM ODS.BI_WELLCOMP_V C
LEFT JOIN REGULATORY.UIC_WELLS_TESTS_V U
    ON TO_NUMBER(SUBSTR(C.API_NO14, 1, 10)) = U.APINUMBER
LEFT JOIN (
    SELECT API_NO, MAX(UIC_PROJECT_NUMBER) UIC_PROJECT_NUMBER, MAX(UIC_FLAG) UIC_FLAG
    FROM GOVERNANCE.A_MASTER_WELL_LIST_T GROUP BY API_NO
) MW ON TO_NUMBER(SUBSTR(C.API_NO14, 1, 10)) = MW.API_NO
WHERE C.API_NO14 IN ({api14_list})
ORDER BY C.WELLCOMP_NAME, U.TEST_DATE DESC
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_apis(raw_text):
    """Parse user input into 10-digit API numbers."""
    apis = []
    for token in raw_text.replace(",", " ").replace(";", " ").replace("\n", " ").split():
        token = token.strip()
        # Strip leading zeros then re-pad (handles both 0402927612 and 402927612)
        digits = token.lstrip("0")
        if not digits or not digits.isdigit():
            continue
        api10 = digits.zfill(10)
        # If user entered 14-digit, take first 10
        if len(token) > 10 and token.isdigit():
            api10 = token[:10]
        apis.append(api10)
    return list(dict.fromkeys(apis))


def build_api10_filter(api_list):
    """Build OR filter for API10 → API14 resolution."""
    parts = [f"SUBSTR(API_NO14, 1, 10) = '{api}'" for api in api_list]
    return " OR ".join(parts)


def build_api14_in(api14_list):
    """Build IN clause with quoted API14 values."""
    return ", ".join(f"'{a}'" for a in api14_list)


def run_query_with_conn(conn, sql):
    """Run query on an existing connection, return (cols, rows)."""
    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    cursor.close()
    return cols, rows


def run_query_new_conn(sql_key, sql):
    """Open new connection, run query, return (key, cols, rows)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return sql_key, cols, rows


def fmt_val(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    return str(val)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class EKPSPPViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EKPSPP Well Data Viewer v4")

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.82), int(sh * 0.82)
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(1100, 650)

        self.api_list = []       # 10-digit APIs from user
        self.api14_list = []     # resolved 14-digit APIs
        self.api14_map = {}      # api14 → (name, type)
        self.data = {}
        self.headers = {}
        self._build_ui()

    def _build_ui(self):
        # Top: API input
        top = ttk.LabelFrame(self,
            text="  Enter API Numbers (10-digit, comma / space / newline separated)  ",
            padding=8)
        top.pack(fill="x", padx=10, pady=(8, 4))

        self.api_text = scrolledtext.ScrolledText(top, height=3, font=("Consolas", 10))
        self.api_text.pack(fill="x", side="left", expand=True, padx=(0, 10))

        btn_frame = ttk.Frame(top)
        btn_frame.pack(side="right")
        self.fetch_btn = ttk.Button(btn_frame, text="⟳  Fetch Data", command=self._on_fetch)
        self.fetch_btn.pack(pady=(0, 4), fill="x")
        ttk.Button(btn_frame, text="💾  Export Tab → CSV", command=self._on_export).pack(fill="x")

        # Progress + status + timer
        pf = ttk.Frame(self)
        pf.pack(fill="x", padx=10, pady=(0, 4))
        self.progress = ttk.Progressbar(pf, mode="determinate", maximum=5)  # 1 resolve + 4 queries
        self.progress.pack(fill="x", side="left", expand=True, padx=(0, 8))
        self.elapsed_var = tk.StringVar(value="")
        ttk.Label(pf, textvariable=self.elapsed_var, width=12, anchor="e").pack(side="right", padx=(0, 6))
        self.status_var = tk.StringVar(value=f"Ready — {DB_USER}@{DB_TNS_ALIAS}")
        ttk.Label(pf, textvariable=self.status_var, width=80, anchor="w").pack(side="right")

        # Notebook
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self._build_instructions_tab()
        self._build_wellinfo_tab()
        self._build_chart_tab("production")
        self._build_chart_tab("injection")
        self._build_regulatory_tab()

    # --- Tab builders ---
    def _build_instructions_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Instructions  ")
        txt = tk.Text(frame, font=("Consolas", 11), wrap="word", bg="#f9f9f9")
        txt.insert("1.0",
            "EKPSPP Well Data Viewer v4\n"
            "══════════════════════════\n\n"
            "1. Enter 10-digit API numbers above (comma / space / newline).\n"
            "   Example: 0402927612, 0402953680, 0402966780\n\n"
            "2. Click 'Fetch Data'.\n"
            "   Step 1: Resolves API10 → API14 (~40s)\n"
            "   Step 2: Fetches well info, production, injection, regulatory in parallel\n\n"
            "3. Tabs:\n"
            "   • Well Info    — completion attributes, status, reservoir, lift method\n"
            "   • Production  — BOPD/BWPD/MCFD table + chart (ALL history)\n"
            "   • Injection    — injection rates table + chart (ALL history)\n"
            "   • Regulatory  — UIC/MIT tests, MASP, pass/fail\n\n"
            "4. Production & Injection tabs:\n"
            "   • Select well from list to filter chart (or 'ALL WELLS')\n"
            "   • '📋 Copy to Clipboard' copies tab-separated data for Excel\n\n"
            f"Connection: {DB_USER}@{DB_TNS_ALIAS}"
        )
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=5, pady=5)

    def _build_wellinfo_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Well Info  ")
        self.wi_tree, self.wi_rc = self._make_treeview(frame)

    def _build_regulatory_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Regulatory  ")
        self.reg_tree, self.reg_rc = self._make_treeview(frame)

    def _make_treeview(self, parent):
        tree = ttk.Treeview(parent, show="headings", selectmode="extended")
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        rc = tk.StringVar(value="0 rows")
        ttk.Label(parent, textvariable=rc, anchor="e").grid(row=2, column=0, sticky="e", padx=5)
        return tree, rc

    def _build_chart_tab(self, data_key):
        outer = ttk.Frame(self.notebook)
        label = "  Production  " if data_key == "production" else "  Injection  "
        self.notebook.add(outer, text=label)

        pw = ttk.PanedWindow(outer, orient="horizontal")
        pw.pack(fill="both", expand=True)

        # LEFT: well list + buttons + table
        left = ttk.Frame(pw)
        pw.add(left, weight=3)

        sel_frame = ttk.LabelFrame(left, text=" Select Well ", padding=4)
        sel_frame.pack(fill="x", padx=4, pady=(4, 2))

        lb_inner = ttk.Frame(sel_frame)
        lb_inner.pack(fill="both", expand=True)
        listbox = tk.Listbox(lb_inner, height=6, font=("Consolas", 9), exportselection=False)
        listbox.pack(fill="both", side="left", expand=True)
        lb_sb = ttk.Scrollbar(lb_inner, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=lb_sb.set)
        lb_sb.pack(side="right", fill="y")
        listbox.insert("end", "── ALL WELLS ──")
        listbox.selection_set(0)

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", padx=4, pady=2)
        ttk.Button(btn_row, text="📋 Copy to Clipboard",
                   command=lambda k=data_key: self._copy_to_clipboard(k)).pack(side="left", padx=(0, 4))
        info_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=info_var, anchor="w", foreground="gray").pack(side="left")

        tbl_frame = ttk.Frame(left)
        tbl_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        tree, rc_var = self._make_treeview(tbl_frame)

        # RIGHT: chart
        right = ttk.Frame(pw)
        pw.add(right, weight=2)
        ttk.Label(right, text="Fetch data to see chart", anchor="center").pack(fill="both", expand=True)

        if data_key == "production":
            self.prod_tree, self.prod_rc = tree, rc_var
            self.prod_listbox = listbox
            self.prod_chart_frame = right
            self.prod_info = info_var
            listbox.bind("<<ListboxSelect>>", lambda e: self._on_prod_select())
        else:
            self.inj_tree, self.inj_rc = tree, rc_var
            self.inj_listbox = listbox
            self.inj_chart_frame = right
            self.inj_info = info_var
            listbox.bind("<<ListboxSelect>>", lambda e: self._on_inj_select())

    # -----------------------------------------------------------------------
    # FETCH — Two-step: resolve API14, then parallel queries
    # -----------------------------------------------------------------------
    def _on_fetch(self):
        raw = self.api_text.get("1.0", "end")
        self.api_list = normalize_apis(raw)
        if not self.api_list:
            messagebox.showwarning("No APIs", "Enter at least one valid 10-digit API number.")
            return
        self.fetch_btn.config(state="disabled")
        self.progress["value"] = 0
        self._fetch_start = datetime.now()
        self._timer_running = True
        self._tick_timer()
        self.status_var.set("Step 1: Resolving API10 → API14...")
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def _tick_timer(self):
        if self._timer_running:
            elapsed = (datetime.now() - self._fetch_start).seconds
            self.elapsed_var.set(f"{elapsed}s elapsed")
            self.after(1000, self._tick_timer)

    def _fetch_all(self):
        try:
            # ── STEP 1: Resolve API10 → API14 ──
            resolve_sql = SQL_RESOLVE_API14.replace(
                "{api10_filter}", build_api10_filter(self.api_list))
            conn = get_connection()
            _, resolve_rows = run_query_with_conn(conn, resolve_sql)
            conn.close()

            self.api14_list = [row[0] for row in resolve_rows]
            self.api14_map = {row[0]: (row[1], row[2]) for row in resolve_rows}

            self.after(0, lambda: self.progress.configure(value=1))

            if not self.api14_list:
                self._timer_running = False
                self.after(0, lambda: messagebox.showwarning(
                    "No Wells Found",
                    f"No wells found for API(s): {', '.join(self.api_list)}\n"
                    "Check that the API numbers are correct."))
                self.after(0, lambda: self.status_var.set("No wells found"))
                return

            api14_in = build_api14_in(self.api14_list)
            n_wells = len(self.api14_list)
            names = ", ".join(self.api14_map[a][0] for a in self.api14_list[:5])
            if n_wells > 5:
                names += f" ... +{n_wells - 5} more"
            self.after(0, lambda: self.status_var.set(
                f"Found {n_wells} completion(s): {names}. Fetching data..."))

            # ── STEP 2: Run 4 queries in parallel using exact PID match ──
            queries = {
                "well_info":  SQL_WELL_INFO.replace("{api14_list}", api14_in),
                "production": SQL_PRODUCTION.replace("{api14_list}", api14_in),
                "injection":  SQL_INJECTION.replace("{api14_list}", api14_in),
                "regulatory": SQL_REGULATORY.replace("{api14_list}", api14_in),
            }

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(run_query_new_conn, k, sql): k
                           for k, sql in queries.items()}
                done_count = 1  # already did resolve
                for future in as_completed(futures):
                    key, cols, rows = future.result()
                    self.headers[key] = cols
                    self.data[key] = rows
                    done_count += 1
                    self.after(0, lambda v=done_count: self.progress.configure(value=v))
                    self.after(0, lambda k=key, r=len(rows):
                               self.status_var.set(f"Fetched {k.replace('_',' ')}: {r} rows"))

            self._timer_running = False
            elapsed = (datetime.now() - self._fetch_start).seconds
            total_rows = sum(len(v) for v in self.data.values())
            self.after(0, lambda: self.elapsed_var.set(f"{elapsed}s total"))
            self.after(0, self._populate_all)
            self.after(0, lambda: self.status_var.set(
                f"Done — {n_wells} completion(s), {total_rows} rows in {elapsed}s"))

        except Exception as e:
            self._timer_running = False
            self.after(0, lambda: messagebox.showerror("Database Error", str(e)))
            self.after(0, lambda: self.status_var.set(f"Error: {e}"))
        finally:
            self.after(0, lambda: self.fetch_btn.config(state="normal"))

    # -----------------------------------------------------------------------
    # POPULATE
    # -----------------------------------------------------------------------
    def _populate_all(self):
        self._fill_tree(self.wi_tree, self.wi_rc, "well_info")
        self._fill_tree(self.prod_tree, self.prod_rc, "production")
        self._fill_tree(self.inj_tree, self.inj_rc, "injection")
        self._fill_tree(self.reg_tree, self.reg_rc, "regulatory")

        self._populate_listbox(self.prod_listbox, self.data.get("production", []))
        self._populate_listbox(self.inj_listbox, self.data.get("injection", []))

        prod_rows = self.data.get("production", [])
        inj_rows = self.data.get("injection", [])
        self.prod_info.set(
            f"{len(prod_rows)} rows, {len(set(r[0] for r in prod_rows))} wells"
            if prod_rows else "No production data for these wells")
        self.inj_info.set(
            f"{len(inj_rows)} rows, {len(set(r[0] for r in inj_rows))} wells"
            if inj_rows else "No injection data (producers only?)")

        self._draw_production_chart()
        self._draw_injection_chart()
        self.notebook.select(1)

    def _fill_tree(self, tree, rc_var, data_key):
        tree.delete(*tree.get_children())
        cols = self.headers.get(data_key, [])
        rows = self.data.get(data_key, [])
        if not cols:
            rc_var.set("0 rows")
            return
        tree["columns"] = cols
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=110, minwidth=50)
        for row in rows:
            tree.insert("", "end", values=[fmt_val(v) for v in row])
        rc_var.set(f"{len(rows)} rows")

    def _populate_listbox(self, listbox, rows):
        listbox.delete(0, "end")
        listbox.insert("end", "── ALL WELLS ──")
        if rows:
            names = list(dict.fromkeys(row[0] for row in rows))
            for name in sorted(names):
                listbox.insert("end", name)
        listbox.selection_set(0)

    # -----------------------------------------------------------------------
    # CHARTS
    # -----------------------------------------------------------------------
    def _get_selected_well(self, listbox):
        sel = listbox.curselection()
        if not sel or sel[0] == 0:
            return None
        return listbox.get(sel[0])

    def _draw_production_chart(self, well_filter=None):
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            import matplotlib.dates as mdates
        except ImportError:
            for w in self.prod_chart_frame.winfo_children():
                w.destroy()
            ttk.Label(self.prod_chart_frame,
                      text="pip install matplotlib\nfor charts",
                      anchor="center").pack(fill="both", expand=True)
            return

        rows = self.data.get("production", [])
        for w in self.prod_chart_frame.winfo_children():
            w.destroy()

        if not rows:
            ttk.Label(self.prod_chart_frame,
                      text="No production data to chart",
                      anchor="center", font=("Arial", 11)).pack(fill="both", expand=True)
            return

        fig = Figure(figsize=(6, 4), dpi=95)
        fig.subplots_adjust(left=0.10, right=0.88, top=0.92, bottom=0.18)
        ax = fig.add_subplot(111)

        wells = {}
        for row in rows:
            name = row[0]
            if well_filter and name != well_filter:
                continue
            dt = row[2] if isinstance(row[2], datetime) else datetime.strptime(str(row[2])[:10], "%Y-%m-%d")
            bopd = float(row[3] or 0)
            bwpd = float(row[4] or 0)
            mcfd = float(row[5] or 0)
            wells.setdefault(name, []).append((dt, bopd, bwpd, mcfd))

        if not wells:
            ttk.Label(self.prod_chart_frame, text="No data for selected well",
                      anchor="center").pack(fill="both", expand=True)
            return

        if len(wells) == 1:
            name = list(wells.keys())[0]
            data = sorted(wells[name])
            dates = [d[0] for d in data]
            ax.plot(dates, [d[1] for d in data], 'g-o', markersize=3, linewidth=1.2, label="Oil (BOPD)")
            ax.plot(dates, [d[2] for d in data], 'b-s', markersize=3, linewidth=1.2, label="Water (BWPD)")
            ax2 = ax.twinx()
            ax2.plot(dates, [d[3] for d in data], 'r-^', markersize=3, linewidth=1.2, label="Gas (MCFD)")
            ax2.set_ylabel("Gas (MCFD)", color='r', fontsize=8)
            ax2.tick_params(axis='y', labelcolor='r', labelsize=7)
            ax.set_title(f"{name} — Production", fontsize=10, fontweight='bold')
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")
        else:
            for name, wdata in wells.items():
                wdata.sort()
                ax.plot([d[0] for d in wdata], [d[1] for d in wdata],
                        '-o', markersize=2, linewidth=1.2, label=name)
            ax.set_title("Oil Production (BOPD) — All Wells", fontsize=10, fontweight='bold')
            ax.legend(fontsize=7, loc="upper left")

        ax.set_ylabel("Rate (BOPD / BWPD)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        fig.autofmt_xdate(rotation=30)
        ax.grid(True, alpha=0.3)

        canvas = FigureCanvasTkAgg(fig, master=self.prod_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def _draw_injection_chart(self, well_filter=None):
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            import matplotlib.dates as mdates
        except ImportError:
            return

        rows = self.data.get("injection", [])
        for w in self.inj_chart_frame.winfo_children():
            w.destroy()

        if not rows:
            ttk.Label(self.inj_chart_frame,
                      text="No injection data to chart.\n\n"
                           "The selected well(s) may be producers,\n"
                           "or the well is inactive with no injection history.",
                      anchor="center", font=("Arial", 11), justify="center"
                      ).pack(fill="both", expand=True)
            return

        fig = Figure(figsize=(6, 4), dpi=95)
        fig.subplots_adjust(left=0.10, right=0.88, top=0.92, bottom=0.18)
        ax = fig.add_subplot(111)

        wells = {}
        for row in rows:
            name = row[0]
            if well_filter and name != well_filter:
                continue
            dt = row[2] if isinstance(row[2], datetime) else datetime.strptime(str(row[2])[:10], "%Y-%m-%d")
            wat_inj = float(row[3] or 0)
            disp_inj = float(row[4] or 0)
            gas_inj = float(row[5] or 0)
            steam_inj = float(row[7] or 0)
            wells.setdefault(name, []).append((dt, wat_inj, disp_inj, gas_inj, steam_inj))

        if not wells:
            ttk.Label(self.inj_chart_frame, text="No data for selected well",
                      anchor="center").pack(fill="both", expand=True)
            return

        if len(wells) == 1:
            name = list(wells.keys())[0]
            data = sorted(wells[name])
            dates = [d[0] for d in data]
            ax.plot(dates, [d[1] for d in data], 'b-o', markersize=3, linewidth=1.2, label="Water Inj (BWIPD)")
            ax.plot(dates, [d[2] for d in data], 'c-s', markersize=3, linewidth=1.2, label="Disp Water (BWIPD)")
            ax.plot(dates, [d[4] for d in data], 'm-^', markersize=3, linewidth=1.2, label="Steam (BWE/D)")
            ax2 = ax.twinx()
            ax2.plot(dates, [d[3] for d in data], 'r-d', markersize=3, linewidth=1.2, label="Gas Inj (MCFIPD)")
            ax2.set_ylabel("Gas Inj (MCFIPD)", color='r', fontsize=8)
            ax2.tick_params(axis='y', labelcolor='r', labelsize=7)
            ax.set_title(f"{name} — Injection", fontsize=10, fontweight='bold')
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")
        else:
            for name, wdata in wells.items():
                wdata.sort()
                total = [d[1] + d[2] for d in wdata]
                ax.plot([d[0] for d in wdata], total, '-o', markersize=2, linewidth=1.2, label=name)
            ax.set_title("Total Water Injection (BWIPD) — All Wells", fontsize=10, fontweight='bold')
            ax.legend(fontsize=7, loc="upper left")

        ax.set_ylabel("Injection Rate", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        fig.autofmt_xdate(rotation=30)
        ax.grid(True, alpha=0.3)

        canvas = FigureCanvasTkAgg(fig, master=self.inj_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def _on_prod_select(self):
        self._draw_production_chart(self._get_selected_well(self.prod_listbox))

    def _on_inj_select(self):
        self._draw_injection_chart(self._get_selected_well(self.inj_listbox))

    # -----------------------------------------------------------------------
    # CLIPBOARD / EXPORT
    # -----------------------------------------------------------------------
    def _copy_to_clipboard(self, data_key):
        cols = self.headers.get(data_key, [])
        rows = self.data.get(data_key, [])
        if not cols:
            messagebox.showinfo("No Data", "Fetch data first.")
            return
        lines = ["\t".join(cols)]
        for row in rows:
            lines.append("\t".join(fmt_val(v) for v in row))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status_var.set(f"Copied {len(rows)} rows to clipboard ({data_key})")

    def _on_export(self):
        tab_idx = self.notebook.index(self.notebook.select())
        key_map = {1: "well_info", 2: "production", 3: "injection", 4: "regulatory"}
        key = key_map.get(tab_idx)
        if not key:
            messagebox.showinfo("Export", "Select a data tab first.")
            return
        cols = self.headers.get(key, [])
        rows = self.data.get(key, [])
        if not rows:
            messagebox.showinfo("No Data", "No data to export.")
            return
        fname = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialfile=f"ekpspp_{key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not fname:
            return
        with open(fname, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in rows:
                w.writerow([fmt_val(v) for v in row])
        self.status_var.set(f"Exported {len(rows)} rows → {os.path.basename(fname)}")


if __name__ == "__main__":
    app = EKPSPPViewer()
    app.mainloop()