import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import json
import os
import pandas as pd

DATA_FILE = "project_responses.json"
CSV_FILE = "UIC Checklist.xlsx - UIC Checklist.csv"

class ProjectReviewApp:
    def __init__(self, root):
        self.root = root
        self.root.title("UIC Project Response Manager")
        self.root.geometry("1000x600")
        
        # Load or Initialize data
        self.data = self.load_and_initialize_data()
        self.current_section = None
        self.current_project = None
        
        self.setup_ui()
        self.refresh_sections()

    def load_and_initialize_data(self):
        """Loads data from JSON. If JSON doesn't exist, it builds it from CSV with dummy data."""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as file:
                    return json.load(file)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load JSON: {e}")
        
        # Build initial data from CSV if JSON is missing
        if os.path.exists(CSV_FILE):
            return self.build_from_csv()
            
        return {}

    def build_from_csv(self):
        """Processes CSV to create the initial JSON with dummy projects/responses."""
        try:
            df = pd.read_csv(CSV_FILE)
            new_data = {}
            current_sec = None
            
            for _, row in df.iterrows():
                sec_code = str(row['Section Code']).strip() if pd.notna(row['Section Code']) else ""
                item_text = str(row['Item']).strip() if pd.notna(row['Item']) else ""
                
                if sec_code:
                    current_sec = sec_code
                    new_data[current_sec] = {
                        "description": item_text,
                        "projects": {
                            "Dummy Project": f"This is a dummy response for {current_sec}."
                        }
                    }
                elif current_sec and item_text:
                    new_data[current_sec]["description"] += f"\n- {item_text}"
            
            with open(DATA_FILE, "w") as f:
                json.dump(new_data, f, indent=4)
            return new_data
        except Exception as e:
            messagebox.showerror("Import Error", f"Could not initialize from CSV: {e}")
            return {}

    def save_data(self):
        """Save data to the local JSON file."""
        try:
            with open(DATA_FILE, "w") as file:
                json.dump(self.data, file, indent=4)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save data: {e}")

    def setup_ui(self):
        """Set up the three-pane user interface."""
        paned_window = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Frame 1: Sections ---
        section_frame = ttk.LabelFrame(paned_window, text="1. Sections (from Spreadsheet)")
        paned_window.add(section_frame, weight=1)
        
        self.section_listbox = tk.Listbox(section_frame, font=("Arial", 10), exportselection=False)
        self.section_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.section_listbox.bind('<<ListboxSelect>>', self.on_section_select)

        # --- Frame 2: Projects ---
        project_frame = ttk.LabelFrame(paned_window, text="2. Projects")
        paned_window.add(project_frame, weight=1)
        
        self.project_listbox = tk.Listbox(project_frame, exportselection=False)
        self.project_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.project_listbox.bind('<<ListboxSelect>>', self.on_project_select)
        
        btn_frame = ttk.Frame(project_frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Add Project", command=self.add_project).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_frame, text="Remove", command=self.remove_project).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # --- Frame 3: Response ---
        response_frame = ttk.LabelFrame(paned_window, text="3. Response Details")
        paned_window.add(response_frame, weight=2)
        
        ttk.Label(response_frame, text="Requirement Description (from Spreadsheet):").pack(anchor=tk.W, padx=5)
        self.txt_description = tk.Text(response_frame, height=8, wrap=tk.WORD, bg="#f9f9f9", state=tk.DISABLED, font=("Arial", 9))
        self.txt_description.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(response_frame, text="Project Response:").pack(anchor=tk.W, padx=5)
        self.txt_response = tk.Text(response_frame, wrap=tk.WORD, undo=True)
        self.txt_response.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        ttk.Button(response_frame, text="Save Response", command=self.save_response).pack(fill=tk.X, padx=5, pady=5)

    def refresh_sections(self):
        self.section_listbox.delete(0, tk.END)
        for section in sorted(self.data.keys()):
            self.section_listbox.insert(tk.END, section)

    def refresh_projects(self):
        self.project_listbox.delete(0, tk.END)
        self.txt_response.delete(1.0, tk.END)
        if self.current_section:
            projects = self.data[self.current_section].get("projects", {})
            for proj in projects.keys():
                self.project_listbox.insert(tk.END, proj)

    def on_section_select(self, event):
        selection = self.section_listbox.curselection()
        if selection:
            self.current_section = self.section_listbox.get(selection[0])
            desc = self.data[self.current_section].get("description", "N/A")
            
            self.txt_description.config(state=tk.NORMAL)
            self.txt_description.delete(1.0, tk.END)
            self.txt_description.insert(tk.END, desc)
            self.txt_description.config(state=tk.DISABLED)
            
            self.current_project = None
            self.refresh_projects()

    def on_project_select(self, event):
        selection = self.project_listbox.curselection()
        if selection:
            self.current_project = self.project_listbox.get(selection[0])
            response_text = self.data[self.current_section]["projects"].get(self.current_project, "")
            self.txt_response.delete(1.0, tk.END)
            self.txt_response.insert(tk.END, response_text)

    def add_project(self):
        if not self.current_section:
            messagebox.showinfo("Select Section", "Please select a section code first.")
            return
        proj_name = simpledialog.askstring("New Project", "Enter Project Name:")
        if proj_name:
            if proj_name not in self.data[self.current_section]["projects"]:
                self.data[self.current_section]["projects"][proj_name] = ""
                self.save_data()
                self.refresh_projects()

    def remove_project(self):
        if self.current_project:
            if messagebox.askyesno("Confirm", f"Delete project '{self.current_project}'?"):
                del self.data[self.current_section]["projects"][self.current_project]
                self.save_data()
                self.current_project = None
                self.refresh_projects()

    def save_response(self):
        if not self.current_project:
            messagebox.showinfo("Select Project", "Please select a project to save.")
            return
        new_text = self.txt_response.get(1.0, tk.END).strip()
        self.data[self.current_section]["projects"][self.current_project] = new_text
        self.save_data()
        messagebox.showinfo("Success", "Response saved successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = ProjectReviewApp(root)
    root.mainloop()