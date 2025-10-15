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
Small UI app to query WELL + COMPLETION info for a list of WELL API numbers.

- Enter one API per line
- Click "Run Query"
- Results appear in a table
- "Copy Results to Clipboard" copies the table (Excel format)
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
class WellCompletionApp(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Well & Completion State Lookup")
        self.geometry("1200x800")

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

    # ---------------------------
    # Core actions
    # ---------------------------
    def run_query(self):
        apis = [a.strip() for a in self.api_text.get("1.0", tk.END).splitlines() if a.strip()]
        if not apis:
            messagebox.showwarning("Input Error", "Please enter at least one WELL API number.")
            self.clear_table()
            return

        # --- SAFE (bind) version of your query with dynamic IN list ---
        # It is functionally equivalent to the text you provided.
        bind_names = [f"api{i}" for i in range(len(apis))]
        placeholders = ", ".join([f":{b}" for b in bind_names])
        params = {b: v for b, v in zip(bind_names, apis)}

        sql = f"""
            SELECT
                wd.well_nme,
                wd.well_api_nbr,
                wd.fld_nme,
                cd.prim_purp_type_cde,
                cd.engr_strg_nme,
                cd.cmpl_state_type_desc,
                cd.cmpl_state_eftv_dttm
            FROM well_dmn wd
            JOIN cmpl_dmn cd
                ON wd.well_fac_id = cd.well_fac_id
            WHERE cd.actv_indc = 'Y'
              AND wd.actv_indc = 'Y'
              AND cd.cmpl_state_type_cde NOT IN ('PRPO','FUTR')
              AND wd.well_api_nbr IN ({placeholders})
              AND cd.cmpl_seq_nbr IS NOT NULL
        """

        # --- If you want the literal string interpolation version you pasted, use this instead ---
        # formatted_well_apis = ", ".join([f"'{a}'" for a in apis])
        # sql = f"""
        #     select wd.well_nme,wd.well_api_nbr,wd.fld_nme,
        #     cd.prim_purp_type_cde,
        #     cd.ENGR_STRG_NME, cd.CMPL_STATE_TYPE_DESC, cd.CMPL_STATE_EFTV_DTTM
        #     from well_dmn wd
        #     join cmpl_dmn cd
        #     on wd.well_fac_id = cd.well_fac_id
        #     where cd.actv_indc= 'Y' and wd.actv_indc = 'Y' and cd.cmpl_state_type_cde not in ('PRPO','FUTR')
        #     and wd.well_api_nbr in ({formatted_well_apis})
        #     and cd.CMPL_SEQ_NBR is not null
        # """

        try:
            conn = self.conn_manager.get_connection("odw")
            cur = conn.cursor()
            cur.execute(sql, params)  # pass params for bind placeholders

            rows = cur.fetchall() if cur.description else []
            cols = [c[0] for c in cur.description] if cur.description else []
            cur.close()
            conn.close()

            if not rows:
                messagebox.showinfo("No Results", "No data returned for the given APIs.")
                self.clear_table()
                return

            df = pd.DataFrame(rows, columns=cols)

            # Normalize date column & friendly order
            for col in ["CMPL_STATE_EFTV_DTTM", "cmpl_state_eftv_dttm"]:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")

            # Optional: sort results (API then effective date desc)
            if "WELL_API_NBR" in df.columns:
                sort_cols = ["WELL_API_NBR"]
                if "CMPL_STATE_EFTV_DTTM" in df.columns:
                    sort_cols.append("CMPL_STATE_EFTV_DTTM")
                    df = df.sort_values(sort_cols, ascending=[True, False])
                else:
                    df = df.sort_values(sort_cols)

            self.current_data = df
            self.display_dataframe(df)

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_table()
        except cx_Oracle.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}")
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

        # Column order & widths
        cols = list(df.columns)
        self.tree["columns"] = cols
        self.tree["displaycolumns"] = cols

        for col in cols:
            self.tree.heading(col, text=col, anchor="w")
            self.tree.column(col, width=self.tree_font.measure(col) + 24, stretch=False)

        # Insert rows with light formatting
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

            # Auto-grow columns based on content
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
    app = WellCompletionApp()
    app.mainloop()
