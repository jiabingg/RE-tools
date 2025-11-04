# file: Last3Tests_ByAPI.py
import os
import cx_Oracle
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import ttkbootstrap as tb
import pandas as pd
from datetime import datetime

APP_TITLE = "Last 3 Well Tests by API Numbers"
APP_WIDTH = 2400
APP_HEIGHT = 1480

# ---------------------------
# Oracle Connection Manager
# ---------------------------
class OracleConnectionManager:
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": os.getenv("DB_DSN_ODW", "odw"),
            }
        }
        self._active = "odw"

    def connect(self):
        cfg = self._connections[self._active]
        return cx_Oracle.connect(cfg["user"], cfg["password"], cfg["dsn"])

# ---------------------------
# Query Builder
# ---------------------------
# Reduced column widths by ~25%
COL_WIDTH_DEFAULT = 112  # was 150
COL_WIDTH_WIDE = 135     # was 180


def build_sql_and_binds(api_list, producers_only=True, allocated_only=True):
    if not api_list:
        raise ValueError("No API numbers provided.")

    placeholders = ",".join(f":a{i}" for i in range(len(api_list)))
    binds = {f"a{i}": api for i, api in enumerate(api_list)}

    prod_clause = "AND cd.prim_purp_type_cde = 'PROD'" if producers_only else ""
    alloc_clause = "AND wt.use_for_aloc_indc = 1" if allocated_only else ""

    sql = f"""
    /* Last 3 tests per completion for the given WELL API list */
    WITH ranked AS (
        SELECT
            wd.well_api_nbr,
            wd.well_nme,
            cd.cmpl_fac_id,
            cd.cmpl_nme,
            cd.engr_strg_nme,
            cd.opnl_fld,
            cd.prim_purp_type_cde,
            cd.cmpl_state_type_cde,
            wt.strt_dttm,
            ROUND(wt.bopd, 1)    AS bopd,
            ROUND(wt.bwpd, 1)    AS bwpd,
            ROUND(wt.mcfd, 1)    AS mcfd,
            ROUND(wt.oil_vol, 1) AS oil_vol,
            ROUND(wt.wtr_vol, 1) AS wtr_vol,
            ROUND(wt.gas_vol, 1) AS gas_vol,
            ROUND((wt.drtn_secs_qty / 3600), 1) AS test_duration_hrs,
            ROW_NUMBER() OVER (
                PARTITION BY wt.cmpl_fac_id
                ORDER BY wt.strt_dttm DESC
            ) AS rnk
        FROM dwrptg.well_test wt
        JOIN cmpl_dmn cd
          ON cd.cmpl_fac_id = wt.cmpl_fac_id
        JOIN well_dmn wd
          ON wd.well_fac_id = cd.well_fac_id
        WHERE cd.actv_indc = 'Y'
          {prod_clause}
          AND cd.cmpl_state_type_cde <> 'ABND'
          {alloc_clause}
          AND wd.well_api_nbr IN ({placeholders})
          AND wt.strt_dttm IS NOT NULL
    )
    SELECT
        opnl_fld,
        well_api_nbr,
        well_nme,
        cmpl_fac_id,
        cmpl_nme,
        engr_strg_nme,
        prim_purp_type_cde,
        cmpl_state_type_cde,
        strt_dttm,
        bopd,
        bwpd,
        mcfd,
        oil_vol,
        wtr_vol,
        gas_vol,
        test_duration_hrs
    FROM ranked
    WHERE rnk <= 3
    ORDER BY opnl_fld, well_api_nbr, cmpl_fac_id, strt_dttm DESC
    """
    return sql, binds

# ---------------------------
# UI App
# ---------------------------
class App(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title(APP_TITLE)
        self.geometry(f"{APP_WIDTH}x{APP_HEIGHT}")
        self.conn_mgr = OracleConnectionManager()
        self.df_results = pd.DataFrame()

        self._build_ui()

    def _build_ui(self):
        frm_input = ttk.LabelFrame(self, text="Input: WELL API Numbers (one per line)")
        frm_input.pack(fill="x", padx=10, pady=10)

        self.txt_input = tk.Text(frm_input, height=8)
        self.txt_input.pack(fill="x", padx=8, pady=8)

        frm_opts = ttk.Frame(self)
        frm_opts.pack(fill="x", padx=10)
        self.only_prod = tk.BooleanVar(value=True)
        self.only_alloc = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_opts, text="Producers only (PRIM_PURP = 'PROD')", variable=self.only_prod).pack(side="left", padx=5)
        ttk.Checkbutton(frm_opts, text="Allocated Only (USE_FOR_ALOC_INDC = 1)", variable=self.only_alloc).pack(side="left", padx=5)

        frm_btns = ttk.Frame(self)
        frm_btns.pack(fill="x", padx=10, pady=(6,10))
        self.btn_run = ttk.Button(frm_btns, text="Run Query", command=self.on_run)
        self.btn_run.pack(side="left", padx=5)
        self.btn_copy = ttk.Button(frm_btns, text="Copy Results to Clipboard", command=self.copy_results, state="disabled")
        self.btn_copy.pack(side="left", padx=5)
        self.btn_export = ttk.Button(frm_btns, text="Export CSV", command=self.export_csv, state="disabled")
        self.btn_export.pack(side="left", padx=5)

        frm_tbl = ttk.LabelFrame(self, text="Results")
        frm_tbl.pack(fill="both", expand=True, padx=10, pady=10)

        cols = [
            "opnl_fld", "well_api_nbr", "well_nme", "cmpl_fac_id", "cmpl_nme",
            "engr_strg_nme", "prim_purp_type_cde", "cmpl_state_type_cde",
            "strt_dttm", "bopd", "bwpd", "mcfd", "oil_vol", "wtr_vol", "gas_vol", "test_duration_hrs"
        ]

        self.tree = ttk.Treeview(frm_tbl, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=(COL_WIDTH_WIDE if c == "strt_dttm" else COL_WIDTH_DEFAULT), anchor="w")
        self.tree.tag_configure("highlight", background="#fff3cd")  # pale yellow
        self.tree.pack(fill="both", expand=True, side="left")

        vsb = ttk.Scrollbar(frm_tbl, orient="vertical", command=self.tree.yview)
        vsb.pack(fill="y", side="right")
        self.tree.configure(yscrollcommand=vsb.set)

        self.status = ttk.Label(self, text="Ready.", anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0,10))

    def parse_api_list(self):
        raw = self.txt_input.get("1.0", "end").strip()
        items = []
        for token in raw.replace(",", "\n").splitlines():
            tok = token.strip()
            if tok:
                items.append(tok)
        seen = set()
        deduped = []
        for x in items:
            if x not in seen:
                seen.add(x)
                deduped.append(x)
        return deduped

    def on_run(self):
        try:
            api_list = self.parse_api_list()
            if not api_list:
                messagebox.showwarning("Input Required", "Please paste at least one API number.")
                return

            sql, binds = build_sql_and_binds(
                api_list,
                producers_only=self.only_prod.get(),
                allocated_only=self.only_alloc.get(),
            )

            self.status.config(text="Running query...")
            self.update_idletasks()

            with self.conn_mgr.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, binds)
                cols = [d[0].lower() for d in cur.description]
                rows = cur.fetchall()

            self.df_results = pd.DataFrame(rows, columns=cols)
            self.populate_tree(self.df_results)

            self.btn_copy.config(state="normal")
            self.btn_export.config(state="normal")
            self.status.config(text=f"Returned {len(self.df_results)} rows. Completed {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except cx_Oracle.DatabaseError as e:
            err = str(e)
            messagebox.showerror("Database Error", err)
            self.status.config(text="Database error.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status.config(text="Error.")

    def populate_tree(self, df: pd.DataFrame):
        # Clear existing
        for i in self.tree.get_children():
            self.tree.delete(i)

        # Rebuild columns if needed
        current_cols = self.tree["columns"]
        df_cols = list(df.columns)
        if tuple(current_cols) != tuple(df_cols):
            self.tree["columns"] = df_cols
            for c in df_cols:
                self.tree.heading(c, text=c)
                self.tree.column(c, width=(COL_WIDTH_WIDE if c == "strt_dttm" else COL_WIDTH_DEFAULT), anchor="w")

        # Insert rows with highlighting when bopd or oil_vol > 20
        for _, row in df.iterrows():
            values = [row[c] for c in df.columns]
            taglist = []
            try:
                bopd_val = float(row.get("bopd", 0) or 0)
                oil_val = float(row.get("oil_vol", 0) or 0)
                if bopd_val > 20 or oil_val > 20:
                    taglist.append("highlight")
            except Exception:
                pass
            self.tree.insert("", "end", values=values, tags=taglist)

    def copy_results(self):
        if self.df_results.empty:
            messagebox.showinfo("No Data", "There are no results to copy.")
            return
        try:
            tsv = self.df_results.to_csv(sep="\t", index=False)
            self.clipboard_clear()
            self.clipboard_append(tsv)
            messagebox.showinfo("Copied", "Results copied to clipboard (TSV).")
        except Exception as e:
            messagebox.showerror("Error", f"Copy failed: {e}")

    def export_csv(self):
        if self.df_results.empty:
            messagebox.showinfo("No Data", "There are no results to export.")
            return
        try:
            fname = f"last3_welltests_by_api_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.df_results.to_csv(fname, index=False)
            messagebox.showinfo("Exported", f"Saved: {fname}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")

if __name__ == "__main__":
    App().mainloop()