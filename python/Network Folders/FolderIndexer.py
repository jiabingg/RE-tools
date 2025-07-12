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
        self.root.title("File Indexer")
        self.root.geometry("700x450")

        # --- UI Elements ---
        self.log_area = scrolledtext.ScrolledText(root, wrap=tk.WORD, state='disabled')
        self.log_area.pack(padx=10, pady=10, expand=True, fill='both')

        self.start_button = tk.Button(root, text="Start Indexing", command=self.start_indexing_thread)
        self.start_button.pack(pady=10)

        # --- Queue for thread communication ---
        self.log_queue = queue.Queue()
        self.root.after(100, self.process_log_queue)

    def log_message(self, message):
        """Adds a message to the log area."""
        self.log_area.configure(state='normal')
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.configure(state='disabled')
        self.log_area.see(tk.END)

    def process_log_queue(self):
        """Processes messages from the log queue to update the GUI."""
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.log_message(message)
        self.root.after(100, self.process_log_queue)
        
    def start_indexing_thread(self):
        """Starts the indexing process in a separate thread to keep the GUI responsive."""
        self.start_button.config(state=tk.DISABLED, text="Indexing...")
        self.log_area.configure(state='normal')
        self.log_area.delete('1.0', tk.END)
        self.log_area.configure(state='disabled')
        
        self.log_message("Starting indexing process...")
        
        # Create and start the thread
        self.indexing_thread = threading.Thread(target=self.run_indexer, daemon=True)
        self.indexing_thread.start()
        
        # Check thread status
        self.root.after(100, self.check_thread)

    def check_thread(self):
        """Checks if the indexing thread is still running."""
        if self.indexing_thread.is_alive():
            self.root.after(100, self.check_thread)
        else:
            self.start_button.config(state=tk.NORMAL, text="Start Indexing")
            self.log_message("\n--- Indexing Complete ---")
            messagebox.showinfo("Complete", "The indexing process has finished.")

    def run_indexer(self):
        """The core indexing logic, adapted to send messages to the GUI queue."""
        """The core indexing logic, adapted to send messages to the GUI queue."""
        # --- MODIFICATION START ---
        # Get the directory where the script itself is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Create a full, absolute path to folders.json
        config_path = os.path.join(script_dir, 'folders.json')
        # --- MODIFICATION END ---
        
        def sanitize_filename(name):
            return "".join(c if c.isalnum() else '_' for c in name)

        try:
            with open(config_path, 'r') as f:
                folders_to_index = json.load(f)
        except FileNotFoundError:
            self.log_queue.put(f"ERROR: Configuration file '{config_path}' not found.")
            return
        except json.JSONDecodeError:
            self.log_queue.put(f"ERROR: Configuration file '{config_path}' is not a valid JSON.")
            return

        folders_were_updated = False
        for folder_info in folders_to_index:
            if folder_info.get("Indexed") == "No":
                path = folder_info["path"]
                self.log_queue.put(f"\nScanning folder: {path}")

                file_data = []
                try:
                    if not os.path.exists(path):
                        self.log_queue.put(f"  -> WARNING: Path does not exist. Skipping.")
                        continue

                    for root, _, files in os.walk(path):
                        for file in files:
                            full_path = os.path.join(root, file)
                            try:
                                modified_time = os.path.getmtime(full_path)
                                modified_date = datetime.fromtimestamp(modified_time).strftime('%Y-%m-%d %H:%M:%S')
                                file_data.append({
                                    "name": file,
                                    "path": full_path,
                                    "modified_date": modified_date
                                })
                            except (OSError, PermissionError) as e:
                                self.log_queue.put(f"  -> WARNING: Could not access file '{full_path}'. Skipping.")
                                continue
                    
                        # --- MODIFICATION START ---
                        base_filename = f"{sanitize_filename(folder_info['name'])}.json"
                        output_filename = os.path.join(script_dir, base_filename)
                        # --- MODIFICATION END ---
                        
                        with open(output_filename, 'w') as out_file:
                            json.dump(file_data, out_file, indent=4)

                    self.log_queue.put(f"  -> SUCCESS: Indexed {len(file_data)} files into '{output_filename}'.")
                    
                    folder_info["Indexed"] = "Yes"
                    folder_info["Date Indexed"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    folders_were_updated = True

                except PermissionError:
                    self.log_queue.put(f"  -> ERROR: Permission denied for this folder. Skipping.")
                except Exception as e:
                    self.log_queue.put(f"  -> ERROR: An unexpected error occurred: {e}. Skipping.")
        
        if folders_were_updated:
            try:
                with open(config_path, 'w') as f:
                    json.dump(folders_to_index, f, indent=4)
                self.log_queue.put("\nUpdated 'folders.json' successfully.")
            except Exception as e:
                 self.log_queue.put(f"\nERROR: Could not write to 'folders.json': {e}")
        else:
            self.log_queue.put("\nNo new folders needed indexing.")


if __name__ == "__main__":
    root = tk.Tk()
    app = IndexerGUI(root)
    root.mainloop()