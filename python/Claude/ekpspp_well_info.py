"""
EKPSPP Well Data Viewer
=======================
Tabbed GUI: Well Info | Production | Injection | Regulatory

SETUP:
  1. pip install oracledb    (or cx_Oracle)
  2. Make sure EKPSPD.WORLD is in your tnsnames.ora (same as SQL Developer)
  3. Run:  python ekpspp_well_viewer.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import csv
import os
from datetime import datetime

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  EKPSPP Connection (TNS alias — same as SQL Developer)                  ║
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
# SQL — follows all EKPSPP rules (PTYPE=COMP, non-zero filter, pre-agg, etc.)
# ---------------------------------------------------------------------------

SQL_WELL_INFO = """
SELECT
    C.WELLCOMP_NAME,
    C.API_NO14,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    C.ORGLEV4_NAME AS AREA,
    C.ORGLEV5_NAME AS SUB_AREA,
    C.REG_NAME AS REGION,
    C.FIELD_NAME,
    C.CURR_COMP_TYPE,
    C.CURR_COMP_STATUS,
    C.STATUS_EFF_DATE,
    C.RESERVOIR_CD,
    C.CURR_METHOD_PROD AS LIFT_METHOD,
    C.WELLBORE_NAME,
    C.WELL_SPUD_DATE,
    C.COMPLETION_DATE,
    C.FIRST_PROD_DATE,
    C.BOTTOM_HOLE_TMD,
    W.SECTION,
    W.SURF_LATITUDE,
    W.SURF_LONGITUDE
FROM ODS.BI_WELLCOMP_V C
LEFT JOIN ODS.BI_WELL W ON SUBSTR(C.API_NO14, 1, 10) = W.API_NO10
WHERE SUBSTR(C.API_NO14, 1, 10) IN ({placeholders})
ORDER BY C.WELLCOMP_NAME
"""

SQL_PRODUCTION = """
SELECT
    C.WELLCOMP_NAME,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    MV.PROD_INJ_DATE,
    MV.CDOIL_PROD AS BOPD,
    MV.CDWAT_PROD AS BWPD,
    MV.CDGAS_PROD AS MCFD,
    MV.CDN2_PROD AS N2_MCFD,
    MV.WCUT_PROD AS WATER_CUT_PCT,
    MV.GOR_PROD,
    MV.OIL_PROD AS MONTHLY_OIL_BBL,
    MV.WATER_PROD AS MONTHLY_WAT_BBL,
    MV.GAS_PROD AS MONTHLY_GAS_MCF,
    MV.DAYS_PROD,
    MV.ACTIVE_PROD
FROM DSS.MONTHLY_VOLUMES MV
JOIN ODS.BI_WELLCOMP_V C ON MV.PID = C.API_NO14
WHERE MV.PTYPE = 'COMP'
  AND (MV.OIL_PROD > 0 OR MV.WATER_PROD > 0 OR MV.GAS_PROD > 0)
  AND SUBSTR(C.API_NO14, 1, 10) IN ({placeholders})
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
    MV.DAYS_INJECT,
    MV.ACTIVE_WINJ,
    MV.ACTIVE_GINJ,
    MV.ACTIVE_WD
FROM DSS.MONTHLY_VOLUMES MV
JOIN ODS.BI_WELLCOMP_V C ON MV.PID = C.API_NO14
WHERE MV.PTYPE = 'COMP'
  AND (MV.WATER_INJ > 0 OR MV.DISP_WATER_INJ > 0
       OR MV.GAS_INJ > 0 OR MV.STEAM_INJ > 0 OR MV.NITROGEN_INJ > 0)
  AND SUBSTR(C.API_NO14, 1, 10) IN ({placeholders})
ORDER BY C.WELLCOMP_NAME, MV.PROD_INJ_DATE DESC
"""

SQL_REGULATORY = """
SELECT
    C.WELLCOMP_NAME,
    SUBSTR(C.API_NO14, 1, 10) AS API_10,
    C.CURR_COMP_TYPE,
    C.CURR_COMP_STATUS,
    U.TEST_TYPE,
    U.TEST_DATE,
    U.TEST_PSI,
    U.MASP,
    U.PASS_OR_FAIL,
    U.NEXT_SURVEY,
    U.NEXT_SAPT,
    U.VARIANCE,
    U.COMMENTS,
    MW.UIC_PROJECT_NUMBER,
    MW.UIC_FLAG
FROM ODS.BI_WELLCOMP_V C
LEFT JOIN REGULATORY.UIC_WELLS_TESTS_V U
    ON TO_NUMBER(SUBSTR(C.API_NO14, 1, 10)) = U.APINUMBER
LEFT JOIN (
    SELECT API_NO, MAX(UIC_PROJECT_NUMBER) UIC_PROJECT_NUMBER, MAX(UIC_FLAG) UIC_FLAG
    FROM GOVERNANCE.A_MASTER_WELL_LIST_T GROUP BY API_NO
) MW ON TO_NUMBER(SUBSTR(C.API_NO14, 1, 10)) = MW.API_NO
WHERE SUBSTR(C.API_NO14, 1, 10) IN ({placeholders})
ORDER BY C.WELLCOMP_NAME, U.TEST_DATE DESC
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_apis(raw_text):
    apis = []
    for token in raw_text.replace(",", " ").replace(";", " ").replace("\n", " ").split():
        token = token.strip().lstrip("0").strip()
        if not token or not token.isdigit():
            continue
        if len(token) <= 10:
            apis.append(token.zfill(10))
        elif len(token) == 14:
            apis.append(token[:10])
        elif len(token) == 12:
            apis.append(token[:10])
        else:
            apis.append(token[:10].zfill(10))
    return list(dict.fromkeys(apis))


def build_query(sql_template, api_list):
    markers = ", ".join([f":a{i}" for i in range(len(api_list))])
    return sql_template.replace("{placeholders}", markers)


def bind_params(api_list):
    return {f"a{i}": api for i, api in enumerate(api_list)}


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class EKPSPPViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EKPSPP Well Data Viewer")
        self.geometry("1280x720")
        self.minsize(900, 500)
        self.api_list = []
        self.data = {"well_info": [], "production": [], "injection": [], "regulatory": []}
        self.headers = {"well_info": [], "production": [], "injection": [], "regulatory": []}
        self._build_ui()

    def _build_ui(self):
        top = ttk.LabelFrame(self, text="  Enter API Numbers (10 or 14 digit, comma/space/newline separated)  ", padding=10)
        top.pack(fill="x", padx=10, pady=(10, 5))

        self.api_text = scrolledtext.ScrolledText(top, height=3, font=("Consolas", 10))
        self.api_text.pack(fill="x", side="left", expand=True, padx=(0, 10))

        btn_frame = ttk.Frame(top)
        btn_frame.pack(side="right")
        self.fetch_btn = ttk.Button(btn_frame, text="Fetch Data", command=self._on_fetch)
        self.fetch_btn.pack(pady=(0, 5))
        self.export_btn = ttk.Button(btn_frame, text="Export to CSV", command=self._on_export)
        self.export_btn.pack()

        self.status_var = tk.StringVar(value="Ready — enter API numbers and click Fetch Data")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w",
                  padding=(5, 2)).pack(fill="x", padx=10, pady=(0, 5))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Instructions tab
        instr_frame = ttk.Frame(self.notebook)
        self.notebook.add(instr_frame, text="  Instructions  ")
        instructions = (
            "EKPSPP Well Data Viewer\n"
            "═══════════════════════\n\n"
            "1. Enter one or more API numbers in the text box above.\n"
            "   • Accepts 10-digit or 14-digit API numbers\n"
            "   • Separate with commas, spaces, or newlines\n"
            "   • Example: 0402927612, 04029300250000\n\n"
            "2. Click 'Fetch Data' to query the EKPSPP database.\n\n"
            "3. Browse tabs:\n"
            "   • Well Info      — completion attributes, status, reservoir, lift method\n"
            "   • Production    — monthly BOPD, BWPD, MCFD, water cut, GOR\n"
            "   • Injection      — water, gas, steam injection rates\n"
            "   • Regulatory    — UIC/MIT test results, MASP, pass/fail\n\n"
            "4. Click 'Export to CSV' to save the current tab's data.\n\n"
            f"Connection: {DB_USER}@{DB_TNS_ALIAS}"
        )
        txt = tk.Text(instr_frame, font=("Consolas", 11), wrap="word", bg="#f9f9f9")
        txt.insert("1.0", instructions)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=5, pady=5)

        # Data tabs
        self.trees = {}
        for key, label in [("well_info", "  Well Info  "), ("production", "  Production  "),
                           ("injection", "  Injection  "), ("regulatory", "  Regulatory  ")]:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=label)
            tree = ttk.Treeview(frame, show="headings", selectmode="extended")
            vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            rc_var = tk.StringVar(value="0 rows")
            ttk.Label(frame, textvariable=rc_var, anchor="e").grid(row=2, column=0, sticky="e", padx=5)
            self.trees[key] = (tree, rc_var)

    def _on_fetch(self):
        raw = self.api_text.get("1.0", "end")
        self.api_list = normalize_apis(raw)
        if not self.api_list:
            messagebox.showwarning("No APIs", "Please enter at least one valid API number.")
            return
        self.fetch_btn.config(state="disabled")
        self.status_var.set(f"Connecting to {DB_TNS_ALIAS}... querying {len(self.api_list)} well(s)")
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def _fetch_all(self):
        try:
            conn = get_connection()
            cursor = conn.cursor()
            params = bind_params(self.api_list)
            for key, sql_tmpl in [("well_info", SQL_WELL_INFO), ("production", SQL_PRODUCTION),
                                  ("injection", SQL_INJECTION), ("regulatory", SQL_REGULATORY)]:
                self.status_var.set(f"Fetching {key.replace('_', ' ')}...")
                sql = build_query(sql_tmpl, self.api_list)
                cursor.execute(sql, params)
                self.headers[key] = [d[0] for d in cursor.description]
                self.data[key] = cursor.fetchall()
            cursor.close()
            conn.close()
            self.after(0, self._populate_all)
            self.after(0, lambda: self.status_var.set(
                f"Done — {len(self.api_list)} well(s) queried successfully"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Database Error", str(e)))
            self.after(0, lambda: self.status_var.set(f"Error: {e}"))
        finally:
            self.after(0, lambda: self.fetch_btn.config(state="normal"))

    def _populate_all(self):
        for key in self.trees:
            tree, rc_var = self.trees[key]
            tree.delete(*tree.get_children())
            cols = self.headers[key]
            rows = self.data[key]
            if not cols:
                continue
            tree["columns"] = cols
            for col in cols:
                tree.heading(col, text=col, command=lambda c=col, k=key: self._sort_column(k, c))
                tree.column(col, width=120, minwidth=60)
            for row in rows:
                display = []
                for val in row:
                    if val is None:
                        display.append("")
                    elif isinstance(val, datetime):
                        display.append(val.strftime("%Y-%m-%d"))
                    else:
                        display.append(str(val))
                tree.insert("", "end", values=display)
            rc_var.set(f"{len(rows)} rows")
        self.notebook.select(1)

    def _sort_column(self, key, col):
        tree, _ = self.trees[key]
        items = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            items.sort(key=lambda t: float(t[0]) if t[0] else 0)
        except ValueError:
            items.sort(key=lambda t: t[0])
        for idx, (_, k) in enumerate(items):
            tree.move(k, "", idx)

    def _on_export(self):
        tab_idx = self.notebook.index(self.notebook.select())
        tab_keys = [None, "well_info", "production", "injection", "regulatory"]
        if tab_idx == 0 or tab_idx >= len(tab_keys):
            messagebox.showinfo("Export", "Select a data tab first.")
            return
        key = tab_keys[tab_idx]
        if not self.data.get(key):
            messagebox.showinfo("No Data", "No data to export. Fetch data first.")
            return
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"ekpspp_{key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not filename:
            return
        with open(filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(self.headers[key])
            for row in self.data[key]:
                writer.writerow([
                    v.strftime("%Y-%m-%d") if isinstance(v, datetime) else ("" if v is None else v)
                    for v in row
                ])
        self.status_var.set(f"Exported {len(self.data[key])} rows to {os.path.basename(filename)}")


if __name__ == "__main__":
    app = EKPSPPViewer()
    app.mainloop()