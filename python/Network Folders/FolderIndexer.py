import os
import json
import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime
import threading
import queue

class IndexerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Granular File Indexer")
        self.root.geometry("750x550")

        self.log_area = scrolledtext.ScrolledText(root, wrap=tk.WORD, state='disabled', font=("TkFixedFont", 9))
        self.log_area.pack(padx=10, pady=10, expand=True, fill='both')

        self.start_button = tk.Button(root, text="Start Indexing", command=self.start_indexing_thread)
        self.start_button.pack(pady=10)

        self.log_queue = queue.Queue()
        self.root.after(100, self.process_log_queue)

    def log_message(self, message, indent=0):
        timestamp = datetime.now().strftime('%H:%M:%S')
        prefix = "  " * indent
        full_message = f"[{timestamp}] {prefix}{message}"
        self.log_area.configure(state='normal')
        self.log_area.insert(tk.END, full_message + "\n")
        self.log_area.configure(state='disabled')
        self.log_area.see(tk.END)

    def process_log_queue(self):
        while not self.log_queue.empty():
            message, indent = self.log_queue.get()
            self.log_message(message, indent)
        self.root.after(100, self.process_log_queue)
        
    def start_indexing_thread(self):
        self.start_button.config(state=tk.DISABLED, text="Indexing...")
        self.log_area.configure(state='normal')
        self.log_area.delete('1.0', tk.END)
        self.log_area.configure(state='disabled')
        
        self.log_queue.put(("Starting indexing process...", 0))
        
        self.indexing_thread = threading.Thread(target=self.run_indexer, daemon=True)
        self.indexing_thread.start()
        
        self.root.after(100, self.check_thread)

    def check_thread(self):
        if self.indexing_thread.is_alive():
            self.root.after(100, self.check_thread)
        else:
            self.start_button.config(state=tk.NORMAL, text="Start Indexing")
            self.log_queue.put(("--- Indexing process has finished ---", 0))
            messagebox.showinfo("Complete", "The indexing process has finished.")

    def run_indexer(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        def sanitize_filename(name):
            return "".join(c if c.isalnum() else '_' for c in name)

        def write_json(file_path, data):
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=4)
        
        def read_json(file_path):
            try:
                with open(file_path, 'r') as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        main_config_path = os.path.join(script_dir, 'folders.json')
        main_config = read_json(main_config_path)
        if not main_config:
            self.log_queue.put(("CRITICAL: Could not load or parse folders.json.", 0))
            return

        for top_folder_info in main_config:
            if top_folder_info.get("Indexed") == "Yes":
                continue

            self.log_queue.put((f"Processing Top-Level Folder: {top_folder_info['name']}", 0))
            base_name = sanitize_filename(top_folder_info['name'])
            status_file_path = os.path.join(script_dir, f"{base_name}_status.json")
            data_file_path = os.path.join(script_dir, f"{base_name}_files.json")

            # --- PHASE 1: DISCOVERY ---
            status_data = read_json(status_file_path)
            if not status_data or not status_data.get("meta", {}).get("discovery_complete", False):
                self.log_queue.put(("Starting/Resuming Phase 1: Discovery.", 1))
                try:
                    if not os.path.exists(top_folder_info['path']):
                         self.log_queue.put((f"ERROR: Path not found: {top_folder_info['path']}. Skipping.", 1))
                         continue
                    
                    status_data = {"meta": {"discovery_complete": False}, "folders": []}
                    all_subfolders = [top_folder_info['path']] 
                    subfolders_found = 0

                    for root, dirs, _ in os.walk(top_folder_info['path']):
                        for d in dirs:
                            all_subfolders.append(os.path.join(root, d))
                            subfolders_found += 1
                            if subfolders_found % 500 == 0:
                                status_data["folders"] = [{"path": p, "status": "No"} for p in all_subfolders]
                                write_json(status_file_path, status_data)
                                self.log_queue.put((f"Discovery checkpoint: {subfolders_found} folders found and saved.", 2))
                    
                    status_data["folders"] = [{"path": p, "status": "No"} for p in all_subfolders]
                    status_data["meta"]["discovery_complete"] = True 
                    write_json(status_file_path, status_data)
                    self.log_queue.put((f"Discovery complete. Found a total of {len(all_subfolders)} subfolders.", 1))
                except Exception as e:
                    self.log_queue.put((f"ERROR during Discovery Phase: {e}. Progress saved. Will retry on next run.", 1))
                    continue
            
            # --- PHASE 2: GRANULAR INDEXING ---
            self.log_queue.put(("Starting Phase 2: Granular Indexing.", 1))
            
            subfolder_statuses = status_data["folders"]
            indexed_files_data = read_json(data_file_path) or []
            
            something_was_indexed_in_run = False
            subfolders_since_update = 0 
            total_subfolders = len(subfolder_statuses)

            for i, folder_entry in enumerate(subfolder_statuses):
                if folder_entry['status'] == 'Yes':
                    continue

                try:
                    # The following line was removed to reduce log messages
                    # self.log_queue.put((f"Scanning subfolder: {folder_entry['path']}", 2))
                    with os.scandir(folder_entry['path']) as it:
                        for entry in it:
                            if entry.is_file(follow_symlinks=False):
                                try:
                                    mod_time = datetime.fromtimestamp(entry.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                                    indexed_files_data.append({
                                        "name": entry.name,
                                        "path": entry.path,
                                        "modified_date": mod_time
                                    })
                                except (OSError, PermissionError):
                                    pass
                    
                    folder_entry['status'] = 'Yes'
                    something_was_indexed_in_run = True
                    subfolders_since_update += 1

                    if subfolders_since_update >= 500:
                        self.log_queue.put((f"CHECKPOINT: Saving progress. {i + 1} of {total_subfolders} subfolders processed.", 2))
                        write_json(status_file_path, status_data)
                        write_json(data_file_path, indexed_files_data)
                        subfolders_since_update = 0

                except (OSError, PermissionError) as e:
                    self.log_queue.put((f"WARNING: Skipping inaccessible subfolder {folder_entry['path']}: {e}", 2))
                    folder_entry['status'] = 'Yes' 
                    something_was_indexed_in_run = True
                    continue

            if something_was_indexed_in_run:
                self.log_queue.put(("Saving final data for this run...", 1))
                write_json(status_file_path, status_data)
                write_json(data_file_path, indexed_files_data)

            if all(f['status'] == 'Yes' for f in subfolder_statuses):
                self.log_queue.put((f"SUCCESS: Granular indexing complete for {top_folder_info['name']}.", 1))
                top_folder_info['Indexed'] = "Yes"
                top_folder_info['Date Indexed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                top_folder_info['Index File'] = os.path.basename(data_file_path) 
                write_json(main_config_path, main_config)
            elif something_was_indexed_in_run:
                 self.log_queue.put((f"Finished a partial indexing run for {top_folder_info['name']}. Restart to continue.", 1))

        self.log_queue.put(("All top-level folders processed.", 0))

if __name__ == "__main__":
    root = tk.Tk()
    app = IndexerGUI(root)
    root.mainloop()