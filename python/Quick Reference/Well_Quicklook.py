#Help: Single-Well Encyclopedia
"""
Well Passport — Single-Well Encyclopedia
=========================================
Enter a completion name OR API number (or both) and get a full snapshot:
  Tab 1: Completion & Wellbore Details
  Tab 2: Casing / Tubing / Perforations
  Tab 3: Current Status & Latest Well Test
  Tab 4: WRA Notes (engineer comments)
  Tab 5: 36-Month Production Chart
  Tab 6: Workover History

Connects to CRC Oracle Data Warehouse (ODW) via oracledb.

Performance notes (v2):
  - Well is resolved ONCE via cmpl_dmn; all subsequent queries use
    numeric IDs (cmpl_fac_id, well_fac_id, cmpl_dmn_key, wlbr_fac_id)
    which hit indexed columns directly — no cmpl_dmn re-scan.
  - Connection is opened once and reused across lookups.
  - WRA notes limited to last 5 years + 200-row cap.
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
        pass  # thin mode fallback
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


# ============================================================================
# SQL QUERIES
# ============================================================================

# ---------- Step 0: Resolve well -> get all IDs in one shot ---------------
# Sources:
#   cmpl_dmn (cd)            — completion dimension
#   wlbr_dmn (wd)            — wellbore dimension
#   well_dmn (wdm)           — well dimension (DOG Critical, Functional Location, KB)
#   fac_lctn_dmn (fl)        — surface coordinates + section/township/range
#   cmpl_opnl_stat_fact (osf)— on/off status + down reason (covers ALL wells incl PA)

_RESOLVE_COLS = """
    -- Keys (internal)
    cd.cmpl_fac_id,
    cd.well_fac_id,
    cd.cmpl_dmn_key,
    wd.wlbr_fac_id,

    -- Master: identity
    cd.cmpl_nme,                                -- cmpl_dmn.cmpl_nme
    cd.cmpl_state_type_desc AS state,           -- cmpl_dmn.cmpl_state_type_desc
    cd.in_svc_indc,                             -- cmpl_dmn.in_svc_indc
    cd.well_api_nbr,                            -- cmpl_dmn.well_api_nbr

    -- Master: status (latest from cmpl_opnl_stat_fact — works for ALL wells)
    osf.opnl_stat_on_indc AS status_on_off,     -- cmpl_opnl_stat_fact.opnl_stat_on_indc
    osf.opnl_stat_eftv_dttm AS last_status_eftv_date,
    osf.off_rsn_type_desc AS down_reason,       -- cmpl_opnl_stat_fact.off_rsn_type_desc

    -- Master: purpose & method
    cd.prim_purp_type_desc AS primary_purpose,  -- cmpl_dmn.prim_purp_type_desc
    cd.prim_fluid_cvyn_mthd_desc AS method,     -- cmpl_dmn.prim_fluid_cvyn_mthd_desc
    cd.prim_matl_desc AS material,              -- cmpl_dmn.prim_matl_desc

    -- Master: dates
    cd.init_inj_dte,                            -- cmpl_dmn.init_inj_dte
    cd.init_prod_dte,                           -- cmpl_dmn.init_prod_dte

    -- Master: location (well_dmn + fac_lctn_dmn)
    cd.opnl_fld,                                -- cmpl_dmn.opnl_fld
    wdm.well_nme,                               -- well_dmn.well_nme
    wdm.dog_crit_indc,                          -- well_dmn.dog_crit_indc
    wdm.fctl_locn AS functional_location,       -- well_dmn.fctl_locn
    wdm.orig_grnd_elev_qty AS kb_elevation,     -- well_dmn.orig_grnd_elev_qty
    wdm.well_datum_type_desc AS datum_type,     -- well_dmn.well_datum_type_desc
    fl.SXN_NBR,                                 -- fac_lctn_dmn.sxn_nbr
    fl.TWP,                                     -- fac_lctn_dmn.twp
    fl.RNGE,                                    -- fac_lctn_dmn.rnge
    fl.XCRD AS surface_x,                       -- fac_lctn_dmn.xcrd
    fl.YCRD AS surface_y,                       -- fac_lctn_dmn.ycrd

    -- Master: reservoir & engineering
    cd.prdu_nme AS reservoir_name,              -- cmpl_dmn.prdu_nme
    cd.engr_strg_nme,                           -- cmpl_dmn.engr_strg_nme
    cd.rsvr_engr_strg_nme,                      -- cmpl_dmn.rsvr_engr_strg_nme
    cd.fncl_fld_nme AS mgmt_plant,              -- cmpl_dmn.fncl_fld_nme

    -- Completion
    cd.strg_nme AS operational_string,          -- cmpl_dmn.strg_nme
    cd.prod_mfld_nme,                           -- cmpl_dmn.prod_mfld_nme
    cd.inj_mfld_nme,                            -- cmpl_dmn.inj_mfld_nme

    -- Wellbore
    wd.wlbr_nme AS wellbore_name,               -- wlbr_dmn.wlbr_nme
    wd.wlbr_api_suff_nbr AS wellbore_suffix,    -- wlbr_dmn.wlbr_api_suff_nbr
    wd.wlbr_incl_type_desc AS wellbore_curve_type,
    wd.total_dpth_qty AS wellbore_depth_md,     -- wlbr_dmn.total_dpth_qty
    wd.total_dpth_tvd_qty AS wellbore_depth_tvd,

    -- Extra context (used by other tabs)
    cd.cmpl_state_type_cde,
    cd.prim_purp_type_cde
"""

_RESOLVE_FROM = """
FROM dwrptg.cmpl_dmn cd
LEFT JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
LEFT JOIN dwrptg.well_dmn wdm ON cd.well_fac_id = wdm.well_fac_id
LEFT JOIN dwrptg.fac_lctn_dmn fl ON fl.fac_id = cd.well_fac_id
LEFT JOIN (
    SELECT cmpl_fac_id, opnl_stat_on_indc, opnl_stat_eftv_dttm,
           off_rsn_type_desc,
           ROW_NUMBER() OVER (PARTITION BY cmpl_fac_id
                              ORDER BY opnl_stat_eftv_dttm DESC) AS rn
    FROM dwrptg.cmpl_opnl_stat_fact
) osf ON osf.cmpl_fac_id = cd.cmpl_fac_id AND osf.rn = 1
"""

SQL_RESOLVE_BY_NAME = (
    "SELECT " + _RESOLVE_COLS + _RESOLVE_FROM +
    "WHERE cd.cmpl_nme = :well_name AND cd.actv_indc = 'Y' "
    "FETCH FIRST 1 ROW ONLY"
)

SQL_RESOLVE_BY_API = (
    "SELECT " + _RESOLVE_COLS + _RESOLVE_FROM +
    "WHERE cd.well_api_nbr = :api_nbr AND cd.actv_indc = 'Y' "
    "FETCH FIRST 1 ROW ONLY"
)

# ---------- All subsequent queries use numeric IDs, no cmpl_dmn re-scan ---

SQL_CASING_TUBING = """
SELECT
    wa.wlbr_asbly_cls_desc AS assembly_class,
    ws.item_desc,
    ws.sttr_top_md_qty AS top_md,
    ws.sttr_btm_md_qty AS btm_md,
    ws.nmnl_size_qty AS size_in,
    ws.wt_qty AS weight_ppf,
    ws.grd_octg_cde AS grade,
    ws.thd_prlf_octg_desc AS thread,
    ws.top_lthsg_unit_nme AS top_formation,
    ws.btm_lthsg_unit_nme AS btm_formation
FROM dwrptg.wlbr_asbly_sttr_fact ws
JOIN dwrptg.wlbr_asbly_dmn wa ON ws.wlbr_asbly_dmn_key = wa.wlbr_asbly_dmn_key
WHERE wa.wlbr_fac_id = :wlbr_fac_id
  AND wa.pull_dttm IS NULL
ORDER BY ws.sttr_top_md_qty
"""

SQL_PERFORATIONS = """
SELECT
    aon.ACTL_OPG_NTVL_TYPE_DESC AS perf_type,
    aon.top_md_qty AS top_md,
    aon.btm_md_qty AS btm_md,
    aon.WLBR_OPG_NTVL_STAT_DESC AS status,
    aon.top_lthsg_unit_nme AS top_formation,
    aon.btm_lthsg_unit_nme AS btm_formation,
    aon.perf_dens_qty AS shots_per_foot,
    aon.cmpl_eftv_dttm AS perf_date
FROM dwrptg.actl_wlbr_opg_ntvl_dmn aon
WHERE aon.wlbr_fac_id = :wlbr_fac_id
ORDER BY aon.top_md_qty
"""

SQL_CURRENT_STATUS = """
SELECT
    cof.off_rsn_type_cde,
    cof.off_rsn_type_desc,
    cof.off_rsn_eftv_dttm,
    ROUND(SYSDATE - cof.off_rsn_eftv_dttm, 0) AS days_down
FROM dwrptg.cmpl_off_rsn_fact cof
WHERE cof.cmpl_fac_id = :cmpl_fac_id
  AND cof.off_rsn_term_dttm IS NULL
"""

SQL_LATEST_WELL_TEST = """
SELECT
    f.prod_msmt_strt_dttm AS test_date,
    f.bopd_qty AS oil_bopd,
    ROUND(f.bopd_qty + NVL(f.bwpd_qty, 0), 1) AS gross_bfpd,
    f.bwpd_qty AS water_bwpd,
    f.prod_wtr_cut_pct AS water_cut_pct,
    f.test_temp_qty AS test_temp,
    ROUND(f.drtn_secs_qty / 3600, 1) AS test_hours,
    d.use_for_aloc_indc
FROM dwrptg.cmpl_prod_tst_fact f
JOIN dwrptg.cmpl_prod_tst_dmn d ON d.cmpl_prod_tst_dmn_key = f.cmpl_prod_tst_dmn_key
WHERE f.cmpl_fac_id = :cmpl_fac_id
  AND d.use_for_aloc_indc = 'Y'
ORDER BY f.prod_msmt_strt_dttm DESC
FETCH FIRST 5 ROWS ONLY
"""

# WRA notes: limited to last 5 years + row cap to avoid unbounded scan
SQL_WRA_NOTES = """
SELECT
    wnc.well_notes_cmnt_dte,
    wnc.well_notes_cmnt_txt
FROM dwrptg.well_notes_cmnt_tb wnc
WHERE wnc.well_fac_id = :well_fac_id
  AND wnc.well_notes_cmnt_dte >= ADD_MONTHS(SYSDATE, -60)
ORDER BY wnc.well_notes_cmnt_dte DESC
FETCH FIRST 200 ROWS ONLY
"""

SQL_MONTHLY_PROD = """
SELECT
    cmf.eftv_dttm AS prod_month,
    ROUND(cmf.aloc_oil_prod_dly_rte_qty, 1) AS oil_bopd,
    ROUND(cmf.aloc_gros_prod_dly_rte_qty, 1) AS gross_bfpd,
    ROUND(cmf.aloc_wtr_prod_dly_rte_qty, 1) AS water_bwpd,
    ROUND(CASE WHEN NVL(cmf.aloc_gros_prod_dly_rte_qty, 0) > 0
          THEN cmf.aloc_wtr_prod_dly_rte_qty / cmf.aloc_gros_prod_dly_rte_qty * 100
          ELSE NULL END, 1) AS water_cut_pct,
    ROUND(cmf.aloc_cnts_stm_inj_dly_rte_qty, 1) AS steam_inj_bspd,
    ROUND(cmf.aloc_wtr_inj_dly_rte_qty, 1) AS water_inj_bwpd,
    ROUND(cmf.aloc_cycl_stm_inj_dly_rte_qty, 1) AS cyclic_stm_bspd
FROM dwrptg.cmpl_mnly_fact cmf
WHERE cmf.cmpl_dmn_key = :cmpl_dmn_key
  AND cmf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -36)
ORDER BY cmf.eftv_dttm
"""

SQL_WORKOVERS = """
SELECT
    wo.STARTDATE,
    wo.ENDDATE,
    wo.JOBTYPE,
    wo.JOBNUMBER,
    wo.COMMNT
FROM dss.dss_work_over wo
WHERE wo.PID = :cmpl_fac_id
ORDER BY wo.STARTDATE DESC
"""


# ============================================================================
# APPLICATION
# ============================================================================

class WellPassportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Well Passport — Single-Well Encyclopedia")
        self.root.geometry("1200x800")
        self.root.minsize(1000, 600)

        self._conn = None       # persistent connection
        self._well_data = {}    # cache for completion data

        self._build_ui()

    # ---- CONNECTION --------------------------------------------------------

    def _get_conn(self):
        """Return a persistent connection, reopening if stale."""
        if self._conn is not None:
            try:
                self._conn.ping()
                return self._conn
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        self._conn = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)
        return self._conn

    # ---- UI BUILD ----------------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=5)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Completion Name:").pack(side=tk.LEFT, padx=(0, 3))
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(top, textvariable=self.name_var, width=22,
                                     font=("Consolas", 11))
        self.name_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.name_entry.bind("<Return>", lambda e: self._on_lookup())

        ttk.Label(top, text="API #:").pack(side=tk.LEFT, padx=(0, 3))
        self.api_var = tk.StringVar()
        self.api_entry = ttk.Entry(top, textvariable=self.api_var, width=18,
                                    font=("Consolas", 11))
        self.api_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.api_entry.bind("<Return>", lambda e: self._on_lookup())

        self.lookup_btn = ttk.Button(top, text="Lookup", command=self._on_lookup)
        self.lookup_btn.pack(side=tk.LEFT, padx=(0, 15))

        self.status_var = tk.StringVar(
            value="Enter a completion name or API # and click Lookup")
        ttk.Label(top, textvariable=self.status_var, foreground="gray").pack(
            side=tk.LEFT)

        # Notebook (tabs)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tab_info = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_info, text="  Completion & Wellbore  ")

        self.tab_mech = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_mech, text="  Casing / Tubing / Perfs  ")

        self.tab_status = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_status, text="  Status & Well Test  ")

        self.tab_wra = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_wra, text="  WRA Notes  ")

        self.tab_prod = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_prod, text="  Production Chart (36 mo)  ")

        self.tab_wo = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_wo, text="  Workover History  ")

    # ---- LOOKUP DISPATCH ---------------------------------------------------

    def _on_lookup(self):
        name = self.name_var.get().strip()
        api = self.api_var.get().strip()

        if not name and not api:
            messagebox.showwarning("Input",
                                   "Enter a completion name, an API number, or both.")
            return

        self.lookup_btn.config(state=tk.DISABLED)
        self.status_var.set("Resolving well...")
        threading.Thread(target=self._load_all, args=(name, api), daemon=True).start()

    @staticmethod
    def _safe_query(cur, sql, params):
        """Execute a query, returning (cols, rows).  On error return ([], [])
        and the error message so the caller can keep going."""
        try:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return cols, rows, None
        except Exception as e:
            return [], [], str(e)

    def _load_all(self, name_input, api_input):
        errors = []          # collect per-query errors, show at end
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            # ----------------------------------------------------------
            # Step 0: Resolve the well — name, API, or both
            # This step MUST succeed; if it fails, abort entirely.
            # ----------------------------------------------------------
            info_by_name = None
            info_by_api = None

            if name_input:
                cur.execute(SQL_RESOLVE_BY_NAME, {"well_name": name_input})
                cols = [d[0] for d in cur.description]
                row = cur.fetchone()
                if row:
                    info_by_name = dict(zip(cols, row))

            if api_input:
                cur.execute(SQL_RESOLVE_BY_API, {"api_nbr": api_input})
                cols = [d[0] for d in cur.description]
                row = cur.fetchone()
                if row:
                    info_by_api = dict(zip(cols, row))

            # Decide which result to use, handle mismatches
            if name_input and api_input:
                if info_by_name and info_by_api:
                    if info_by_name["CMPL_FAC_ID"] != info_by_api["CMPL_FAC_ID"]:
                        msg = (
                            f"Name '{name_input}' resolves to:\n"
                            f"  {info_by_name['CMPL_NME']}  "
                            f"(API {info_by_name['WELL_API_NBR']})\n\n"
                            f"API '{api_input}' resolves to:\n"
                            f"  {info_by_api['CMPL_NME']}  "
                            f"(API {info_by_api['WELL_API_NBR']})\n\n"
                            f"These are DIFFERENT wells.\n"
                            f"Proceeding with the completion name entry."
                        )
                        self.root.after(0, lambda m=msg: messagebox.showwarning(
                            "Mismatch \u2014 Different Wells", m))
                    info = info_by_name
                elif info_by_name:
                    info = info_by_name
                elif info_by_api:
                    info = info_by_api
                else:
                    self.root.after(0, lambda: self._show_not_found(
                        name_input or api_input))
                    return
            elif name_input:
                if not info_by_name:
                    self.root.after(0, lambda: self._show_not_found(name_input))
                    return
                info = info_by_name
            else:
                if not info_by_api:
                    self.root.after(0, lambda: self._show_not_found(api_input))
                    return
                info = info_by_api

            self._well_data = info

            # Auto-fill both fields so user can see both identifiers
            resolved_name = info.get("CMPL_NME", "")
            resolved_api = info.get("WELL_API_NBR", "")
            self.root.after(0, lambda n=resolved_name: self.name_var.set(n))
            self.root.after(0, lambda a=resolved_api or "": self.api_var.set(a))
            self.root.after(0, lambda n=resolved_name: self.status_var.set(
                f"Loading {n}..."))

            # ----------------------------------------------------------
            # Extract numeric IDs — used by ALL subsequent queries
            # ----------------------------------------------------------
            cmpl_fac_id = info["CMPL_FAC_ID"]
            well_fac_id = info["WELL_FAC_ID"]
            cmpl_dmn_key = info["CMPL_DMN_KEY"]
            wlbr_fac_id = info.get("WLBR_FAC_ID")  # may be None

            # ----------------------------------------------------------
            # Each query is wrapped individually so one failure doesn't
            # prevent the remaining tabs from loading.
            # ----------------------------------------------------------

            # Casing / tubing
            if wlbr_fac_id:
                cols_ct, rows_ct, err = self._safe_query(
                    cur, SQL_CASING_TUBING, {"wlbr_fac_id": wlbr_fac_id})
                if err:
                    errors.append(f"Casing/Tubing: {err}")

                cols_pf, rows_pf, err = self._safe_query(
                    cur, SQL_PERFORATIONS, {"wlbr_fac_id": wlbr_fac_id})
                if err:
                    errors.append(f"Perforations: {err}")
            else:
                cols_ct, rows_ct = [], []
                cols_pf, rows_pf = [], []

            # Current status
            cols_st, rows_st, err = self._safe_query(
                cur, SQL_CURRENT_STATUS, {"cmpl_fac_id": cmpl_fac_id})
            if err:
                errors.append(f"Status: {err}")

            # Latest well tests
            cols_wt, rows_wt, err = self._safe_query(
                cur, SQL_LATEST_WELL_TEST, {"cmpl_fac_id": cmpl_fac_id})
            if err:
                errors.append(f"Well Tests: {err}")

            # WRA Notes
            cols_wr, rows_wr, err = self._safe_query(
                cur, SQL_WRA_NOTES, {"well_fac_id": well_fac_id})
            if err:
                errors.append(f"WRA Notes: {err}")

            # Monthly production
            cols_mp, rows_mp, err = self._safe_query(
                cur, SQL_MONTHLY_PROD, {"cmpl_dmn_key": cmpl_dmn_key})
            if err:
                errors.append(f"Monthly Prod: {err}")

            # Workovers
            cols_wo, rows_wo, err = self._safe_query(
                cur, SQL_WORKOVERS, {"cmpl_fac_id": cmpl_fac_id})
            if err:
                errors.append(f"Workovers: {err}")

            # Dispatch to UI — always, even if some queries failed
            self.root.after(0, lambda: self._populate_all(
                resolved_name, info,
                cols_ct, rows_ct, cols_pf, rows_pf,
                cols_st, rows_st, cols_wt, rows_wt,
                cols_wr, rows_wr,
                cols_mp, rows_mp,
                cols_wo, rows_wo,
            ))

            # Show collected errors as a non-blocking warning
            if errors:
                summary = "\n\n".join(errors)
                self.root.after(0, lambda s=summary: messagebox.showwarning(
                    "Some Tabs Had Errors",
                    f"The following queries failed (other tabs still loaded):\n\n{s}"))

        except Exception as e:
            # Only reaches here if connection or resolution itself failed
            self.root.after(0, lambda msg=str(e): self._show_error(msg))

    # ---- POPULATE ----------------------------------------------------------

    def _show_not_found(self, identifier):
        self.status_var.set(f"No active completion found for '{identifier}'")
        self.lookup_btn.config(state=tk.NORMAL)

    def _show_error(self, msg):
        self.status_var.set("Error — see popup")
        self.lookup_btn.config(state=tk.NORMAL)
        messagebox.showerror("Database Error", msg)

    def _populate_all(self, well, info,
                      cols_ct, rows_ct, cols_pf, rows_pf,
                      cols_st, rows_st, cols_wt, rows_wt,
                      cols_wr, rows_wr,
                      cols_mp, rows_mp,
                      cols_wo, rows_wo):

        self.status_var.set(
            f"{well}  —  {info.get('OPNL_FLD', '')}  |  "
            f"{info.get('ENGR_STRG_NME', '')}  |  "
            f"{info.get('PRIM_PURP_TYPE_CDE', '')}")
        self.lookup_btn.config(state=tk.NORMAL)

        self._populate_info(info)
        self._populate_mechanical(cols_ct, rows_ct, cols_pf, rows_pf)
        self._populate_status(info, cols_st, rows_st, cols_wt, rows_wt)
        self._populate_wra(cols_wr, rows_wr)
        self._populate_prod_chart(cols_mp, rows_mp, well, info)
        self._populate_workovers(cols_wo, rows_wo)

    # ---- TAB 1: Completion & Wellbore --------------------------------------

    def _populate_info(self, info):
        for w in self.tab_info.winfo_children():
            w.destroy()

        # Scrollable canvas for the content
        canvas = tk.Canvas(self.tab_info, highlightthickness=0)
        vsb = ttk.Scrollbar(self.tab_info, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        frame = ttk.Frame(canvas, padding=15)
        canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Helper: format status On/Off
        status_raw = info.get("STATUS_ON_OFF", "")
        if status_raw == "Y":
            status_display = "On"
        elif status_raw == "N":
            status_display = "Off"
        else:
            status_display = status_raw or "\u2014"

        # Helper: section/township/range composite
        sxn = info.get("SXN_NBR")
        twp = info.get("TWP") or ""
        rnge = info.get("RNGE") or ""
        if sxn is not None:
            sxn_str = str(int(sxn)) if isinstance(sxn, float) else str(sxn)
            str_display = f"{sxn_str}-{twp}-{rnge}"
        else:
            str_display = "\u2014"

        # Down reason: only show text if status is Off
        down_reason = info.get("DOWN_REASON", "") or ""
        if status_display == "On":
            down_reason = ""

        # DOG Critical: Y/N -> Yes/No
        dog_raw = info.get("DOG_CRIT_INDC", "")
        dog_display = {"Y": "Yes", "N": "No"}.get(dog_raw, dog_raw or "\u2014")

        # In Service: Y/N -> Yes/No
        in_svc_raw = info.get("IN_SVC_INDC", "")
        in_svc_display = {"Y": "Yes", "N": "No"}.get(in_svc_raw, in_svc_raw or "\u2014")

        # ---- Field definitions: (label, value, source) --------------------
        fields = [
            # -- SECTION: Master --
            ("HEADER", "Master", ""),
            ("Completion Name",       info.get("CMPL_NME"),
             "cmpl_dmn.cmpl_nme"),
            ("Well Name",             info.get("WELL_NME"),
             "well_dmn.well_nme"),
            ("State",                 info.get("STATE"),
             "cmpl_dmn.cmpl_state_type_desc"),
            ("Status",                status_display,
             "cmpl_opnl_stat_fact.opnl_stat_on_indc"),
            ("Last Status Eftv Date", self._fmt_date(info.get("LAST_STATUS_EFTV_DATE")),
             "cmpl_opnl_stat_fact.opnl_stat_eftv_dttm"),
            ("In Service",            in_svc_display,
             "cmpl_dmn.in_svc_indc"),
            ("Down Reason",           down_reason,
             "cmpl_opnl_stat_fact.off_rsn_type_desc"),
            ("SEP", None, ""),
            ("Primary Purpose",       info.get("PRIMARY_PURPOSE"),
             "cmpl_dmn.prim_purp_type_desc"),
            ("Method",                info.get("METHOD"),
             "cmpl_dmn.prim_fluid_cvyn_mthd_desc"),
            ("Material",              info.get("MATERIAL"),
             "cmpl_dmn.prim_matl_desc"),
            ("SEP", None, ""),
            ("Initial Injection Date",  self._fmt_date(info.get("INIT_INJ_DTE")),
             "cmpl_dmn.init_inj_dte"),
            ("Initial Production Date", self._fmt_date(info.get("INIT_PROD_DTE")),
             "cmpl_dmn.init_prod_dte"),
            ("Operational Field",     info.get("OPNL_FLD"),
             "cmpl_dmn.opnl_fld"),
            ("DOG Critical",          dog_display,
             "well_dmn.dog_crit_indc"),
            ("Section Township Range", str_display,
             "fac_lctn_dmn.sxn_nbr / twp / rnge"),
            ("Surface X Coordinate",  self._fmt_num(info.get("SURFACE_X")),
             "fac_lctn_dmn.xcrd"),
            ("Surface Y Coordinate",  self._fmt_num(info.get("SURFACE_Y")),
             "fac_lctn_dmn.ycrd"),
            ("API #",                 info.get("WELL_API_NBR"),
             "cmpl_dmn.well_api_nbr"),
            ("Functional Location",   info.get("FUNCTIONAL_LOCATION"),
             "well_dmn.fctl_locn"),
            ("KB Elevation",          self._fmt_num(info.get("KB_ELEVATION")),
             "well_dmn.orig_grnd_elev_qty"),
            ("Datum Type",            info.get("DATUM_TYPE"),
             "well_dmn.well_datum_type_desc"),
            ("SEP", None, ""),
            ("Reservoir Name",        info.get("RESERVOIR_NAME"),
             "cmpl_dmn.prdu_nme"),
            ("Engineering String",    info.get("ENGR_STRG_NME"),
             "cmpl_dmn.engr_strg_nme"),
            ("Reservoir Engineering String", info.get("RSVR_ENGR_STRG_NME"),
             "cmpl_dmn.rsvr_engr_strg_nme"),
            ("Management Plant",      info.get("MGMT_PLANT"),
             "cmpl_dmn.fncl_fld_nme"),

            # -- SECTION: Completion (only fields NOT already in Master) --
            ("HEADER", "Completion", ""),
            ("Operational String",    info.get("OPERATIONAL_STRING"),
             "cmpl_dmn.strg_nme"),
            ("Production Manifold",   info.get("PROD_MFLD_NME"),
             "cmpl_dmn.prod_mfld_nme"),
            ("Injection Manifold",    info.get("INJ_MFLD_NME"),
             "cmpl_dmn.inj_mfld_nme"),

            # -- SECTION: Wellbore --
            ("HEADER", "Wellbore", ""),
            ("Wellbore Name",         info.get("WELLBORE_NAME"),
             "wlbr_dmn.wlbr_nme"),
            ("Wellbore Suffix Number", info.get("WELLBORE_SUFFIX"),
             "wlbr_dmn.wlbr_api_suff_nbr"),
            ("Wellbore Curve Type",   info.get("WELLBORE_CURVE_TYPE"),
             "wlbr_dmn.wlbr_incl_type_desc"),
            ("Wellbore Depth",        self._fmt_num(info.get("WELLBORE_DEPTH_MD")),
             "wlbr_dmn.total_dpth_qty"),
            ("Wellbore Depth TVD",    self._fmt_num(info.get("WELLBORE_DEPTH_TVD")),
             "wlbr_dmn.total_dpth_tvd_qty"),
        ]

        row_idx = 0
        for label, value, source in fields:
            if label == "HEADER":
                if row_idx > 0:
                    ttk.Label(frame, text="").grid(row=row_idx, column=0)
                    row_idx += 1
                ttk.Label(frame, text=value,
                          font=("Segoe UI", 12, "bold"),
                          foreground="#2C3E50").grid(
                    row=row_idx, column=0, columnspan=3, sticky=tk.W, pady=(5, 3))
                ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
                    row=row_idx + 1, column=0, columnspan=3, sticky="ew", pady=(0, 5))
                row_idx += 2
                continue

            if label == "SEP":
                ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
                    row=row_idx, column=0, columnspan=3, sticky="ew", pady=4)
                row_idx += 1
                continue

            ttk.Label(frame, text=label,
                      font=("Segoe UI", 10, "bold")).grid(
                row=row_idx, column=0, sticky=tk.W, padx=(0, 15), pady=2)

            display_val = str(value) if value is not None and str(value).strip() else "\u2014"
            ttk.Label(frame, text=display_val,
                      font=("Consolas", 10)).grid(
                row=row_idx, column=1, sticky=tk.W, padx=(0, 20), pady=2)

            if source:
                ttk.Label(frame, text=source,
                          font=("Segoe UI", 8),
                          foreground="#95A5A6").grid(
                    row=row_idx, column=2, sticky=tk.W, pady=2)

            row_idx += 1

    # ---- TAB 2: Casing / Tubing / Perfs -----------------------------------

    def _populate_mechanical(self, cols_ct, rows_ct, cols_pf, rows_pf):
        for w in self.tab_mech.winfo_children():
            w.destroy()

        ttk.Label(self.tab_mech, text="Casing & Tubing (current assemblies)",
                  font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, padx=10, pady=(10, 2))

        if cols_ct:
            tree_ct = self._make_treeview(
                self.tab_mech, cols_ct, rows_ct, height=12)
            tree_ct.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))
        else:
            ttk.Label(self.tab_mech,
                      text="No wellbore data (wlbr_fac_id is NULL)",
                      font=("Consolas", 10)).pack(anchor=tk.W, padx=15)

        ttk.Label(self.tab_mech, text="Perforations / Slots",
                  font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, padx=10, pady=(5, 2))

        if cols_pf:
            tree_pf = self._make_treeview(
                self.tab_mech, cols_pf, rows_pf, height=8)
            tree_pf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        else:
            ttk.Label(self.tab_mech, text="No perforation data",
                      font=("Consolas", 10)).pack(anchor=tk.W, padx=15)

    # ---- TAB 3: Status & Well Test -----------------------------------------

    def _populate_status(self, info, cols_st, rows_st, cols_wt, rows_wt):
        for w in self.tab_status.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_status, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Current Off-Reason",
                  font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, pady=(0, 3))

        if rows_st:
            for row in rows_st:
                d = dict(zip(cols_st, row))
                txt = (f"{d.get('OFF_RSN_TYPE_CDE', '')} \u2014 "
                       f"{d.get('OFF_RSN_TYPE_DESC', '')}   "
                       f"(since {self._fmt_date(d.get('OFF_RSN_EFTV_DTTM'))}"
                       f", {d.get('DAYS_DOWN', '?')} days)")
                ttk.Label(frm, text=txt, font=("Consolas", 10),
                          foreground="red").pack(anchor=tk.W, padx=15)
        else:
            in_svc = info.get("IN_SVC_INDC", "")
            color = "green" if in_svc == "Y" else "orange"
            ttk.Label(frm,
                      text=f"No current off-reason  (in_svc_indc = {in_svc})",
                      font=("Consolas", 10), foreground=color).pack(
                anchor=tk.W, padx=15)

        ttk.Separator(frm, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        ttk.Label(frm, text="Latest Allocated Well Tests (up to 5)",
                  font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, pady=(0, 3))

        if cols_wt:
            tree_wt = self._make_treeview(frm, cols_wt, rows_wt, height=6)
            tree_wt.pack(fill=tk.BOTH, expand=True)
        else:
            ttk.Label(frm, text="No allocated well tests found",
                      font=("Consolas", 10)).pack(anchor=tk.W, padx=15)

    # ---- TAB 4: WRA Notes --------------------------------------------------

    def _populate_wra(self, cols_wr, rows_wr):
        for w in self.tab_wra.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_wra, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm,
                  text=f"WRA Notes \u2014 last 5 years  ({len(rows_wr)} entries)",
                  font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, pady=(0, 5))

        text_widget = tk.Text(frm, wrap=tk.WORD, font=("Consolas", 9))
        sb = ttk.Scrollbar(frm, command=text_widget.yview)
        text_widget.config(yscrollcommand=sb.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        for row in rows_wr:
            date_val = row[0]
            note_txt = row[1] or ""
            date_str = self._fmt_date(date_val)
            text_widget.insert(tk.END, f"[{date_str}]\n", "date_tag")
            text_widget.insert(tk.END, f"{note_txt.strip()}\n\n")

        text_widget.tag_config("date_tag", foreground="blue",
                               font=("Consolas", 9, "bold"))
        text_widget.config(state=tk.DISABLED)

    # ---- TAB 5: Production Chart -------------------------------------------

    def _populate_prod_chart(self, cols_mp, rows_mp, well, info):
        for w in self.tab_prod.winfo_children():
            w.destroy()

        if not HAS_MPL:
            ttk.Label(self.tab_prod,
                      text="matplotlib not installed \u2014 chart unavailable",
                      font=("Segoe UI", 11)).pack(padx=20, pady=20)
            return

        if not rows_mp:
            ttk.Label(self.tab_prod,
                      text="No monthly production data in last 36 months",
                      font=("Segoe UI", 11)).pack(padx=20, pady=20)
            return

        dates = [r[0] for r in rows_mp]
        oil = [float(r[1] or 0) for r in rows_mp]
        gross = [float(r[2] or 0) for r in rows_mp]
        wc = [float(r[4]) if r[4] is not None else None for r in rows_mp]
        steam_inj = [float(r[5] or 0) for r in rows_mp]
        water_inj = [float(r[6] or 0) for r in rows_mp]
        cyclic = [float(r[7] or 0) for r in rows_mp]

        purpose = info.get("PRIM_PURP_TYPE_CDE", "PROD")

        fig = Figure(figsize=(12, 7), dpi=100)

        if purpose == "INJ":
            ax1 = fig.add_subplot(111)
            ax1.bar(dates, steam_inj, width=25,
                    label="Steam Inj (BSPD)", color="#E74C3C", alpha=0.8)
            ax1.bar(dates, water_inj, width=25, bottom=steam_inj,
                    label="Water Inj (BWPD)", color="#3498DB", alpha=0.8)
            ax1.set_ylabel("Injection Rate (bbl/day)")
            ax1.set_title(f"{well} \u2014 Injection History (36 mo)")
            ax1.legend(loc="upper left")
            ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            fig.autofmt_xdate()
        else:
            ax1 = fig.add_subplot(211)
            ax1.fill_between(dates, gross, alpha=0.25, color="#3498DB",
                             label="Gross (BFPD)")
            ax1.plot(dates, oil, color="#2ECC71", linewidth=2,
                     label="Oil (BOPD)")
            if any(c > 0 for c in cyclic):
                ax1.bar(dates, cyclic, width=25, alpha=0.4, color="#E74C3C",
                        label="Cyclic Stm (BSPD)")
            ax1.set_ylabel("Rate (bbl/day)")
            ax1.set_title(f"{well} \u2014 Production History (36 mo)")
            ax1.legend(loc="upper left", fontsize=8)
            ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

            ax2 = fig.add_subplot(212, sharex=ax1)
            wc_dates = [d for d, w in zip(dates, wc) if w is not None]
            wc_vals = [w for w in wc if w is not None]
            if wc_vals:
                ax2.plot(wc_dates, wc_vals, color="#E67E22", linewidth=1.5,
                         marker=".", markersize=3)
            ax2.set_ylabel("Water Cut (%)")
            ax2.set_ylim(0, 105)
            ax2.set_xlabel("Month")
            fig.autofmt_xdate()

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, self.tab_prod)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar = NavigationToolbar2Tk(canvas, self.tab_prod)
        toolbar.update()

    # ---- TAB 6: Workover History -------------------------------------------

    def _populate_workovers(self, cols_wo, rows_wo):
        for w in self.tab_wo.winfo_children():
            w.destroy()

        frm = ttk.Frame(self.tab_wo, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Workover History  ({len(rows_wo)} records)",
                  font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, pady=(0, 5))

        if not rows_wo:
            ttk.Label(frm,
                      text="No workover records found in dss.dss_work_over",
                      font=("Consolas", 10)).pack(anchor=tk.W, padx=15)
            return

        tree = self._make_treeview(frm, cols_wo, rows_wo, height=20)
        tree.pack(fill=tk.BOTH, expand=True)

    # ---- HELPERS -----------------------------------------------------------

    def _make_treeview(self, parent, columns, rows, height=15):
        """Create a scrollable treeview with data."""
        container = ttk.Frame(parent)

        display_cols = [c.replace("_", " ").title() for c in columns]
        tree = ttk.Treeview(container, columns=columns, show="headings",
                            height=height)

        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=tree.yview)
        hsb = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col, disp in zip(columns, display_cols):
            tree.heading(col, text=disp)
            tree.column(col, width=110, minwidth=60)

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
    def _fmt_date(val):
        if val is None:
            return "\u2014"
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d")
        return str(val)

    @staticmethod
    def _fmt_num(val):
        if val is None:
            return "\u2014"
        try:
            return f"{float(val):,.1f}"
        except (ValueError, TypeError):
            return str(val)


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = WellPassportApp(root)
    root.mainloop()