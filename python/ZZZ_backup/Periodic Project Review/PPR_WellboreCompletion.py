import os
import oracledb
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd

# --- CONNECTION FIX: Enable Thick Mode (same as PPR.py) ---
try:
    oracledb.init_oracle_client()
except Exception as e:
    print(f"Warning: Could not initialize Oracle thick client. {e}")
# -----------------------------------------


class OracleConnectionManager:
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"
            },
            "sandbox": {
                "user": os.getenv("DB_USER_SANDBOX", "engsb"),
                "password": os.getenv("DB_PASSWORD_SANDBOX", "Engine33r_SB"),
                "dsn": "odw"
            },
            "openwells": {
                "user": os.getenv("DB_USER_OW", "gen_user"),
                "password": os.getenv("DB_PASSWORD_OW", "allusers"),
                "dsn": "owdb1"
            }
        }

    def get_connection(self, name="odw"):
        if name not in self._connections:
            raise ValueError(f"Unknown DB connection name: {name}")
        config = self._connections[name]
        try:
            return oracledb.connect(
                user=config['user'],
                password=config['password'],
                dsn=config['dsn']
            )
        except oracledb.Error as e:
            error_obj, = e.args
            raise ConnectionError(f"Failed to connect to Oracle DB '{name}': {error_obj.message}") from e


def format_well_api_list(raw_api_list):
    cleaned = []
    for item in raw_api_list:
        if item is None:
            continue
        api = str(item).strip()
        if not api:
            continue
        if api not in cleaned:
            cleaned.append(api)
    if not cleaned:
        return None
    escaped = [f"'{x.replace("'", "''")}'" for x in cleaned]
    return ", ".join(escaped)


class Page(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.current_data = None

    def show_page_frame(self):
        self.tkraise()

    def display_results(self, df):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df is None or df.empty:
            messagebox.showinfo("No Results", "No data found for the query.")
            self.result_tree["columns"] = []
            self.current_data = pd.DataFrame()
            return

        df_copy = df.copy()
        columns = list(df_copy.columns)

        self.result_tree["columns"] = columns
        self.result_tree["displaycolumns"] = columns

        try:
            treeview_font_name = ttk.Style().lookup("Treeview", "font")
            tree_font = tkinter.font.Font(font=treeview_font_name)
        except Exception:
            tree_font = tkinter.font.Font(family="TkDefaultFont", size=10)

        for col in columns:
            self.result_tree.heading(col, text=col, anchor="w")
            self.result_tree.column(col, width=tree_font.measure(col) + 20, stretch=False)

        for _, row in df_copy.iterrows():
            display_values = []
            for item in row:
                if isinstance(item, pd.Timestamp):
                    display_values.append(item.strftime('%Y-%m-%d') if not pd.isna(item) else '')
                elif pd.isna(item):
                    display_values.append('')
                else:
                    display_values.append(item)
            self.result_tree.insert("", "end", values=display_values)

            for i, item in enumerate(display_values):
                col = columns[i]
                width = tree_font.measure(str(item)) + 10
                if self.result_tree.column(col, width=None) < width:
                    self.result_tree.column(col, width=width)

        self.current_data = df_copy

    def clear_results(self):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)
        self.result_tree["columns"] = []
        self.current_data = pd.DataFrame()

    def copy_to_clipboard(self):
        if self.current_data is not None and not self.current_data.empty:
            try:
                self.current_data.to_clipboard(excel=True, index=False, header=True)
                messagebox.showinfo("Copy Success", "Results copied to clipboard (Excel format).")
            except Exception as e:
                messagebox.showerror("Copy Error", f"Failed to copy to clipboard: {e}")
        else:
            messagebox.showwarning("No Data", "No results to copy to clipboard.")


class WellboreCompletionPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.conn_manager = OracleConnectionManager()
        self.create_widgets()

    def create_widgets(self):
        header_label = tb.Label(self, text="Wellbore Completion Lookup", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        info_label = tb.Label(self, text="Enter API numbers (one per line). Click Pull Data to execute query.", font=("Helvetica", 11))
        info_label.pack(pady=5)

        self.api_text = scrolledtext.ScrolledText(self, wrap=tk.WORD, width=50, height=15, font=("Courier New", 10))
        self.api_text.pack(padx=20, pady=5, fill="both", expand=False)

        default_example = "0403038737"
        self.api_text.insert(tk.END, default_example)

        button_frame = tb.Frame(self)
        button_frame.pack(pady=10)

        pull_btn = tb.Button(button_frame, text="Pull Data", command=self.pull_data, bootstyle="info")
        pull_btn.grid(row=0, column=0, padx=5)

        clear_btn = tb.Button(button_frame, text="Clear", command=self.clear_results, bootstyle="warning")
        clear_btn.grid(row=0, column=1, padx=5)

        copy_btn = tb.Button(button_frame, text="Copy Results", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_btn.grid(row=0, column=2, padx=5)

        self.tree_frame = tb.Frame(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        self.tree_scroll_y = tb.Scrollbar(self.tree_frame, orient="vertical")
        self.tree_scroll_y.pack(side="right", fill="y")
        self.tree_scroll_x = tb.Scrollbar(self.tree_frame, orient="horizontal")
        self.tree_scroll_x.pack(side="bottom", fill="x")

        self.result_tree = ttk.Treeview(self.tree_frame, show="headings", yscrollcommand=self.tree_scroll_y.set, xscrollcommand=self.tree_scroll_x.set)
        self.result_tree.pack(fill="both", expand=True)
        self.tree_scroll_y.config(command=self.result_tree.yview)
        self.tree_scroll_x.config(command=self.result_tree.xview)

    def pull_data(self):
        raw = self.api_text.get("1.0", tk.END).strip().splitlines()
        well_apis = [a.strip() for a in raw if a.strip()]
        well_apis = list(dict.fromkeys(well_apis))

        if not well_apis:
            messagebox.showwarning("Input Error", "Please provide at least one API number.")
            return

        formatted = format_well_api_list(well_apis)
        if not formatted:
            messagebox.showwarning("Input Error", "No valid API numbers found after cleaning input.")
            return

        sql_query = f"""
SELECT
    wd.wlbr_nme                    AS well_name,
    cd.opnl_fld                    AS field_name,
    cd.cmpl_nme                    AS completion_name,
    cd.well_api_nbr                AS api_number,
    wd.wlbr_api_suff_nbr           AS wellbore_suffix,
    wd.wlbr_incl_type_desc         AS wellbore_type,
    cd.cmpl_state_type_cde         AS status,
    cd.in_svc_indc                 AS in_service,
    cd.init_prod_dte               AS initial_prod_date
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
WHERE cd.actv_indc = 'Y' AND cd.well_api_nbr IN ({formatted})
ORDER BY cd.well_api_nbr, wd.wlbr_api_suff_nbr, cd.cmpl_nme
"""

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                date_cols = ['INITIAL_PROD_DATE']
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')

                self.display_results(df)
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "SQL query executed successfully, no results returned.")
                self.clear_results()

            cursor.close()
            conn.close()

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_results()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}")
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {e}")
            self.clear_results()


class MainApplication(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("PPR Wellbore Completion Lookup")
        self.geometry("1200x900")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.container = tb.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        frame = WellboreCompletionPage(parent=self.container, controller=self)
        self.frames[WellboreCompletionPage.__name__] = frame
        frame.grid(row=0, column=0, sticky="nsew")

        self.show_page(WellboreCompletionPage)

    def show_page(self, page_class):
        frame = self.frames[page_class.__name__]
        frame.show_page_frame()


if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()
