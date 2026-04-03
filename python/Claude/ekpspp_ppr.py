#!/usr/bin/env python3
"""
Well Data Viewer — EKPSPP Oracle Database
Tab 1: Well Input   — 10-digit API entry, validation, deduplication
Tab 2: Basic Well Data — well name, field, API, type, status, etc.
Tab 3: Top Perf Depths — top/bottom perf MD & TVD (interpolated from survey)

Performance: 3-step query (LIKE→API14→separate table lookups, join in Python)

Tables:
  ODS.BI_WELLCOMP_V   — completion master (WELL_ID, API_NO14, well name…)
  ODS.BI_WELL          — surface info (section, township, lat/lon)
  DSS.COMPMASTER       — team / sector
  EDM.CD_PERF_INTERVAL_X — perforation intervals (MD_TOP_SHOT, MD_BOTTOM_SHOT)
  DSS.SURVEY           — directional survey (PID, MD, TVD) for TVD interpolation
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re, threading, traceback, time

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  EKPSPP Connection                                                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
DB_TNS_ALIAS = "EKPSPP.WORLD"
DB_USER      = "oxy_read"
DB_PASSWORD  = "oxy_read"
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_TEST_APIS = """0403031235
0402918665
0402918827
0402918828
0402918832
0402918833
0402918834
0402918836
0402918837
0402918895
0402918897
0402918898
0402918899
0402918903
0402918905
0402918907
0402918907
0402918908
0402918910
0402986052
0402986052"""


def get_oracle_connection():
    try:
        import cx_Oracle as ora
        return ora.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_TNS_ALIAS)
    except ImportError:
        pass
    try:
        import oracledb as ora
        try:
            ora.init_oracle_client()
        except ora.ProgrammingError:
            pass
        return ora.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_TNS_ALIAS)
    except ImportError:
        pass
    raise ImportError("Neither cx_Oracle nor oracledb installed.")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def autofit_columns(tree):
    import tkinter.font as tkfont
    try:
        f = ttk.Style().lookup("Treeview", "font") or "TkDefaultFont"
        mf = tkfont.nametofont(f)
    except Exception:
        mf = tkfont.nametofont("TkDefaultFont")
    for col in tree["columns"]:
        hw = mf.measure(tree.heading(col, "text")) + 24
        mx = hw
        for iid in tree.get_children():
            cw = mf.measure(str(tree.set(iid, col))) + 16
            if cw > mx:
                mx = cw
        tree.column(col, width=min(mx, 350))


def copy_tree_to_clipboard(tree, root):
    cols = tree["columns"]
    lines = ["\t".join(tree.heading(c, "text") for c in cols)]
    for iid in tree.get_children():
        lines.append("\t".join(str(tree.set(iid, c)) for c in cols))
    root.clipboard_clear()
    root.clipboard_append("\n".join(lines))
    messagebox.showinfo("Copied", f"{len(lines)-1} rows copied to clipboard.")


def _quoted(items):
    return ", ".join(f"'{v}'" for v in items)


def _chunked_in(col, items, limit=999):
    items = list(items)
    ch = []
    for s in range(0, len(items), limit):
        ch.append(f"{col} IN ({_quoted(items[s:s+limit])})")
    return ch[0] if len(ch) == 1 else "(" + " OR ".join(ch) + ")"


def _interp_tvd(md_val, survey_pts):
    """Linear-interpolate TVD at md_val from sorted [(md, tvd), …].
    Returns None if no survey data."""
    if not survey_pts or md_val is None:
        return None
    if md_val <= survey_pts[0][0]:
        return survey_pts[0][1]
    if md_val >= survey_pts[-1][0]:
        return survey_pts[-1][1]
    for i in range(len(survey_pts) - 1):
        md1, tvd1 = survey_pts[i]
        md2, tvd2 = survey_pts[i + 1]
        if md1 <= md_val <= md2:
            if md2 == md1:
                return tvd1
            frac = (md_val - md1) / (md2 - md1)
            return round(tvd1 + frac * (tvd2 - tvd1), 1)
    return None


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------
def step1_api14_lookup(cur, api10_list):
    """LIKE-based API14 lookup (index-friendly)."""
    like = " OR ".join(f"API_NO14 LIKE '{a}%'" for a in api10_list)
    sql = f"SELECT API_NO14, WELL_ID, WELLBORE_ID FROM ODS.BI_WELLCOMP_V WHERE {like}"
    cur.execute(sql)
    rows = cur.fetchall()
    # Returns list of (api14, well_id, wellbore_id)
    return rows, sql


def fetch_tab2(cur, api14_list, api10_from_14, log):
    """Tab 2: Basic Well Data — 3 parallel lookups merged in Python."""
    all_sql = []

    # Main completion data
    t = time.time()
    w = _chunked_in("C.API_NO14", api14_list)
    sql2 = f"""SELECT C.WELLCOMP_NAME AS WELL_NAME, C.API_NO14 AS API_14,
    C.FIELD_NAME AS FIELD, C.ORGLEV4_NAME AS AREA, C.CURR_COMP_TYPE AS COMP_TYPE,
    C.CURR_COMP_STATUS AS STATUS, TO_CHAR(C.STATUS_EFF_DATE,'YYYY-MM-DD') AS STATUS_DATE,
    C.RESERVOIR_CD AS RESERVOIR, C.CURR_METHOD_PROD AS LIFT_METHOD,
    TO_CHAR(C.WELL_SPUD_DATE,'YYYY-MM-DD') AS SPUD_DATE,
    TO_CHAR(C.COMPLETION_DATE,'YYYY-MM-DD') AS COMPLETION_DATE,
    TO_CHAR(C.FIRST_PROD_DATE,'YYYY-MM-DD') AS FIRST_PROD_DATE
FROM ODS.BI_WELLCOMP_V C WHERE {w} ORDER BY C.CURR_COMP_TYPE, C.API_NO14"""
    all_sql.append(sql2)
    cur.execute(sql2)
    comp_rows = cur.fetchall()
    comp_cols = [d[0] for d in cur.description]
    log(f"Tab2-Main: {len(comp_rows)} rows ({time.time()-t:.1f}s)")

    # BI_WELL
    t = time.time()
    w3a = _chunked_in("W.API_NO10", api10_from_14)
    sql3a = f"""SELECT W.API_NO10, W.SECTION, W.TOWNSHIP, W.RANGE_NO,
    ROUND(W.SURF_LATITUDE,5) AS LATITUDE, ROUND(W.SURF_LONGITUDE,5) AS LONGITUDE
FROM ODS.BI_WELL W WHERE {w3a}"""
    all_sql.append(sql3a)
    cur.execute(sql3a)
    well_map = {str(r[0]): {"SECTION":r[1],"TOWNSHIP":r[2],"RANGE_NO":r[3],
                             "LATITUDE":r[4],"LONGITUDE":r[5]} for r in cur.fetchall()}
    log(f"Tab2-BI_WELL: {len(well_map)} rows ({time.time()-t:.1f}s)")

    # COMPMASTER
    t = time.time()
    w3b = _chunked_in("PID", api14_list)
    sql3b = f"SELECT PID, MAX(TEAM) AS TEAM, MAX(SECTOR) AS SECTOR FROM DSS.COMPMASTER WHERE {w3b} GROUP BY PID"
    all_sql.append(sql3b)
    cur.execute(sql3b)
    cm_map = {str(r[0]): {"TEAM":r[1],"SECTOR":r[2]} for r in cur.fetchall()}
    log(f"Tab2-COMPMASTER: {len(cm_map)} rows ({time.time()-t:.1f}s)")

    # Merge
    results = []
    for row in comp_rows:
        d = dict(zip(comp_cols, row))
        a14 = str(d["API_14"]); a10 = a14[:10]
        d["API_10"] = a10
        d.update(well_map.get(a10, {}))
        d.update(cm_map.get(a14, {}))
        results.append(d)
    return results, "\n\n".join(all_sql)


def fetch_tab3(cur, api14_wellid_map, log):
    """Tab 3: Top Perf Depths with TVD interpolation from directional survey.

    Tables:
      EDM.CD_PERF_INTERVAL_X — perf intervals (WELL_ID, WELLBORE_ID, MD_TOP_SHOT, MD_BOTTOM_SHOT)
      DSS.SURVEY             — directional survey (PID→API14, MD, TVD)

    Strategy:
      1. Get all perf intervals for our WELL_IDs
      2. Get survey data for our API14s
      3. For each completion (API14/WELLBORE_ID): find min(MD_TOP_SHOT), max(MD_BOTTOM_SHOT)
      4. Interpolate TVD from survey; if no survey → TVD ≈ MD (flag as vertical)
    """
    all_sql = []
    well_ids = list(set(v["well_id"] for v in api14_wellid_map.values() if v.get("well_id")))
    api14s = list(api14_wellid_map.keys())

    if not well_ids:
        log("Tab3: No WELL_IDs available — skipping.")
        return [], "-- No WELL_IDs found"

    # Step A: Perf intervals
    t = time.time()
    w_perf = _chunked_in("P.WELL_ID", well_ids)
    sql_perf = f"""SELECT P.WELL_ID, P.WELLBORE_ID, P.PERF_ID, P.PERF_INTERVAL_ID,
    P.MD_TOP_SHOT, P.MD_BOTTOM_SHOT,
    TO_CHAR(P.DATE_INTERVAL_SHOT,'YYYY-MM-DD') AS SHOT_DATE, P.INTERVAL_TYPE
FROM EDM.CD_PERF_INTERVAL_X P WHERE {w_perf} ORDER BY P.WELL_ID, P.MD_TOP_SHOT"""
    all_sql.append(sql_perf)
    cur.execute(sql_perf)
    perf_rows = cur.fetchall()
    perf_cols = [d[0] for d in cur.description]
    log(f"Tab3-Perfs: {len(perf_rows)} intervals ({time.time()-t:.1f}s)")

    # Step B: Directional survey data
    t = time.time()
    w_svy = _chunked_in("S.PID", api14s)
    sql_svy = f"SELECT S.PID, S.MD, S.TVD FROM DSS.SURVEY S WHERE {w_svy} ORDER BY S.PID, S.MD"
    all_sql.append(sql_svy)
    cur.execute(sql_svy)
    svy_rows = cur.fetchall()
    log(f"Tab3-Survey: {len(svy_rows)} stations ({time.time()-t:.1f}s)")

    # Build survey lookup: PID → sorted [(md, tvd), …]
    svy_map = {}
    for pid, md, tvd in svy_rows:
        svy_map.setdefault(str(pid), []).append((float(md), float(tvd)))

    # Build reverse map: (well_id, wellbore_id) → api14
    wb_to_api14 = {}
    for a14, info in api14_wellid_map.items():
        key = (info.get("well_id",""), info.get("wellbore_id",""))
        wb_to_api14[key] = a14

    # Group perfs by (well_id, wellbore_id) → get top/bottom
    from collections import defaultdict
    perf_groups = defaultdict(list)
    for row in perf_rows:
        d = dict(zip(perf_cols, row))
        key = (str(d["WELL_ID"]).strip(), str(d["WELLBORE_ID"]).strip())
        top = d["MD_TOP_SHOT"]
        btm = d["MD_BOTTOM_SHOT"]
        if top is not None and btm is not None:
            perf_groups[key].append((float(top), float(btm),
                                     d.get("SHOT_DATE",""), d.get("INTERVAL_TYPE","")))

    # Build results per completion
    results = []
    for (wid, wbid), intervals in perf_groups.items():
        api14 = wb_to_api14.get((wid, wbid), "")
        if not api14:
            # Try to find by well_id alone
            for a14, info in api14_wellid_map.items():
                if info.get("well_id","") == wid:
                    api14 = a14
                    break
        api10 = api14[:10] if api14 else ""
        well_name = api14_wellid_map.get(api14, {}).get("well_name", "")

        # Overall top/bottom across all intervals
        all_tops = [i[0] for i in intervals]
        all_btms = [i[1] for i in intervals]
        top_md = min(all_tops) if all_tops else None
        btm_md = max(all_btms) if all_btms else None

        # TVD interpolation
        survey = svy_map.get(api14, [])
        has_survey = len(survey) > 0
        if has_survey:
            top_tvd = _interp_tvd(top_md, survey)
            btm_tvd = _interp_tvd(btm_md, survey)
            tvd_method = "SURVEY"
        else:
            # Vertical well assumption: TVD ≈ MD
            top_tvd = top_md
            btm_tvd = btm_md
            tvd_method = "MD≈TVD (no survey)"

        # Count perf intervals, earliest/latest shot dates
        dates = [i[2] for i in intervals if i[2]]
        types = list(set(i[3] for i in intervals if i[3]))
        n_intervals = len(intervals)
        earliest = min(dates) if dates else ""
        latest = max(dates) if dates else ""

        results.append({
            "WELL_NAME": well_name,
            "API_10": api10,
            "API_14": api14,
            "TOP_PERF_MD": f"{top_md:.1f}" if top_md is not None else "",
            "BTM_PERF_MD": f"{btm_md:.1f}" if btm_md is not None else "",
            "TOP_PERF_TVD": f"{top_tvd:.1f}" if top_tvd is not None else "",
            "BTM_PERF_TVD": f"{btm_tvd:.1f}" if btm_tvd is not None else "",
            "TVD_METHOD": tvd_method,
            "N_INTERVALS": str(n_intervals),
            "INTERVAL_TYPES": ", ".join(types),
            "EARLIEST_SHOT": earliest,
            "LATEST_SHOT": latest,
        })

    results.sort(key=lambda d: (d.get("API_10",""), d.get("API_14","")))
    return results, "\n\n".join(all_sql)


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class WellDataViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Well Data Viewer — EKPSPP")
        self.geometry("1450x800")
        self.minsize(900, 500)
        self.validated_apis = []

        # Log bar
        lf = ttk.LabelFrame(self, text="Log", padding=2)
        lf.pack(side="bottom", fill="x", padx=6, pady=(0,4))
        self.log_text = scrolledtext.ScrolledText(lf, height=4, font=("Consolas",9),
                                                   state="disabled", wrap="word")
        self.log_text.pack(fill="x")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=(6,2))
        self._build_tab1()
        self._build_tab2()
        self._build_tab3()

        self.log("Application started.")
        self.after(200, self._detect_driver)

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _detect_driver(self):
        d = "none"
        try:
            import cx_Oracle; d = f"cx_Oracle {cx_Oracle.version}"
        except ImportError:
            try:
                import oracledb; d = f"oracledb {oracledb.__version__}"
            except ImportError: pass
        self.log(f"Oracle driver: {d}")

    # ── Shared tree builder ──
    def _make_tree_tab(self, parent, cols, headings_map, sql_label="SQL Query Used"):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", padx=8, pady=6)

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0,4))

        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            tree.heading(c, text=headings_map.get(c, c),
                         command=lambda _c=c, _t=tree: self._sort_tree(_t, _c))
            tree.column(c, width=100, minwidth=50)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        sf = ttk.LabelFrame(parent, text=sql_label, padding=4)
        sf.pack(fill="x", padx=8, pady=(0,4))
        sql_box = scrolledtext.ScrolledText(sf, height=4, font=("Consolas",9),
                                             state="disabled", wrap="word")
        sql_box.pack(fill="x")

        return toolbar, tree, sql_box

    def _sort_tree(self, tree, col):
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            data.sort(key=lambda t: float(t[0]) if t[0] else 0)
        except ValueError:
            data.sort(key=lambda t: t[0].lower() if t[0] else "")
        for i, (_, k) in enumerate(data):
            tree.move(k, "", i)

    # -----------------------------------------------------------------------
    # TAB 1 — Well Input
    # -----------------------------------------------------------------------
    def _build_tab1(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Well Input  ")

        ttk.Label(tab, text="Enter 10-digit API numbers (one per line). "
                  "Duplicates removed automatically. Click 'Validate & Fetch All' "
                  "to validate then auto-query all data tabs.",
                  wraplength=900, justify="left").pack(anchor="w", padx=12, pady=(10,4))

        ft = ttk.Frame(tab)
        ft.pack(fill="both", expand=True, padx=12, pady=4)
        self.api_text = scrolledtext.ScrolledText(ft, width=40, height=20,
                                                   font=("Consolas",11))
        self.api_text.pack(side="left", fill="both", expand=True)
        if DEFAULT_TEST_APIS.strip():
            self.api_text.insert("1.0", DEFAULT_TEST_APIS.strip())

        side = ttk.LabelFrame(ft, text="Validation Summary", padding=10)
        side.pack(side="right", fill="y", padx=(10,0))
        self.lbl_total   = ttk.Label(side, text="Lines entered: 0")
        self.lbl_valid   = ttk.Label(side, text="Valid APIs: 0")
        self.lbl_dupes   = ttk.Label(side, text="Duplicates removed: 0")
        self.lbl_invalid = ttk.Label(side, text="Invalid lines: 0")
        for l in (self.lbl_total, self.lbl_valid, self.lbl_dupes, self.lbl_invalid):
            l.pack(anchor="w", pady=2)
        self.invalid_detail = scrolledtext.ScrolledText(side, width=30, height=8,
                                                         font=("Consolas",9), state="disabled")
        self.invalid_detail.pack(fill="both", expand=True, pady=(6,0))

        bf = ttk.Frame(tab)
        bf.pack(fill="x", padx=12, pady=(4,10))
        ttk.Button(bf, text="Validate & Fetch All",
                   command=self._validate_and_fetch).pack(side="left", padx=4)
        ttk.Button(bf, text="Clear", command=self._clear_input).pack(side="left", padx=4)
        ttk.Button(bf, text="Paste from Clipboard",
                   command=self._paste_clipboard).pack(side="left", padx=4)
        ttk.Button(bf, text="Test Connection",
                   command=self._test_connection).pack(side="left", padx=12)
        self.lbl_status = ttk.Label(bf, text="", foreground="green")
        self.lbl_status.pack(side="left", padx=12)

    def _paste_clipboard(self):
        try: self.api_text.insert("end", self.clipboard_get())
        except tk.TclError: messagebox.showwarning("Clipboard","Nothing to paste.")

    def _clear_input(self):
        self.api_text.delete("1.0","end")
        self.validated_apis.clear()
        for l in (self.lbl_total, self.lbl_valid, self.lbl_dupes, self.lbl_invalid):
            l.config(text=l.cget("text").split(":")[0]+": 0")
        self.lbl_status.config(text="")
        self.invalid_detail.config(state="normal")
        self.invalid_detail.delete("1.0","end")
        self.invalid_detail.config(state="disabled")

    def _validate_and_fetch(self):
        raw = self.api_text.get("1.0","end").strip()
        if not raw:
            messagebox.showinfo("Input","Please enter at least one API number.")
            return
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        valid, invalid, seen, dupes = [], [], set(), 0
        for line in lines:
            c = re.sub(r"[^0-9]","",line)
            if len(c)==10 and c.isdigit():
                if c in seen: dupes+=1
                else: seen.add(c); valid.append(c)
            else: invalid.append(line)
        self.validated_apis = valid
        self.lbl_total.config(text=f"Lines entered: {len(lines)}")
        self.lbl_valid.config(text=f"Valid APIs: {len(valid)}")
        self.lbl_dupes.config(text=f"Duplicates removed: {dupes}")
        self.lbl_invalid.config(text=f"Invalid lines: {len(invalid)}")
        self.invalid_detail.config(state="normal")
        self.invalid_detail.delete("1.0","end")
        if invalid: self.invalid_detail.insert("end","\n".join(invalid))
        self.invalid_detail.config(state="disabled")
        self.log(f"Validated {len(valid)} unique APIs, {dupes} dupes, {len(invalid)} invalid.")

        if not valid:
            self.lbl_status.config(text="No valid APIs found.", foreground="red")
            return

        self.lbl_status.config(text=f"✓ {len(valid)} APIs — fetching all tabs…",
                               foreground="blue")
        # Auto-fetch all tabs
        self._set_tab_status(self.lbl_tab2_status, "Queued…", "blue")
        self._set_tab_status(self.lbl_tab3_status, "Queued…", "blue")
        self.update_idletasks()
        threading.Thread(target=self._fetch_all_tabs, daemon=True).start()

    def _set_tab_status(self, lbl, text, color):
        lbl.config(text=text, foreground=color)

    def _test_connection(self):
        self.log("Testing connection…")
        self.lbl_status.config(text="Testing…", foreground="blue")
        self.update_idletasks()
        def w():
            t0=time.time()
            try:
                c=get_oracle_connection(); cur=c.cursor()
                cur.execute("SELECT 1 FROM DUAL"); cur.fetchone()
                e=time.time()-t0; cur.close(); c.close()
                self.after(0,self.log,f"Connection OK ({e:.1f}s)")
                self.after(0,lambda:self.lbl_status.config(text=f"✓ Connected ({e:.1f}s)",foreground="green"))
            except Exception as ex:
                self.after(0,self.log,f"FAILED: {ex}")
                self.after(0,lambda:self.lbl_status.config(text="✗ Failed",foreground="red"))
        threading.Thread(target=w,daemon=True).start()

    # -----------------------------------------------------------------------
    # TAB 2 — Basic Well Data
    # -----------------------------------------------------------------------
    def _build_tab2(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Basic Well Data  ")

        cols = ("WELL_NAME","API_10","API_14","FIELD","AREA","COMP_TYPE","STATUS",
                "STATUS_DATE","RESERVOIR","LIFT_METHOD","TEAM","SECTOR",
                "SPUD_DATE","COMPLETION_DATE","FIRST_PROD_DATE",
                "SECTION","TOWNSHIP","RANGE_NO","LATITUDE","LONGITUDE")
        hm = {"WELL_NAME":"Well Name","API_10":"API 10","API_14":"API 14",
              "FIELD":"Field","AREA":"Area","COMP_TYPE":"Comp Type","STATUS":"Status",
              "STATUS_DATE":"Status Date","RESERVOIR":"Reservoir","LIFT_METHOD":"Lift Method",
              "TEAM":"Team","SECTOR":"Sector","SPUD_DATE":"Spud Date",
              "COMPLETION_DATE":"Completion Date","FIRST_PROD_DATE":"First Prod Date",
              "SECTION":"Section","TOWNSHIP":"Township","RANGE_NO":"Range",
              "LATITUDE":"Latitude","LONGITUDE":"Longitude"}

        toolbar, self.tree2, self.sql2 = self._make_tree_tab(tab, cols, hm)

        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree2, self)).pack(side="left", padx=4)
        self.lbl_tab2_status = ttk.Label(toolbar, text="")
        self.lbl_tab2_status.pack(side="right", padx=8)

    # -----------------------------------------------------------------------
    # TAB 3 — Top Perf Depths
    # -----------------------------------------------------------------------
    def _build_tab3(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Top Perf Depths  ")

        cols = ("WELL_NAME","API_10","API_14",
                "TOP_PERF_MD","BTM_PERF_MD","TOP_PERF_TVD","BTM_PERF_TVD",
                "TVD_METHOD","N_INTERVALS","INTERVAL_TYPES",
                "EARLIEST_SHOT","LATEST_SHOT")
        hm = {"WELL_NAME":"Well Name","API_10":"API 10","API_14":"API 14",
              "TOP_PERF_MD":"Top Perf MD","BTM_PERF_MD":"Btm Perf MD",
              "TOP_PERF_TVD":"Top Perf TVD","BTM_PERF_TVD":"Btm Perf TVD",
              "TVD_METHOD":"TVD Method","N_INTERVALS":"# Intervals",
              "INTERVAL_TYPES":"Interval Types",
              "EARLIEST_SHOT":"Earliest Shot","LATEST_SHOT":"Latest Shot"}

        toolbar, self.tree3, self.sql3 = self._make_tree_tab(
            tab, cols, hm, sql_label="SQL Queries Used (Perf + Survey)")

        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree3, self)).pack(side="left", padx=4)

        note = ttk.Label(toolbar, text="NOTE: TVD interpolated from DSS.SURVEY; "
                         "vertical wells default TVD≈MD",
                         foreground="gray", font=("TkDefaultFont",8))
        note.pack(side="left", padx=12)

        self.lbl_tab3_status = ttk.Label(toolbar, text="")
        self.lbl_tab3_status.pack(side="right", padx=8)

    # -----------------------------------------------------------------------
    # Fetch All Tabs (single connection, sequential queries)
    # -----------------------------------------------------------------------
    def _fetch_all_tabs(self):
        t_total = time.time()
        try:
            self.after(0, self.log, f"Connecting to EKPSPP for {len(self.validated_apis)} APIs…")
            conn = get_oracle_connection()
            cur = conn.cursor()
            self.after(0, self.log, "Connected.")

            # Step 1: API14 lookup (shared across all tabs)
            t = time.time()
            self.after(0, self._set_tab_status, self.lbl_tab2_status, "Step 1: API14 lookup…", "blue")
            self.after(0, self._set_tab_status, self.lbl_tab3_status, "Waiting…", "blue")
            rows1, sql1 = step1_api14_lookup(cur, self.validated_apis)
            api14_list = list(set(str(r[0]) for r in rows1))
            api10_from_14 = list(set(str(r[0])[:10] for r in rows1))

            # Build wellid map for tab3
            api14_wellid_map = {}
            for r in rows1:
                a14 = str(r[0])
                api14_wellid_map[a14] = {
                    "well_id": str(r[1]).strip() if r[1] else "",
                    "wellbore_id": str(r[2]).strip() if r[2] else "",
                }

            self.after(0, self.log, f"Step 1: {len(api14_list)} API14s found ({time.time()-t:.1f}s)")

            if not api14_list:
                self.after(0, self._set_tab_status, self.lbl_tab2_status,
                           "No completions found.", "orange")
                self.after(0, self._set_tab_status, self.lbl_tab3_status,
                           "No completions found.", "orange")
                self.after(0, self.lbl_status.config,
                           {"text":"No completions found for those APIs.","foreground":"orange"})
                cur.close(); conn.close()
                return

            # ── Tab 2 ──
            self.after(0, self._set_tab_status, self.lbl_tab2_status, "Fetching…", "blue")
            t2_results, t2_sql = fetch_tab2(cur, api14_list, api10_from_14,
                                             lambda m: self.after(0, self.log, m))
            self.after(0, self._populate_tab2, t2_results, f"-- Step 1\n{sql1}\n\n{t2_sql}")

            # ── Tab 3 — also add well_name to the map ──
            self.after(0, self._set_tab_status, self.lbl_tab3_status, "Fetching…", "blue")
            for d in t2_results:
                a14 = str(d.get("API_14",""))
                if a14 in api14_wellid_map:
                    api14_wellid_map[a14]["well_name"] = d.get("WELL_NAME","")

            t3_results, t3_sql = fetch_tab3(cur, api14_wellid_map,
                                             lambda m: self.after(0, self.log, m))
            self.after(0, self._populate_tab3, t3_results, t3_sql)

            cur.close(); conn.close()
            elapsed = time.time() - t_total
            self.after(0, self.log, f"All tabs complete in {elapsed:.1f}s.")
            self.after(0, lambda: self.lbl_status.config(
                text=f"✓ All tabs loaded ({elapsed:.1f}s)", foreground="green"))

        except Exception as e:
            tb = traceback.format_exc()
            self.after(0, self.log, f"ERROR: {e}")
            self.after(0, self.log, tb)
            self.after(0, self._set_tab_status, self.lbl_tab2_status, "Error", "red")
            self.after(0, self._set_tab_status, self.lbl_tab3_status, "Error", "red")
            self.after(0, lambda: self.lbl_status.config(text="✗ Error — see log", foreground="red"))
            self.after(0, lambda: messagebox.showerror("Error",
                f"Failed:\n{e}\n\nSee log for details."))

    # ── Populate helpers ──
    def _populate_tab2(self, results, sql_text):
        self._fill_tree(self.tree2, results)
        self._fill_sql(self.sql2, sql_text)
        n = len(results)
        self._set_tab_status(self.lbl_tab2_status,
                             f"{n} completions" if n else "No results", "green" if n else "orange")

    def _populate_tab3(self, results, sql_text):
        self._fill_tree(self.tree3, results)
        self._fill_sql(self.sql3, sql_text)
        n = len(results)
        self._set_tab_status(self.lbl_tab3_status,
                             f"{n} completions" if n else "No perf data found", "green" if n else "orange")

    def _fill_tree(self, tree, results):
        for iid in tree.get_children():
            tree.delete(iid)
        if not results:
            return
        cols = list(tree["columns"])
        for d in results:
            vals = ["" if d.get(c) is None else str(d.get(c,"")) for c in cols]
            tree.insert("", "end", values=vals)
        autofit_columns(tree)

    def _fill_sql(self, sql_box, text):
        sql_box.config(state="normal")
        sql_box.delete("1.0","end")
        sql_box.insert("end", text.strip())
        sql_box.config(state="disabled")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = WellDataViewer()
    app.mainloop()