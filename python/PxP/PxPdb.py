import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE = os.path.join(BASE_DIR, "project_data.json")
SECTIONS_LIBRARY_FILE = os.path.join(BASE_DIR, "section_details.json")

class ProjectReviewApp:
    def __init__(self, root):
        self.root = root
        self.root.title("UIC Project Manager - Injector Tracking")
        self.root.geometry("1440x1020")
        
        self.sections_library = self.load_json(SECTIONS_LIBRARY_FILE)
        self.projects_data = self.load_json(PROJECTS_FILE)
        self.project_order = list(self.projects_data.keys())
        self.section_order = sorted(list(self.sections_library.keys()))
        
        self.current_project = None
        self.current_section = None
        self.save_timer = None 
        
        self.setup_ui()
        self.refresh_projects()
        self.refresh_sections()

    def load_json(self, filepath):
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    return json.load(f)
            except: return {}
        return {}

    def save_all(self):
        ordered_projects = {k: self.projects_data[k] for k in self.project_order if k in self.projects_data}
        with open(PROJECTS_FILE, "w") as f:
            json.dump(ordered_projects, f, indent=4)

    def setup_ui(self):
        self.main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Pane 1: Projects & Injectors ---
        proj_column = ttk.Frame(self.main_paned)
        self.main_paned.add(proj_column, weight=1)

        # Projects List
        proj_frame = ttk.LabelFrame(proj_column, text="1. Projects")
        proj_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.proj_listbox = tk.Listbox(proj_frame, font=("Arial", 11), exportselection=False)
        self.proj_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.proj_listbox.bind('<<ListboxSelect>>', self.on_project_select)

        # PxP Link & Injector Table
        meta_frame = ttk.Frame(proj_frame)
        meta_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(meta_frame, text="PxP App Link:").pack(anchor=tk.W)
        self.pxp_link_var = tk.StringVar()
        ttk.Entry(meta_frame, textvariable=self.pxp_link_var).pack(fill=tk.X, pady=2)
        self.pxp_link_var.trace("w", lambda n, i, m: self.save_pxp_metadata())

        # Injectors Table
        inj_frame = ttk.LabelFrame(proj_column, text="Project Injectors")
        inj_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        cols = ("Well_Name", "API", "BH_X", "BH_Y")
        self.inj_tree = ttk.Treeview(inj_frame, columns=cols, show='headings', height=5)
        for col in cols:
            self.inj_tree.heading(col, text=col)
            self.inj_tree.column(col, width=80)
        self.inj_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        inj_btn_frame = ttk.Frame(inj_frame)
        inj_btn_frame.pack(fill=tk.X, pady=2)
        ttk.Button(inj_btn_frame, text="+ Add Injector", command=self.add_injector).pack(side=tk.LEFT, expand=True)
        ttk.Button(inj_btn_frame, text="- Remove", command=self.remove_injector).pack(side=tk.LEFT, expand=True)

        # Project Controls
        btn_proj_ctrl = ttk.Frame(proj_frame)
        btn_proj_ctrl.pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(btn_proj_ctrl, text="Add Proj", command=self.add_project).pack(side=tk.LEFT, expand=True)
        ttk.Button(btn_proj_ctrl, text="Rename", command=self.rename_project).pack(side=tk.LEFT, expand=True)
        ttk.Button(btn_proj_ctrl, text="Del Proj", command=self.remove_project).pack(side=tk.LEFT, expand=True)

        # --- Pane 2: Sections ---
        sec_frame = ttk.LabelFrame(self.main_paned, text="2. Checklist Sections")
        self.main_paned.add(sec_frame, weight=1)
        
        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda n, i, m: self.refresh_sections())
        ttk.Entry(sec_frame, textvariable=self.search_var, font=("Arial", 11)).pack(fill=tk.X, padx=5, pady=5)
        
        self.sec_listbox = tk.Listbox(sec_frame, font=("Arial", 11), exportselection=False)
        self.sec_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.sec_listbox.bind('<<ListboxSelect>>', self.on_section_select)

        # --- Pane 3: Content ---
        self.content_paned = ttk.PanedWindow(self.main_paned, orient=tk.VERTICAL)
        self.main_paned.add(self.content_paned, weight=3)
        
        desc_frame = ttk.LabelFrame(self.content_paned, text="Requirement Description")
        self.content_paned.add(desc_frame, weight=1)
        self.txt_desc = tk.Text(desc_frame, wrap=tk.WORD, bg="#f8f8f8", state=tk.DISABLED, font=("Arial", 11))
        self.txt_desc.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        resp_frame = ttk.LabelFrame(self.content_paned, text="Your Project Response")
        self.content_paned.add(resp_frame, weight=2)
        self.txt_resp = tk.Text(resp_frame, wrap=tk.WORD, undo=True, font=("Arial", 12))
        self.txt_resp.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.txt_resp.bind("<KeyRelease>", self.reset_save_timer)
        self.txt_resp.bind("<FocusOut>", lambda e: self.immediate_save())

    def on_project_select(self, event):
        sel = self.proj_listbox.curselection()
        if sel:
            self.current_project = self.project_order[sel[0]]
            proj_data = self.projects_data[self.current_project]
            
            # Load Meta & Injectors
            self.pxp_link_var.set(proj_data.get("pxp_application_link", ""))
            self.refresh_injector_table()
            
            self.current_section = None
            self.txt_resp.delete(1.0, tk.END)
            self.sec_listbox.selection_clear(0, tk.END)

    def refresh_injector_table(self):
        for item in self.inj_tree.get_children(): self.inj_tree.delete(item)
        injectors = self.projects_data[self.current_project].get("injectors", [])
        for inj in injectors:
            self.inj_tree.insert('', tk.END, values=(inj.get("Well_Name",""), inj.get("API",""), inj.get("BH_X",""), inj.get("BH_Y","")))

    def add_injector(self):
        if not self.current_project: return
        # Simple dialog-based entry for speed
        raw = simpledialog.askstring("New Injector", "Enter Well Name, API, BH_X, BH_Y (comma separated):")
        if raw:
            parts = [p.strip() for p in raw.split(',')]
            if len(parts) == 4:
                new_inj = {"Well_Name": parts[0], "API": parts[1], "BH_X": parts[2], "BH_Y": parts[3]}
                if "injectors" not in self.projects_data[self.current_project]:
                    self.projects_data[self.current_project]["injectors"] = []
                self.projects_data[self.current_project]["injectors"].append(new_inj)
                self.save_all(); self.refresh_injector_table()

    def remove_injector(self):
        selected = self.inj_tree.selection()
        if not selected or not self.current_project: return
        idx = self.inj_tree.index(selected[0])
        self.projects_data[self.current_project]["injectors"].pop(idx)
        self.save_all(); self.refresh_injector_table()

    def on_section_select(self, event):
        sel = self.sec_listbox.curselection()
        if sel and self.current_project:
            self.current_section = self.sec_listbox.get(sel[0])
            desc = self.sections_library.get(self.current_section, "")
            self.txt_desc.config(state=tk.NORMAL)
            self.txt_desc.delete(1.0, tk.END); self.txt_desc.insert(tk.END, desc)
            self.txt_desc.config(state=tk.DISABLED)
            
            # Compatibility Logic for Responses
            proj = self.projects_data[self.current_project]
            resp_dict = proj.get("responses", proj) # Fallback to old flat structure
            resp = resp_dict.get(self.current_section, "")
            self.txt_resp.delete(1.0, tk.END); self.txt_resp.insert(tk.END, resp)

    def save_pxp_metadata(self):
        if self.current_project:
            self.projects_data[self.current_project]["pxp_application_link"] = self.pxp_link_var.get()
            self.save_all()

    def refresh_sections(self):
        query = self.search_var.get().lower()
        self.sec_listbox.delete(0, tk.END)
        for code in self.section_order:
            if query in code.lower(): self.sec_listbox.insert(tk.END, code)

    def refresh_projects(self, index=None):
        """Updates the project listbox and optionally selects an item."""
        self.proj_listbox.delete(0, tk.END)
        for p in self.project_order:
            self.proj_listbox.insert(tk.END, p)
        if index is not None: 
            self.proj_listbox.selection_set(index)

    def add_project(self):
        name = simpledialog.askstring("New Project", "Project Name:")
        if name and name not in self.projects_data:
            self.projects_data[name] = {"pxp_application_link": "", "responses": {}, "injectors": []}
            self.project_order.append(name); self.save_all(); self.refresh_projects()

    def rename_project(self):
        if not self.current_project: return
        new_name = simpledialog.askstring("Rename", "New Name:")
        if new_name:
            self.projects_data[new_name] = self.projects_data.pop(self.current_project)
            self.project_order[self.project_order.index(self.current_project)] = new_name
            self.current_project = new_name; self.save_all(); self.refresh_projects()

    def remove_project(self):
        if self.current_project and messagebox.askyesno("Delete", "Delete project?"):
            del self.projects_data[self.current_project]
            self.project_order.remove(self.current_project)
            self.current_project = None; self.save_all(); self.refresh_projects()

    def reset_save_timer(self, event=None):
        if self.save_timer: self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(1000, self.immediate_save)

    def immediate_save(self):
        if self.current_project and self.current_section:
            if "responses" not in self.projects_data[self.current_project]:
                self.projects_data[self.current_project]["responses"] = {}
            self.projects_data[self.current_project]["responses"][self.current_section] = self.txt_resp.get(1.0, tk.END).strip()
            self.save_all()

if __name__ == "__main__":
    root = tk.Tk(); app = ProjectReviewApp(root); root.mainloop()