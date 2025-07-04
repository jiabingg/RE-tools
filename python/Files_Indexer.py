import os
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, Listbox, Scrollbar, Frame, Button, Label

CONFIG_FILE = 'config.json'

def create_index(folder_paths, output_file, status_callback, progress_callback):
    """
    Scans folders and creates/updates a JSON index file with file metadata.
    Supports incremental updates to speed up re-scans.
    """
    
    # --- Step 1: Load existing index if it exists ---
    old_index = {}
    if os.path.exists(output_file):
        status_callback("Loading existing index for comparison...")
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                # Create a dictionary for fast lookups by path
                old_data = json.load(f)
                for item in old_data:
                    old_index[item['path']] = item
        except (json.JSONDecodeError, IOError) as e:
            status_callback(f"Warning: Could not read old index. Performing full scan. Error: {e}")
            old_index = {}

    # --- Step 2: Walk through directories and update index ---
    new_index = []
    paths_found = set()
    status_callback("Starting to scan folders...")
    
    for folder_path in folder_paths:
        status_callback(f"Scanning: {folder_path}...")
        if not os.path.exists(folder_path):
            status_callback(f"WARNING: Path not found, skipping: {folder_path}")
            continue
        
        for root, _, files in os.walk(folder_path):
            progress_callback() # Update progress bar for visual feedback
            for filename in files:
                try:
                    full_path = os.path.join(root, filename)
                    path_for_html = full_path.replace('/', '\\')
                    paths_found.add(path_for_html)
                    
                    # Get file metadata
                    mtime = os.path.getmtime(full_path)
                    
                    # If file is in old index and hasn't changed, reuse old data
                    if full_path in old_index and old_index[full_path]['mtime'] == mtime:
                        new_index.append(old_index[full_path])
                        continue

                    # Otherwise, it's a new or modified file, get fresh data
                    file_stat = os.stat(full_path)
                    _, extension = os.path.splitext(filename)
                    
                    new_index.append({
                        'name': filename,
                        'path': full_path,
                        'size': file_stat.st_size,
                        'mtime': file_stat.st_mtime,
                        'type': extension.lower() if extension else '.file'
                    })
                except Exception as e:
                    print(f"Could not process a file in {root}. Error: {e}")

    # --- Step 3: Report results and save the new index ---
    deleted_count = len(old_index) - len(paths_found.intersection(old_index.keys()))
    status_callback(f"Scan complete. Found {len(new_index)} files. ({deleted_count} files removed).")
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(new_index, f, indent=4)
        status_callback(f"Successfully saved index to: '{os.path.basename(output_file)}'")
        messagebox.showinfo("Success", f"Indexing complete!\nFound {len(new_index)} files.\n\nSaved to: {output_file}")
    except IOError as e:
        status_callback(f"FATAL ERROR: Could not write to index file: {e}")
        messagebox.showerror("Error", f"Could not write to index file:\n{e}")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("File Indexer v2")
        self.geometry("700x500")
        self.configure(bg="#f0f0f0")

        self.folder_paths = []
        self.output_file_path = ""

        # --- UI Elements ---
        main_frame = Frame(self, bg="#f0f0f0", padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Profile management
        profile_frame = Frame(main_frame, bg="#f0f0f0")
        profile_frame.pack(fill=tk.X, pady=(0, 10))
        Label(profile_frame, text="Configuration:", bg="#f0f0f0", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT)
        Button(profile_frame, text="Save Profile", command=self.save_profile).pack(side=tk.LEFT, padx=5)
        Button(profile_frame, text="Load Profile", command=self.load_profile).pack(side=tk.LEFT, padx=5)

        # Folder management
        folder_frame = Frame(main_frame)
        folder_frame.pack(fill=tk.BOTH, expand=True)
        
        list_frame = Frame(folder_frame)
        list_frame.pack(pady=5, fill=tk.BOTH, expand=True)
        self.listbox = Listbox(list_frame, selectmode=tk.SINGLE)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        
        folder_buttons_frame = Frame(main_frame, bg="#f0f0f0")
        folder_buttons_frame.pack(fill=tk.X, pady=5)
        Button(folder_buttons_frame, text="Add Folder to Scan", command=self.add_folder).pack(side=tk.LEFT, padx=0)
        Button(folder_buttons_frame, text="Remove Selected", command=self.remove_folder).pack(side=tk.LEFT, padx=5)

        # Output file selection
        output_frame = Frame(main_frame, bg="#f0f0f0")
        output_frame.pack(fill=tk.X, pady=5)
        Button(output_frame, text="Set Output File", command=self.set_output_file).pack(side=tk.LEFT, padx=0)
        self.output_label = Label(output_frame, text="Output file not set.", bg="#f0f0f0", fg="red", anchor="w")
        self.output_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Progress bar and status
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=5)
        self.status_label = Label(self, text="Ready. Load a profile or add folders.", bd=1, relief=tk.SUNKEN, anchor=tk.W, bg="#dfdfdf")
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # Start button
        self.start_btn = Button(main_frame, text="Start Indexing", command=self.start_indexing, bg="#28a745", fg="white", font=("Helvetica", 10, "bold"))
        self.start_btn.pack(pady=10, fill=tk.X)
        
        self.load_profile(silent=True)

    def add_folder(self):
        folder = filedialog.askdirectory()
        if folder and folder not in self.folder_paths:
            self.folder_paths.append(folder)
            self.update_listbox()
            self.update_status(f"Added: {folder}")

    def remove_folder(self):
        indices = self.listbox.curselection()
        if not indices: return
        path = self.listbox.get(indices[0])
        self.folder_paths.remove(path)
        self.update_listbox()
        self.update_status(f"Removed: {path}")

    def set_output_file(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")], initialfile="index.json")
        if path:
            self.output_file_path = path
            self.output_label.config(text=f"Save to: {self.output_file_path}", fg="black")
            self.update_status(f"Output file set to: {self.output_file_path}")

    def update_listbox(self):
        self.listbox.delete(0, tk.END)
        for path in self.folder_paths:
            self.listbox.insert(tk.END, path)

    def update_status(self, message):
        self.status_label.config(text=message)
        self.update_idletasks()

    def progress_step(self):
        self.progress.step(1)
        self.update_idletasks()

    def start_indexing(self):
        if not self.folder_paths:
            messagebox.showwarning("No Folders", "Please add at least one folder to scan.")
            return
        if not self.output_file_path:
            messagebox.showwarning("No Output File", "Please set the output file location first.")
            return
        
        self.toggle_controls(tk.DISABLED)
        self.progress.start(10)
        create_index(self.folder_paths, self.output_file_path, self.update_status, self.progress_step)
        self.progress.stop()
        self.toggle_controls(tk.NORMAL)

    def toggle_controls(self, state):
        for child in self.winfo_children():
            if isinstance(child, Frame):
                for widget in child.winfo_children():
                    if isinstance(widget, Button):
                        widget.config(state=state)

    def save_profile(self):
        if not self.output_file_path:
            messagebox.showwarning("Cannot Save", "Please set an output file path before saving a profile.")
            return
        config = {
            'folders': self.folder_paths,
            'output_file': self.output_file_path
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            self.update_status("Profile saved successfully.")
        except IOError as e:
            messagebox.showerror("Error", f"Could not save profile: {e}")

    def load_profile(self, silent=False):
        if not os.path.exists(CONFIG_FILE):
            if not silent: messagebox.showinfo("Info", "No profile file found.")
            return
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            self.folder_paths = config.get('folders', [])
            self.output_file_path = config.get('output_file', '')
            self.update_listbox()
            if self.output_file_path:
                self.output_label.config(text=f"Save to: {self.output_file_path}", fg="black")
            if not silent: self.update_status("Profile loaded successfully.")
        except (IOError, json.JSONDecodeError) as e:
            if not silent: messagebox.showerror("Error", f"Could not load profile: {e}")

if __name__ == '__main__':
    app = App()
    app.mainloop()
