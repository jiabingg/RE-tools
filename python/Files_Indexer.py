import os
import json
import tkinter as tk
from tkinter import filedialog, messagebox, Listbox, Scrollbar, Frame, Button, Label

def create_index(folder_paths, output_file, status_callback):
    """
    Scans the provided list of folder paths and creates a JSON index file.
    This function is safe and only reads file and directory information.
    It uses a callback to update the GUI with its status.
    """
    file_index = []
    status_callback("Starting to scan folders...")
    
    for folder_path in folder_paths:
        status_callback(f"Scanning: {folder_path}...")
        
        if not os.path.exists(folder_path):
            status_callback(f"WARNING: Path not found, skipping: {folder_path}")
            continue
        
        # os.walk is a safe and efficient way to traverse all directories and files
        for root, _, files in os.walk(folder_path):
            for filename in files:
                try:
                    full_path = os.path.join(root, filename)
                    file_index.append({
                        'name': filename,
                        'path': full_path
                    })
                except Exception as e:
                    # This helps to skip files that might have encoding issues
                    print(f"Could not process a file in {root}. Error: {e}")
    
    status_callback(f"Scan complete. Found {len(file_index)} files.")
    
    try:
        # Writing the index to the specified output file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(file_index, f, indent=4)
        status_callback(f"Successfully created index file: '{os.path.basename(output_file)}'")
        messagebox.showinfo("Success", f"Indexing complete!\nFound {len(file_index)} files.\n\nSaved to: {output_file}")
    except IOError as e:
        status_callback(f"FATAL ERROR: Could not write to index file: {e}")
        messagebox.showerror("Error", f"Could not write to index file:\n{e}")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("File Indexer")
        self.geometry("600x450") # Increased height for new elements
        self.configure(bg="#f0f0f0")

        self.folder_paths = []
        self.output_file_path = None

        # --- UI Elements ---
        
        # Frame for folder buttons
        top_frame = Frame(self, bg="#f0f0f0")
        top_frame.pack(pady=(10, 5), padx=10, fill=tk.X)

        self.add_btn = Button(top_frame, text="Add Folder to Scan", command=self.add_folder)
        self.add_btn.pack(side=tk.LEFT, padx=5)

        self.remove_btn = Button(top_frame, text="Remove Selected", command=self.remove_folder)
        self.remove_btn.pack(side=tk.LEFT, padx=5)

        # Frame for the listbox and scrollbar
        list_frame = Frame(self)
        list_frame.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

        self.listbox = Listbox(list_frame, selectmode=tk.SINGLE)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)

        # Frame for output file selection
        output_frame = Frame(self, bg="#f0f0f0")
        output_frame.pack(pady=5, padx=10, fill=tk.X)

        self.set_output_btn = Button(output_frame, text="Set Output File", command=self.set_output_file)
        self.set_output_btn.pack(side=tk.LEFT, padx=5)

        self.output_label = Label(output_frame, text="Output file not set.", bg="#f0f0f0", fg="red")
        self.output_label.pack(side=tk.LEFT, padx=5)

        # Status label
        self.status_label = Label(self, text="Add folders and set an output file.", bd=1, relief=tk.SUNKEN, anchor=tk.W, bg="#dfdfdf")
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # Start button
        self.start_btn = Button(self, text="Start Indexing", command=self.start_indexing, bg="#28a745", fg="white", font=("Helvetica", 10, "bold"))
        self.start_btn.pack(pady=10, padx=10, fill=tk.X)

    def add_folder(self):
        """Opens a dialog to select a folder and adds it to the list."""
        folder_selected = filedialog.askdirectory()
        if folder_selected and folder_selected not in self.folder_paths:
            self.folder_paths.append(folder_selected)
            self.update_listbox()
            self.update_status(f"Added: {folder_selected}")

    def remove_folder(self):
        """Removes the selected folder from the list."""
        selected_indices = self.listbox.curselection()
        if not selected_indices:
            return
        
        selected_path = self.listbox.get(selected_indices[0])
        self.folder_paths.remove(selected_path)
        self.update_listbox()
        self.update_status(f"Removed: {selected_path}")

    def set_output_file(self):
        """Opens a save dialog to choose the output file path."""
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="index.json",
            title="Choose location to save index file"
        )
        if path:
            self.output_file_path = path
            self.output_label.config(text=f"Save to: {self.output_file_path}", fg="black")
            self.update_status(f"Output file set to: {self.output_file_path}")

    def update_listbox(self):
        """Refreshes the listbox with the current folder paths."""
        self.listbox.delete(0, tk.END)
        for path in self.folder_paths:
            self.listbox.insert(tk.END, path)

    def update_status(self, message):
        """Updates the status bar text."""
        self.status_label.config(text=message)
        self.update_idletasks() # Force GUI update

    def start_indexing(self):
        """Starts the indexing process."""
        if not self.folder_paths:
            messagebox.showwarning("No Folders", "Please add at least one folder to scan.")
            return

        if not self.output_file_path:
            messagebox.showwarning("No Output File", "Please set the output file location first.")
            return

        self.add_btn.config(state=tk.DISABLED)
        self.remove_btn.config(state=tk.DISABLED)
        self.set_output_btn.config(state=tk.DISABLED)
        self.start_btn.config(state=tk.DISABLED)
        
        # The actual indexing is done here, passing the output path
        create_index(self.folder_paths, self.output_file_path, self.update_status)
        
        self.add_btn.config(state=tk.NORMAL)
        self.remove_btn.config(state=tk.NORMAL)
        self.set_output_btn.config(state=tk.NORMAL)
        self.start_btn.config(state=tk.NORMAL)

if __name__ == '__main__':
    app = App()
    app.mainloop()
