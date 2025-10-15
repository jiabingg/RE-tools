# PDF Data Extractor
# This script provides a graphical user interface (GUI) to select folders,
# scan PDF files within those folders, extract specific data (API, Wellbore, Initials, Date),
# and display it in a table. It also allows copying the data to the clipboard
# or exporting it as a CSV file.
#
# Required library: PyMuPDF
# You can install it by running this command in your terminal or command prompt:
# pip install PyMuPDF

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import re
import fitz  # PyMuPDF
import csv
import threading

class PDFExtractorApp:
    def __init__(self, root):
        """Initializes the application's GUI."""
        self.root = root
        self.root.title("PDF Data Extractor")
        self.root.geometry("950x600")

        # --- Main Frame ---
        main_frame = tk.Frame(root, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Top Controls Frame ---
        controls_frame = tk.Frame(main_frame)
        controls_frame.pack(fill=tk.X, pady=(0, 10))

        self.select_button = tk.Button(controls_frame, text="Select Folders", command=self.start_processing_thread)
        self.select_button.pack(side=tk.LEFT, padx=(0, 10))

        self.copy_button = tk.Button(controls_frame, text="Copy to Clipboard", command=self.copy_to_clipboard)
        self.copy_button.pack(side=tk.LEFT, padx=(0, 10))

        self.export_button = tk.Button(controls_frame, text="Export to CSV", command=self.export_to_csv)
        self.export_button.pack(side=tk.LEFT)
        
        # --- Treeview for Data Display ---
        tree_frame = tk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("File Name", "API", "Wellbore", "Full Well API", "Initials", "Date"), show="headings")
        self.tree.heading("File Name", text="File Name")
        self.tree.heading("API", text="API")
        self.tree.heading("Wellbore", text="Wellbore")
        self.tree.heading("Full Well API", text="Full Well API")
        self.tree.heading("Initials", text="Initials")
        self.tree.heading("Date", text="Date")

        self.tree.column("File Name", width=250)
        self.tree.column("API", width=120)
        self.tree.column("Wellbore", width=80)
        self.tree.column("Full Well API", width=150)
        self.tree.column("Initials", width=120)
        self.tree.column("Date", width=100)
        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vsb.pack(side='right', fill='y')
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        hsb.pack(side='bottom', fill='x')
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.pack(fill=tk.BOTH, expand=True)

        # --- Status Bar ---
        self.status_label = tk.Label(main_frame, text="Ready. Select folders to begin.", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.extracted_data = []

    def start_processing_thread(self):
        """Starts the PDF processing in a separate thread to keep the GUI responsive."""
        folder_paths = filedialog.askdirectory(
            title="Select a Folder",
            mustexist=True
        )
        if not folder_paths:
            self.update_status("Folder selection cancelled.")
            return
            
        folders_to_process = [folder_paths]

        # Disable buttons during processing
        self.select_button.config(state=tk.DISABLED)
        self.copy_button.config(state=tk.DISABLED)
        self.export_button.config(state=tk.DISABLED)
        
        # Clear previous results
        self.clear_treeview()
        self.extracted_data = []

        # Run the processing in a new thread
        process_thread = threading.Thread(target=self.process_folders, args=(folders_to_process,))
        process_thread.start()

    def process_folders(self, folder_paths):
        """Iterates through folders and processes PDF files."""
        total_files = 0
        processed_count = 0
        for folder_path in folder_paths:
            try:
                pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".pdf")]
                total_files += len(pdf_files)

                for filename in pdf_files:
                    file_path = os.path.join(folder_path, filename)
                    self.update_status(f"Processing: {filename}")
                    data = self.extract_data_from_pdf(file_path)
                    if data:
                        self.extracted_data.append(data)
                        self.root.after(0, self.add_to_treeview, data)
                    processed_count += 1
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to process folder {folder_path}:\n{e}"))

        self.update_status(f"Processing complete. Found data in {len(self.extracted_data)} of {total_files} files.")
        
        # Re-enable buttons on the main thread
        self.root.after(0, self.enable_buttons)

    def extract_data_from_pdf(self, file_path):
        """Extracts text and finds required data from a single PDF file."""
        try:
            doc = fitz.open(file_path)
            full_text = ""
            for page in doc:
                full_text += page.get_text()
            doc.close()

            # Regex patterns
            initials_date_pattern = re.compile(r"Initials:\s*(\S+)\s*Date:\s*(\S+)")
            api_pattern = re.compile(r"(?:API|ΑΡΙ)\s*:\s*(\d+)")
            wellbore_pattern = re.compile(r"Wellbore:\s*(\d{2})")

            # Find matches
            initials_date_match = initials_date_pattern.search(full_text)
            api_match = api_pattern.search(full_text)
            wellbore_match = wellbore_pattern.search(full_text)

            initials = initials_date_match.group(1) if initials_date_match else "Not Found"
            date = initials_date_match.group(2) if initials_date_match else "Not Found"
            
            api_base = api_match.group(1) if api_match else "Not Found"
            wellbore_code = wellbore_match.group(1) if wellbore_match else "Not Found"
            
            full_api = "Not Found"
            if api_base != "Not Found" and wellbore_code != "Not Found":
                 full_api = api_base + wellbore_code

            return {
                "File Name": os.path.basename(file_path),
                "API": api_base,
                "Wellbore": wellbore_code,
                "Full Well API": full_api,
                "Initials": initials,
                "Date": date,
            }

        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            return None
            
    def add_to_treeview(self, data):
        """Adds a row of data to the GUI table."""
        if data:
            self.tree.insert("", "end", values=(
                data["File Name"],
                data["API"],
                data["Wellbore"],
                data["Full Well API"],
                data["Initials"],
                data["Date"]
            ))

    def clear_treeview(self):
        """Clears all entries from the GUI table."""
        for item in self.tree.get_children():
            self.tree.delete(item)

    def update_status(self, message):
        """Updates the text in the status bar."""
        self.root.after(0, lambda: self.status_label.config(text=message))
        
    def enable_buttons(self):
        """Re-enables the control buttons after processing is complete."""
        self.select_button.config(state=tk.NORMAL)
        self.copy_button.config(state=tk.NORMAL)
        self.export_button.config(state=tk.NORMAL)

    def copy_to_clipboard(self):
        """Copies the table data to the system clipboard."""
        if not self.extracted_data:
            messagebox.showinfo("Info", "No data to copy.")
            return

        # Prepare data with headers
        header = "\t".join(self.tree["columns"])
        data_rows = [header]
        for item in self.tree.get_children():
            row_values = self.tree.item(item)['values']
            data_rows.append("\t".join(map(str, row_values)))
        
        clipboard_data = "\n".join(data_rows)

        self.root.clipboard_clear()
        self.root.clipboard_append(clipboard_data)
        self.update_status("Data copied to clipboard!")

    def export_to_csv(self):
        """Exports the table data to a CSV file."""
        if not self.extracted_data:
            messagebox.showinfo("Info", "No data to export.")
            return
            
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save data as CSV"
        )

        if not file_path:
            self.update_status("Export cancelled.")
            return

        try:
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                # Use the column display names as headers in the CSV
                headers = self.tree["columns"]
                writer = csv.writer(csvfile)
                writer.writerow(headers)
                # Write the data rows
                for item in self.tree.get_children():
                    writer.writerow(self.tree.item(item)['values'])

            self.update_status(f"Data successfully exported to {os.path.basename(file_path)}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export data:\n{e}")
            self.update_status("Export failed.")


if __name__ == "__main__":
    root = tk.Tk()
    app = PDFExtractorApp(root)
    root.mainloop()

