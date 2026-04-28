#Help: Periodic Project Review using EKPSPP data
#!/usr/bin/env python3
"""
Well Data Viewer — EKPSPP Oracle Database
Tab 1: Well Input — 10-digit API entry, validation, deduplication
Tab 2: Basic Well Data — well name, field, API, type, status, etc.
Tab 3: Top Perf Depths — top/bottom perf MD & TVD (interpolated from survey)
Tab 4: Monthly Prod & Inj — 5-year history with chart per well
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re, threading, traceback, time
from collections import defaultdict

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
        try: ora.init_oracle_client()
        except ora.ProgrammingError: pass
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
            if cw > mx: mx = cw
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
    if not survey_pts or md_val is None: return None
    if md_val <= survey_pts[0][0]: return survey_pts[0][1]
    if md_val >= survey_pts[-1][0]: return survey_pts[-1][1]
    for i in range(len(survey_pts) - 1):
        md1, tvd1 = survey_pts[i]
        md2, tvd2 = survey_pts[i + 1]
        if md1 <= md_val <= md2:
            if md2 == md1: return tvd1
            return round(tvd1 + (md_val - md1) / (md2 - md1) * (tvd2 - tvd1), 1)
    return None


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------
def step1_api14_lookup(cur, api10_list):
    like = " OR ".join(f"API_NO14 LIKE '{a}%'" for a in api10_list)
    sql = f"SELECT API_NO14, WELL_ID, WELLBORE_ID, WELLCOMP_NAME FROM ODS.BI_WELLCOMP_V WHERE {like}"
    cur.execute(sql)
    return cur.fetchall(), sql


def fetch_tab2(cur, api14_list, api10_from_14, log):
    all_sql = []
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

    t = time.time()
    w3b = _chunked_in("PID", api14_list)
    sql3b = f"SELECT PID, MAX(TEAM) AS TEAM, MAX(SECTOR) AS SECTOR FROM DSS.COMPMASTER WHERE {w3b} GROUP BY PID"
    all_sql.append(sql3b)
    cur.execute(sql3b)
    cm_map = {str(r[0]): {"TEAM":r[1],"SECTOR":r[2]} for r in cur.fetchall()}
    log(f"Tab2-COMPMASTER: {len(cm_map)} rows ({time.time()-t:.1f}s)")

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
    all_sql = []
    well_ids = list(set(v["well_id"] for v in api14_wellid_map.values() if v.get("well_id")))
    api14s = list(api14_wellid_map.keys())
    if not well_ids:
        log("Tab3: No WELL_IDs — skipping.")
        return [], "-- No WELL_IDs"

    t = time.time()
    w_perf = _chunked_in("P.WELL_ID", well_ids)
    sql_perf = f"""SELECT P.WELL_ID, P.WELLBORE_ID, P.MD_TOP_SHOT, P.MD_BOTTOM_SHOT,
    TO_CHAR(P.DATE_INTERVAL_SHOT,'YYYY-MM-DD') AS SHOT_DATE, P.INTERVAL_TYPE
FROM EDM.CD_PERF_INTERVAL_X P WHERE {w_perf} ORDER BY P.WELL_ID, P.MD_TOP_SHOT"""
    all_sql.append(sql_perf)
    cur.execute(sql_perf)
    perf_rows = cur.fetchall()
    perf_cols = [d[0] for d in cur.description]
    log(f"Tab3-Perfs: {len(perf_rows)} intervals ({time.time()-t:.1f}s)")

    t = time.time()
    w_svy = _chunked_in("S.PID", api14s)
    sql_svy = f"SELECT S.PID, S.MD, S.TVD FROM DSS.SURVEY S WHERE {w_svy} ORDER BY S.PID, S.MD"
    all_sql.append(sql_svy)
    cur.execute(sql_svy)
    svy_rows = cur.fetchall()
    log(f"Tab3-Survey: {len(svy_rows)} stations ({time.time()-t:.1f}s)")

    svy_map = {}
    for pid, md, tvd in svy_rows:
        svy_map.setdefault(str(pid), []).append((float(md), float(tvd)))

    wb_to_api14 = {}
    for a14, info in api14_wellid_map.items():
        key = (info.get("well_id",""), info.get("wellbore_id",""))
        wb_to_api14[key] = a14

    perf_groups = defaultdict(list)
    for row in perf_rows:
        d = dict(zip(perf_cols, row))
        key = (str(d["WELL_ID"]).strip(), str(d["WELLBORE_ID"]).strip())
        top, btm = d["MD_TOP_SHOT"], d["MD_BOTTOM_SHOT"]
        if top is not None and btm is not None:
            perf_groups[key].append((float(top), float(btm), d.get("SHOT_DATE",""), d.get("INTERVAL_TYPE","")))

    results = []
    for (wid, wbid), intervals in perf_groups.items():
        api14 = wb_to_api14.get((wid, wbid), "")
        if not api14:
            for a14, info in api14_wellid_map.items():
                if info.get("well_id","") == wid: api14 = a14; break
        api10 = api14[:10] if api14 else ""
        well_name = api14_wellid_map.get(api14, {}).get("well_name", "")
        all_tops = [i[0] for i in intervals]; all_btms = [i[1] for i in intervals]
        top_md = min(all_tops) if all_tops else None
        btm_md = max(all_btms) if all_btms else None
        survey = svy_map.get(api14, [])
        if survey:
            top_tvd = _interp_tvd(top_md, survey); btm_tvd = _interp_tvd(btm_md, survey)
            tvd_method = "SURVEY"
        else:
            top_tvd = top_md; btm_tvd = btm_md; tvd_method = "MD≈TVD (no survey)"
        dates = [i[2] for i in intervals if i[2]]
        types = list(set(i[3] for i in intervals if i[3]))
        results.append({
            "WELL_NAME": well_name, "API_10": api10, "API_14": api14,
            "TOP_PERF_MD": f"{top_md:.1f}" if top_md is not None else "",
            "BTM_PERF_MD": f"{btm_md:.1f}" if btm_md is not None else "",
            "TOP_PERF_TVD": f"{top_tvd:.1f}" if top_tvd is not None else "",
            "BTM_PERF_TVD": f"{btm_tvd:.1f}" if btm_tvd is not None else "",
            "TVD_METHOD": tvd_method, "N_INTERVALS": str(len(intervals)),
            "INTERVAL_TYPES": ", ".join(types),
            "EARLIEST_SHOT": min(dates) if dates else "",
            "LATEST_SHOT": max(dates) if dates else "",
        })
    results.sort(key=lambda d: (d.get("API_10",""), d.get("API_14","")))
    return results, "\n\n".join(all_sql)


def fetch_tab4(cur, api14_list, api14_name_map, log):
    """Tab 4: Monthly Production & Injection — 5 years from DSS.MONTHLY_VOLUMES.
    Uses PTYPE='COMP' and CD rates. Returns (rows_list_of_dicts, sql_text)."""
    t = time.time()
    w = _chunked_in("MV.PID", api14_list)
    sql = f"""SELECT MV.PID AS API_14, MV.NAME AS WELL_NAME,
    TO_CHAR(MV.PROD_INJ_DATE,'YYYY-MM-DD') AS PROD_DATE,
    MV.OIL_PROD, MV.WATER_PROD, MV.GAS_PROD,
    MV.CDOIL_PROD, MV.CDWAT_PROD, MV.CDGAS_PROD,
    MV.WATER_INJ, MV.DISP_WATER_INJ, MV.GAS_INJ, MV.STEAM_INJ,
    MV.CDWAT_INJ, MV.CDDISPWAT_INJ, MV.CDGAS_INJ, MV.CDSTEAM_INJ,
    MV.DAYS_PROD, MV.DAYS_INJECT
FROM DSS.MONTHLY_VOLUMES MV
WHERE MV.PTYPE = 'COMP' AND {w}
  AND MV.PROD_INJ_DATE >= ADD_MONTHS(SYSDATE, -60)
ORDER BY MV.PID, MV.PROD_INJ_DATE"""
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    log(f"Tab4: {len(rows)} monthly rows ({time.time()-t:.1f}s)")

    results = []
    for row in rows:
        d = dict(zip(cols, row))
        a14 = str(d.get("API_14",""))
        d["API_10"] = a14[:10]
        # Fill well name from map if missing
        if not d.get("WELL_NAME"):
            d["WELL_NAME"] = api14_name_map.get(a14, "")
        results.append(d)
    return results, sql


def fetch_tab5(cur, api14_list, api14_name_map, log):
    """Tab 5: Daily Injection & Pressure — 60 days from HMR.DAILY_INJECTION_DATA.
    Columns: API14, well name, date, calculated rate, accumulated volume,
    wellhead pressure, injection pressure, upstream/downstream pressure,
    pressure ratio, choke size, hours, 7-day averages, comp type/status."""
    t = time.time()
    w = _chunked_in("D.API_NO14", api14_list)
    sql = f"""SELECT D.API_NO14 AS API_14, D.AUTOMATION_NAME AS WELL_NAME,
    TO_CHAR(D.INJ_DATE, 'YYYY-MM-DD') AS INJ_DATE,
    ROUND(D.CALCULATED_RATE, 2) AS CALC_RATE,
    ROUND(D.ACCUM_VOL, 2) AS ACCUM_VOL,
    ROUND(D.WELL_HEAD_PSI, 1) AS WH_PRESS,
    ROUND(D.WELL_INJ_PRESS, 1) AS INJ_PRESS,
    ROUND(D.UPSTREAM_PRESS, 1) AS UP_PRESS,
    ROUND(D.DOWNSTREAM_PRESS, 1) AS DN_PRESS,
    ROUND(D.PRESSURE_RATIO, 4) AS PRESS_RATIO,
    D.CHOKE_SIZE,
    ROUND(D.HOURS, 1) AS HOURS,
    D.CURR_COMP_TYPE AS COMP_TYPE,
    D.CURR_COMP_STATUS AS STATUS,
    ROUND(D.INJ_RATE_7D_AVG, 2) AS RATE_7D_AVG,
    ROUND(D.UPSTREAM_PRESS_7D_AVG, 1) AS UP_PRESS_7D,
    ROUND(D.DOWNSTREAM_PRESS_7D_AVG, 1) AS DN_PRESS_7D
FROM HMR.DAILY_INJECTION_DATA D
WHERE {w}
  AND D.INJ_DATE >= SYSDATE - 60
ORDER BY D.API_NO14, D.INJ_DATE"""
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    log(f"Tab5: {len(rows)} daily rows ({time.time()-t:.1f}s)")

    results = []
    for row in rows:
        d = dict(zip(cols, row))
        a14 = str(d.get("API_14", ""))
        d["API_10"] = a14[:10]
        if not d.get("WELL_NAME"):
            d["WELL_NAME"] = api14_name_map.get(a14, "")
        results.append(d)
    return results, sql


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class WellDataViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Well Data Viewer — EKPSPP")
        self.geometry("1500x850")
        self.minsize(1000, 600)
        self.validated_apis = []
        self.tab4_data = []  # store for chart

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
        self._build_tab4()
        self._build_tab5()

        self.log("Application started.")
        self.after(200, self._detect_driver)
        if DEFAULT_TEST_APIS.strip():
            self.after(500, self._validate_and_fetch)

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

    def _make_tree_tab(self, parent, cols, hm, sql_label="SQL Query Used"):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", padx=8, pady=6)
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0,4))
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            tree.heading(c, text=hm.get(c, c),
                         command=lambda _c=c, _t=tree: self._sort_tree(_t, _c))
            tree.column(c, width=90, minwidth=50)
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
        sql_box = scrolledtext.ScrolledText(sf, height=3, font=("Consolas",9),
                                             state="disabled", wrap="word")
        sql_box.pack(fill="x")
        return toolbar, tree, sql_box

    def _sort_tree(self, tree, col):
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        try: data.sort(key=lambda t: float(t[0]) if t[0] else 0)
        except ValueError: data.sort(key=lambda t: t[0].lower() if t[0] else "")
        for i, (_, k) in enumerate(data): tree.move(k, "", i)

    # ── TAB 1 ──
    def _build_tab1(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Well Input  ")
        ttk.Label(tab, text="Enter 10-digit API numbers (one per line). "
                  "Click 'Validate & Fetch All' to query all data tabs.",
                  wraplength=900, justify="left").pack(anchor="w", padx=12, pady=(10,4))
        ft = ttk.Frame(tab)
        ft.pack(fill="both", expand=True, padx=12, pady=4)
        self.api_text = scrolledtext.ScrolledText(ft, width=40, height=18, font=("Consolas",11))
        self.api_text.pack(side="left", fill="both", expand=True)
        if DEFAULT_TEST_APIS.strip(): self.api_text.insert("1.0", DEFAULT_TEST_APIS.strip())
        side = ttk.LabelFrame(ft, text="Validation Summary", padding=10)
        side.pack(side="right", fill="y", padx=(10,0))
        self.lbl_total   = ttk.Label(side, text="Lines entered: 0")
        self.lbl_valid   = ttk.Label(side, text="Valid APIs: 0")
        self.lbl_dupes   = ttk.Label(side, text="Duplicates removed: 0")
        self.lbl_invalid = ttk.Label(side, text="Invalid lines: 0")
        for l in (self.lbl_total, self.lbl_valid, self.lbl_dupes, self.lbl_invalid):
            l.pack(anchor="w", pady=2)
        self.invalid_detail = scrolledtext.ScrolledText(side, width=30, height=6,
                                                         font=("Consolas",9), state="disabled")
        self.invalid_detail.pack(fill="both", expand=True, pady=(6,0))
        bf = ttk.Frame(tab)
        bf.pack(fill="x", padx=12, pady=(4,10))
        ttk.Button(bf, text="Validate & Fetch All",
                   command=self._validate_and_fetch).pack(side="left", padx=4)
        ttk.Button(bf, text="Clear", command=self._clear_input).pack(side="left", padx=4)
        ttk.Button(bf, text="Paste from Clipboard",
                   command=lambda: self._paste_clipboard()).pack(side="left", padx=4)
        ttk.Button(bf, text="Test Connection",
                   command=self._test_connection).pack(side="left", padx=12)
        self.lbl_status = ttk.Label(bf, text="", foreground="green")
        self.lbl_status.pack(side="left", padx=12)

    def _paste_clipboard(self):
        try: self.api_text.insert("end", self.clipboard_get())
        except tk.TclError: pass

    def _clear_input(self):
        self.api_text.delete("1.0","end"); self.validated_apis.clear()
        for l in (self.lbl_total, self.lbl_valid, self.lbl_dupes, self.lbl_invalid):
            l.config(text=l.cget("text").split(":")[0]+": 0")
        self.lbl_status.config(text="")
        self.invalid_detail.config(state="normal"); self.invalid_detail.delete("1.0","end")
        self.invalid_detail.config(state="disabled")

    def _validate_and_fetch(self):
        raw = self.api_text.get("1.0","end").strip()
        if not raw: messagebox.showinfo("Input","Enter at least one API."); return
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
        self.invalid_detail.config(state="normal"); self.invalid_detail.delete("1.0","end")
        if invalid: self.invalid_detail.insert("end","\n".join(invalid))
        self.invalid_detail.config(state="disabled")
        self.log(f"Validated {len(valid)} unique APIs, {dupes} dupes, {len(invalid)} invalid.")
        if not valid: self.lbl_status.config(text="No valid APIs.", foreground="red"); return
        self.lbl_status.config(text=f"✓ {len(valid)} APIs — fetching all tabs…", foreground="blue")
        for lbl in (self.lbl_tab2_status, self.lbl_tab3_status, self.lbl_tab4_status, self.lbl_tab5_status):
            lbl.config(text="Queued…", foreground="blue")
        self.update_idletasks()
        threading.Thread(target=self._fetch_all_tabs, daemon=True).start()

    def _test_connection(self):
        self.log("Testing…"); self.lbl_status.config(text="Testing…", foreground="blue")
        self.update_idletasks()
        def w():
            t0=time.time()
            try:
                c=get_oracle_connection(); cur=c.cursor()
                cur.execute("SELECT 1 FROM DUAL"); cur.fetchone()
                e=time.time()-t0; cur.close(); c.close()
                self.after(0,self.log,f"OK ({e:.1f}s)")
                self.after(0,lambda:self.lbl_status.config(text=f"✓ Connected ({e:.1f}s)",foreground="green"))
            except Exception as ex:
                self.after(0,self.log,f"FAILED: {ex}")
                self.after(0,lambda:self.lbl_status.config(text="✗ Failed",foreground="red"))
        threading.Thread(target=w,daemon=True).start()

    # ── TAB 2 ──
    def _build_tab2(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Basic Well Data  ")
        cols = ("WELL_NAME","API_10","API_14","FIELD","AREA","COMP_TYPE","STATUS",
                "STATUS_DATE","RESERVOIR","LIFT_METHOD","TEAM","SECTOR",
                "SPUD_DATE","COMPLETION_DATE","FIRST_PROD_DATE",
                "SECTION","TOWNSHIP","RANGE_NO","LATITUDE","LONGITUDE")
        hm = {"WELL_NAME":"Well Name","API_10":"API 10","API_14":"API 14","FIELD":"Field",
              "AREA":"Area","COMP_TYPE":"Comp Type","STATUS":"Status","STATUS_DATE":"Status Date",
              "RESERVOIR":"Reservoir","LIFT_METHOD":"Lift Method","TEAM":"Team","SECTOR":"Sector",
              "SPUD_DATE":"Spud Date","COMPLETION_DATE":"Completion Date",
              "FIRST_PROD_DATE":"First Prod Date","SECTION":"Section","TOWNSHIP":"Township",
              "RANGE_NO":"Range","LATITUDE":"Latitude","LONGITUDE":"Longitude"}
        toolbar, self.tree2, self.sql2 = self._make_tree_tab(tab, cols, hm)
        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree2, self)).pack(side="left", padx=4)
        self.lbl_tab2_status = ttk.Label(toolbar, text=""); self.lbl_tab2_status.pack(side="right", padx=8)

    # ── TAB 3 ──
    def _build_tab3(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Top Perf Depths  ")
        cols = ("WELL_NAME","API_10","API_14","TOP_PERF_MD","BTM_PERF_MD",
                "TOP_PERF_TVD","BTM_PERF_TVD","TVD_METHOD","N_INTERVALS",
                "INTERVAL_TYPES","EARLIEST_SHOT","LATEST_SHOT")
        hm = {"WELL_NAME":"Well Name","API_10":"API 10","API_14":"API 14",
              "TOP_PERF_MD":"Top Perf MD","BTM_PERF_MD":"Btm Perf MD",
              "TOP_PERF_TVD":"Top Perf TVD","BTM_PERF_TVD":"Btm Perf TVD",
              "TVD_METHOD":"TVD Method","N_INTERVALS":"# Intervals",
              "INTERVAL_TYPES":"Interval Types","EARLIEST_SHOT":"Earliest Shot",
              "LATEST_SHOT":"Latest Shot"}
        toolbar, self.tree3, self.sql3 = self._make_tree_tab(tab, cols, hm, "SQL (Perf + Survey)")
        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree3, self)).pack(side="left", padx=4)
        self.lbl_tab3_status = ttk.Label(toolbar, text=""); self.lbl_tab3_status.pack(side="right", padx=8)

    # ── TAB 4 ──
    def _build_tab4(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Monthly Prod & Inj  ")

        # Top: toolbar
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill="x", padx=8, pady=6)
        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree4, self)).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Show Chart",
                   command=self._show_chart).pack(side="left", padx=4)

        ttk.Label(toolbar, text="  Chart well:").pack(side="left", padx=(12,4))
        self.chart_well_var = tk.StringVar(value="ALL")
        self.chart_well_combo = ttk.Combobox(toolbar, textvariable=self.chart_well_var,
                                              width=35, state="readonly")
        self.chart_well_combo.pack(side="left", padx=4)

        self.lbl_tab4_status = ttk.Label(toolbar, text="")
        self.lbl_tab4_status.pack(side="right", padx=8)

        # Middle: PanedWindow for table + chart
        pane = ttk.PanedWindow(tab, orient="vertical")
        pane.pack(fill="both", expand=True, padx=8, pady=(0,4))

        # Tree
        tree_frame = ttk.Frame(pane)
        pane.add(tree_frame, weight=3)

        cols = ("WELL_NAME","API_10","API_14","PROD_DATE",
                "CDOIL_PROD","CDWAT_PROD","CDGAS_PROD",
                "OIL_PROD","WATER_PROD","GAS_PROD",
                "CDWAT_INJ","CDDISPWAT_INJ","CDGAS_INJ","CDSTEAM_INJ",
                "WATER_INJ","DISP_WATER_INJ","GAS_INJ","STEAM_INJ",
                "DAYS_PROD","DAYS_INJECT")
        hm = {"WELL_NAME":"Well Name","API_10":"API 10","API_14":"API 14",
              "PROD_DATE":"Date","CDOIL_PROD":"Oil CD (BOPD)","CDWAT_PROD":"Water CD (BWPD)",
              "CDGAS_PROD":"Gas CD (MCFD)","OIL_PROD":"Oil Vol (BBL)","WATER_PROD":"Water Vol (BBL)",
              "GAS_PROD":"Gas Vol (MCF)","CDWAT_INJ":"WI CD (BWIPD)","CDDISPWAT_INJ":"DI CD (BWIPD)",
              "CDGAS_INJ":"GI CD (MCFIPD)","CDSTEAM_INJ":"Steam CD (BWE/D)",
              "WATER_INJ":"WI Vol","DISP_WATER_INJ":"DI Vol","GAS_INJ":"GI Vol",
              "STEAM_INJ":"Steam Vol","DAYS_PROD":"Days Prod","DAYS_INJECT":"Days Inj"}
        self.tree4 = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            self.tree4.heading(c, text=hm.get(c, c),
                               command=lambda _c=c: self._sort_tree(self.tree4, _c))
            self.tree4.column(c, width=85, minwidth=50)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree4.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree4.xview)
        self.tree4.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree4.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Chart frame
        self.chart_frame = ttk.LabelFrame(pane, text="Production & Injection Chart", padding=4)
        pane.add(self.chart_frame, weight=2)

        # SQL
        sf = ttk.LabelFrame(tab, text="SQL Query Used", padding=4)
        sf.pack(fill="x", padx=8, pady=(0,4))
        self.sql4 = scrolledtext.ScrolledText(sf, height=3, font=("Consolas",9),
                                               state="disabled", wrap="word")
        self.sql4.pack(fill="x")

    def _show_chart(self):
        """Render matplotlib chart in the chart_frame.
        - Specific well selected → show only that well's data.
        - ALL selected → aggregate (sum) all wells per month into one line.
        """
        if not self.tab4_data:
            messagebox.showinfo("No Data", "No production/injection data to chart.")
            return

        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from datetime import datetime
        except ImportError:
            messagebox.showerror("Missing Library",
                                 "matplotlib is required for charts.\n"
                                 "Install with: pip install matplotlib")
            return

        # Clear previous chart
        for w in self.chart_frame.winfo_children():
            w.destroy()

        selected_well = self.chart_well_var.get()

        # ── Build chart series ──
        _fv = lambda d, k: float(d.get(k, 0) or 0)

        if selected_well and selected_well != "ALL":
            # Single well selected
            api14 = selected_well.split("(")[-1].rstrip(")")
            data = [d for d in self.tab4_data if str(d.get("API_14","")) == api14]
            chart_title = selected_well
            # Sort by date
            data.sort(key=lambda d: d.get("PROD_DATE",""))
            dates = [datetime.strptime(d["PROD_DATE"], "%Y-%m-%d") for d in data if d.get("PROD_DATE")]
            oil  = [_fv(d,"CDOIL_PROD") for d in data if d.get("PROD_DATE")]
            wat  = [_fv(d,"CDWAT_PROD") for d in data if d.get("PROD_DATE")]
            gas  = [_fv(d,"CDGAS_PROD") for d in data if d.get("PROD_DATE")]
            wi   = [_fv(d,"CDWAT_INJ") for d in data if d.get("PROD_DATE")]
            di   = [_fv(d,"CDDISPWAT_INJ") for d in data if d.get("PROD_DATE")]
            si   = [_fv(d,"CDSTEAM_INJ") for d in data if d.get("PROD_DATE")]
            gi   = [_fv(d,"CDGAS_INJ") for d in data if d.get("PROD_DATE")]
        else:
            # ALL → sum across all wells per month
            chart_title = "All Wells (Summed)"
            monthly = defaultdict(lambda: {"oil":0,"wat":0,"gas":0,"wi":0,"di":0,"si":0,"gi":0})
            for d in self.tab4_data:
                dt = d.get("PROD_DATE","")
                if not dt: continue
                m = monthly[dt]
                m["oil"] += _fv(d,"CDOIL_PROD")
                m["wat"] += _fv(d,"CDWAT_PROD")
                m["gas"] += _fv(d,"CDGAS_PROD")
                m["wi"]  += _fv(d,"CDWAT_INJ")
                m["di"]  += _fv(d,"CDDISPWAT_INJ")
                m["si"]  += _fv(d,"CDSTEAM_INJ")
                m["gi"]  += _fv(d,"CDGAS_INJ")
            sorted_months = sorted(monthly.keys())
            dates = [datetime.strptime(dt, "%Y-%m-%d") for dt in sorted_months]
            oil  = [monthly[dt]["oil"] for dt in sorted_months]
            wat  = [monthly[dt]["wat"] for dt in sorted_months]
            gas  = [monthly[dt]["gas"] for dt in sorted_months]
            wi   = [monthly[dt]["wi"]  for dt in sorted_months]
            di   = [monthly[dt]["di"]  for dt in sorted_months]
            si   = [monthly[dt]["si"]  for dt in sorted_months]
            gi   = [monthly[dt]["gi"]  for dt in sorted_months]

        if not dates:
            ttk.Label(self.chart_frame, text="No data for selected well.").pack()
            return

        has_prod = any(v > 0 for v in oil) or any(v > 0 for v in wat) or any(v > 0 for v in gas)
        has_inj  = any(v > 0 for v in wi) or any(v > 0 for v in di) or any(v > 0 for v in si) or any(v > 0 for v in gi)

        n_plots = (1 if has_prod else 0) + (1 if has_inj else 0)
        if n_plots == 0: n_plots = 1

        fig = Figure(figsize=(12, 4.5), dpi=90)
        fig.suptitle(chart_title, fontsize=11, fontweight="bold")
        plot_idx = 1

        # ── Production subplot ──
        if has_prod or not has_inj:
            ax1 = fig.add_subplot(1, n_plots, plot_idx); plot_idx += 1
            ax1.set_title("Production (CD Rates)", fontsize=9)
            ax1.set_ylabel("BOPD / BWPD"); ax1.set_xlabel("Date")
            if any(v > 0 for v in oil):
                ax1.plot(dates, oil, '-o', markersize=3, color='green', linewidth=1.5, label='Oil (BOPD)')
            if any(v > 0 for v in wat):
                ax1.plot(dates, wat, '-s', markersize=3, color='blue', linewidth=1.5, label='Water (BWPD)')
            ax1.legend(loc="upper left", fontsize=8)
            if any(v > 0 for v in gas):
                ax1_gas = ax1.twinx()
                ax1_gas.set_ylabel("Gas (MCFD)", color="red", alpha=0.7)
                ax1_gas.plot(dates, gas, '--^', markersize=2, color='red', alpha=0.6, linewidth=1, label='Gas (MCFD)')
                ax1_gas.legend(loc="upper right", fontsize=8)
            ax1.tick_params(axis='x', rotation=45, labelsize=7)
            ax1.grid(True, alpha=0.3)

        # ── Injection subplot ──
        if has_inj:
            ax2 = fig.add_subplot(1, n_plots, plot_idx)
            ax2.set_title("Injection (CD Rates)", fontsize=9)
            ax2.set_ylabel("BWIPD / BWE/D"); ax2.set_xlabel("Date")
            if any(v > 0 for v in wi):
                ax2.plot(dates, wi, '-o', markersize=3, color='blue', linewidth=1.5, label='Water Inj (BWIPD)')
            if any(v > 0 for v in di):
                ax2.plot(dates, di, '-s', markersize=3, color='teal', linewidth=1.5, label='Disp Water (BWIPD)')
            if any(v > 0 for v in si):
                ax2.plot(dates, si, '-^', markersize=3, color='orange', linewidth=1.5, label='Steam (BWE/D)')
            if any(v > 0 for v in gi):
                ax2.plot(dates, gi, '-d', markersize=3, color='purple', linewidth=1.5, label='Gas Inj (MCFIPD)')
            ax2.legend(loc="upper left", fontsize=8)
            ax2.tick_params(axis='x', rotation=45, labelsize=7)
            ax2.grid(True, alpha=0.3)

        fig.tight_layout(rect=[0, 0, 1, 0.94])
        canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # ── TAB 5 — Daily Injection & Pressure ──
    def _build_tab5(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Daily Inj & Pressure  ")

        cols = ("WELL_NAME","API_10","API_14","INJ_DATE",
                "CALC_RATE","ACCUM_VOL",
                "WH_PRESS","INJ_PRESS","UP_PRESS","DN_PRESS",
                "PRESS_RATIO","CHOKE_SIZE","HOURS",
                "COMP_TYPE","STATUS",
                "RATE_7D_AVG","UP_PRESS_7D","DN_PRESS_7D")
        hm = {"WELL_NAME":"Well Name","API_10":"API 10","API_14":"API 14",
              "INJ_DATE":"Date","CALC_RATE":"Calc Rate (BPD)",
              "ACCUM_VOL":"Accum Vol (BBL)",
              "WH_PRESS":"WH Press (psi)","INJ_PRESS":"Inj Press (psi)",
              "UP_PRESS":"Upstream (psi)","DN_PRESS":"Downstream (psi)",
              "PRESS_RATIO":"Press Ratio","CHOKE_SIZE":"Choke",
              "HOURS":"Hours","COMP_TYPE":"Comp Type","STATUS":"Status",
              "RATE_7D_AVG":"Rate 7D Avg","UP_PRESS_7D":"Up Press 7D",
              "DN_PRESS_7D":"Dn Press 7D"}

        toolbar, self.tree5, self.sql5 = self._make_tree_tab(
            tab, cols, hm, sql_label="SQL Query (HMR.DAILY_INJECTION_DATA, 60 days)")

        ttk.Button(toolbar, text="Copy to Clipboard",
                   command=lambda: copy_tree_to_clipboard(self.tree5, self)).pack(side="left", padx=4)

        ttk.Label(toolbar, text="  Source: HMR.DAILY_INJECTION_DATA (60 days)",
                  foreground="gray", font=("TkDefaultFont",8)).pack(side="left", padx=12)

        self.lbl_tab5_status = ttk.Label(toolbar, text="")
        self.lbl_tab5_status.pack(side="right", padx=8)

    # ── FETCH ALL ──
    def _fetch_all_tabs(self):
        t_total = time.time()
        try:
            self.after(0, self.log, f"Connecting for {len(self.validated_apis)} APIs…")
            conn = get_oracle_connection()
            cur = conn.cursor()
            self.after(0, self.log, "Connected.")

            # Step 1
            t = time.time()
            for lbl in (self.lbl_tab2_status, self.lbl_tab3_status, self.lbl_tab4_status, self.lbl_tab5_status):
                self.after(0, lambda l=lbl: l.config(text="Step 1: API14 lookup…", foreground="blue"))
            rows1, sql1 = step1_api14_lookup(cur, self.validated_apis)
            api14_list = list(set(str(r[0]) for r in rows1))
            api10_from_14 = list(set(str(r[0])[:10] for r in rows1))

            api14_wellid_map = {}
            api14_name_map = {}
            for r in rows1:
                a14 = str(r[0])
                api14_wellid_map[a14] = {
                    "well_id": str(r[1]).strip() if r[1] else "",
                    "wellbore_id": str(r[2]).strip() if r[2] else "",
                    "well_name": str(r[3]).strip() if r[3] else "",
                }
                api14_name_map[a14] = str(r[3]).strip() if r[3] else ""
            self.after(0, self.log, f"Step 1: {len(api14_list)} API14s ({time.time()-t:.1f}s)")

            if not api14_list:
                for lbl in (self.lbl_tab2_status, self.lbl_tab3_status, self.lbl_tab4_status, self.lbl_tab5_status):
                    self.after(0, lambda l=lbl: l.config(text="No completions found.", foreground="orange"))
                self.after(0, lambda: self.lbl_status.config(text="No completions.", foreground="orange"))
                cur.close(); conn.close(); return

            # Tab 2
            self.after(0, lambda: self.lbl_tab2_status.config(text="Fetching…", foreground="blue"))
            t2_res, t2_sql = fetch_tab2(cur, api14_list, api10_from_14,
                                         lambda m: self.after(0, self.log, m))
            self.after(0, self._populate_generic, self.tree2, t2_res)
            self.after(0, self._fill_sql, self.sql2, f"-- Step 1\n{sql1}\n\n{t2_sql}")
            n2 = len(t2_res)
            self.after(0, lambda: self.lbl_tab2_status.config(
                text=f"{n2} completions" if n2 else "No results", foreground="green" if n2 else "orange"))

            # Tab 3
            self.after(0, lambda: self.lbl_tab3_status.config(text="Fetching…", foreground="blue"))
            for d in t2_res:
                a14 = str(d.get("API_14",""))
                if a14 in api14_wellid_map:
                    api14_wellid_map[a14]["well_name"] = d.get("WELL_NAME","")
            t3_res, t3_sql = fetch_tab3(cur, api14_wellid_map,
                                         lambda m: self.after(0, self.log, m))
            self.after(0, self._populate_generic, self.tree3, t3_res)
            self.after(0, self._fill_sql, self.sql3, t3_sql)
            n3 = len(t3_res)
            self.after(0, lambda: self.lbl_tab3_status.config(
                text=f"{n3} completions" if n3 else "No perf data", foreground="green" if n3 else "orange"))

            # Tab 4
            self.after(0, lambda: self.lbl_tab4_status.config(text="Fetching…", foreground="blue"))
            t4_res, t4_sql = fetch_tab4(cur, api14_list, api14_name_map,
                                         lambda m: self.after(0, self.log, m))
            self.tab4_data = t4_res
            self.after(0, self._populate_generic, self.tree4, t4_res)
            self.after(0, self._fill_sql, self.sql4, t4_sql)
            n4 = len(t4_res)
            self.after(0, lambda: self.lbl_tab4_status.config(
                text=f"{n4} monthly rows" if n4 else "No prod/inj data", foreground="green" if n4 else "orange"))

            # Update chart well selector
            well_choices = ["ALL"]
            seen_wells = set()
            for d in t4_res:
                label = f"{d.get('WELL_NAME','')} ({d.get('API_14','')})"
                if label not in seen_wells:
                    seen_wells.add(label); well_choices.append(label)
            self.after(0, lambda: self.chart_well_combo.config(values=well_choices))
            self.after(0, lambda: self.chart_well_var.set("ALL"))
            # Auto-show chart
            self.after(100, self._show_chart)

            # Tab 5
            self.after(0, lambda: self.lbl_tab5_status.config(text="Fetching…", foreground="blue"))
            t5_res, t5_sql = fetch_tab5(cur, api14_list, api14_name_map,
                                         lambda m: self.after(0, self.log, m))
            self.after(0, self._populate_generic, self.tree5, t5_res)
            self.after(0, self._fill_sql, self.sql5, t5_sql)
            n5 = len(t5_res)
            self.after(0, lambda: self.lbl_tab5_status.config(
                text=f"{n5} daily rows" if n5 else "No daily injection data (60 days)",
                foreground="green" if n5 else "orange"))

            cur.close(); conn.close()
            elapsed = time.time() - t_total
            self.after(0, self.log, f"All tabs done in {elapsed:.1f}s.")
            self.after(0, lambda: self.lbl_status.config(
                text=f"✓ All tabs loaded ({elapsed:.1f}s)", foreground="green"))

        except Exception as e:
            tb = traceback.format_exc()
            self.after(0, self.log, f"ERROR: {e}")
            self.after(0, self.log, tb)
            for lbl in (self.lbl_tab2_status, self.lbl_tab3_status, self.lbl_tab4_status, self.lbl_tab5_status):
                self.after(0, lambda l=lbl: l.config(text="Error", foreground="red"))
            self.after(0, lambda: self.lbl_status.config(text="✗ Error — see log", foreground="red"))
            self.after(0, lambda: messagebox.showerror("Error", f"Failed:\n{e}"))

    def _populate_generic(self, tree, results):
        for iid in tree.get_children(): tree.delete(iid)
        if not results: return
        cols = list(tree["columns"])
        for d in results:
            vals = ["" if d.get(c) is None else str(d.get(c,"")) for c in cols]
            tree.insert("", "end", values=vals)
        autofit_columns(tree)

    def _fill_sql(self, sql_box, text):
        sql_box.config(state="normal"); sql_box.delete("1.0","end")
        sql_box.insert("end", text.strip()); sql_box.config(state="disabled")


if __name__ == "__main__":
    app = WellDataViewer()
    app.mainloop()