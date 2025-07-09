import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import pandas as pd # Recommended for easy table handling and clipboard copy
import io

class WellDataApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Well Data Application")
        self.geometry("800x600")

        self.frames = {}
        self.current_frame = None

        # Shared data between pages
        self.well_apis = []
        self.engineering_strings = []
        self.query_results_df = None # To store pandas DataFrame of results

        self.create_frames()
        self.create_navigation_buttons()
        self.show_frame("Page1")

    def create_frames(self):
        # Page 1: Well API Input
        self.frames["Page1"] = Page1(self, self)
        self.frames["Page1"].grid(row=0, column=0, sticky="nsew")

        # Page 2: Engineering String and Results
        self.frames["Page2"] = Page2(self, self)
        self.frames["Page2"].grid(row=0, column=0, sticky="nsew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def create_navigation_buttons(self):
        nav_frame = ttk.Frame(self)
        nav_frame.grid(row=1, column=0, sticky="ew", pady=10)
        nav_frame.grid_columnconfigure(0, weight=1)
        nav_frame.grid_columnconfigure(1, weight=1)

        self.back_button = ttk.Button(nav_frame, text="Back", command=self.go_back, state=tk.DISABLED)
        self.back_button.grid(row=0, column=0, padx=5)

        self.next_button = ttk.Button(nav_frame, text="Next", command=self.go_next)
        self.next_button.grid(row=0, column=1, padx=5)

    def show_frame(self, page_name):
        frame = self.frames[page_name]
        frame.tkraise()
        self.current_frame = page_name
        self.update_navigation_buttons()

    def update_navigation_buttons(self):
        if self.current_frame == "Page1":
            self.back_button.config(state=tk.DISABLED)
            self.next_button.config(state=tk.NORMAL)
        elif self.current_frame == "Page2":
            self.back_button.config(state=tk.NORMAL)
            # You might disable next if there are no more pages, or keep it enabled
            self.next_button.config(state=tk.DISABLED) # No further pages in this example

    def go_next(self):
        if self.current_frame == "Page1":
            # Before moving, get data from Page1
            self.frames["Page1"].save_well_apis()
            self.show_frame("Page2")
        # Add more conditions for other pages if you have them

    def go_back(self):
        if self.current_frame == "Page2":
            self.show_frame("Page1")
        # Add more conditions for other pages if you have them

class Page1(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        self.label = ttk.Label(self, text="Enter Well APIs (one per line):")
        self.label.pack(pady=10)

        self.well_api_text = scrolledtext.ScrolledText(self, width=50, height=15)
        self.well_api_text.pack(pady=10)

    def save_well_apis(self):
        api_input = self.well_api_text.get("1.0", tk.END).strip()
        self.controller.well_apis = [api.strip() for api in api_input.split('\n') if api.strip()]
        if not self.controller.well_apis:
            messagebox.showwarning("Input Error", "Please enter at least one Well API.")
            return False
        return True

class Page2(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        # Engineering Strings Section
        eng_str_frame = ttk.LabelFrame(self, text="Engineering Strings")
        eng_str_frame.pack(pady=10, padx=10, fill="x")

        self.eng_str_entry = ttk.Entry(eng_str_frame, width=40)
        self.eng_str_entry.grid(row=0, column=0, padx=5, pady=5)

        self.add_button = ttk.Button(eng_str_frame, text="Add", command=self.add_engineering_string)
        self.add_button.grid(row=0, column=1, padx=5, pady=5)

        self.remove_button = ttk.Button(eng_str_frame, text="Remove Selected", command=self.remove_engineering_string)
        self.remove_button.grid(row=0, column=2, padx=5, pady=5)

        self.eng_str_listbox = tk.Listbox(eng_str_frame, height=5, width=50)
        self.eng_str_listbox.grid(row=1, column=0, columnspan=3, padx=5, pady=5)
        self.eng_str_listbox.bind("<Double-Button-1>", self.edit_selected_string) # Optional: double click to edit

        # Populate initial list if any
        self.populate_engineering_strings_listbox()

        # Pull Data Button
        self.pull_data_button = ttk.Button(self, text="Pull 'Top Perf' Data", command=self.pull_top_perf_data)
        self.pull_data_button.pack(pady=10)

        # Query Results Table
        self.results_frame = ttk.LabelFrame(self, text="Query Results")
        self.results_frame.pack(pady=10, padx=10, fill="both", expand=True)

        self.tree = ttk.Treeview(self.results_frame, show="headings")
        self.tree.pack(side="left", fill="both", expand=True)

        self.treescrollx = ttk.Scrollbar(self.results_frame, orient="horizontal", command=self.tree.xview)
        self.treescrollx.pack(side="bottom", fill="x")
        self.treescrolly = ttk.Scrollbar(self.results_frame, orient="vertical", command=self.tree.yview)
        self.treescrolly.pack(side="right", fill="y")
        self.tree.configure(xscrollcommand=self.treescrollx.set, yscrollcommand=self.treescrolly.set)

        # Copy to Clipboard Button
        self.copy_button = ttk.Button(self, text="Copy Results to Clipboard", command=self.copy_results_to_clipboard)
        self.copy_button.pack(pady=10)

    def populate_engineering_strings_listbox(self):
        self.eng_str_listbox.delete(0, tk.END)
        for s in self.controller.engineering_strings:
            self.eng_str_listbox.insert(tk.END, s)

    def add_engineering_string(self):
        new_string = self.eng_str_entry.get().strip()
        if new_string and new_string not in self.controller.engineering_strings:
            self.controller.engineering_strings.append(new_string)
            self.eng_str_listbox.insert(tk.END, new_string)
            self.eng_str_entry.delete(0, tk.END)
        elif new_string in self.controller.engineering_strings:
            messagebox.showinfo("Duplicate", "This engineering string already exists.")

    def remove_engineering_string(self):
        selected_indices = self.eng_str_listbox.curselection()
        if selected_indices:
            # Delete from end to beginning to avoid index issues
            for index in sorted(selected_indices, reverse=True):
                del self.controller.engineering_strings[index]
                self.eng_str_listbox.delete(index)
        else:
            messagebox.showwarning("No Selection", "Please select an engineering string to remove.")

    def edit_selected_string(self, event):
        selected_index = self.eng_str_listbox.curselection()
        if selected_index:
            current_text = self.eng_str_listbox.get(selected_index[0])
            self.eng_str_entry.delete(0, tk.END)
            self.eng_str_entry.insert(0, current_text)
            self.remove_engineering_string() # Remove the old one, user will add edited one

    def pull_top_perf_data(self):
        well_apis = self.controller.well_apis
        engineering_strings = self.controller.engineering_strings

        if not well_apis:
            messagebox.showwarning("Missing Data", "Please input Well APIs on the first page.")
            self.controller.show_frame("Page1")
            return
        if not engineering_strings:
            messagebox.showwarning("Missing Data", "Please add at least one Engineering String.")
            return

        # --- Placeholder for your SQL Query Logic ---
        # This is where you would connect to your database and execute your SQL script.
        # For demonstration, I'm creating dummy data.
        messagebox.showinfo("Querying Data", "Simulating data retrieval... (Replace with actual SQL query)")

        # Example SQL script structure (conceptual)
        # Assuming you're connecting to a database and executing a query that returns these columns.
        # import pyodbc # or psycopg2, sqlite3, etc.
        # conn = pyodbc.connect(...)
        # cursor = conn.cursor()
        # query = f"""
        #     SELECT
        #         w.WellName,
        #         w.API,
        #         es.EngineeringString,
        #         p.TopPerf AS TopPerf_ft,
        #         p.BottomPerf AS BottomPerf_ft
        #     FROM
        #         Wells w
        #     JOIN
        #         EngineeringStrings es ON w.EngStrID = es.ID
        #     JOIN
        #         Perforations p ON w.WellID = p.WellID
        #     WHERE
        #         w.API IN ({','.join(['?' for _ in well_apis])})
        #         AND es.EngineeringString IN ({','.join(['?' for _ in engineering_strings])})
        # """
        # cursor.execute(query, (*well_apis, *engineering_strings))
        # results = cursor.fetchall()
        # columns = [column[0] for column in cursor.description] # Get column names

        # Dummy Data for demonstration
        dummy_data = [
            {"Well Name": "Well A", "API": "123-456", "Engineering String": "String X", "Top Perf (ft)": 1000, "Bottom Perf (ft)": 1050},
            {"Well Name": "Well B", "API": "789-012", "Engineering String": "String Y", "Top Perf (ft)": 1200, "Bottom Perf (ft)": 1280},
            {"Well Name": "Well A", "API": "123-456", "Engineering String": "String Y", "Top Perf (ft)": 1100, "Bottom Perf (ft)": 1130},
            {"Well Name": "Well C", "API": "345-678", "Engineering String": "String X", "Top Perf (ft)": 900, "Bottom Perf (ft)": 950},
        ]
        # Filter dummy data based on user input
        filtered_data = []
        for row in dummy_data:
            if row["API"] in well_apis and row["Engineering String"] in engineering_strings:
                filtered_data.append(row)

        if not filtered_data:
            messagebox.showinfo("No Results", "No data found for the selected Well APIs and Engineering Strings.")
            self.controller.query_results_df = None
            self.display_results_in_table(pd.DataFrame()) # Clear table
            return

        self.controller.query_results_df = pd.DataFrame(filtered_data)
        self.display_results_in_table(self.controller.query_results_df)

    def display_results_in_table(self, df):
        # Clear existing treeview data
        for i in self.tree.get_children():
            self.tree.delete(i)

        if df.empty:
            self.tree["columns"] = ()
            return

        # Set columns
        self.tree["columns"] = list(df.columns)
        for col in df.columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, anchor="w", width=100) # Adjust width as needed

        # Insert data
        for index, row in df.iterrows():
            self.tree.insert("", "end", values=list(row.values))

    def copy_results_to_clipboard(self):
        if self.controller.query_results_df is not None and not self.controller.query_results_df.empty:
            # Use tab-separated values for easy pasting into Excel
            output = io.StringIO()
            self.controller.query_results_df.to_csv(output, sep='\t', index=False, header=True)
            self.clipboard_clear()
            self.clipboard_append(output.getvalue())
            messagebox.showinfo("Copied", "Results copied to clipboard (tab-separated).")
            output.close()
        else:
            messagebox.showwarning("No Data", "No results to copy to clipboard.")

if __name__ == "__main__":
    app = WellDataApp()
    app.mainloop()