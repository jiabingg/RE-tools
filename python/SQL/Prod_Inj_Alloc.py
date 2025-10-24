import os
import cx_Oracle
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd
from datetime import datetime

"""
UI app to query PRODUCTION / INJECTION for a list of WELL API numbers.

- Uses your OracleConnectionManager (odw/sandbox/openwells) config pattern
- Enter one API per line
- Pulls ONLY the last 3 months of data (DB-side using SYSDATE)
- Displays results in a table
- Button to copy results to clipboard (Excel-friendly)

Query elements:
- CTE st picks latest cmpl_state per well
- Filters: AE-CYM, active flags, excludes PRPO/FUTR
"""

# ---------------------------
# Oracle Connection Manager
# ---------------------------
class OracleConnectionManager:
    def __init__(self):
        # Environment variables can override these defaults
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
        config = self._connections[name]
        try:
            return cx_Oracle.connect(
                user=config["user"], password=config["password"], dsn=config["dsn"]
            )
        except cx_Oracle.Error as e:
            error_obj, = e.args
            raise ConnectionError(
                f"Failed to connect to Oracle DB '{name}': {error_obj.message}"
            ) from e

    def available_connections(self):
        return list(self._connections.keys())


# ---------------------------
# UI App
# ---------------------------
class ProdInjLast3MonthsApp(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Prod/Inj – Last 3 Months by API")
        self.geometry("1200x820")

        # style
        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.conn_manager = OracleConnectionManager()
        self.current_data = None

        container = tb.Frame(self)
        container.pack(fill="both", expand=True, padx=12, pady=12)

        # Input panel
        input_frame = tb.LabelFrame(container, text="Input", bootstyle="info")
        input_frame.pack(fill="x", pady=8)

        lbl = tb.Label(
            input_frame,
            text="Enter WELL API numbers (one per line):",
            font=("Helvetica", 12),
        )
        lbl.pack(anchor="w", padx=8, pady=6)

        self.api_text = scrolledtext.ScrolledText(
            input_frame, wrap=tk.WORD, width=40, height=8, font=("Courier New", 10)
        )
        self.api_text.pack(fill="x", padx=8, pady=6)

        # Buttons row
        btn_row = tb.Frame(input_frame)
        btn_row.pack(fill="x", padx=8, pady=6)

        run_btn = tb.Button(btn_row, text="Run Query", bootstyle="primary", command=self.run_query)
        run_btn.pack(side="left")

        copy_btn = tb.Button(
            btn_row,
            text="Copy Results to Clipboard",
            bootstyle="secondary",
            command=self.copy_to_clipboard,
        )
        copy_btn.pack(side="left", padx=8)

        # Results table
        table_frame = tb.Frame(container)
        table_frame.pack(fill="both", expand=True)

        self.tree_scroll_y = tb.Scrollbar(table_frame, orient="vertical")
        self.tree_scroll_y.pack(side="right", fill="y")
        self.tree_scroll_x = tb.Scrollbar(table_frame, orient="horizontal")
        self.tree_scroll_x.pack(side="bottom", fill="x")

        self.tree = ttk.Treeview(
            table_frame,
            show="headings",
            yscrollcommand=self.tree_scroll_y.set,
            xscrollcommand=self.tree_scroll_x.set,
        )
        self.tree.pack(fill="both", expand=True)
        self.tree_scroll_y.config(command=self.tree.yview)
        self.tree_scroll_x.config(command=self.tree.xview)

        # compute font for auto-width
        try:
            treeview_font_name = ttk.Style().lookup("Treeview", "font")
            self.tree_font = tkinter.font.Font(font=treeview_font_name)
        except Exception:
            self.tree_font = tkinter.font.Font(family="TkDefaultFont", size=10)

        # Connection selector (optional, defaults to 'odw')
        conn_row = tb.Frame(container)
        conn_row.pack(fill="x", pady=(8, 0))
        tb.Label(conn_row, text="Connection:", font=("Helvetica", 10)).pack(side="left", padx=(2, 6))
        self.conn_var = tk.StringVar(value="odw")
        self.conn_combo = ttk.Combobox(conn_row, textvariable=self.conn_var, state="readonly")
        self.conn_combo["values"] = self.conn_manager.available_connections()
        self.conn_combo.pack(side="left")

        # Date window label (DB-side SYSDATE used)
        tb.Label(conn_row, text="Date window: last 3 months (SYSDATE-based)", bootstyle="secondary").pack(
            side="left", padx=12
        )

    # ---------------------------
    # Core actions
    # ---------------------------
    def run_query(self):
        apis = [a.strip() for a in self.api_text.get("1.0", tk.END).splitlines() if a.strip()]
        if not apis:
            messagebox.showwarning("Input Error", "Please enter at least one WELL API number.")
            self.clear_table()
            return

        # Dynamic IN list (bind-safe)
        bind_names = [f"api{i}" for i in range(len(apis))]
        placeholders = ", ".join([f":{b}" for b in bind_names])
        params = {b: v for b, v in zip(bind_names, apis)}

        # Query per your spec with last-3-months filter DB-side
        sql = f"""
            WITH st AS (
                -- latest completion state per well
                SELECT well_fac_id, cmpl_state_type_desc AS well_state
                FROM (
                    SELECT well_fac_id,
                           cmpl_fac_id,
                           cmpl_nme,
                           cmpl_state_type_desc,
                           RANK() OVER (PARTITION BY well_fac_id ORDER BY cmpl_fac_id DESC) AS rnk
                    FROM cmpl_dmn
                    WHERE actv_indc = 'Y'
                      AND cmpl_state_type_cde NOT IN ('PRPO','FUTR')
                      AND engr_strg_nme = 'AE-CYM'
                )
                WHERE rnk = 1
            )
            SELECT
                wd.well_nme                         AS "WELL NAME",
                wd.well_api_nbr                     AS "WELL API",
                cf.eftv_dttm                        AS "DATE",
                cf.aloc_oil_prod_dly_rte_qty        AS "OIL PROD BBL",
                cf.aloc_wtr_prod_dly_rte_qty        AS "WATER PROD BBL",
                cf.aloc_gas_prod_dly_rte_qty        AS "GAS PROD MCF",
                cf.aloc_stm_inj_dly_rte_qty         AS "STEAM INJ BBL",
                cf.aloc_wtr_inj_on_days_rte_qty     AS "WATER INJ BBL",
                st.well_state                       AS "STATUS"
            FROM well_dmn wd
            JOIN cmpl_dmn cd        ON wd.well_fac_id = cd.well_fac_id
            JOIN st                 ON wd.well_fac_id = st.well_fac_id
            JOIN cmpl_mnly_fact cf  ON cd.cmpl_fac_id = cf.cmpl_fac_id
            WHERE cd.actv_indc = 'Y'
              AND wd.actv_indc = 'Y'
              AND cd.engr_strg_nme = 'AE-CYM'
              AND cd.cmpl_state_type_cde NOT IN ('PRPO','FUTR')
              AND wd.well_api_nbr IN ({placeholders})
              -- last 3 months (inclusive) based on DB clock
              AND cf.eftv_dttm >= ADD_MONTHS(SYSDATE, -3)
              AND cf.eftv_dttm <= SYSDATE
            ORDER BY wd.well_api_nbr, cf.eftv_dttm
        """

        try:
            conn_name = self.conn_var.get() if hasattr(self, "conn_var") else "odw"
            conn = self.conn_manager.get_connection(conn_name)
            cur = conn.cursor()
            cur.execute(sql, params)

            rows = cur.fetchall() if cur.description else []
            cols = [c[0] for c in cur.description] if cur.description else []
            cur.close()
            conn.close()

            if not rows:
                messagebox.showinfo("No Results", "No data returned for the given APIs within the last 3 months.")
                self.clear_table()
                return

            df = pd.DataFrame(rows, columns=cols)

            # Normalize date column
            for c in ["DATE", "date", "EFTV_DTTM", "eftv_dttm"]:
                if c in df.columns:
                    df[c] = pd.to_datetime(df[c], errors="coerce")

            # Friendly sort: API then DATE
            if "WELL API" in df.columns and "DATE" in df.columns:
                df = df.sort_values(["WELL API", "DATE"], ascending=[True, True])

            self.current_data = df
            self.display_dataframe(df)

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_table()
        except cx_Oracle.Error as e:
            try:
                error_obj, = e.args
                msg = f"Oracle Error: {error_obj.message}"
            except Exception:
                msg = f"Oracle Error: {e}"
            messagebox.showerror("Database Error", msg)
            self.clear_table()
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {e}")
            self.clear_table()

    def display_dataframe(self, df: pd.DataFrame):
        # Clear existing
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

        # Insert rows with formatting
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

            # Auto-grow columns
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
    app = ProdInjLast3MonthsApp()
    app.mainloop()
