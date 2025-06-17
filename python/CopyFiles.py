import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import shutil
import threading
from datetime import datetime

class FileCopierApp:
    """
    A GUI application to copy files from a source directory to a target directory
    based on a list of filenames in a text file.
    """

    def __init__(self, root):
        """Initializes the application window and its widgets."""
        self.root = root
        self.root.title("File Copier Utility")
        self.root.geometry("800x600")
        self.root.configure(bg="#eaf0f2")

        # --- Main frame ---
        main_frame = tk.Frame(self.root, padx=15, pady=15, bg="#eaf0f2")
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.columnconfigure(1, weight=1)

        # --- Input Fields ---
        # 1. Source Directory
        tk.Label(main_frame, text="Source Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=0, column=0, sticky="w", pady=5)
        self.source_path = tk.StringVar()
        source_entry = tk.Entry(main_frame, textvariable=self.source_path, font=("Arial", 10), width=70)
        source_entry.grid(row=0, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_source_dir).grid(row=0, column=2, padx=5)

        # 2. Target Directory
        tk.Label(main_frame, text="Target Folder:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=1, column=0, sticky="w", pady=5)
        self.target_path = tk.StringVar()
        target_entry = tk.Entry(main_frame, textvariable=self.target_path, font=("Arial", 10))
        target_entry.grid(row=1, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_target_dir).grid(row=1, column=2, padx=5)

        # 3. File List (.txt)
        tk.Label(main_frame, text="File List (.txt):", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=2, column=0, sticky="w", pady=5)
        self.file_list_path = tk.StringVar()
        file_list_entry = tk.Entry(main_frame, textvariable=self.file_list_path, font=("Arial", 10))
        file_list_entry.grid(row=2, column=1, sticky="ew", padx=5)
        tk.Button(main_frame, text="Browse...", command=self.select_file_list).grid(row=2, column=2, padx=5)

        # --- Action Button ---
        self.process_button = tk.Button(
            main_frame,
            text="Start Copying Files",
            font=("Helvetica", 12, "bold"),
            bg="#007bff",
            fg="white",
            command=self.start_processing_thread,
            pady=8
        )
        self.process_button.grid(row=3, column=0, columnspan=3, pady=20, sticky="ew")

        # --- Log Display ---
        tk.Label(main_frame, text="Log:", font=("Helvetica", 11, "bold"), bg="#eaf0f2").grid(row=4, column=0, sticky="w", pady=(10,0))
        self.log_display = scrolledtext.ScrolledText(
            main_frame,
            wrap=tk.WORD,
            font=("Courier New", 9),
            height=15,
            bg="white",
            relief=tk.SOLID,
            borderwidth=1
        )
        self.log_display.grid(row=5, column=0, columnspan=3, sticky="nsew")
        main_frame.rowconfigure(5, weight=1)

    def select_source_dir(self):
        """Opens a dialog to select the source directory."""
        path = filedialog.askdirectory(title="Select Source Folder")
        if path:
            self.source_path.set(path)

    def select_target_dir(self):
        """Opens a dialog to select the target directory."""
        path = filedialog.askdirectory(title="Select Target Folder")
        if path:
            self.target_path.set(path)

    def select_file_list(self):
        """Opens a dialog to select the file list .txt file."""
        path = filedialog.askopenfilename(
            title="Select File List",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if path:
            self.file_list_path.set(path)

    def log(self, message):
        """Adds a message to the GUI log display."""
        self.log_display.insert(tk.END, message + "\n")
        self.log_display.see(tk.END) # Auto-scroll to the bottom

    def start_processing_thread(self):
        """Starts the file copying process in a separate thread to avoid freezing the GUI."""
        # Validate inputs
        if not all([self.source_path.get(), self.target_path.get(), self.file_list_path.get()]):
            messagebox.showerror("Error", "All three paths (Source, Target, and File List) are required.")
            return

        # Disable the button to prevent multiple clicks
        self.process_button.config(state=tk.DISABLED, text="Processing...")
        
        # Start the background task
        thread = threading.Thread(target=self.process_files)
        thread.daemon = True # Allows the main window to exit even if the thread is running
        thread.start()

    def process_files(self):
        """The core logic for reading the list, finding files, and copying them."""
        # Clear the log display at the beginning of the process
        self.log_display.delete('1.0', tk.END)

        source_dir = self.source_path.get()
        target_dir = self.target_path.get()
        file_list_path = self.file_list_path.get()

        log_messages = []

        def gui_log(message):
            """Helper to log to both the list and the GUI."""
            log_messages.append(message)
            self.root.after(0, self.log, message) # Schedule GUI update on the main thread

        try:
            # --- 1. Read the list of filenames ---
            gui_log(f"INFO: Reading file list from: {file_list_path}")
            with open(file_list_path, 'r', encoding='utf-8') as f:
                # Use a set for efficient lookup. Strip whitespace from each line.
                filenames_to_find = {line.strip() for line in f if line.strip()}
            
            if not filenames_to_find:
                 gui_log("WARNING: The file list is empty. No files to copy.")
                 return # Exit early

            files_found_map = {name: False for name in filenames_to_find}
            gui_log(f"INFO: Loaded {len(filenames_to_find)} unique filenames to find.")
            gui_log("-" * 50)


            # --- 2. Walk through the source directory ---
            files_copied_count = 0
            for dirpath, _, filenames in os.walk(source_dir):
                for filename in filenames:
                    if filename in filenames_to_find:
                        source_file_path = os.path.join(dirpath, filename)
                        target_file_path = os.path.join(target_dir, filename)
                        
                        try:
                            # Ensure target directory exists
                            os.makedirs(target_dir, exist_ok=True)
                            
                            # Copy the file
                            shutil.copy2(source_file_path, target_file_path)
                            gui_log(f"COPIED: '{source_file_path}' -> '{target_file_path}'")
                            files_found_map[filename] = True
                            files_copied_count += 1
                        except Exception as e:
                            gui_log(f"ERROR: Could not copy '{filename}'. Reason: {e}")

            # --- 3. Report files that were not found ---
            gui_log("-" * 50)
            gui_log("INFO: Search complete. Verifying results...")

            not_found_count = 0
            for filename, found in files_found_map.items():
                if not found:
                    gui_log(f"NOT FOUND: '{filename}' was not found in the source folder or its subfolders.")
                    not_found_count += 1
            
            gui_log("-" * 50)
            summary = f"Process Finished. Files Copied: {files_copied_count}. Files Not Found: {not_found_count}."
            gui_log(summary)
            
            # --- 4. Write the log to a file ---
            log_file_path = os.path.join(target_dir, f"copy_log_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt")
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(log_messages))
            gui_log(f"\nINFO: A detailed log has been saved to: {log_file_path}")

        except FileNotFoundError:
            gui_log(f"ERROR: The specified file list was not found at: {file_list_path}")
        except Exception as e:
            gui_log(f"An unexpected error occurred: {e}")
        finally:
            # Re-enable the button once processing is complete or an error occurs
            self.root.after(0, self.process_button.config, {'state': tk.NORMAL, 'text': 'Start Copying Files'})
            # Show a final popup message
            self.root.after(1, lambda: messagebox.showinfo("Complete", summary))


if __name__ == "__main__":
    root = tk.Tk()
    app = FileCopierApp(root)
    root.mainloop()
