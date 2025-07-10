import os
import cx_Oracle
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd
from datetime import datetime, date, timedelta

# Oracle Connection Manager
class OracleConnectionManager:
    def __init__(self):
        # Define connection configurations.
        # Uses os.getenv to allow environment variables to override defaults for security.
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"  # lower-case for cx_Oracle compatibility
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
        """
        Returns a live Oracle connection for the given configuration name.
        Raises ConnectionError if connection fails.
        """
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
        """Returns a list of available connection names."""
        return list(self._connections.keys())

# Abstract Base Class for Application Pages
class Page(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent)
        self.controller = controller

    def show_page_frame(self):
        """Brings this specific page frame to the front."""
        self.tkraise()

    # --- Common Display, Clear, Copy Methods for all pages with Treeviews ---
    def display_results(self, df):
        """Displays DataFrame results in the Treeview widget with common sorting."""
        # Clear existing Treeview data
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df.empty:
            messagebox.showinfo("No Results", "No data found for the query.")
            self.result_tree["columns"] = [] # Clear columns if no data
            return

        # --- GLOBAL SORTING LOGIC ---
        # Ensure sorting columns exist before attempting to sort
        sort_columns = ['PRIM_PURP_TYPE_CDE', 'WELL_API_NBR']
        available_sort_columns = [col for col in sort_columns if col in df.columns]

        if len(available_sort_columns) > 0:
            try:
                # Convert WELL_API_NBR to string type to avoid numerical sorting issues if mixed types
                if 'WELL_API_NBR' in df.columns:
                    df['WELL_API_NBR'] = df['WELL_API_NBR'].astype(str)
                df.sort_values(by=available_sort_columns, inplace=True)
            except Exception as e:
                messagebox.showwarning("Sorting Warning", f"Error during sorting: {e}. Displaying unsorted data.")
        else:
            messagebox.showwarning("Sorting Warning", "Could not sort data: 'PRIM_PURP_TYPE_CDE' or 'WELL_API_NBR' column(s) not found.")
        # --- END GLOBAL SORTING LOGIC ---


        # Dynamically set columns based on DataFrame columns
        columns = list(df.columns)
        self.result_tree["columns"] = columns
        self.result_tree["displaycolumns"] = columns

        try:
            # Create a font object to measure text width for column auto-sizing
            # Correctly gets the font from the ttk.Style for the Treeview element
            treeview_font_name = ttk.Style().lookup("Treeview", "font")
            self.tree_font = tkinter.font.Font(font=treeview_font_name)
        except Exception:
            # Fallback to a default font if lookup fails
            self.tree_font = tkinter.font.Font(family="TkDefaultFont", size=10)
            print("Warning: Could not determine Treeview font from style, using TkDefaultFont.")

        for col in columns:
            self.result_tree.heading(col, text=col, anchor="w")
            # Set initial column width based on header text width
            # Use stretch=False to allow dynamic resizing based on content
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

# Page 1: Well API Input
class WellAPIInputPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()

    def create_widgets(self):
        label = tb.Label(self, text="Enter Well APIs (one per line, results on next page will be filtered by these):", font=("Helvetica", 14))
        label.pack(pady=10)

        # --- TEXTBOX HEIGHT CHANGE ---
        self.well_api_text = scrolledtext.ScrolledText(self, wrap=tk.WORD, width=50, height=25, font=("Courier New", 10))
        self.well_api_text.pack(pady=10)
        # --- END TEXTBOX HEIGHT CHANGE ---

        default_apis = [
            "0401920171",
            "0401922081",
            "0401922236"
        ]
        self.well_api_text.insert(tk.END, "\n".join(default_apis))

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        next_button = tb.Button(self, text="Next", command=self.go_to_next_page, bootstyle="primary")
        next_button.pack(pady=10)
        # --- END BUTTON FONT SIZE CHANGE ---

    def go_to_next_page(self):
        well_apis = self.well_api_text.get("1.0", tk.END).strip().split('\n')
        well_apis = [api.strip() for api in well_apis if api.strip()]
        self.controller.shared_data["well_apis"] = well_apis
        # Navigate to the new WellBasicDataPage (now Page 2)
        self.controller.show_page(WellBasicDataPage)

# New Page 2: Well Basic Data Display
class WellBasicDataPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None # Store DataFrame for clipboard copy

    def create_widgets(self):
        header_label = tb.Label(self, text="Well Basic Data", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        pull_data_btn = tb.Button(self, text="Pull Basic Well Data", command=self.pull_basic_data, bootstyle="info")
        pull_data_btn.pack(pady=10)
        # --- END BUTTON FONT SIZE CHANGE ---

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

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)
        # --- END BUTTON FONT SIZE CHANGE ---

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(WellAPIInputPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)

        next_button = tb.Button(nav_frame, text="Next (Field Selection)", command=lambda: self.controller.show_page(EngineeringStringPage), bootstyle="primary")
        next_button.grid(row=0, column=1, padx=10)
        # --- END BUTTON FONT SIZE CHANGE ---

    def pull_basic_data(self):
        """Pulls basic well data, filtered by Well APIs from Page 1."""
        well_apis_to_query = self.controller.shared_data.get("well_apis", [])
        if not well_apis_to_query:
            messagebox.showwarning("Input Error", "No Well APIs entered on the first page. Please go back.")
            self.clear_results()
            return

        # Format Well APIs for SQL IN clause
        formatted_well_apis = ', '.join([f"'{api}'" for api in well_apis_to_query])

        # --- UPDATED SQL QUERY for WellBasicDataPage (re-added cd.CMPL_SEQ_NBR is not null) ---
        sql_query = f"""
        select wd.well_nme,wd.well_api_nbr,wd.fld_nme,
        cd.prim_purp_type_cde,
        cd.ENGR_STRG_NME, cd.CMPL_STATE_TYPE_DESC, cd.CMPL_STATE_EFTV_DTTM
        from well_dmn wd
        join cmpl_dmn cd
        on wd.well_fac_id = cd.well_fac_id
        where cd.actv_indc= 'Y' and wd.actv_indc = 'Y' and cd.cmpl_state_type_cde not in ('PRPO','FUTR')
        and wd.well_api_nbr in ({formatted_well_apis})
        and cd.CMPL_SEQ_NBR is not null -- Re-added this condition
        """
        # --- END UPDATED SQL QUERY ---

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                # Convert date columns to datetime objects for proper comparison/display
                date_cols = ['CMPL_STATE_EFTV_DTTM']
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')

                self.display_results(df) # Call common display method which includes sorting
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
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}") # Consistent error message
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()

# Page 3: Field Selection and Results Display (Top Perf) - Formerly Page 2
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
        self.last_selected_fields = [] # Store selected fields for the next page

    def create_widgets(self):
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

        field_buttons_frame = tb.Frame(field_selection_frame)
        field_buttons_frame.pack(pady=(0,5), padx=5)

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        select_all_btn = tb.Button(field_buttons_frame, text="Select All", command=self.select_all_fields, bootstyle="secondary")
        select_all_btn.grid(row=0, column=0, padx=5)

        clear_all_btn = tb.Button(field_buttons_frame, text="Clear All", command=self.clear_all_fields, bootstyle="secondary")
        clear_all_btn.grid(row=0, column=1, padx=5)
        # --- END BUTTON FONT SIZE CHANGE ---

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        execute_button = tb.Button(self, text="Run Top Perf Query for Selected Fields", command=self.execute_field_query, bootstyle="info")
        execute_button.pack(pady=10)
        # --- END BUTTON FONT SIZE CHANGE ---

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

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)
        # --- END BUTTON FONT SIZE CHANGE ---

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(WellBasicDataPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)

        next_button = tb.Button(nav_frame, text="Next (Summary Data)", command=self.go_to_summary_page, bootstyle="primary")
        next_button.grid(row=0, column=1, padx=10)
        # --- END BUTTON FONT SIZE CHANGE ---

    def select_all_fields(self):
        self.field_listbox.selection_set(0, tk.END)

    def clear_all_fields(self):
        self.field_listbox.selection_clear(0, tk.END)

    def go_to_summary_page(self):
        selected_indices = self.field_listbox.curselection()
        self.last_selected_fields = [self.field_listbox.get(i) for i in selected_indices]
        if not self.last_selected_fields:
            messagebox.showwarning("Selection Required", "Please select at least one field before proceeding to the summary page.")
            return

        self.controller.shared_data["selected_fields"] = self.last_selected_fields
        self.controller.show_page(PerformanceSummaryPage)

    def execute_field_query(self):
        selected_indices = self.field_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Input Error", "Please select at least one field from the list.")
            return

        selected_fields = [self.field_listbox.get(i) for i in selected_indices]
        self.last_selected_fields = selected_fields
        self.controller.shared_data["selected_fields"] = selected_fields

        formatted_fields = ', '.join([f"'{field}'" for field in selected_fields])

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

                self.display_results(df) # Call common display method which includes sorting
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
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}") # Consistent error message
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()

# Page 4: Performance Summary - Formerly Page 3
class PerformanceSummaryPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.calculation_labels = {} # Initialized BEFORE create_widgets
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None


    def create_widgets(self):
        header_label = tb.Label(self, text="Performance Summary by Field", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        query_calc_frame = tb.Frame(self)
        query_calc_frame.pack(pady=10, padx=20, fill="x", expand=False)
        query_calc_frame.columnconfigure(0, weight=1) # Left column for date label/entry
        query_calc_frame.columnconfigure(1, weight=1) # Right column for button

        # Frame for Date Entry and Button on the same row
        top_row_frame = tb.Frame(query_calc_frame)
        top_row_frame.grid(row=0, column=0, columnspan=2, pady=5, sticky="ew")
        top_row_frame.columnconfigure(0, weight=1) # For date entry frame
        top_row_frame.columnconfigure(1, weight=1) # For button


        # Last Project Update Date input
        date_input_frame = tb.Frame(top_row_frame)
        date_input_frame.grid(row=0, column=0, padx=5, sticky="w")
        tb.Label(date_input_frame, text="Last Project Update Date:").pack(side="left", padx=5)
        self.project_update_date_entry = tb.DateEntry(date_input_frame, dateformat="%Y-%m-%d")
        self.project_update_date_entry.pack(side="left", padx=5)
        # Set default date to 2 years ago from today
        two_years_ago = date.today() - timedelta(days=730)
        self.project_update_date_entry.entry.delete(0, tk.END) # Clear any default ttkbootstrap might put
        self.project_update_date_entry.entry.insert(0, two_years_ago.strftime("%Y-%m-%d"))

        # --- MOVE BUTTON POSITION ---
        # Position the button in the second column of top_row_frame
        pull_data_btn = tb.Button(top_row_frame, text="Pull Performance Summary Data", command=self.pull_summary_data, bootstyle="info")
        pull_data_btn.grid(row=0, column=1, padx=5, sticky="e")
        # --- END MOVE BUTTON POSITION ---


        calc_label_frame = tb.LabelFrame(query_calc_frame, text="Calculations", bootstyle="info")
        # Adjusted row to 1 as row 0 is now occupied by top_row_frame
        calc_label_frame.grid(row=1, column=0, columnspan=2, pady=10, padx=5, sticky="nsew")
        calc_label_frame.columnconfigure(1, weight=1)

        calc_row = 0
        def add_calc_row(text_label, key):
            nonlocal calc_row
            tb.Label(calc_label_frame, text=text_label, anchor="w").grid(row=calc_row, column=0, sticky="ew", padx=5, pady=2)
            # Labels will be green from 'success' bootstyle, and bold font
            self.calculation_labels[key] = tb.Label(calc_label_frame, text="N/A", bootstyle="success", font=("TkDefaultFont", 10, "bold"))
            self.calculation_labels[key].grid(row=calc_row, column=1, sticky="ew", padx=5, pady=2)
            calc_row += 1

        add_calc_row("Total INJ wells (Active/TA):", "total_inj") # Updated description
        add_calc_row("Total PROD wells:", "total_prod")
        add_calc_row("Active Injectors (Last 2 yrs):", "active_injectors")
        add_calc_row("Idle Injectors (Last 2 yrs):", "idle_injectors")
        add_calc_row("Injectors Drilled Since Last Update:", "injectors_drilled")
        # Removed: add_calc_row("Injectors Abandoned Since Last Update:", "injectors_abandoned")
        add_calc_row("Active Producers (Last 2 yrs):", "active_producers")
        add_calc_row("Idle Producers (Last 2 yrs):", "idle_producers")
        add_calc_row("Producers Drilled Since Last Update:", "producers_drilled")
        add_calc_row("Producers Abandoned Since Last Update:", "producers_abandoned")


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

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)
        # --- END BUTTON FONT SIZE CHANGE ---

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        # --- BUTTON FONT SIZE CHANGE (via ttk.Style config in MainApplication) ---
        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(EngineeringStringPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)
        # --- END BUTTON FONT SIZE CHANGE ---


    def pull_summary_data(self):
        """Pulls data using the specific summary query based on selected fields and filters."""
        selected_fields = self.controller.shared_data.get("selected_fields", [])
        if not selected_fields:
            messagebox.showwarning("Missing Selection", "Please go back to the previous page and select fields first.")
            self.clear_calculations()
            return

        formatted_fields = ', '.join([f"'{field}'" for field in selected_fields])

        sql_query = f"""
        WITH T1 AS (
            SELECT cmpl_fac_id, eftv_dttm AS last_inj_dte FROM
            (
                SELECT cmpl_fac_id, eftv_dttm,aloc_stm_inj_dly_rte_qty, aloc_wtr_inj_dly_rte_qty,
                       DENSE_RANK() OVER (PARTITION BY cmpl_fac_id ORDER BY eftv_dttm DESC) AS rnk
                FROM cmpl_mnly_fact
                WHERE aloc_wtr_inj_dly_rte_qty >0 or aloc_stm_inj_dly_rte_qty > 0
            ) WHERE rnk = 1
        ),
        T2 AS (
            SELECT cmpl_fac_id, eftv_dttm AS last_prod_dte FROM
            (
                SELECT cmpl_fac_id, eftv_dttm,aloc_gros_prod_dly_rte_qty ,
                       DENSE_RANK() OVER (PARTITION BY cmpl_fac_id ORDER BY eftv_dttm DESC) AS rnk
                FROM cmpl_mnly_fact
                WHERE aloc_gros_prod_dly_rte_qty >0
            ) WHERE rnk = 1
        )

        SELECT wd.well_nme, wd.well_api_nbr, wd.fld_nme,
               cd.init_prod_dte, cd.init_inj_dte, cd.prim_purp_type_cde,
               cd.ENGR_STRG_NME,
               t1.last_inj_dte, t2.last_prod_dte,
               cd.CMPL_STATE_TYPE_DESC, cd.CMPL_STATE_EFTV_DTTM
        FROM well_dmn wd
        JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
        LEFT JOIN cmpl_non_ver_dmn cnd ON cd.cmpl_fac_id = cnd.cmpl_fac_id
        LEFT JOIN curr_cmpl_opnl_stat os ON cd.cmpl_fac_id = os.cmpl_fac_id
        JOIN cmpl_mnly_cum_fact ccf ON (cd.cmpl_fac_id = ccf.cmpl_fac_id AND eftv_dttm = TRUNC(SYSDATE, 'mm'))
        LEFT JOIN T1 ON cd.cmpl_fac_id = T1.cmpl_fac_id
        LEFT JOIN T2 ON cd.cmpl_fac_id = T2.cmpl_fac_id
        WHERE cd.actv_indc = 'Y' AND wd.actv_indc = 'Y'
        AND cd.fncl_fld_nme IN ({formatted_fields})
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                date_cols = ['LAST_INJ_DTE', 'LAST_PROD_DTE', 'INIT_INJ_DTE', 'INIT_PROD_DTE', 'CMPL_STATE_EFTV_DTTM']
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')


                well_apis_to_filter = self.controller.shared_data.get("well_apis", [])
                if well_apis_to_filter:
                    if 'WELL_API_NBR' in df.columns:
                        original_rows_before_api_filter = len(df)
                        df = df[df['WELL_API_NBR'].isin(well_apis_to_filter)]
                        if len(df) < original_rows_before_api_filter:
                            messagebox.showinfo("Well API Filtering Applied",
                                                f"Filtered summary results to show only {len(df)} rows matching Well APIs from Page 1.")
                        if df.empty:
                            messagebox.showinfo("No Results After Well API Filtering",
                                                "No summary rows found matching the entered Well APIs after applying the filter.")
                    else:
                        messagebox.showwarning("Well API Filtering Warning",
                                                "Could not filter by Well API: 'WELL_API_NBR' column not found in summary query results.")

                self.display_results(df) # Call common display method which includes sorting
                self.current_data = df # Store the filtered DF for calculations and copy

                # Perform calculations on the filtered data
                self.perform_calculations(df)

            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "SQL query executed successfully. No results to display.")
                self.clear_results()
                self.clear_calculations()
                self.current_data = None

            cursor.close()
            conn.close()

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_results()
            self.clear_calculations()
        except cx_Oracle.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}") # Consistent error message
            self.clear_results()
            self.clear_calculations()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()
            self.clear_calculations()

    def perform_calculations(self, df):
        """Performs the requested calculations and updates the labels."""
        today = date.today()
        try:
            project_update_date_str = self.project_update_date_entry.entry.get()
            project_update_date = datetime.strptime(project_update_date_str, "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("Date Error", "Invalid 'Last Project Update Date' format. Please use %Y-%m-%d.")
            self.clear_calculations()
            return

        # Ensure comparison dates are pandas Timestamps for consistency with df columns
        today_ts = pd.Timestamp(today)
        project_update_date_ts = pd.Timestamp(project_update_date)
        two_years_ago_ts = pd.Timestamp(today - timedelta(days=730)) # Consistent 730 days for 2 years

        # Filter main DataFrame into Injectors and Producers
        inj_df = df[df['PRIM_PURP_TYPE_CDE'] == 'INJ']
        prod_df = df[df['PRIM_PURP_TYPE_CDE'] == 'PROD']

        # Helper function to update label
        def update_label(key, value):
            if key in self.calculation_labels:
                self.calculation_labels[key].config(text=str(value))

        # 1. Total INJ wells
        # Criteria: PRIM_PURP_TYPE_CDE = "INJ" AND CMPL_STATE_TYPE_DESC <> 'Permanently Abandoned'
        total_inj = len(inj_df[inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned'])
        update_label("total_inj", total_inj)

        # 2. Total PROD wells (No change in criteria)
        total_prod = len(prod_df)
        update_label("total_prod", total_prod)

        # Active Injectors
        # Criteria: PRIM_PURP_TYPE_CDE = "INJ" AND LAST_INJ_DTE >= Today()-730
        active_inj = inj_df[inj_df['LAST_INJ_DTE'] >= two_years_ago_ts]
        update_label("active_injectors", len(active_inj))

        # Idle Injectors
        # Criteria: PRIM_PURP_TYPE_CDE = "INJ", LAST_INJ_DTE < Today()-730, CMPL_STATE_TYPE_DESC <> 'Permanently Abandoned'
        idle_inj = inj_df[(inj_df['LAST_INJ_DTE'] < two_years_ago_ts) &
                          (inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]
        update_label("idle_injectors", len(idle_inj))

        # Injectors Drilled Since Last Update
        # Criteria: PRIM_PURP_TYPE_CDE = "INJ", INIT_INJ_DTE > Last Project Update Date, CMPL_STATE_TYPE_DESC <> 'Permanently Abandoned'
        inj_drilled = inj_df[(inj_df['INIT_INJ_DTE'] > project_update_date_ts) &
                             (inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]
        update_label("injectors_drilled", len(inj_drilled))

        # Injectors Abandoned Since Last Update - THIS CALCULATION IS REMOVED as per request
        # No corresponding update_label call here.


        # Active Producers
        # Criteria: PRIM_PURP_TYPE_CDE = "PROD" AND LAST_PROD_DTE >= Today()-730
        active_prod = prod_df[prod_df['LAST_PROD_DTE'] >= two_years_ago_ts]
        update_label("active_producers", len(active_prod))

        # Idle Producers
        # Criteria: PRIM_PURP_TYPE_CDE = "PROD", LAST_PROD_DTE < Today()-730, CMPL_STATE_TYPE_DESC <> 'Permanently Abandoned'
        idle_prod = prod_df[(prod_df['LAST_PROD_DTE'] < two_years_ago_ts) &
                            (prod_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]
        update_label("idle_producers", len(idle_prod))

        # Producers Drilled Since Last Update
        # Criteria: PRIM_PURP_TYPE_CDE = "PROD" AND INIT_PROD_DTE > Last Project Update Date
        prod_drilled = prod_df[prod_df['INIT_PROD_DTE'] > project_update_date_ts]
        update_label("producers_drilled", len(prod_drilled))

        # Producers Abandoned Since Last Update
        # Criteria: PRIM_PURP_TYPE_CDE = "PROD", CMPL_STATE_TYPE_DESC = 'Permanently Abandoned', CMPL_STATE_EFTV_DTTM > Last Project Update Date
        prod_abandoned = prod_df[(prod_df['CMPL_STATE_TYPE_DESC'] == 'Permanently Abandoned') &
                                 (prod_df['CMPL_STATE_EFTV_DTTM'] > project_update_date_ts)]
        update_label("producers_abandoned", len(prod_abandoned))


    def clear_calculations(self):
        """Resets all calculation labels to N/A."""
        for key in self.calculation_labels:
            self.calculation_labels[key].config(text="N/A")


    def display_results(self, df):
        """Displays DataFrame results in the Treeview widget with common sorting."""
        # This method is inherited from the Page class and is intended to be shared.
        # Calling super().display_results(df) ensures the common logic is used.
        super().display_results(df)


    def clear_results(self):
        """Clears all data and columns from the Treeview."""
        super().clear_results()


    def copy_to_clipboard(self):
        """Copies the current Treeview data (from DataFrame) to clipboard in Excel format."""
        super().copy_to_clipboard()


# Main Application Class
class MainApplication(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Oracle Field Data Puller")
        self.geometry("1400x1100") # Increased height to 1100

        # Configure ttk.Button font globally
        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.container = tb.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        # List all pages to be created in order of appearance
        for F in (WellAPIInputPage, WellBasicDataPage, EngineeringStringPage, PerformanceSummaryPage):
            page_name = F.__name__
            frame = F(parent=self.container, controller=self)
            self.frames[page_name] = frame
            # All frames occupy the same grid cell (0,0)
            # This allows tkraise() to bring the desired frame to the top
            frame.grid(row=0, column=0, sticky="nsew")

        self.shared_data = {} # Dictionary to share data (like Well APIs, selected fields) between pages

        # Display the first page upon application startup
        self.show_page(WellAPIInputPage)

    def show_page(self, page_class):
        """
        Switches the currently displayed page.
        It retrieves the frame instance from self.frames and calls its show_page_frame method.
        """
        frame = self.frames[page_class.__name__]
        frame.show_page_frame() # Delegates the tkraise() call to the page itself

# Entry point for the application
if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()