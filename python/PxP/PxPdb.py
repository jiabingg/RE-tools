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
        self.root.geometry("1100x650")
        
        self.data = self.load_and_initialize_data()
        # We store the order in a list to allow moving items up/down
        self.section_order = list(self.data.keys())
        
        self.current_section = None
        self.current_project = None
        
        self.setup_ui()
        self.refresh_sections()

    def load_and_initialize_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as file:
                    return json.load(file)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load JSON: {e}")
        
        if os.path.exists(CSV_FILE):
            return self.build_from_csv()
        return {}

    def build_from_csv(self):
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
                        "projects": {"Dummy Project": f"This is a dummy response for {current_sec}."}
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
        # We reconstruct the dictionary based on the current section_order
        ordered_data = {key: self.data[key] for key in self.section_order if key in self.data}
        try:
            with open(DATA_FILE, "w") as file:
                json.dump(ordered_data, file, indent=4)
            self.data = ordered_data # Update internal state
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")

    def setup_ui(self):
        paned_window = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Frame 1: Sections ---
        section_frame = ttk.LabelFrame(paned_window, text="1. Sections")
        paned_window.add(section_frame, weight=1)
        
        self.section_listbox = tk.Listbox(section_frame, font=("Arial", 10), exportselection=False)
        self.section_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.section_listbox.bind('<<ListboxSelect>>', self.on_section_select)

        # Movement Buttons for Sections
        move_btn_frame = ttk.Frame(section_frame)
        move_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(move_btn_frame, text="Move Up ↑", command=lambda: self.move_section(-1)).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(move_btn_frame, text="Move Down ↓", command=lambda: self.move_section(1)).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # --- Frame 2: Projects ---
        project_frame = ttk.LabelFrame(paned_window, text="2. Projects")
        paned_window.add(project_frame, weight=1)
        self.project_listbox = tk.Listbox(project_frame, exportselection=False)
        self.project_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.project_listbox.bind('<<ListboxSelect>>', self.on_project_select)
        
        proj_btn_frame = ttk.Frame(project_frame)
        proj_btn_frame.pack(fill=tk.X)
        ttk.Button(proj_btn_frame, text="+ Add", command=self.add_project).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(proj_btn_frame, text="- Del", command=self.remove_project).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        # --- Frame 3: Details ---
        response_frame = ttk.LabelFrame(paned_window, text="3. Response Details")
        paned_window.add(response_frame, weight=2)
        
        ttk.Label(response_frame, text="Checklist Item:").pack(anchor=tk.W, padx=5)
        self.txt_description = tk.Text(response_frame, height=8, wrap=tk.WORD, bg="#f9f9f9", state=tk.DISABLED, font=("Arial", 9))
        self.txt_description.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(response_frame, text="Your Response:").pack(anchor=tk.W, padx=5)
        self.txt_response = tk.Text(response_frame, wrap=tk.WORD, undo=True)
        self.txt_response.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        ttk.Button(response_frame, text="Save Response", command=self.save_response).pack(fill=tk.X, padx=5, pady=5)

    def refresh_sections(self, select_index=None):
        self.section_listbox.delete(0, tk.END)
        for section in self.section_order:
            self.section_listbox.insert(tk.END, section)
        if select_index is not None:
            self.section_listbox.selection_set(select_index)
            self.section_listbox.see(select_index)

    def move_section(self, direction):
        """Moves the selected section key within the list and saves."""
        selection = self.section_listbox.curselection()
        if not selection:
            return
        
        idx = selection[0]
        new_idx = idx + direction
        
        if 0 <= new_idx < len(self.section_order):
            # Swap in the order list
            self.section_order[idx], self.section_order[new_idx] = self.section_order[new_idx], self.section_order[idx]
            self.save_data()
            self.refresh_sections(select_index=new_idx)

    def on_section_select(self, event):
        selection = self.section_listbox.curselection()
        if selection:
            self.current_section = self.section_order[selection[0]]
            desc = self.data[self.current_section].get("description", "N/A")
            self.txt_description.config(state=tk.NORMAL)
            self.txt_description.delete(1.0, tk.END); self.txt_description.insert(tk.END, desc)
            self.txt_description.config(state=tk.DISABLED)
            self.current_project = None
            self.refresh_projects()

    def refresh_projects(self):
        self.project_listbox.delete(0, tk.END)
        self.txt_response.delete(1.0, tk.END)
        if self.current_section:
            for proj in self.data[self.current_section].get("projects", {}).keys():
                self.project_listbox.insert(tk.END, proj)

    def on_project_select(self, event):
        selection = self.project_listbox.curselection()
        if selection:
            self.current_project = self.project_listbox.get(selection[0])
            resp = self.data[self.current_section]["projects"].get(self.current_project, "")
            self.txt_response.delete(1.0, tk.END); self.txt_response.insert(tk.END, resp)

    def add_project(self):
        if not self.current_section: return
        name = simpledialog.askstring("Project", "Project Name:")
        if name:
            self.data[self.current_section]["projects"][name] = ""
            self.save_data(); self.refresh_projects()

    def remove_project(self):
        if self.current_project and messagebox.askyesno("Confirm", "Delete project?"):
            del self.data[self.current_section]["projects"][self.current_project]
            self.save_data(); self.current_project = None; self.refresh_projects()

    def save_response(self):
        if not self.current_project: return
        self.data[self.current_section]["projects"][self.current_project] = self.txt_response.get(1.0, tk.END).strip()
        self.save_data(); messagebox.showinfo("Success", "Saved.")

if __name__ == "__main__":
    root = tk.Tk(); app = ProjectReviewApp(root); root.mainloop()