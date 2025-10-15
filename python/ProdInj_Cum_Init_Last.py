import os
import cx_Oracle
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd

"""
UI tool: Well Status + Cumulative Snapshot

- Enter WELL API numbers (one per line)
- Runs a query that returns:
  * well name, API, field
  * primary purpose, reservoir, init prod/inj dates
  * Last injection & production dates (from CTEs)
  * Current-month cumulative volumes (oil/water/gas prod, steam/water injected)
  * Bottom-hole X/Y (from cmpl_non_ver_dmn)
- Displays in a table with auto-sized columns
- Button to copy results to clipboard (Excel format)

Uses the same OracleConnectionManager style/credentials as your other tools.
"""

# ---------------------------
# Oracle Connection Manager
# ---------------------------
class OracleConnectionManager:
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw",
            },
            "sandbox": {
                "user": os.getenv("DB_USER_SANDBOX", "engsb"),
                "password": os.getenv("DB_PASSWORD_SANDBOX", "Engine33r_SB"),
                "dsn": "odw",
            },
            "openwells": {
                "user": os.getenv("DB_USER_OW", "gen_user"),
                "password": os.getenv("DB_PASSWORD_OW", "allusers"),
                "dsn": "owdb1",
            },
        }

    def get_connection(self, name):
        if name not in self._connections:
            raise ValueError(f"Unknown DB connection name: {name}")
        cfg = self._connections[name]
        try:
            return cx_Oracle.connect(user=cfg["user"], password=cfg["password"], dsn=cfg["dsn"])
        except cx_Oracle.Error as e:
            (err,) = e.args
            raise ConnectionError(f"Failed to connect to Oracle DB '{name}': {err.message}") from e

    def available_connections(self):
        return list(self._connections.keys())


# ---------------------------
# UI App
# ---------------------------
class WellStatusSummaryApp(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Well Status + Cumulative Snapshot")
        self.geometry("1400x900")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.conn_manager = OracleConnectionManager()
        self.current_data = None

        container = tb.Frame(self)
        container.pack(fill="both", expand=True, padx=12, pady=12)

        # Input area
        input_frame = tb.LabelFrame(container, text="Input", bootstyle="info")
        input_frame.pack(fill="x", pady=8)

        tb.Label(input_frame, text="Enter WELL API numbers (one per line):", font=("Helvetica", 12)).pack(anchor="w", padx=8, pady=6)

        self.api_text = scrolledtext.ScrolledText(input_frame, wrap=tk.WORD, width=50, height=10, font=("Courier New", 10))
        self.api_text.pack(fill="x", padx=8, pady=6)

        btn_row = tb.Frame(input_frame)
        btn_row.pack(fill="x", padx=8, pady=6)

        tb.Button(btn_row, text="Run Query", bootstyle="primary", command=self.run_query).pack(side="left")
        tb.Button(btn_row, text="Copy Results to Clipboard", bootstyle="secondary", command=self.copy_to_clipboard).pack(side="left", padx=8)

        # Results table
        table_frame = tb.Frame(container)
        table_frame.pack(fill="both", expand=True)

        self.tree_scroll_y = tb.Scrollbar(table_frame, orient="vertical")
        self.tree_scroll_y.pack(side="right", fill="y")
        self.tree_scroll_x = tb.Scrollbar(table_frame, orient="horizontal")
        self.tree_scroll_x.pack(side="bottom", fill="x")

        self.tree = ttk.Treeview(table_frame, show="headings", yscrollcommand=self.tree_scroll_y.set, xscrollcommand=self.tree_scroll_x.set)
        self.tree.pack(fill="both", expand=True)
        self.tree_scroll_y.config(command=self.tree.yview)
        self.tree_scroll_x.config(command=self.tree.xview)

        # Font for measuring column widths
        try:
            treeview_font_name = ttk.Style().lookup("Treeview", "font")
            self.tree_font = tkinter.font.Font(font=treeview_font_name)
        except Exception:
            self.tree_font = tkinter.font.Font(family="TkDefaultFont", size=10)

    # ---------------------------
    # Core actions
    # ---------------------------
    def run_query(self):
        apis = [a.strip() for a in self.api_text.get("1.0", tk.END).splitlines() if a.strip()]
        if not apis:
            messagebox.showwarning("Input Error", "Please enter at least one WELL API number.")
            self.clear_table()
            return
        in_list = ", ".join([f"'{a}'" for a in apis])

        sql = f"""
            -- Last Injection Date
            WITH T1 AS (
                SELECT cmpl_fac_id, eftv_dttm AS last_inj_dte FROM (
                    SELECT cmpl_fac_id, eftv_dttm, aloc_stm_inj_dly_rte_qty,
                           DENSE_RANK() OVER (PARTITION BY cmpl_fac_id ORDER BY eftv_dttm DESC) AS rnk
                    FROM cmpl_mnly_fact
                    WHERE aloc_stm_inj_dly_rte_qty > 0
                ) WHERE rnk = 1
            ),
            -- Last Production Date
            T2 AS (
                SELECT cmpl_fac_id, eftv_dttm AS last_prod_dte FROM (
                    SELECT cmpl_fac_id, eftv_dttm, aloc_gros_prod_dly_rte_qty,
                           DENSE_RANK() OVER (PARTITION BY cmpl_fac_id ORDER BY eftv_dttm DESC) AS rnk
                    FROM cmpl_mnly_fact
                    WHERE aloc_gros_prod_dly_rte_qty > 0
                ) WHERE rnk = 1
            )
            SELECT
                wd.well_nme,
                wd.well_api_nbr,
                wd.fld_nme,
                cd.prim_purp_type_cde,
                cd.ENGR_STRG_NME,
                cd.init_prod_dte,
                cd.init_inj_dte,
                T1.last_inj_dte,
                T2.last_prod_dte,
                ccf.aloc_cum_oil_prod_vol_qty,
                ccf.aloc_cum_wtr_prod_vol_qty,
                ccf.aloc_cum_gas_prod_vol_qty,
                ccf.aloc_cum_stm_inj_vol_qty,
                ccf.aloc_cum_wtr_inj_vol_qty,
                cnd.btm_xcrd_qty,
                cnd.btm_ycrd_qty
            FROM well_dmn wd
            JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
            LEFT JOIN cmpl_non_ver_dmn cnd ON cd.cmpl_fac_id = cnd.cmpl_fac_id
            LEFT JOIN curr_cmpl_opnl_stat os ON cd.cmpl_fac_id = os.cmpl_fac_id
            JOIN cmpl_mnly_cum_fact ccf ON (cd.cmpl_fac_id = ccf.cmpl_fac_id AND ccf.eftv_dttm = TRUNC(SYSDATE, 'MM'))
            LEFT JOIN T1 ON cd.cmpl_fac_id = T1.cmpl_fac_id
            LEFT JOIN T2 ON cd.cmpl_fac_id = T2.cmpl_fac_id
            WHERE cd.actv_indc = 'Y'
              AND wd.actv_indc = 'Y'
              AND wd.well_api_nbr IN ({in_list})
        """

        try:
            conn = self.conn_manager.get_connection("odw")
            cur = conn.cursor()
            cur.execute(sql)

            rows = cur.fetchall() if cur.description else []
            cols = [c[0] for c in cur.description] if cur.description else []
            cur.close()
            conn.close()

            if not rows:
                messagebox.showinfo("No Results", "No data returned for the given APIs.")
                self.clear_table()
                return

            df = pd.DataFrame(rows, columns=cols)

            # Normalize date columns
            for col in [
                "INIT_PROD_DTE",
                "INIT_INJ_DTE",
                "LAST_INJ_DTE",
                "LAST_PROD_DTE",
            ]:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")

            self.current_data = df
            self.display_dataframe(df)

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_table()
        except cx_Oracle.Error as e:
            (err,) = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {err.message}")
            self.clear_table()
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {e}")
            self.clear_table()

    def display_dataframe(self, df: pd.DataFrame):
        # clear
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tree["columns"] = []
        if df.empty:
            return

        cols = list(df.columns)
        self.tree["columns"] = cols
        self.tree["displaycolumns"] = cols

        for col in cols:
            self.tree.heading(col, text=col, anchor="w")
            self.tree.column(col, width=self.tree_font.measure(col) + 24, stretch=False)

        for _, row in df.iterrows():
            vals = []
            for item in row:
                if isinstance(item, pd.Timestamp):
                    vals.append(item.strftime("%Y-%m-%d"))
                elif pd.isna(item):
                    vals.append("")
                else:
                    vals.append(item)
            self.tree.insert("", "end", values=vals)

            for i, item in enumerate(vals):
                width = self.tree_font.measure(str(item)) + 12
                col_id = cols[i]
                if self.tree.column(col_id, width=None) < width:
                    self.tree.column(col_id, width=width)

    def clear_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tree["columns"] = []
        self.current_data = None

    def copy_to_clipboard(self):
        if self.current_data is None or self.current_data.empty:
            messagebox.showwarning("No Data", "Nothing to copy yet.")
            return
        try:
            self.current_data.to_clipboard(excel=True, index=False, header=True)
            messagebox.showinfo("Copied", "Results copied to clipboard (Excel format).")
        except Exception as e:
            messagebox.showerror("Copy Error", f"Failed to copy: {e}")


if __name__ == "__main__":
    app = WellStatusSummaryApp()
    app.mainloop()
