import os
import oracledb
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd
from datetime import datetime, date, timedelta

# --- CONNECTION FIX: Enable Thick Mode ---
# This resolves the "DPY-3015: password verifier type 0x939 is not supported" error
try:
    oracledb.init_oracle_client()
except Exception as e:
    print(f"Warning: Could not initialize Oracle thick client. {e}")
# -----------------------------------------

# Oracle Connection Manager
class OracleConnectionManager:
    def __init__(self):
        # Define connection configurations.
        # Uses os.getenv to allow environment variables to override defaults for security.
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"  # lower-case for oracledb compatibility
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
            return oracledb.connect(
                user=config['user'],
                password=config['password'],
                dsn=config['dsn']
            )
        except oracledb.Error as e:
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
    def display_results(self, df, apply_global_sort=True): # Added apply_global_sort parameter
        """Displays DataFrame results in the Treeview widget with common sorting."""
        # Clear existing Treeview data
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df.empty:
            messagebox.showinfo("No Results", "No data found for the query.")
            self.result_tree["columns"] = [] # Clear columns if no data
            return

        # --- GLOBAL SORTING LOGIC (now conditional) ---
        if apply_global_sort:
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
                pass # Suppress warning for pages that don't need these columns (like Daily Injection)
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
                    # --- UPDATED DATE FORMAT ---
                    display_values.append(item.strftime('%Y-%m-%d') if not pd.isna(item) else '')
                    # --- END UPDATED DATE FORMAT ---
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

# Page 2: Well Basic Data Display
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

        next_button = tb.Button(nav_frame, text="Next (Top Perf)", command=lambda: self.controller.show_page(EngineeringStringPage), bootstyle="primary")
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

        sql_query = f"""
        select wd.well_nme,wd.well_api_nbr,wd.fld_nme,
        cd.prim_purp_type_cde,
        cd.ENGR_STRG_NME, cd.CMPL_STATE_TYPE_DESC, cd.CMPL_STATE_EFTV_DTTM
        from well_dmn wd
        join cmpl_dmn cd
        on wd.well_fac_id = cd.well_fac_id
        where cd.actv_indc= 'Y' and wd.actv_indc = 'Y' and cd.cmpl_state_type_cde not in ('PRPO','FUTR')
        and wd.well_api_nbr in ({formatted_well_apis})
        and cd.CMPL_SEQ_NBR is not null
        """

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
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}") # Consistent error message
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()

# Page 3: Top Perf Data
class EngineeringStringPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.available_fields = [ # Kept for consistency of attribute, but not used in UI or query filtering
            "San Ardo", "Belridge", "Wilmington", "Coalinga", "Huntington Beach",
            "Elk Hills", "Lost Hills", "Arco Misc.", "Ventura", "Santa Maria",
            "Beta", "Midway Sunset", "Brea - Yorba Linda"
        ]
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None

    def create_widgets(self):
        # The execute button and treeview will now be closer to the top
        execute_button = tb.Button(self, text="Run Top Perf Query", command=self.execute_field_query, bootstyle="info")
        execute_button.pack(pady=10)

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

        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(WellBasicDataPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)

        next_button = tb.Button(nav_frame, text="Next (Summary Data)", command=self.go_to_summary_page, bootstyle="primary")
        next_button.grid(row=0, column=1, padx=10)

    def go_to_summary_page(self):
        self.controller.shared_data["selected_fields"] = [] # Pass empty list, as no fields are selected here anymore
        self.controller.show_page(PerformanceSummaryPage)

    def execute_field_query(self):
        well_apis_to_query = self.controller.shared_data.get("well_apis", [])
        if not well_apis_to_query:
            messagebox.showwarning("Input Error", "No Well APIs entered on the first page. Please go back to Page 1.")
            self.clear_results()
            return
        formatted_well_apis = ', '.join([f"'{api}'" for api in well_apis_to_query])

        sql_query = f"""
        with T as
        (
        select cd.cmpl_nme, cd.cmpl_fac_id, wd.well_api_nbr, engr_strg_nme, max(cf.eftv_Dttm) as last_inj_dte
        from cmpl_dmn cd
        join cmpl_mnly_fact cf on cd.cmpl_fac_id = cf.cmpl_fac_id
        join well_dmn wd on cd.well_fac_id = wd.well_fac_id -- Join well_dmn here to filter by well_api_nbr
        where cd.actv_indc = 'Y'
        and wd.actv_indc = 'Y' -- Ensure the well is active as well
        and wd.well_api_nbr in ({formatted_well_apis}) -- Filter by WELL_API_NBR
        and cmpl_state_type_cde in ('OPNL', 'TA')
        group by cd.cmpl_nme, cd.cmpl_fac_id, wd.well_api_nbr, engr_strg_nme
        )

        select t.well_api_nbr, t.cmpl_nme, t.engr_strg_nme, opg.top_perf, opg.btm_perf
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
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}") # Consistent error message
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()

# Page 4: Performance Summary
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


        calc_label_frame = ttk.LabelFrame(query_calc_frame, text="Calculations", style="info.TLabelframe")
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

        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(EngineeringStringPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)
        
        next_button = tb.Button(nav_frame, text="Next (Avg Tubing Pressure)", command=lambda: self.controller.show_page(TubingPressurePage), bootstyle="primary")
        next_button.grid(row=0, column=1, padx=10)


    def pull_summary_data(self):
        well_apis_to_query = self.controller.shared_data.get("well_apis", [])
        if not well_apis_to_query:
            messagebox.showwarning("Input Error", "No Well APIs entered on the first page. Please go back to Page 1.")
            self.clear_calculations()
            return
        formatted_well_apis = ', '.join([f"'{api}'" for api in well_apis_to_query])

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
        LEFT JOIN T1 ON cd.cmpl_fac_id = T1.cmpl_fac_id
        LEFT JOIN T2 ON cd.cmpl_fac_id = T2.cmpl_fac_id
        WHERE
            cd.actv_indc = 'Y'
            AND wd.actv_indc = 'Y'
            AND wd.well_api_nbr IN ({formatted_well_apis}) 
            AND cd.prim_purp_type_cde IN ('PROD', 'INJ') 
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

                self.display_results(df) 
                self.current_data = df 

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
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}") 
            self.clear_results()
            self.clear_calculations()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()
            self.clear_calculations()

    def perform_calculations(self, df):
        today = date.today()
        try:
            project_update_date_str = self.project_update_date_entry.entry.get()
            project_update_date = datetime.strptime(project_update_date_str, "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("Date Error", "Invalid 'Last Project Update Date' format. Please use %Y-%m-%d.")
            self.clear_calculations()
            return

        today_ts = pd.Timestamp(today)
        project_update_date_ts = pd.Timestamp(project_update_date)
        two_years_ago_ts = pd.Timestamp(today - timedelta(days=730))

        inj_df = df[df['PRIM_PURP_TYPE_CDE'] == 'INJ']
        prod_df = df[df['PRIM_PURP_TYPE_CDE'] == 'PROD']

        def update_label(key, value):
            if key in self.calculation_labels:
                self.calculation_labels[key].config(text=str(value))

        total_inj = len(inj_df[inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned'])
        update_label("total_inj", total_inj)

        total_prod = len(prod_df)
        update_label("total_prod", total_prod)

        active_inj = inj_df[inj_df['LAST_INJ_DTE'] >= two_years_ago_ts]
        update_label("active_injectors", len(active_inj))

        idle_inj = inj_df[(inj_df['LAST_INJ_DTE'] < two_years_ago_ts) &
                          (inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]
        update_label("idle_injectors", len(idle_inj))

        inj_drilled = inj_df[(inj_df['INIT_INJ_DTE'] > project_update_date_ts) &
                             (inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]
        update_label("injectors_drilled", len(inj_drilled))

        active_prod = prod_df[prod_df['LAST_PROD_DTE'] >= two_years_ago_ts]
        update_label("active_producers", len(active_prod))

        idle_prod = prod_df[(prod_df['LAST_PROD_DTE'] < two_years_ago_ts) &
                            (prod_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]
        update_label("idle_producers", len(idle_prod))

        prod_drilled = prod_df[prod_df['INIT_PROD_DTE'] > project_update_date_ts]
        update_label("producers_drilled", len(prod_drilled))

        prod_abandoned = prod_df[(prod_df['CMPL_STATE_TYPE_DESC'] == 'Permanently Abandoned') &
                                 (prod_df['CMPL_STATE_EFTV_DTTM'] > project_update_date_ts)]
        update_label("producers_abandoned", len(prod_abandoned))

    def clear_calculations(self):
        for key in self.calculation_labels:
            self.calculation_labels[key].config(text="N/A")


# Page 5: Avg Tubing Pressure & Inj Vol Data
class TubingPressurePage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self.avg_pressure_label = None

    def create_widgets(self):
        header_label = tb.Label(self, text="Avg Injection Volume and Wlhd Tubing Pressure", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        control_frame = tb.Frame(self)
        control_frame.pack(pady=10, padx=20, fill="x", expand=False)
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        pull_data_btn = tb.Button(control_frame, text="Pull Averages Data", command=self.pull_tubing_pressure_data, bootstyle="info")
        pull_data_btn.grid(row=0, column=0, padx=5, sticky="w")

        avg_pressure_frame = tb.Frame(control_frame)
        avg_pressure_frame.grid(row=0, column=1, padx=5, sticky="e")
        tb.Label(avg_pressure_frame, text="Overall Avg Tubing Pressure:").pack(side="left", padx=5)
        self.avg_pressure_label = tb.Label(avg_pressure_frame, text="N/A", bootstyle="success", font=("TkDefaultFont", 10, "bold"))
        self.avg_pressure_label.pack(side="left", padx=5)

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

        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(PerformanceSummaryPage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)
        
        next_button = tb.Button(nav_frame, text="Next (Daily Prod/Inj)", command=lambda: self.controller.show_page(ProductionInjectionPage), bootstyle="primary")
        next_button.grid(row=0, column=1, padx=10)

    def pull_tubing_pressure_data(self):
        well_apis_to_query = self.controller.shared_data.get("well_apis", [])
        if not well_apis_to_query:
            messagebox.showwarning("Input Error", "No Well APIs entered on the first page. Please go back to Page 1.")
            self.clear_results()
            self.clear_average_pressure()
            return
        formatted_well_apis = ', '.join([f"'{api}'" for api in well_apis_to_query])

        sql_query = f"""
        select wd.well_nme, wd.well_api_nbr,cd.cmpl_nme, cd.cmpl_fac_id, 
        avg(case when cf.aloc_stm_inj_vol_qty > 0 then cf.aloc_stm_inj_vol_qty end) as avg_stm_inj_vol,
        round(avg(case when cf.aloc_wtr_inj_vol_qty > 0 then cf.aloc_wtr_inj_vol_qty end),2) as avg_wtr_inj_vol,
        round(avg(case when cf.wlhd_tbg_prsr_qty> 0 then cf.wlhd_tbg_prsr_qty end ),2) as avg_wlhd_tbg_prsr
        from 
        well_dmn wd
        join
        cmpl_dmn cd 
        on wd.well_fac_id = cd.well_fac_id
        join cmpl_dly_fact cf
        on cd.cmpl_fac_id = cf.cmpl_fac_id
        where wd.actv_indc = 'Y' and cd.actv_indc = 'Y' and cf.eftv_Dttm >= trunc(sysdate)-60
        and wd.well_api_nbr in ({formatted_well_apis})
        group by wd.well_nme, wd.well_api_nbr,cd.cmpl_nme, cd.cmpl_fac_id
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                self.display_results(df) 
                self.current_data = df 
                self.calculate_average_pressure(df)
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "SQL query executed successfully. No results to display.")
                self.clear_results()
                self.clear_average_pressure()
                self.current_data = None

            cursor.close()
            conn.close()

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_results()
            self.clear_average_pressure()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}")
            self.clear_results()
            self.clear_average_pressure()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()
            self.clear_average_pressure()

    def calculate_average_pressure(self, df):
        if self.avg_pressure_label is None:
            return

        if 'AVG_WLHD_TBG_PRSR' in df.columns:
            pressures = pd.to_numeric(df['AVG_WLHD_TBG_PRSR'], errors='coerce').dropna()
            if not pressures.empty:
                avg_pressure = pressures.mean()
                self.avg_pressure_label.config(text=f"{avg_pressure:.1f}")
            else:
                self.avg_pressure_label.config(text="No valid pressure data")
        else:
            self.avg_pressure_label.config(text="Column not found")
            if not df.empty:
                messagebox.showwarning("Calculation Warning", "AVG_WLHD_TBG_PRSR column not found for average calculation.")

    def clear_average_pressure(self):
        if self.avg_pressure_label: 
            self.avg_pressure_label.config(text="N/A")

# Page 6: Production and Injection Data
class ProductionInjectionPage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None

    def create_widgets(self):
        header_label = tb.Label(self, text="Production and Injection Data", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        pull_data_btn = tb.Button(self, text="Pull Production/Injection Data", command=self.pull_data, bootstyle="info")
        pull_data_btn.pack(pady=10)

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

        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(TubingPressurePage), bootstyle="warning")
        back_button.grid(row=0, column=0, padx=10)
        
        next_button = tb.Button(nav_frame, text="Next (Daily Inj/Pressure)", command=lambda: self.controller.show_page(DailyInjectionPressurePage), bootstyle="primary")
        next_button.grid(row=0, column=1, padx=10)

    def pull_data(self):
        well_apis_to_query = self.controller.shared_data.get("well_apis", [])
        if not well_apis_to_query:
            messagebox.showwarning("Input Error", "No Well APIs entered on the first page. Please go back to Page 1.")
            self.clear_results()
            return
        formatted_well_apis = ', '.join([f"'{api}'" for api in well_apis_to_query])

        # Updated query with GAS INJ Per Day and corrected WATER INJ mapping
        sql_query = f"""
        SELECT
            wd.well_nme AS "WELL NAME",
            wd.well_api_nbr AS "WELL API",
            cf.eftv_dttm AS "DATE",
            cf.aloc_oil_prod_dly_rte_qty AS "OIL PROD BOPD",
            cf.aloc_wtr_prod_dly_rte_qty AS "WATER PROD BWPD",
            cf.aloc_gas_prod_dly_rte_qty AS "GAS PROD MCFD",
            cf.aloc_stm_inj_dly_rte_qty AS "STEAM INJ Per Day",
            cf.aloc_wtr_inj_dly_rte_qty AS "WATER INJ Per Day",
            cf.aloc_gas_inj_dly_rte_qty AS "GAS INJ Per Day"
        FROM
            well_dmn wd
            JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
            JOIN cmpl_mnly_fact cf ON cd.cmpl_fac_id = cf.cmpl_fac_id
        WHERE
            cd.actv_indc = 'Y'
            AND wd.actv_indc = 'Y'
            AND wd.well_api_nbr IN ({formatted_well_apis})
            AND cf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE), -62)
            AND cf.eftv_dttm <= TRUNC(SYSDATE)
        ORDER BY
            wd.well_api_nbr,
            cf.eftv_dttm
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                if 'DATE' in df.columns:
                    df['DATE'] = pd.to_datetime(df['DATE'], errors='coerce')

                self.display_results(df, apply_global_sort=False) 
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
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}")
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results()

# NEW Page 7: Daily Injection and Tubing Pressure (Full Data)
class DailyInjectionPressurePage(Page):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.create_widgets()
        self.conn_manager = OracleConnectionManager()
        self.current_data = None

    def create_widgets(self):
        header_label = tb.Label(self, text="Daily Injection and Tubing Pressure (Full Data)", font=("Helvetica", 16, "bold"))
        header_label.pack(pady=10)

        pull_data_btn = tb.Button(self, text="Pull Daily Data", command=self.pull_data, bootstyle="info")
        pull_data_btn.pack(pady=10)

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

        copy_button = tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard, bootstyle="secondary")
        copy_button.pack(pady=10)

        nav_frame = tb.Frame(self)
        nav_frame.pack(pady=10)

        back_button = tb.Button(nav_frame, text="Back", command=lambda: self.controller.show_page(ProductionInjectionPage), bootstyle="warning")
        back_button.pack()

    def pull_data(self):
        well_apis_to_query = self.controller.shared_data.get("well_apis", [])
        if not well_apis_to_query:
            messagebox.showwarning("Input Error", "No Well APIs entered on the first page. Please go back to Page 1.")
            self.clear_results()
            return
        formatted_well_apis = ', '.join([f"'{api}'" for api in well_apis_to_query])

        sql_query = f"""
        select wd.well_nme, wd.well_api_nbr,cd.cmpl_nme, cd.cmpl_fac_id, cf.eftv_dttm, 
        cf.aloc_stm_inj_vol_qty,
        cf.aloc_wtr_inj_vol_qty,
        cf.wlhd_tbg_prsr_qty
        from
        well_dmn wd
        join
        cmpl_dmn cd
        on wd.well_fac_id = cd.well_fac_id
        join cmpl_dly_fact cf
        on cd.cmpl_fac_id = cf.cmpl_fac_id
        where wd.actv_indc = 'Y' and cd.actv_indc = 'Y' and cf.eftv_Dttm >= trunc(sysdate)-60 
        and wd.well_api_nbr in ({formatted_well_apis})
        order by cf.eftv_dttm
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)

            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)

                if 'EFTV_DTTM' in df.columns:
                    df['EFTV_DTTM'] = pd.to_datetime(df['EFTV_DTTM'], errors='coerce')

                self.display_results(df, apply_global_sort=False) 
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
        except oracledb.Error as e:
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
        self.title("Periodic Project Review")
        self.geometry("1600x1200") 

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))

        self.container = tb.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (WellAPIInputPage, WellBasicDataPage, EngineeringStringPage, PerformanceSummaryPage, TubingPressurePage, ProductionInjectionPage, DailyInjectionPressurePage):
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