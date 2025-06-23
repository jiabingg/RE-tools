import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import re
import json
import threading
from datetime import datetime

class PdfIndexerApp:
    """
    A GUI application to scan a list of directories for PDF files, extract a 10-digit
    Well API from the filenames, and save the data to a JSON file.
    """

    def __init__(self, root):
        """Initializes the application window and its widgets."""
        self.root = root
        self.root.title("PDF Well API Indexer")
        self.root.geometry("800x600")
        self.root.configure(bg="#f0f2f5")

        # --- List of folders to scan. ---
        # --- YOU CAN EDIT THIS LIST TO ADD YOUR FOLDER PATHS ---
        # Use forward slashes (/) or escaped backslashes (\\) for paths.
        self.folders_to_scan = [
            "C:/Users/bj211404/Downloads/CopyFile/Test/Source",
            "C:/Users/bj211404/Downloads/CopyFile/Test/Source2",
            # Add more folder paths here
        ]

        # --- Main frame ---
        main_frame = tk.Frame(self.root, padx=20, pady=20, bg="#f0f2f5")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(1, weight=1)

        # --- Input Fields ---
        # Removed the UI for selecting a single source folder.
        # 1. Output Data File
        tk.Label(main_frame, text="Save Data File As:", font=("Helvetica", 11, "bold"), bg="#f0f2f5").grid(row=0, column=0, sticky="w", pady=5)
        self.output_path = tk.StringVar()
        output_entry = tk.Entry(main_frame, textvariable=self.output_path, font=("Arial", 10))
        output_entry.grid(row=0, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_output_file).grid(row=0, column=2, padx=5)

        # --- Action Button ---
        self.process_button = tk.Button(
            main_frame,
            text="Start Scanning and Create Index File",
            font=("Helvetica", 12, "bold"),
            bg="#28a745", # Green color
            fg="white",
            command=self.start_scanning_thread,
            pady=8
        )
        self.process_button.grid(row=1, column=0, columnspan=3, pady=20, sticky="ew")

        # --- Log Display ---
        tk.Label(main_frame, text="Log:", font=("Helvetica", 11, "bold"), bg="#f0f2f5").grid(row=2, column=0, sticky="w", pady=(10,0))
        self.log_display = scrolledtext.ScrolledText(
            main_frame,
            wrap=tk.WORD,
            font=("Courier New", 9),
            height=15,
            bg="white",
            relief=tk.SOLID,
            borderwidth=1
        )
        self.log_display.grid(row=3, column=0, columnspan=3, sticky="nsew")
        main_frame.rowconfigure(3, weight=1)
        self.log_display.insert(tk.END, "Please select an output file location and click the button to start.")


    def select_output_file(self):
        """Opens a dialog to select the output JSON file save location."""
        path = filedialog.asksaveasfilename(
            title="Save Data File As",
            defaultextension=".json",
            initialfile="well_data.json",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*"))
        )
        if path:
            self.output_path.set(path)

    def log(self, message):
        """Adds a message to the GUI log display on the main thread."""
        self.root.after(0, self._log_update, message)

    def _log_update(self, message):
        """Internal method to update the widget, ensures it runs on main thread."""
        self.log_display.insert(tk.END, message + "\n")
        self.log_display.see(tk.END) # Auto-scroll to the bottom

    def start_scanning_thread(self):
        """Starts the file scanning process in a separate thread to keep the GUI responsive."""
        if not self.output_path.get():
            messagebox.showerror("Error", "The 'Save Data File As' path is required.")
            return

        self.process_button.config(state=tk.DISABLED, text="Scanning...")
        self.log_display.delete('1.0', tk.END)
        
        thread = threading.Thread(target=self.scan_and_index_files)
        thread.daemon = True
        thread.start()

    def scan_and_index_files(self):
        """The core logic for scanning files, extracting APIs, and writing the JSON file."""
        output_file = self.output_path.get()
        
        self.log(f"INFO: Output will be saved to '{output_file}'")
        self.log("-" * 60)

        well_data = []
        pdf_count = 0
        
        # Regex to find a 10-digit number. \b ensures we match whole words only.
        api_regex = re.compile(r'\b(\d{10})\b')

        try:
            # Loop through the list of folders provided in the code
            for folder_to_scan in self.folders_to_scan:
                if not os.path.isdir(folder_to_scan):
                    self.log(f"WARNING: Folder not found, skipping: {folder_to_scan}")
                    continue
                
                self.log(f"INFO: Scanning folder: {folder_to_scan}")
                for dirpath, _, filenames in os.walk(folder_to_scan):
                    for filename in filenames:
                        if filename.lower().endswith('.pdf'):
                            match = api_regex.search(filename)
                            if match:
                                api = match.group(1)
                                full_path = os.path.join(dirpath, filename)
                                path_for_html = full_path.replace('/', '\\')

                                # Get file modification date
                                mod_timestamp = os.path.getmtime(full_path)
                                mod_date_str = datetime.fromtimestamp(mod_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                                
                                well_data.append({
                                    'api': api,
                                    'filename': filename,
                                    'path': path_for_html, # Using original path with backslashes
                                    'modified': mod_date_str
                                })
                                pdf_count += 1
                                self.log(f"  -> FOUND: API {api} in '{filename}' (Modified: {mod_date_str})")
            
            self.log("-" * 60)
            
            if not well_data:
                self.log("WARNING: Scan complete, but no PDFs with 10-digit APIs were found.")
                messagebox.showwarning("Scan Complete", "No PDFs with valid 10-digit APIs were found in the specified folders.")
                return

            # Write the collected data to the JSON file
            self.log(f"INFO: Found {pdf_count} PDFs with APIs. Writing to JSON file...")
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(well_data, f, indent=2)

            self.log("SUCCESS: Index file created successfully!")
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Successfully created index file with {pdf_count} entries at:\n{output_file}"))

        except Exception as e:
            self.log(f"ERROR: An unexpected error occurred: {e}")
            self.root.after(0, lambda: messagebox.showerror("Error", f"An error occurred during scanning: {e}"))
        finally:
            # Re-enable the button
            self.root.after(0, self.process_button.config, {'state': tk.NORMAL, 'text': 'Start Scanning and Create Index File'})


if __name__ == "__main__":
    root = tk.Tk()
    app = PdfIndexerApp(root)
    root.mainloop()
