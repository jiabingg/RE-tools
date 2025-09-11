import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import shutil
import threading
from datetime import datetime
import csv

class MainApp(tk.Tk):
    """
    The main application window that manages and switches between different pages (frames).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.title("File Management Workflow")
        self.geometry("850x700")
        self.configure(bg="#eaf0f2")
        self.minsize(700, 600)

        # Container frame that will hold all the pages
        container = tk.Frame(self)
        container.pack(side="top", fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.frames = {}

        # Initialize each page and add it to the frames dictionary
        for F in (ConsolidatorPage, FinderPage):
            frame = F(container, self)
            self.frames[F] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.show_frame(ConsolidatorPage)

    def show_frame(self, cont):
        """Raises the selected frame to the top."""
        frame = self.frames[cont]
        frame.tkraise()

    def share_path_to_finder(self, path):
        """
        Passes the target path from the consolidator to the finder page
        and automatically switches to the finder page.
        """
        finder_frame = self.frames[FinderPage]
        finder_frame.set_source_path(path)
        messagebox.showinfo(
            "Step 1 Complete",
            f"Consolidation is finished.\nThe source path for Step 2 has been set to:\n\n{path}\n\nYou will now be taken to Step 2."
        )
        self.show_frame(FinderPage)


class ConsolidatorPage(tk.Frame):
    """
    Page 1: Consolidates files from multiple sources into one target folder,
    keeping only the newest version of each file.
    """
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#eaf0f2")
        self.controller = controller

        main_frame = tk.Frame(self, padx=15, pady=15, bg="#eaf0f2")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(1, weight=1)

        # --- Title and Navigation ---
        title_frame = tk.Frame(main_frame, bg="#eaf0f2")
        title_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        tk.Label(title_frame, text="Step 1: Consolidate Files", font=("Helvetica", 16, "bold"), bg="#eaf0f2").pack(side=tk.LEFT)
        tk.Button(title_frame, text="Go to Step 2 ->", command=lambda: controller.show_frame(FinderPage)).pack(side=tk.RIGHT)

        # --- Section 1: Source Folders ---
        tk.Label(main_frame, text="Source Folders:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=1, column=0, sticky="nw", pady=5)
        source_frame = tk.Frame(main_frame, bg="#eaf0f2")
        source_frame.grid(row=1, column=1, columnspan=2, sticky="nsew", padx=5)
        source_frame.columnconfigure(0, weight=1)
        self.source_listbox = tk.Listbox(source_frame, font=("Arial", 10), height=8, relief=tk.SOLID, borderwidth=1, selectmode=tk.EXTENDED)
        self.source_listbox.grid(row=1, column=0, sticky="ew")
        source_button_frame = tk.Frame(source_frame, bg="#eaf0f2")
        source_button_frame.grid(row=1, column=1, sticky="ns", padx=5)
        tk.Button(source_button_frame, text="Add Folder...", command=self.add_source_dir).pack(fill=tk.X, pady=2)
        tk.Button(source_button_frame, text="Remove Selected", command=self.remove_source_dir).pack(fill=tk.X, pady=2)
        main_frame.rowconfigure(1, weight=1)

        # --- Section 2: Target Folder ---
        tk.Label(main_frame, text="Target Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=2, column=0, sticky="w", pady=15)
        self.target_path = tk.StringVar()
        target_entry = tk.Entry(main_frame, textvariable=self.target_path, font=("Arial", 10))
        target_entry.grid(row=2, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_target_dir).grid(row=2, column=2, padx=5)

        # --- Action Button ---
        self.process_button = tk.Button(
            main_frame, text="Start Consolidating Files", font=("Helvetica", 12, "bold"),
            bg="#007bff", fg="white", command=self.start_processing_thread, pady=8
        )
        self.process_button.grid(row=3, column=0, columnspan=3, pady=20, sticky="ew")

        # --- Log Display ---
        tk.Label(main_frame, text="Log:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.log_display = scrolledtext.ScrolledText(
            main_frame, wrap=tk.WORD, font=("Courier New", 9),
            bg="white", relief=tk.SOLID, borderwidth=1
        )
        self.log_display.grid(row=5, column=0, columnspan=3, sticky="nsew")
        main_frame.rowconfigure(5, weight=2)

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
        if self.source_listbox.size() == 0 or not self.target_path.get():
            messagebox.showerror("Error", "Please provide at least one source folder and a target folder.")
            return

        self.process_button.config(state=tk.DISABLED, text="Processing...")
        thread = threading.Thread(target=self.process_files)
        thread.daemon = True
        thread.start()

    def process_files(self):
        self.log_display.delete('1.0', tk.END)
        source_dirs = self.source_listbox.get(0, tk.END)
        target_dir = self.target_path.get()
        
        counts = {'copied': 0, 'overwritten': 0, 'skipped': 0, 'errors': 0}
        success = False

        def gui_log(message):
            self.controller.after(0, self.log, message)

        try:
            os.makedirs(target_dir, exist_ok=True)
            gui_log(f"INFO: Target folder is '{target_dir}'")
            gui_log("-" * 60)

            for source_dir in source_dirs:
                gui_log(f"INFO: Scanning source folder: '{source_dir}'")
                for dirpath, _, filenames in os.walk(source_dir):
                    for filename in filenames:
                        try:
                            source_path = os.path.join(dirpath, filename)
                            target_path = os.path.join(target_dir, filename)

                            if not os.path.exists(target_path):
                                shutil.copy2(source_path, target_path)
                                gui_log(f"COPIED: '{filename}'")
                                counts['copied'] += 1
                            else:
                                source_mtime = os.path.getmtime(source_path)
                                target_mtime = os.path.getmtime(target_path)
                                if source_mtime > target_mtime:
                                    shutil.copy2(source_path, target_path)
                                    gui_log(f"OVERWRITTEN (newer): '{filename}'")
                                    counts['overwritten'] += 1
                                else:
                                    counts['skipped'] += 1
                        except Exception as e:
                            gui_log(f"ERROR processing '{filename}': {e}")
                            counts['errors'] += 1
                gui_log(f"INFO: Finished scanning '{source_dir}'.")

            gui_log("-" * 60)
            summary = (f"Consolidation Finished. Copied: {counts['copied']}, "
                       f"Overwritten: {counts['overwritten']}, Skipped (older): {counts['skipped']}, Errors: {counts['errors']}.")
            gui_log(summary)
            if counts['errors'] == 0:
                success = True
            
        except Exception as e:
            summary = f"An unexpected error occurred: {e}"
            gui_log(summary)
        finally:
            self.controller.after(0, self.process_button.config, {'state': tk.NORMAL, 'text': 'Start Consolidating Files'})
            if success:
                # Pass the path to the controller to share with the other page
                self.controller.after(10, self.controller.share_path_to_finder, target_dir)
            else:
                self.controller.after(1, lambda: messagebox.showerror("Process Incomplete", "The consolidation process finished with errors. Please check the log."))


class FinderPage(tk.Frame):
    """
    Page 2: Finds specific files in the consolidated folder based on 12-digit
    UWI numbers and copies them to a final destination.
    """
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#eaf0f2")
        self.controller = controller

        main_frame = tk.Frame(self, padx=15, pady=15, bg="#eaf0f2")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(1, weight=1)

        # --- Title and Navigation ---
        title_frame = tk.Frame(main_frame, bg="#eaf0f2")
        title_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        tk.Label(title_frame, text="Step 2: Find Files by UWI", font=("Helvetica", 16, "bold"), bg="#eaf0f2").pack(side=tk.LEFT)
        tk.Button(title_frame, text="<- Back to Step 1", command=lambda: controller.show_frame(ConsolidatorPage)).pack(side=tk.RIGHT)

        # --- Section 1: Source Folder ---
        tk.Label(main_frame, text="Source Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=1, column=0, sticky="w", pady=5)
        self.source_path = tk.StringVar()
        source_entry = tk.Entry(main_frame, textvariable=self.source_path, font=("Arial", 10))
        source_entry.grid(row=1, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=lambda: self.select_dir(self.source_path, "Select Source Folder (Consolidated)")).grid(row=1, column=2, padx=5)

        # --- Section 2: Target Folder ---
        tk.Label(main_frame, text="Target Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=2, column=0, sticky="w", pady=5)
        self.target_path = tk.StringVar()
        target_entry = tk.Entry(main_frame, textvariable=self.target_path, font=("Arial", 10))
        target_entry.grid(row=2, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=lambda: self.select_dir(self.target_path, "Select Final Target Folder")).grid(row=2, column=2, padx=5)

        # --- Section 3: 12-Digit Numbers ---
        tk.Label(main_frame, text="12-Digit Numbers:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=3, column=0, sticky="nw", pady=(15, 5))
        tk.Label(main_frame, text="(one per line)", font=("Helvetica", 9), bg="#eaf0f2").grid(row=4, column=0, sticky="nw", pady=0)
        self.file_list_textbox = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, font=("Courier New", 10), height=8, relief=tk.SOLID, borderwidth=1)
        self.file_list_textbox.grid(row=3, column=1, columnspan=2, rowspan=2, sticky="nsew", padx=5, pady=(15, 5))
        main_frame.rowconfigure(3, weight=1)

        # --- Action Button ---
        self.process_button = tk.Button(
            main_frame, text="Find and Copy Files", font=("Helvetica", 12, "bold"),
            bg="#28a745", fg="white", command=self.start_processing_thread, pady=8
        )
        self.process_button.grid(row=5, column=0, columnspan=3, pady=20, sticky="ew")

        # --- Log Display ---
        tk.Label(main_frame, text="Log:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=6, column=0, sticky="w", pady=(10, 0))
        self.log_display = scrolledtext.ScrolledText(
            main_frame, wrap=tk.WORD, font=("Courier New", 9),
            bg="white", relief=tk.SOLID, borderwidth=1
        )
        self.log_display.grid(row=7, column=0, columnspan=3, sticky="nsew")
        main_frame.rowconfigure(7, weight=2)

    def set_source_path(self, path):
        """Method to allow the controller to set the source path."""
        self.source_path.set(path)

    def select_dir(self, string_var, title):
        path = filedialog.askdirectory(title=title)
        if path:
            string_var.set(path)

    def log(self, message):
        self.log_display.insert(tk.END, message + "\n")
        self.log_display.see(tk.END)

    def start_processing_thread(self):
        if not self.source_path.get() or not self.target_path.get() or not self.file_list_textbox.get('1.0', tk.END).strip():
            messagebox.showerror("Error", "Please provide a source folder, a target folder, and at least one 12-digit number.")
            return

        self.process_button.config(state=tk.DISABLED, text="Processing...")
        thread = threading.Thread(target=self.process_files)
        thread.daemon = True
        thread.start()

    def process_files(self):
        self.log_display.delete('1.0', tk.END)
        source_dir = self.source_path.get()
        target_dir = self.target_path.get()
        input_lines = self.file_list_textbox.get('1.0', tk.END).splitlines()
        
        csv_log_data = []
        summary = "Process did not complete."

        def gui_log(message):
            self.controller.after(0, self.log, message)

        try:
            gui_log("INFO: Reading files from the source folder...")
            source_files = set(os.listdir(source_dir))
            
            os.makedirs(target_dir, exist_ok=True)
            gui_log(f"INFO: Found {len(source_files)} files in '{source_dir}'")
            gui_log("-" * 60)

            files_copied = 0
            not_found = 0

            for line in input_lines:
                clean_line = line.strip()
                if not (len(clean_line) == 12 and clean_line.isdigit()):
                    gui_log(f"WARNING: Skipping invalid input line: '{line}'")
                    csv_log_data.append({'InputNumber': line, 'Status': 'Invalid Input', 'ErrorMessage': 'Must be exactly 12 digits.'})
                    continue
                
                api = clean_line[:10]
                bore = clean_line[10:]
                
                if bore == "00":
                    possible_names = [f"{api}.pdf", f"{api}_00.pdf"]
                else:
                    possible_names = [f"{api}_{bore}.pdf"]
                
                file_found = False
                for filename in possible_names:
                    if filename in source_files:
                        source_path = os.path.join(source_dir, filename)
                        target_path = os.path.join(target_dir, filename)
                        mod_time = os.path.getmtime(source_path)

                        shutil.copy2(source_path, target_path)
                        gui_log(f"COPIED: '{filename}' for input {clean_line}")
                        files_copied += 1
                        
                        csv_log_data.append({
                            'InputNumber': clean_line, 'API': api, 'Bore': bore,
                            'Status': 'Copied', 'CopiedFilePath': source_path,
                            'LastModified': datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S'),
                            'TargetFilePath': target_path
                        })
                        file_found = True
                        break
                
                if not file_found:
                    gui_log(f"NOT FOUND: No file found for input {clean_line}")
                    not_found += 1
                    csv_log_data.append({'InputNumber': clean_line, 'API': api, 'Bore': bore, 'Status': 'Not Found'})

            gui_log("-" * 60)
            invalid_inputs = len(input_lines) - (files_copied + not_found)
            summary = (f"Process Finished. Copied: {files_copied}, Not Found: {not_found}, Invalid Inputs: {invalid_inputs}.")
            gui_log(summary)

            log_file_path = os.path.join(target_dir, f"copy_log_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv")
            headers = ['InputNumber', 'API', 'Bore', 'Status', 'CopiedFilePath', 'LastModified', 'TargetFilePath', 'ErrorMessage']
            with open(log_file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(csv_log_data)
            
            gui_log(f"\nINFO: A detailed CSV log has been saved to: {log_file_path}")

        except Exception as e:
            summary = f"An unexpected error occurred: {e}"
            gui_log(summary)
        finally:
            self.controller.after(0, self.process_button.config, {'state': tk.NORMAL, 'text': 'Find and Copy Files'})
            self.controller.after(1, lambda: messagebox.showinfo("Complete", summary))


if __name__ == "__main__":
    app = MainApp()
    app.mainloop()

