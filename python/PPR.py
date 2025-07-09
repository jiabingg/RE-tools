import os
import cx_Oracle
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd

# Oracle Connection Manager
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

    def get_connection(self, name):
        if name not in self._connections:
            raise ValueError(f"Unknown DB connection name: {name}")
        config = self._connections[name]
        try:
            return cx_Oracle.connect(
                user=config['user'],
                password=config['password'],
                dsn=config['dsn']
            )
        except cx_Oracle.Error as e:
            error_obj, = e.args
            raise ConnectionError(f"Failed to connect to Oracle DB '{name}': {error_obj.message}") from e

    def available_connections(self):
        return list(self._connections.keys())

# Abstract Base Class for Application Pages
class Page(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent)
        self.controller = controller

    def show_page_frame(self):
        self.tkraise()

# Page 1: Well API Input
class WellAPIInputPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()

    def create_widgets(self):
        label = tb.Label(self, text="Enter Well APIs (one per line):", font=("Helvetica", 14))
        label.pack(pady=10)

        self.well_api_text = scrolledtext.ScrolledText(self, wrap=tk.WORD, width=50, height=15, font=("Courier New", 10))
        self.well_api_text.pack(pady=10)

        default_apis = [
            "0401920171",
            "0401922081",
            "0401922236"
        ]
        self.well_api_text.insert(tk.END, "\n".join(default_apis))

        next_button = tb.Button(self, text="Next", command=self.go_to_next_page, bootstyle="primary")
        next_button.pack(pady=10)

    def go_to_next_page(self):
        well_apis = self.well_api_text.get("1.0", tk.END).strip().split('\n')
        well_apis = [api.strip() for api in well_apis if api.strip()]
        self.controller.shared_data["well_apis"] = well_apis
        self.controller.show_page(EngineeringStringPage)

# Page 2: Field Selection and Results Display
class EngineeringStringPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.available_fields = [
            "San Ardo", "Belridge", "Wilmington", "Coalinga", "Huntington Beach",
            "Elk Hills", "Lost Hills", "Arco Misc.", "Ventura", "Santa Maria",
            "Beta", "Midway Sunset", "Brea - Yorba Linda"
        ]
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None

    def create_widgets(self):
        # Field Selection Frame
        field_selection_frame = tb.LabelFrame(self, text="Select Fields", bootstyle="primary")
        field_selection_frame.pack(pady=10, padx=20, fill="x", expand=False)

        field_list_label = tb.Label(field_selection_frame, text="Available Fields:")
        field_list_label.pack(pady=(5,0), padx=5, anchor="w")

        listbox_frame = tb.Frame(field_selection_frame)
        listbox_frame.pack(pady=5, padx=5, fill="both", expand=True)

        self.field_listbox = tk.Listbox(listbox_frame, selectmode=tk.MULTIPLE, height=len(self.available_fields), font=("TkDefaultFont", 10))
        self.field_listbox.pack(side="left", fill="both", expand=True)

        field_list_scrollbar = tb.Scrollbar(listbox_frame, orient="vertical", command=self.field_listbox.yview)
        field_list_scrollbar.pack(side="right", fill="y")
        self.field_listbox.config(yscrollcommand=field_list_scrollbar.set)

        for field in self.available_fields:
            self.field_listbox.insert(tk.END, field)

        # Select/Clear All Buttons
        field_buttons_frame = tb.Frame(field_selection_frame)
        field_buttons_frame.pack(pady=(0,5), padx=5)

        select_all_btn = tb.Button(field_buttons_frame, text="Select All", command=self.select_all_fields, bootstyle="secondary")
        select_all_btn.grid(row=0, column=0, padx=5)

        clear_all_btn = tb.Button(field_buttons_frame, text="Clear All", command=self.clear_all_fields, bootstyle="secondary")
        clear_all_btn.grid(row=0, column=1, padx=5)

        # Query Execution Button
        execute_button = tb.Button(self, text="Run Query for Selected Fields", command=self.execute_field_query, bootstyle="info")
        execute_button.pack(pady=10)

        # Results Display (Treeview)
        self.tree_frame = tb.Frame(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        self.tree_scroll_y = tb.Scrollbar(self.tree_frame, orient="vertical")
        self.tree_scroll_y.pack(side="right", fill="y")
        self.tree_scroll_x = tb.Scrollbar(self.tree_frame, orient="horizontal")
        self.tree_scroll_x.pack(side="bottom", fill="x")

        self.result_tree = ttk.Treeview(self.tree_frame, show="headings",
                                        yscrollcommand=self.tree_scroll_y.set,
                                        xscrollcommand=self.tree_scroll_x.set)
        self.result_tree.pack(fill="both", expand=True)
        self.tree_scroll_y.config(command=self.result_tree.yview)
        self.tree_scroll_x.config(command=self.result_tree.xview)

        # Button to copy results to clipboard
        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        # Navigation Buttons
        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(WellAPIInputPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)

    def select_all_fields(self):
        """Selects all items in the field listbox."""
        self.field_listbox.selection_set(0, tk.END)

    def clear_all_fields(self):
        """Clears all selections in the field listbox."""
        self.field_listbox.selection_clear(0, tk.END)

    def execute_field_query(self):
        """Constructs and executes the query based on selected fields."""
        selected_indices = self.field_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Input Error", "Please select at least one field from the list.")
            return

        selected_fields = [self.field_listbox.get(i) for i in selected_indices]
        # Format field names for SQL IN clause: "'Field1', 'Field2', ..."
        formatted_fields = ', '.join([f"'{field}'" for field in selected_fields])

        # Define the base SQL query with a placeholder for the field names
        sql_query = f"""
        with T as
        (
        select cd.cmpl_nme,cd.cmpl_fac_id, well_api_nbr, engr_strg_nme,max(cf.eftv_Dttm) as last_inj_dte from cmpl_dmn cd
        join cmpl_mnly_fact cf
        on cd.cmpl_fac_id = cf.cmpl_fac_id
        where actv_indc = 'Y' and cd.fncl_fld_nme in ({formatted_fields}) and prim_purp_type_cde = 'INJ' and cmpl_state_type_cde in ('OPNL', 'TA')
        group by cd.cmpl_nme,cd.cmpl_fac_id,well_api_nbr,engr_strg_nme
        )

        select t.well_api_nbr,t.cmpl_nme,t.engr_strg_nme,opg.top_perf,opg.btm_perf
        from T
        join CURR_TOP_BTM_ACTL_WLBR_OPG opg
        on T.cmpl_fac_id = opg.cmpl_fac_id
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                # --- Existing WELL_API_NBR FILTERING LOGIC ---
                well_apis_to_filter = self.controller.shared_data.get("well_apis", [])
                if well_apis_to_filter:
                    if 'WELL_API_NBR' in df.columns:
                        original_rows = len(df)
                        df = df[df['WELL_API_NBR'].isin(well_apis_to_filter)]
                        if len(df) < original_rows:
                            messagebox.showinfo("Filtering Applied",
                                                f"Filtered results to show only {len(df)} rows with Well APIs from the previous page.")
                        if df.empty:
                            messagebox.showinfo("No Results After Filtering",
                                                "No rows found matching the entered Well APIs after applying the filter.")
                    else:
                        messagebox.showwarning("Filtering Warning",
                                                "Could not filter by Well API: 'WELL_API_NBR' column not found in query results.")
                # --- END WELL_API_NBR FILTERING LOGIC ---

                self.display_results(df)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "SQL query executed successfully. No results to display (e.g., DML statement).")
                self.clear_results()
                self.current_data = None

            cursor.close()
            conn.close()

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_results()
        except cx_Oracle.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}")
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()

    def display_results(self, df):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df.empty:
            messagebox.showinfo("No Results", "No data found for the executed query.")
            self.result_tree["columns"] = []
            return

        columns = list(df.columns)
        self.result_tree["columns"] = columns
        self.result_tree["displaycolumns"] = columns

        try:
            treeview_font_name = ttk.Style().lookup("Treeview", "font")
            self.tree_font = tkinter.font.Font(font=treeview_font_name)
        except Exception:
            self.tree_font = tkinter.font.Font(family="TkDefaultFont", size=10)
            print("Warning: Could not determine Treeview font from style, using TkDefaultFont.")

        for col in columns:
            self.result_tree.heading(col, text=col, anchor="w")
            self.result_tree.column(col, width=self.tree_font.measure(col) + 20, stretch=False)

        for index, row in df.iterrows():
            self.result_tree.insert("", "end", values=list(row))
            for i, item in enumerate(row):
                col_width = self.tree_font.measure(str(item)) + 10
                current_col_id = columns[i]
                if self.result_tree.column(current_col_id, width=None) < col_width:
                    self.result_tree.column(current_col_id, width=col_width)

    def clear_results(self):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)
        self.result_tree["columns"] = []
        self.current_data = None

    def copy_to_clipboard(self):
        if self.current_data is not None and not self.current_data.empty:
            try:
                self.current_data.to_clipboard(excel=True, index=False, header=True)
                messagebox.showinfo("Copy Success", "Results copied to clipboard (Excel format).")
            except Exception as e:
                messagebox.showerror("Copy Error", f"Failed to copy to clipboard: {e}")
        else:
            messagebox.showwarning("No Data", "No results to copy to clipboard.")

# Main Application Class
class MainApplication(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        # --- INCREASED GUI SIZE ---
        self.title("Oracle Field Data Puller")
        self.geometry("1200x1200") # Increased size
        # --- END INCREASED GUI SIZE ---

        self.container = tb.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (WellAPIInputPage, EngineeringStringPage):
            page_name = F.__name__
            frame = F(parent=self.container, controller=self)
            self.frames[page_name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.shared_data = {}

        self.show_page(WellAPIInputPage)

    def show_page(self, page_class):
        frame = self.frames[page_class.__name__]
        frame.show_page_frame()

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()