import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox
import pandas as pd
import fitz  # PyMuPDF

def extract_info_from_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text("text")
        
        # Extract Initials and Date
        initials_match = re.search(r"Initials:\s*([A-Z0-9]+)", text)
        date_match = re.search(r"Date:\s*([\d/]+)", text)
        
        initials = initials_match.group(1) if initials_match else ""
        date = date_match.group(1) if date_match else ""
        
        # Extract Well API
        api_match = re.search(r"API\s*:\s*(\d+)", text)
        api = api_match.group(1) if api_match else ""
        
        # Extract Well Name and Wellbore Code
        well_match = re.search(r"Well:\s*([A-Z0-9\-]+)", text)
        wellbore_match = re.search(r"Wellbore:\s*(\d+)", text)
        
        well_name = well_match.group(1) if well_match else ""
        wellbore = wellbore_match.group(1) if wellbore_match else ""
        
        well_api = f"{api}-{wellbore}" if api and wellbore else api
        
        return {
            "File": os.path.basename(pdf_path),
            "Initials": initials,
            "Date": date,
            "Well API": well_api
        }
    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")
        return None

def select_folders_and_process():
    folders = filedialog.askdirectory(mustexist=True, title="Select a folder with PDFs")
    if not folders:
        return
    
    data = []
    for root, _, files in os.walk(folders):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_path = os.path.join(root, file)
                info = extract_info_from_pdf(pdf_path)
                if info:
                    data.append(info)
    
    if not data:
        messagebox.showwarning("No Data", "No PDF data found.")
        return
    
    df = pd.DataFrame(data)
    display_results(df)

def display_results(df):
    result_window = tk.Toplevel()
    result_window.title("Extracted Data")
    
    # Table display
    text = tk.Text(result_window, wrap="none", width=120, height=30)
    text.insert("1.0", df.to_string(index=False))
    text.pack()
    
    def copy_to_clipboard():
        result_window.clipboard_clear()
        result_window.clipboard_append(df.to_csv(index=False, sep="\t"))
        messagebox.showinfo("Copied", "Data copied to clipboard.")
    
    def export_to_csv():
        save_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if save_path:
            df.to_csv(save_path, index=False)
            messagebox.showinfo("Exported", f"Data exported to {save_path}")
    
    copy_button = tk.Button(result_window, text="Copy to Clipboard", command=copy_to_clipboard)
    copy_button.pack(pady=5)
    
    export_button = tk.Button(result_window, text="Export to CSV", command=export_to_csv)
    export_button.pack(pady=5)

# Main window
root = tk.Tk()
root.title("PDF Well Data Extractor")
root.geometry("400x200")

label = tk.Label(root, text="Select folder(s) containing PDF files to process")
label.pack(pady=20)

select_button = tk.Button(root, text="Select Folder", command=select_folders_and_process)
select_button.pack(pady=10)

root.mainloop()
