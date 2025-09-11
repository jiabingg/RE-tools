import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import shutil
import threading
from datetime import datetime
import csv

class FileFinderApp(tk.Tk):
    """
    A GUI application to find and copy the most recently modified PDF files from
    multiple source folders based on a list of 12-digit API/Bore numbers.
    Generates a detailed CSV log.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.title("UWI File Finder")
        self.geometry("850x700")
        self.configure(bg="#eaf0f2")
        self.minsize(700, 600)

        main_frame = tk.Frame(self, padx=15, pady=15, bg="#eaf0f2")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(1, weight=1)

        # --- Section 1: Source Folders ---
        tk.Label(main_frame, text="Source Folders:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=0, column=0, sticky="nw", pady=5)
        source_frame = tk.Frame(main_frame, bg="#eaf0f2")
        source_frame.grid(row=0, column=1, columnspan=2, sticky="nsew", padx=5)
        source_frame.columnconfigure(0, weight=1)
        self.source_listbox = tk.Listbox(source_frame, font=("Arial", 10), height=8, relief=tk.SOLID, borderwidth=1, selectmode=tk.EXTENDED)
        self.source_listbox.grid(row=0, column=0, sticky="ew")
        source_button_frame = tk.Frame(source_frame, bg="#eaf0f2")
        source_button_frame.grid(row=0, column=1, sticky="ns", padx=5)
        tk.Button(source_button_frame, text="Add Folder...", command=self.add_source_dir).pack(fill=tk.X, pady=2)
        tk.Button(source_button_frame, text="Remove Selected", command=self.remove_source_dir).pack(fill=tk.X, pady=2)
        main_frame.rowconfigure(0, weight=1)

        # --- Section 2: Target Folder ---
        tk.Label(main_frame, text="Target Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=1, column=0, sticky="w", pady=15)
        self.target_path = tk.StringVar()
        target_entry = tk.Entry(main_frame, textvariable=self.target_path, font=("Arial", 10))
        target_entry.grid(row=1, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_target_dir).grid(row=1, column=2, padx=5)

        # --- Section 3: 12-Digit Numbers ---
        tk.Label(main_frame, text="12-Digit Numbers:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=2, column=0, sticky="nw", pady=(15, 5))
        tk.Label(main_frame, text="(one per line)", font=("Helvetica", 9), bg="#eaf0f2").grid(row=3, column=0, sticky="nw", pady=0)
        self.file_list_textbox = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, font=("Courier New", 10), height=8, relief=tk.SOLID, borderwidth=1)
        self.file_list_textbox.grid(row=2, column=1, columnspan=2, rowspan=2, sticky="nsew", padx=5, pady=(15, 5))
        main_frame.rowconfigure(2, weight=1)

        # --- Action Button ---
        self.process_button = tk.Button(
            main_frame, text="Find and Copy Files", font=("Helvetica", 12, "bold"),
            bg="#28a745", fg="white", command=self.start_processing_thread, pady=8
        )
        self.process_button.grid(row=4, column=0, columnspan=3, pady=20, sticky="ew")

        # --- Log Display ---
        tk.Label(main_frame, text="Log:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.log_display = scrolledtext.ScrolledText(
            main_frame, wrap=tk.WORD, font=("Courier New", 9),
            bg="white", relief=tk.SOLID, borderwidth=1
        )
        self.log_display.grid(row=6, column=0, columnspan=3, sticky="nsew")
        main_frame.rowconfigure(6, weight=2)

    def add_source_dir(self):
        path = filedialog.askdirectory(title="Select a Source Folder to Add")
        if path and path not in self.source_listbox.get(0, tk.END):
            self.source_listbox.insert(tk.END, path)

    def remove_source_dir(self):
        selected_indices = self.source_listbox.curselection()
        for index in reversed(selected_indices):
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
        thread = threading.Thread(target=self.process_files)
        thread.daemon = True
        thread.start()

    def process_files(self):
        self.log_display.delete('1.0', tk.END)
        source_dirs = self.source_listbox.get(0, tk.END)
        target_dir = self.target_path.get()
        input_lines = self.file_list_textbox.get('1.0', tk.END).splitlines()
        
        csv_log_data = []
        summary = "Process did not complete."

        def gui_log(message):
            self.after(0, self.log, message)

        try:
            # --- 1. Pre-processing: Parse inputs and prepare search terms ---
            gui_log("INFO: Parsing 12-digit numbers and preparing search...")
            tasks = {}
            filename_to_task_key = {}

            for line in input_lines:
                clean_line = line.strip()
                if not (len(clean_line) == 12 and clean_line.isdigit()):
                    gui_log(f"WARNING: Skipping invalid input line: '{line}'")
                    csv_log_data.append({'InputNumber': line, 'Status': 'Invalid Input', 'ErrorMessage': 'Must be exactly 12 digits.'})
                    continue
                
                api = clean_line[:10]
                bore = clean_line[10:]
                tasks[clean_line] = {'api': api, 'bore': bore, 'found_files': []}
                
                if bore == "00":
                    possible_names = [f"{api}.pdf", f"{api}_00.pdf"]
                else:
                    possible_names = [f"{api}_{bore}.pdf"]
                
                for name in possible_names:
                    filename_to_task_key[name] = clean_line

            if not tasks:
                raise ValueError("No valid 12-digit numbers were provided.")
            
            gui_log(f"INFO: Prepared {len(tasks)} valid tasks.")
            gui_log("-" * 60)

            # --- 2. Scanning: Walk through all source directories ---
            for source_dir in source_dirs:
                gui_log(f"INFO: Scanning source folder: '{source_dir}'")
                for dirpath, _, filenames in os.walk(source_dir):
                    for filename in filenames:
                        if filename in filename_to_task_key:
                            task_key = filename_to_task_key[filename]
                            full_path = os.path.join(dirpath, filename)
                            mod_time = os.path.getmtime(full_path)
                            tasks[task_key]['found_files'].append((full_path, mod_time))
            gui_log("INFO: Finished scanning all source folders.")
            gui_log("-" * 60)

            # --- 3. Processing: Find the newest file for each task and copy it ---
            gui_log("INFO: Analyzing findings and copying newest files...")
            files_copied_count = 0
            not_found_count = 0

            os.makedirs(target_dir, exist_ok=True)

            for task_key, data in tasks.items():
                if not data['found_files']:
                    gui_log(f"NOT FOUND: No files found for {task_key}.")
                    csv_log_data.append({'InputNumber': task_key, 'Status': 'Not Found'})
                    not_found_count += 1
                    continue
                
                data['found_files'].sort(key=lambda x: x[1], reverse=True)
                
                newest_file_path, newest_mod_time = data['found_files'][0]
                newest_filename = os.path.basename(newest_file_path)
                target_file_path = os.path.join(target_dir, newest_filename)
                
                try:
                    shutil.copy2(newest_file_path, target_file_path)
                    gui_log(f"COPIED: '{newest_filename}' for input {task_key}.")
                    files_copied_count += 1
                    
                    all_paths = [path for path, mod_time in data['found_files']]
                    csv_log_data.append({
                        'InputNumber': task_key, 'API': data['api'], 'Bore': data['bore'],
                        'Status': 'Copied', 'CopiedFilePath': newest_file_path,
                        'LastModified': datetime.fromtimestamp(newest_mod_time).strftime('%Y-%m-%d %H:%M:%S'),
                        'TargetFilePath': target_file_path, 'AllFoundPaths': '; '.join(all_paths)
                    })
                except Exception as e:
                    gui_log(f"ERROR: Could not copy '{newest_filename}'. Reason: {e}")
                    csv_log_data.append({'InputNumber': task_key, 'Status': 'Copy Error', 'ErrorMessage': str(e), 'CopiedFilePath': newest_file_path})

            gui_log("-" * 60)
            invalid_inputs = len(input_lines) - len(tasks)
            summary = (f"Process Finished. Copied: {files_copied_count}, Not Found: {not_found_count}, "
                       f"Invalid Inputs: {invalid_inputs}.")
            gui_log(summary)

            log_file_path = os.path.join(target_dir, f"copy_log_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv")
            headers = ['InputNumber', 'API', 'Bore', 'Status', 'CopiedFilePath', 'LastModified',
                       'TargetFilePath', 'AllFoundPaths', 'ErrorMessage']
            with open(log_file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(csv_log_data)
            
            gui_log(f"\nINFO: A detailed CSV log has been saved to: {log_file_path}")

        except Exception as e:
            summary = f"An unexpected error occurred: {e}"
            gui_log(summary)
        finally:
            self.after(0, self.process_button.config, {'state': tk.NORMAL, 'text': 'Find and Copy Files'})
            self.after(1, lambda: messagebox.showinfo("Complete", summary))


if __name__ == "__main__":
    app = FileFinderApp()
    app.mainloop()

