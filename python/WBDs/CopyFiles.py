import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import os
import shutil
import threading
from datetime import datetime
import csv
import cx_Oracle
import pandas as pd
import queue

# --- ORACLE CONNECTION MANAGER (from PPR.py) ---
class OracleConnectionManager:
    """ Manages Oracle database connections. """
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"
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

# --- MAIN APPLICATION ---
class MainApp(tk.Tk):
    """ The main application window that manages and switches between pages. """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title("File and Well Abandonment Workflow")
        self.geometry("900x750")
        self.configure(bg="#eaf0f2")
        self.minsize(800, 600)

        container = tk.Frame(self)
        container.pack(side="top", fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        self.shared_data = {} # To pass data between frames

        for F in (FinderPage, AbandonmentCheckPage):
            frame = F(container, self)
            self.frames[F] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.show_frame(FinderPage)

    def show_frame(self, cont):
        frame = self.frames[cont]
        frame.tkraise()

    def transition_to_abandonment_check(self, copied_files_data):
        """ Switches to the abandonment check page and passes data to it. """
        self.shared_data['copied_files'] = copied_files_data
        abandonment_frame = self.frames[AbandonmentCheckPage]
        abandonment_frame.load_data_and_prepare()
        self.show_frame(AbandonmentCheckPage)

# --- PAGE 1: FIND AND COPY FILES ---
class FinderPage(tk.Frame):
    """ Step 1: Find and copy files based on UWI numbers. """
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#eaf0f2")
        self.controller = controller
        self.result_queue = queue.Queue()
        
        main_frame = tk.Frame(self, padx=15, pady=15, bg="#eaf0f2")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(1, weight=1)

        tk.Label(main_frame, text="Step 1: Find and Copy Files", font=("Helvetica", 16, "bold"), bg="#eaf0f2").grid(row=0, column=0, columnspan=3, pady=(0,15), sticky="w")
        
        tk.Label(main_frame, text="Source Folders:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=1, column=0, sticky="nw", pady=5)
        source_frame = tk.Frame(main_frame, bg="#eaf0f2")
        source_frame.grid(row=1, column=1, columnspan=2, sticky="nsew", padx=5)
        source_frame.columnconfigure(0, weight=1)
        self.source_listbox = tk.Listbox(source_frame, font=("Arial", 10), height=8, relief=tk.SOLID, borderwidth=1, selectmode=tk.EXTENDED)
        self.source_listbox.grid(row=0, column=0, sticky="ew")
        source_button_frame = tk.Frame(source_frame, bg="#eaf0f2")
        source_button_frame.grid(row=0, column=1, sticky="ns", padx=5)
        tk.Button(source_button_frame, text="Add Folder...", command=self.add_source_dir).pack(fill=tk.X, pady=2)
        tk.Button(source_button_frame, text="Remove Selected", command=self.remove_source_dir).pack(fill=tk.X, pady=2)
        main_frame.rowconfigure(1, weight=1)

        tk.Label(main_frame, text="Target Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=2, column=0, sticky="w", pady=15)
        self.target_path = tk.StringVar()
        tk.Entry(main_frame, textvariable=self.target_path, font=("Arial", 10)).grid(row=2, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_target_dir).grid(row=2, column=2, padx=5)

        tk.Label(main_frame, text="12-Digit Numbers:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=3, column=0, sticky="nw", pady=(15, 5))
        self.file_list_textbox = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, font=("Courier New", 10), height=8, relief=tk.SOLID, borderwidth=1)
        self.file_list_textbox.grid(row=3, column=1, columnspan=2, rowspan=2, sticky="nsew", padx=5, pady=(15, 5))
        main_frame.rowconfigure(3, weight=1)

        self.process_button = tk.Button(main_frame, text="Find, Copy, and Analyze", font=("Helvetica", 12, "bold"), bg="#28a745", fg="white", command=self.start_processing_thread, pady=8)
        self.process_button.grid(row=5, column=0, columnspan=3, pady=20, sticky="ew")

        tk.Label(main_frame, text="Log:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=6, column=0, sticky="w", pady=(10, 0))
        self.log_display = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, font=("Courier New", 9), bg="white", relief=tk.SOLID, borderwidth=1)
        self.log_display.grid(row=7, column=0, columnspan=3, sticky="nsew")
        main_frame.rowconfigure(7, weight=2)

    def add_source_dir(self):
        path = filedialog.askdirectory(title="Select a Source Folder to Add")
        if path and path not in self.source_listbox.get(0, tk.END):
            self.source_listbox.insert(tk.END, path)

    def remove_source_dir(self):
        for index in reversed(self.source_listbox.curselection()):
            self.source_listbox.delete(index)

    def select_target_dir(self):
        path = filedialog.askdirectory(title="Select Target Folder")
        if path:
            self.target_path.set(path)

    def log(self, message):
        self.log_display.insert(tk.END, message + "\n")
        self.log_display.see(tk.END)

    def start_processing_thread(self):
        if self.source_listbox.size() == 0 or not self.target_path.get() or not self.file_list_textbox.get('1.0', tk.END).strip():
            messagebox.showerror("Error", "Please provide at least one source folder, a target folder, and at least one 12-digit number.")
            return
        self.process_button.config(state=tk.DISABLED, text="Processing...")
        
        thread = threading.Thread(target=self.process_files, args=(self.result_queue,))
        thread.daemon = True
        thread.start()
        
        self.after(100, self.check_queue)

    def check_queue(self):
        """ Periodically check the queue for results from the worker thread. """
        try:
            result = self.result_queue.get_nowait()
            
            self.process_button.config(state=tk.NORMAL, text='Find, Copy, and Analyze')
            
            if isinstance(result, Exception):
                messagebox.showerror("Processing Error", f"An error occurred in the background task: {result}")
            else:
                # Automatic transition upon successful completion
                self.controller.transition_to_abandonment_check(result)

        except queue.Empty:
            if self.process_button['state'] == 'disabled':
                self.after(100, self.check_queue)

    def process_files(self, result_queue):
        """ The background task that finds/copies files and puts the result in a queue. """
        self.log_display.delete('1.0', tk.END)
        source_dirs, target_dir = self.source_listbox.get(0, tk.END), self.target_path.get()
        input_lines = self.file_list_textbox.get('1.0', tk.END).splitlines()
        
        csv_log_data, copied_files_for_step2 = [], []
        
        def gui_log(message):
            self.after(0, self.log, message)

        try:
            gui_log("INFO: Parsing 12-digit numbers...")
            tasks = {line.strip(): {'api': line.strip()[:10], 'bore': line.strip()[10:], 'found_files': []}
                     for line in input_lines if len(line.strip()) == 12 and line.strip().isdigit()}
            
            filename_to_task_key = {}
            for task_key, data in tasks.items():
                names = [f"{data['api']}.pdf", f"{data['api']}_00.pdf"] if data['bore'] == "00" else [f"{data['api']}_{data['bore']}.pdf"]
                for name in names:
                    filename_to_task_key[name] = task_key
            
            gui_log(f"INFO: Prepared {len(tasks)} valid tasks. Scanning folders...")
            for source_dir in source_dirs:
                for dirpath, _, filenames in os.walk(source_dir):
                    for filename in filenames:
                        if filename in filename_to_task_key:
                            task_key = filename_to_task_key[filename]
                            full_path = os.path.join(dirpath, filename)
                            mod_time = os.path.getmtime(full_path)
                            tasks[task_key]['found_files'].append((full_path, mod_time))
            
            gui_log("INFO: Scanning complete. Analyzing and copying newest files...")
            os.makedirs(target_dir, exist_ok=True)

            for task_key, data in tasks.items():
                if not data['found_files']:
                    csv_log_data.append({'InputNumber': task_key, 'Status': 'Not Found'})
                    continue
                
                data['found_files'].sort(key=lambda x: x[1], reverse=True)
                newest_file_path, newest_mod_time = data['found_files'][0]
                newest_filename = os.path.basename(newest_file_path)
                target_file_path = os.path.join(target_dir, newest_filename)
                
                try:
                    shutil.copy2(newest_file_path, target_file_path)
                    mod_time_str = datetime.fromtimestamp(newest_mod_time).strftime('%Y-%m-%d %H:%M:%S')
                    copied_files_for_step2.append({'UWI': task_key, 'FileModifiedDate': mod_time_str, 'FileName': newest_filename})
                    csv_log_data.append({'InputNumber': task_key, 'Status': 'Copied', 'CopiedFilePath': newest_file_path, 'LastModified': mod_time_str})
                except Exception as e:
                    csv_log_data.append({'InputNumber': task_key, 'Status': 'Copy Error', 'ErrorMessage': str(e)})

            summary = f"Process Finished. Found/Copied: {len(copied_files_for_step2)}. Not Found: {len(tasks) - len(copied_files_for_step2)}."
            gui_log(summary)
            
            log_file_path = os.path.join(target_dir, f"copy_log_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv")
            headers = ['InputNumber', 'Status', 'CopiedFilePath', 'LastModified', 'ErrorMessage']
            with open(log_file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(csv_log_data)
            gui_log(f"\nINFO: Detailed CSV log saved. Transitioning to Step 2...")
            
            result_queue.put(copied_files_for_step2)

        except Exception as e:
            result_queue.put(e)
            gui_log(f"An unexpected error occurred in Step 1: {e}")

# --- PAGE 2: ABANDONMENT CHECK ---
class AbandonmentCheckPage(tk.Frame):
    """ Step 2: Check well abandonment status against file modification dates. """
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#eaf0f2")
        self.controller = controller
        self.conn_manager = OracleConnectionManager()
        self.copied_files_data = []

        main_frame = tk.Frame(self, padx=15, pady=15, bg="#eaf0f2")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(0, weight=1)

        title_frame = tk.Frame(main_frame, bg="#eaf0f2")
        title_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        tk.Label(title_frame, text="Step 2: Check for Abandoned Wells", font=("Helvetica", 16, "bold"), bg="#eaf0f2").pack(side=tk.LEFT)
        tk.Button(title_frame, text="<- Back to Step 1", command=lambda: controller.show_frame(FinderPage)).pack(side=tk.RIGHT)

        controls_frame = tk.Frame(main_frame, bg="#eaf0f2")
        controls_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=10)
        self.run_query_button = tk.Button(controls_frame, text="Run Abandonment Query", font=("Helvetica", 12, "bold"), bg="#007bff", fg="white", command=self.run_abandonment_query)
        self.run_query_button.pack(side=tk.LEFT, padx=5)
        self.export_button = tk.Button(controls_frame, text="Export Results to CSV", font=("Helvetica", 12, "bold"), state=tk.DISABLED, command=self.export_to_csv)
        self.export_button.pack(side=tk.LEFT, padx=5)

        tree_frame = ttk.Frame(main_frame)
        tree_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        main_frame.rowconfigure(2, weight=1)
        
        self.result_tree = ttk.Treeview(tree_frame, show="headings")
        self.result_tree.pack(side="left", fill="both", expand=True)
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.result_tree.yview)
        vsb.pack(side='right', fill='y')
        self.result_tree.configure(yscrollcommand=vsb.set)
        
        self.result_tree.tag_configure('highlight', background='red', foreground='white')
        self.results_df = pd.DataFrame()

    def load_data_and_prepare(self):
        """ Loads data from Step 1 and populates the initial Treeview. """
        try:
            self.copied_files_data = self.controller.shared_data.get('copied_files', [])
            for i in self.result_tree.get_children():
                self.result_tree.delete(i)
            
            if not self.copied_files_data:
                self.result_tree["columns"] = ["Status"]
                self.result_tree.heading("Status", text="Status")
                self.result_tree.insert("", "end", values=["No files have been processed yet. Go back to Step 1."])
                return

            df = pd.DataFrame(self.copied_files_data)
            
            # --- FIX: Convert to datetime object immediately upon loading ---
            df['FileModifiedDate'] = pd.to_datetime(df['FileModifiedDate'])
            
            self.results_df = df
            df['WellStatus'] = 'N/A'
            df['AbandonmentDate'] = pd.NaT # Use NaT for empty date column
            
            self.update_treeview(df)
        except Exception as e:
            error_message = f"Failed to load and prepare data for Step 2: {e}"
            print(error_message) # Log to console for debugging
            messagebox.showerror("Data Load Error", error_message)

    def run_abandonment_query(self):
        """ Executes the SQL query to get abandonment data. """
        if not self.copied_files_data:
            messagebox.showinfo("No Data", "No files were copied in Step 1 to check.")
            return

        uwis_to_query = [item['UWI'] for item in self.copied_files_data]
        if not uwis_to_query:
            messagebox.showinfo("No Data", "No valid UWIs to query.")
            return

        formatted_uwis = ', '.join([f"'{uwi[:10]}'" for uwi in uwis_to_query])
        
        sql_query = f"""
        SELECT wd.well_api_nbr, cd.CMPL_STATE_TYPE_DESC, cd.CMPL_STATE_EFTV_DTTM
        FROM well_dmn wd
        JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
        WHERE cd.actv_indc = 'Y' AND wd.actv_indc = 'Y'
        AND cd.cmpl_state_type_cde NOT IN ('PRPO','FUTR')
        AND wd.well_api_nbr IN ({formatted_uwis})
        AND cd.CMPL_SEQ_NBR IS NOT NULL
        """
        
        try:
            with self.conn_manager.get_connection('odw') as conn:
                db_df = pd.read_sql(sql_query, conn)
                db_df['WELL_API_NBR'] = db_df['WELL_API_NBR'].astype(str)
                
                self.results_df['WELL_API_NBR'] = self.results_df['UWI'].str[:10]
                
                # Use a fresh copy of results_df for merging to avoid column conflicts
                base_df = self.results_df[['UWI', 'FileName', 'FileModifiedDate', 'WELL_API_NBR']].copy()
                merged_df = pd.merge(base_df, db_df, on='WELL_API_NBR', how='left')
                
                merged_df.rename(columns={'CMPL_STATE_TYPE_DESC': 'WellStatus', 'CMPL_STATE_EFTV_DTTM': 'StatusEffectiveDate'}, inplace=True)
                
                merged_df['AbandonmentDate'] = pd.NaT
                abandoned_mask = merged_df['WellStatus'] == 'Permanently Abandoned'
                merged_df.loc[abandoned_mask, 'AbandonmentDate'] = merged_df.loc[abandoned_mask, 'StatusEffectiveDate']

                merged_df['AbandonmentDate'] = pd.to_datetime(merged_df['AbandonmentDate'], errors='coerce')
                # FileModifiedDate is already a datetime object from load_data_and_prepare

                self.results_df = merged_df
                self.update_treeview(merged_df)
                self.export_button.config(state=tk.NORMAL)
                messagebox.showinfo("Success", "Query complete. Results updated.")

        except Exception as e:
            messagebox.showerror("DatabaseError", f"An error occurred: {e}")

    def update_treeview(self, df):
        """ Clears and repopulates the treeview with DataFrame content. """
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)
            
        display_cols = ['UWI', 'FileName', 'FileModifiedDate', 'WellStatus', 'AbandonmentDate']
        cols_to_show = [col for col in display_cols if col in df.columns]
        
        self.result_tree["columns"] = cols_to_show
        for col in cols_to_show:
            self.result_tree.heading(col, text=col)
            self.result_tree.column(col, width=150)

        for index, row in df.iterrows():
            tags = ()
            # Highlighting logic now correctly compares two datetime objects
            if pd.notna(row.get('AbandonmentDate')) and pd.notna(row.get('FileModifiedDate')):
                if row['FileModifiedDate'].date() < row['AbandonmentDate'].date():
                    tags = ('highlight',)
            
            display_values = []
            for col in cols_to_show:
                val = row.get(col, '')
                if isinstance(val, pd.Timestamp):
                    # Format dates for display, handling NaT (Not a Time) for empty dates
                    display_values.append(val.strftime('%Y-%m-%d %H:%M:%S') if pd.notna(val) else '')
                else:
                    display_values.append(val if pd.notna(val) else '')

            self.result_tree.insert("", "end", values=display_values, tags=tags)
            
    def export_to_csv(self):
        if self.results_df.empty:
            messagebox.showwarning("No Data", "There is no data to export.")
            return
        
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")], title="Save Results As")
        if not path:
            return
        try:
            # Format dates for CSV export
            export_df = self.results_df.copy()
            if 'FileModifiedDate' in export_df.columns:
                 export_df['FileModifiedDate'] = export_df['FileModifiedDate'].dt.strftime('%Y-%m-%d %H:%M:%S')
            if 'AbandonmentDate' in export_df.columns:
                 export_df['AbandonmentDate'] = export_df['AbandonmentDate'].dt.strftime('%Y-%m-%d')
            
            export_df.to_csv(path, index=False, encoding='utf-8')
            messagebox.showinfo("Success", f"Results successfully exported to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export data: {e}")

if __name__ == "__main__":
    app = MainApp()
    app.mainloop()

