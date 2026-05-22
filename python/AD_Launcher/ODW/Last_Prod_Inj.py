"""
Last Production / Injection Date Lookup
========================================
Input a list of 10-digit API numbers. For each API, queries ODW for:
  - Last month with allocated oil production (+ volumes)
  - Last month with allocated steam injection (+ volumes)
  - Last month with allocated water injection (+ volumes)

Connects as rptguser/allusers via TNS alias ODW (thick mode).
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import csv
import io
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Oracle thick-mode init (must happen before any oracledb.connect)
# ---------------------------------------------------------------------------
try:
    import oracledb
    oracledb.init_oracle_client()
except Exception as e:
    print(f"Oracle client init warning: {e}")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
DB_USER = "rptguser"
DB_PASS = "allusers"
DB_DSN  = "ODW"


def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)


def normalize_api(raw: str) -> str:
    """Strip dashes/spaces and zero-pad to 10 digits."""
    digits = re.sub(r"[^0-9]", "", raw.strip())
    if len(digits) == 0:
        return ""
    return digits.zfill(10)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
QUERY = """
WITH well_base AS (
    SELECT cd.well_api_nbr,
           cd.cmpl_nme,
           cd.cmpl_fac_id,
           cd.prim_purp_type_cde,
           cd.prim_matl_desc,
           cd.cmpl_state_type_desc,
           cd.in_svc_indc,
           cd.engr_strg_nme,
           cd.opnl_fld,
           ROW_NUMBER() OVER (
               PARTITION BY cd.well_api_nbr
               ORDER BY cd.cmpl_fac_id DESC
           ) AS rn
    FROM dwrptg.cmpl_dmn cd
    WHERE cd.well_api_nbr IN ({placeholders})
      AND cd.actv_indc = 'Y'
),
wb AS (
    SELECT * FROM well_base WHERE rn = 1
),
last_prod AS (
    SELECT wb.well_api_nbr,
           cmf.eftv_dttm                   AS last_prod_month,
           cmf.aloc_oil_prod_vol_qty       AS oil_vol,
           cmf.aloc_oil_prod_dly_rte_qty   AS oil_rate,
           cmf.aloc_gros_prod_vol_qty      AS gross_vol,
           cmf.aloc_gros_prod_dly_rte_qty  AS gross_rate,
           cmf.aloc_wtr_prod_vol_qty       AS wtr_prod_vol,
           ROW_NUMBER() OVER (
               PARTITION BY wb.well_api_nbr ORDER BY cmf.eftv_dttm DESC
           ) AS rn
    FROM wb
    JOIN dwrptg.cmpl_mnly_fact cmf ON wb.cmpl_fac_id = cmf.cmpl_fac_id
    WHERE NVL(cmf.aloc_oil_prod_vol_qty, 0) + NVL(cmf.aloc_gros_prod_vol_qty, 0) > 0
),
last_steam AS (
    SELECT wb.well_api_nbr,
           cmf.eftv_dttm                   AS last_steam_month,
           cmf.aloc_stm_inj_vol_qty        AS steam_vol,
           cmf.aloc_stm_inj_dly_rte_qty    AS steam_rate,
           ROW_NUMBER() OVER (
               PARTITION BY wb.well_api_nbr ORDER BY cmf.eftv_dttm DESC
           ) AS rn
    FROM wb
    JOIN dwrptg.cmpl_mnly_fact cmf ON wb.cmpl_fac_id = cmf.cmpl_fac_id
    WHERE NVL(cmf.aloc_stm_inj_vol_qty, 0) > 0
),
last_water_inj AS (
    SELECT wb.well_api_nbr,
           cmf.eftv_dttm                   AS last_wtr_inj_month,
           cmf.aloc_wtr_inj_vol_qty        AS wtr_inj_vol,
           cmf.aloc_wtr_inj_dly_rte_qty    AS wtr_inj_rate,
           ROW_NUMBER() OVER (
               PARTITION BY wb.well_api_nbr ORDER BY cmf.eftv_dttm DESC
           ) AS rn
    FROM wb
    JOIN dwrptg.cmpl_mnly_fact cmf ON wb.cmpl_fac_id = cmf.cmpl_fac_id
    WHERE NVL(cmf.aloc_wtr_inj_vol_qty, 0) > 0
)
SELECT
    wb.well_api_nbr       AS "API",
    wb.cmpl_nme            AS "Completion",
    wb.prim_purp_type_cde  AS "Purpose",
    wb.prim_matl_desc      AS "Material",
    wb.cmpl_state_type_desc AS "State",
    wb.in_svc_indc         AS "InSvc",
    wb.engr_strg_nme       AS "Engr Strategy",
    wb.opnl_fld            AS "Field",
    -- Production
    lp.last_prod_month     AS "Last Prod Month",
    ROUND(lp.oil_vol, 1)   AS "Oil Vol (bbl)",
    ROUND(lp.oil_rate, 1)  AS "Oil Rate (bopd)",
    ROUND(lp.gross_vol, 1) AS "Gross Vol (bbl)",
    ROUND(lp.gross_rate, 1) AS "Gross Rate (bfpd)",
    ROUND(lp.wtr_prod_vol, 1) AS "Wtr Prod Vol (bbl)",
    -- Steam injection
    ls.last_steam_month    AS "Last Steam Month",
    ROUND(ls.steam_vol, 1) AS "Steam Vol (bbl)",
    ROUND(ls.steam_rate, 1) AS "Steam Rate (bspd)",
    -- Water injection
    lw.last_wtr_inj_month  AS "Last Wtr Inj Month",
    ROUND(lw.wtr_inj_vol, 1) AS "Wtr Inj Vol (bbl)",
    ROUND(lw.wtr_inj_rate, 1) AS "Wtr Inj Rate (bwpd)"
FROM wb
LEFT JOIN last_prod lp      ON wb.well_api_nbr = lp.well_api_nbr AND lp.rn = 1
LEFT JOIN last_steam ls     ON wb.well_api_nbr = ls.well_api_nbr AND ls.rn = 1
LEFT JOIN last_water_inj lw ON wb.well_api_nbr = lw.well_api_nbr AND lw.rn = 1
ORDER BY wb.well_api_nbr
"""


def build_query(api_list: list[str]) -> tuple[str, list[str]]:
    """Build the parameterized query for the given API list."""
    placeholders = ", ".join([f":api{i}" for i in range(len(api_list))])
    sql = QUERY.replace("{placeholders}", placeholders)
    bind = {f"api{i}": api for i, api in enumerate(api_list)}
    return sql, bind


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class LastProdInjApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Last Production / Injection Date Lookup")
        self.geometry("1500x700")
        self.minsize(1100, 500)

        self._build_input_frame()
        self._build_results_frame()
        self._build_status_bar()

        self._columns = []
        self._rows = []

    # ----- input area -----
    def _build_input_frame(self):
        frm = ttk.LabelFrame(self, text="Input API Numbers (10-digit, one per line or comma-separated)")
        frm.pack(fill="x", padx=8, pady=(8, 4))

        top = ttk.Frame(frm)
        top.pack(fill="x", padx=6, pady=4)

        self.txt_apis = tk.Text(top, height=5, width=80, font=("Consolas", 10))
        self.txt_apis.pack(side="left", fill="both", expand=True)

        btn_frame = ttk.Frame(top)
        btn_frame.pack(side="left", padx=(8, 0))

        self.btn_run = ttk.Button(btn_frame, text="Run Query", command=self._on_run)
        self.btn_run.pack(fill="x", pady=2)

        self.btn_paste = ttk.Button(btn_frame, text="Paste from Clipboard", command=self._on_paste)
        self.btn_paste.pack(fill="x", pady=2)

        self.btn_clear = ttk.Button(btn_frame, text="Clear", command=self._on_clear)
        self.btn_clear.pack(fill="x", pady=2)

        self.btn_export = ttk.Button(btn_frame, text="Export CSV", command=self._on_export, state="disabled")
        self.btn_export.pack(fill="x", pady=2)

        self.btn_copy = ttk.Button(btn_frame, text="Copy to Clipboard", command=self._on_copy, state="disabled")
        self.btn_copy.pack(fill="x", pady=2)

    # ----- results treeview -----
    def _build_results_frame(self):
        frm = ttk.LabelFrame(self, text="Results")
        frm.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        container = ttk.Frame(frm)
        container.pack(fill="both", expand=True, padx=4, pady=4)

        self.tree = ttk.Treeview(container, show="headings", selectmode="extended")

        vsb = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

    # ----- status bar -----
    def _build_status_bar(self):
        self.status_var = tk.StringVar(value="Ready")
        lbl = ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken")
        lbl.pack(fill="x", padx=8, pady=(0, 8))

    # ----- actions -----
    def _on_paste(self):
        try:
            text = self.clipboard_get()
            self.txt_apis.delete("1.0", "end")
            self.txt_apis.insert("1.0", text)
        except Exception:
            pass

    def _on_clear(self):
        self.txt_apis.delete("1.0", "end")
        self._clear_tree()
        self.status_var.set("Ready")
        self.btn_export.config(state="disabled")
        self.btn_copy.config(state="disabled")

    def _clear_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = []
        self._columns = []
        self._rows = []

    def _parse_apis(self) -> list[str]:
        raw = self.txt_apis.get("1.0", "end")
        # split on newlines, commas, tabs, spaces
        tokens = re.split(r"[,\n\t\r ]+", raw)
        apis = []
        for t in tokens:
            n = normalize_api(t)
            if len(n) == 10 and n not in apis:
                apis.append(n)
        return apis

    def _on_run(self):
        apis = self._parse_apis()
        if not apis:
            messagebox.showwarning("No APIs", "Enter at least one valid 10-digit API number.")
            return
        self.btn_run.config(state="disabled")
        self.status_var.set(f"Querying {len(apis)} API(s)...")
        self._clear_tree()

        threading.Thread(target=self._run_query, args=(apis,), daemon=True).start()

    def _run_query(self, apis: list[str]):
        try:
            sql, bind = build_query(apis)
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(sql, bind)

            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            cur.close()
            conn.close()

            # Find APIs that weren't found
            found_apis = {str(r[0]).zfill(10) for r in rows}
            missing = [a for a in apis if a not in found_apis]

            self.after(0, self._populate_results, columns, rows, missing)

        except Exception as e:
            self.after(0, self._query_error, str(e))

    def _populate_results(self, columns, rows, missing):
        self._columns = columns
        self._rows = rows

        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col, command=lambda c=col: self._sort_column(c, False))
            # Set column widths based on content type
            if "Month" in col:
                self.tree.column(col, width=100, minwidth=80, anchor="center")
            elif "Vol" in col or "Rate" in col:
                self.tree.column(col, width=100, minwidth=70, anchor="e")
            elif col in ("Purpose", "Material", "InSvc", "State"):
                self.tree.column(col, width=80, minwidth=60, anchor="center")
            elif col == "API":
                self.tree.column(col, width=100, minwidth=90, anchor="w")
            elif col == "Completion":
                self.tree.column(col, width=180, minwidth=120, anchor="w")
            elif col in ("Engr Strategy", "Field"):
                self.tree.column(col, width=120, minwidth=80, anchor="w")
            else:
                self.tree.column(col, width=100, minwidth=70, anchor="w")

        for row in rows:
            display = []
            for i, val in enumerate(row):
                if val is None:
                    display.append("")
                elif "Month" in columns[i] and hasattr(val, "strftime"):
                    display.append(val.strftime("%Y-%m"))
                elif isinstance(val, float):
                    display.append(f"{val:,.1f}")
                else:
                    display.append(str(val))
            self.tree.insert("", "end", values=display)

        msg = f"{len(rows)} result(s) returned for {len(rows) + len(missing)} API(s)."
        if missing:
            msg += f"  |  {len(missing)} not found: {', '.join(missing[:5])}"
            if len(missing) > 5:
                msg += f" (+{len(missing)-5} more)"
        self.status_var.set(msg)
        self.btn_run.config(state="normal")
        self.btn_export.config(state="normal")
        self.btn_copy.config(state="normal")

    def _query_error(self, msg):
        self.status_var.set(f"Error: {msg}")
        self.btn_run.config(state="normal")
        messagebox.showerror("Query Error", msg)

    # ----- sorting -----
    def _sort_key(self, val):
        if val == "" or val is None:
            return (2, 0, "")
        try:
            return (0, float(str(val).replace(",", "")), "")
        except (ValueError, TypeError):
            return (1, 0, str(val).lower())

    def _sort_column(self, col, reverse):
        data = [(self._sort_key(self.tree.set(child, col)), child)
                for child in self.tree.get_children("")]
        data.sort(reverse=reverse)
        for i, (_, child) in enumerate(data):
            self.tree.move(child, "", i)
        self.tree.heading(col, command=lambda: self._sort_column(col, not reverse))

    # ----- export / copy -----
    def _on_export(self):
        if not self._rows:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=f"last_prod_inj_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._columns)
                for row in self._rows:
                    out = []
                    for i, val in enumerate(row):
                        if val is None:
                            out.append("")
                        elif "Month" in self._columns[i] and hasattr(val, "strftime"):
                            out.append(val.strftime("%Y-%m"))
                        else:
                            out.append(val)
                    writer.writerow(out)
            self.status_var.set(f"Exported {len(self._rows)} rows to {path}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    def _on_copy(self):
        if not self._rows:
            return
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t")
        writer.writerow(self._columns)
        for row in self._rows:
            out = []
            for i, val in enumerate(row):
                if val is None:
                    out.append("")
                elif "Month" in self._columns[i] and hasattr(val, "strftime"):
                    out.append(val.strftime("%Y-%m"))
                else:
                    out.append(val)
            writer.writerow(out)
        self.clipboard_clear()
        self.clipboard_append(buf.getvalue())
        self.status_var.set(f"Copied {len(self._rows)} rows to clipboard (tab-delimited)")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = LastProdInjApp()
    app.mainloop()