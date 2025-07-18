import os
import cx_Oracle
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd
from datetime import datetime, date, timedelta

# Oracle Connection Manager (Copied from PPR.py)
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

# Abstract Base Class for Application Pages (Re-introduced for consistency with PPR.py structure)
class Page(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent)
        self.controller = controller
        self.conn_manager = OracleConnectionManager()
        self.current_data = None # To store DataFrame for clipboard copy
        self.result_tree = None # Will be set in create_widgets of child classes

    def show_page_frame(self):
        """Brings this specific page frame to the front."""
        self.tkraise()

    # --- Common Display, Clear, Copy Methods for all pages with Treeviews ---
    def display_results(self, df):
        """Displays DataFrame results in the Treeview widget with column auto-sizing."""
        if self.result_tree is None:
            messagebox.showerror("Error", "Treeview widget not initialized for this page.")
            return

        # Clear existing Treeview data
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df.empty:
            messagebox.showinfo("No Results", "No data found for the query.")
            self.result_tree["columns"] = [] # Clear columns if no data
            return

        # Dynamically set columns based on DataFrame columns
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
            display_values = []
            for item in row:
                if isinstance(item, pd.Timestamp):
                    display_values.append(item.strftime('%Y-%m-%d %H:%M:%S') if not pd.isna(item) else '')
                elif pd.isna(item):
                    display_values.append('')
                else:
                    display_values.append(item)
            self.result_tree.insert("", "end", values=display_values)

            for i, item in enumerate(display_values):
                col_width = self.tree_font.measure(str(item)) + 10
                current_col_id = columns[i]
                if self.result_tree.column(current_col_id, width=None) < col_width:
                    self.result_tree.column(current_col_id, width=col_width)

    def clear_results(self):
        """Clears all data and columns from the Treeview."""
        if self.result_tree:
            for i in self.result_tree.get_children():
                self.result_tree.delete(i)
            self.result_tree["columns"] = []
        self.current_data = None

    def copy_to_clipboard(self):
        """Copies the current Treeview data (from DataFrame) to clipboard in Excel format."""
        if self.current_data is not None and not self.current_data.empty:
            try:
                self.current_data.to_clipboard(excel=True, index=False, header=True)
                messagebox.showinfo("Copy Success", "Results copied to clipboard (Excel format).")
            except Exception as e:
                messagebox.showerror("Copy Error", f"Failed to copy to clipboard: {e}")
        else:
            messagebox.showwarning("No Data", "No results to copy to clipboard.")
    # --- End Common Methods ---


# Page 1: Field Data Selection
class FieldDataPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        self.fields = [
            "Coalinga", "Cymric", "Lost Hills", "McKittrick", "Midway-Sunset",
            "North Belridge", "San Ardo", "South Belridge", "Ventura"
        ]
        self.field_vars = {} # To hold IntVar for each checkbox

        self.create_widgets()

    def create_widgets(self):
        header_label = tb.Label(self, text="Well & Completion Data by Selected Field(s)", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        # Field Selection Frame
        field_selection_frame = tb.LabelFrame(self, text="Select Fields", bootstyle="primary")
        field_selection_frame.pack(pady=10, padx=20, fill="x")

        num_columns = 3
        for i, field in enumerate(self.fields):
            row = i // num_columns
            col = i % num_columns
            var = tk.IntVar()
            cb = tb.Checkbutton(field_selection_frame, text=field, variable=var)
            cb.grid(row=row, column=col, padx=10, pady=2, sticky="w")
            self.field_vars[field] = var

        # Select All / Clear All Buttons
        select_buttons_frame = tb.Frame(field_selection_frame)
        select_buttons_frame.grid(row=len(self.fields)//num_columns + (1 if len(self.fields)%num_columns != 0 else 0),
                                  column=0, columnspan=num_columns, pady=5)

        select_all_btn = tb.Button(select_buttons_frame, text="Select All", command=self.select_all_fields, bootstyle="secondary")
        select_all_btn.pack(side="left", padx=5)

        clear_all_btn = tb.Button(select_buttons_frame, text="Clear All", command=self.clear_all_fields, bootstyle="secondary")
        clear_all_btn.pack(side="left", padx=5)

        # Query Button
        pull_data_btn = tb.Button(self, text="Pull Field Data", command=self.pull_field_data, bootstyle="info")
        pull_data_btn.pack(pady=10)

        # Treeview for results
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

        # Copy to Clipboard Button
        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        # Navigation to New Well Page
        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)
        new_well_page_button = tb.Button(nav_frame, text="Go to New Well Data",
                                          command=lambda: self.controller.show_page(NewWellDataPage),
                                          bootstyle="primary")
        new_well_page_button.pack()

    def select_all_fields(self):
        for var in self.field_vars.values():
            var.set(1)

    def clear_all_fields(self):
        for var in self.field_vars.values():
            var.set(0)

    def pull_field_data(self):
        selected_fields = [field for field, var in self.field_vars.items() if var.get() == 1]

        if not selected_fields:
            messagebox.showwarning("Input Error", "Please select at least one field.")
            self.clear_results()
            return

        formatted_fields = ', '.join([f"'{field}'" for field in selected_fields])

        sql_query = f"""
        select distinct
            wd.fld_nme, cd.cmpl_nme, cd.cmpl_fac_id, wd.well_nme, wd.well_api_nbr,
            cd.init_prod_dte, cd.init_inj_dte, cd.prim_purp_type_cde, cd.in_svc_indc,
            os.curr_stat, os.off_rsn_type_cde, os.off_rsn_sub_type_cde, cd.ENGR_STRG_NME,
            cd.RSVR_ENGR_STRG_NME, wbd.TOTAL_DPTH_XCRD_QTY, wbd.TOTAL_DPTH_YCRD_QTY
        from well_dmn wd
        join cmpl_dmn cd
        on wd.well_fac_id = cd.well_fac_id
        left join curr_cmpl_opnl_stat os
        on cd.cmpl_fac_id = os.cmpl_fac_id
        left join wlbr_dmn wbd
        on cd.well_fac_id = wbd.well_fac_id
        where cd.actv_indc= 'Y'
        and wd.actv_indc = 'Y'
        and cd.cmpl_state_type_cde not in ('PRPO','FUTR')
        and wd.fld_nme in ({formatted_fields})
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                date_cols = ['INIT_PROD_DTE', 'INIT_INJ_DTE']
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')

                self.display_results(df)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "SQL query executed successfully. No results to display.")
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


# Page 2: New Well Data
class NewWellDataPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()

    def create_widgets(self):
        header_label = tb.Label(self, text="New Well Data (Drilled Since Jan 1st of Current Year)", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        # New Wells Checkbox
        self.new_wells_var = tk.IntVar() # Variable for the new wells checkbox (local to this page)
        new_wells_cb = tb.Checkbutton(self, text="Only New Wells (Automatically Checked for this Page)",
                                      variable=self.new_wells_var, state='disabled') # Disabled as it's the purpose of the page
        new_wells_cb.pack(pady=5)
        self.new_wells_var.set(1) # Automatically set to checked for this page

        # Query Button
        pull_data_btn = tb.Button(self, text="Pull New Well Data", command=self.pull_new_well_data, bootstyle="info")
        pull_data_btn.pack(pady=10)

        # Treeview for results
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

        # Copy to Clipboard Button
        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        # Navigation to Field Data Page
        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)
        field_data_page_button = tb.Button(nav_frame, text="Go to Field Data Selection",
                                            command=lambda: self.controller.show_page(FieldDataPage),
                                            bootstyle="primary")
        field_data_page_button.pack()

    def pull_new_well_data(self):
        # This page inherently pulls new wells, so no need for explicit checkbox check
        # The query will not be limited by fields
        current_year = datetime.now().year
        first_day_of_year = date(current_year, 1, 1).strftime('%Y-%m-%d') # Example: 2025-01-01

        sql_query = f"""
        select distinct
            wd.fld_nme, cd.cmpl_nme, cd.cmpl_fac_id, wd.well_nme, wd.well_api_nbr,
            cd.init_prod_dte, cd.init_inj_dte, cd.prim_purp_type_cde, cd.in_svc_indc,
            os.curr_stat, os.off_rsn_type_cde, os.off_rsn_sub_type_cde, cd.ENGR_STRG_NME,
            cd.RSVR_ENGR_STRG_NME, wbd.TOTAL_DPTH_XCRD_QTY, wbd.TOTAL_DPTH_YCRD_QTY
        from well_dmn wd
        join cmpl_dmn cd
        on wd.well_fac_id = cd.well_fac_id
        left join curr_cmpl_opnl_stat os
        on cd.cmpl_fac_id = os.cmpl_fac_id
        left join wlbr_dmn wbd
        on cd.well_fac_id = wbd.well_fac_id
        where cd.actv_indc= 'Y'
        and wd.actv_indc = 'Y'
        and cd.cmpl_state_type_cde not in ('PRPO','FUTR')
        AND (cd.init_inj_dte >= TO_DATE('{first_day_of_year}', 'YYYY-MM-DD') OR cd.init_prod_dte >= TO_DATE('{first_day_of_year}', 'YYYY-MM-DD'))
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                date_cols = ['INIT_PROD_DTE', 'INIT_INJ_DTE']
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')

                self.display_results(df)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "SQL query executed successfully. No results to display.")
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


# Main Application Class
class MainApplication(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Well Data Puller") # Changed application title
        self.geometry("1000x800")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))
        s.configure("TLabel", font=("Helvetica", 10)) # Ensure labels are readable

        self.container = tb.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (FieldDataPage, NewWellDataPage):
            page_name = F.__name__
            frame = F(parent=self.container, controller=self)
            self.frames[page_name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        # Display the first page upon application startup
        self.show_page(FieldDataPage)

    def show_page(self, page_class):
        """
        Switches the currently displayed page.
        It retrieves the frame instance from self.frames and calls its show_page_frame method.
        """
        frame = self.frames[page_class.__name__]
        frame.show_page_frame() # Delegates the tkraise() call to the page itself

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()