# -*- coding: utf-8 -*-
"""
Combined PDF Extractor + Abandonment Checker

- Step 1: Select a folder, scan PDFs, extract API/Wellbore/Initials/DiagramDate
- Step 2: Query Oracle for Well Status + Abandonment Effective Date
- Compare AbandonmentDate > DiagramDate, highlight those rows

Requirements:
    pip install PyMuPDF pandas cx_Oracle

Notes:
- Oracle DSN/credentials are read from env vars if present; defaults provided.
- Date parsing for diagram date is flexible; common formats attempted, otherwise pandas fallback.
"""

import os
import re
import csv
import threading
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd
import fitz  # PyMuPDF
import cx_Oracle


# ---------------------------
# Oracle connection manager
# ---------------------------
class OracleConnectionManager:
    """Manages Oracle database connections."""
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": os.getenv("DB_DSN_ODW", "odw"),
            }
        }

    def get_connection(self, name="odw"):
        if name not in self._connections:
            raise ValueError(f"Unknown DB connection name: {name}")
        cfg = self._connections[name]
        try:
            return cx_Oracle.connect(
                user=cfg["user"], password=cfg["password"], dsn=cfg["dsn"]
            )
        except cx_Oracle.Error as e:
            (err,) = e.args
            raise ConnectionError(f"Failed to connect to Oracle DB '{name}': {err.message}") from e


# ---------------------------
# Helpers
# ---------------------------
API_RE = re.compile(r"(?:API|ΑΡΙ)\s*:\s*(\d+)")
WELLBORE_RE = re.compile(r"Wellbore:\s*(\d{2})")
INITIALS_DATE_RE = re.compile(r"Initials:\s*(\S+)\s*Date:\s*([^\s]+)")
# Accept also "Date :" (with space) or other light variants
INITIALS_DATE_FALLBACK = re.compile(r"Initials\s*:\s*(\S+).*?Date\s*:?[\s]*([^\s]+)", re.IGNORECASE | re.DOTALL)

def parse_diagram_date(raw: str):
    """Try multiple formats; return pandas.Timestamp or NaT."""
    if not raw or not isinstance(raw, str):
        return pd.NaT
    raw = raw.strip()
    # Common patterns to try fast
    fmts = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%Y/%m/%d",
        "%m-%d-%Y",
        "%m-%d-%y",
    ]
    for f in fmts:
        try:
            return pd.Timestamp(datetime.strptime(raw, f))
        except Exception:
            pass
    # Fallback: let pandas try
    try:
        return pd.to_datetime(raw, errors="coerce", dayfirst=False, infer_datetime_format=True)
    except Exception:
        return pd.NaT


def extract_from_pdf(file_path):
    """Return dict with File Name, API, Wellbore, Full Well API, Initials, DiagramDateStr, DiagramDate (Timestamp)"""
    try:
        doc = fitz.open(file_path)
        full_text = []
        for page in doc:
            full_text.append(page.get_text())
        doc.close()
        text = "\n".join(full_text)

        api_match = API_RE.search(text)
        wellbore_match = WELLBORE_RE.search(text)
        id_match = INITIALS_DATE_RE.search(text) or INITIALS_DATE_FALLBACK.search(text)

        api_base = api_match.group(1) if api_match else ""
        wellbore = wellbore_match.group(1) if wellbore_match else ""
        initials = id_match.group(1) if id_match else ""
        date_str = id_match.group(2) if id_match else ""
        diagram_dt = parse_diagram_date(date_str)

        full_api = f"{api_base}{wellbore}" if (api_base and wellbore) else ""

        return {
            "File Name": os.path.basename(file_path),
            "API": api_base,
            "Wellbore": wellbore,
            "Full Well API": full_api,
            "Initials": initials,
            "DiagramDate": diagram_dt,          # pandas.Timestamp/NaT
            "DiagramDateStr": date_str,         # original text as found
        }
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return None


# ---------------------------
# Main GUI
# ---------------------------
class CombinedApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Diagram Extractor + Abandonment Checker")
        self.geometry("1100x700")

        self.conn_mgr = OracleConnectionManager()
        self.extracted_rows = []  # list of dicts from PDFs
        self.results_df = pd.DataFrame()

        # Controls
        controls = tk.Frame(self, padx=10, pady=10)
        controls.pack(fill=tk.X)

        self.select_btn = tk.Button(controls, text="Select Folder & Scan PDFs", command=self.start_scan_thread)
        self.select_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.run_db_btn = tk.Button(controls, text="Run Abandonment Check", command=self.start_db_thread, state=tk.DISABLED, bg="#007bff", fg="white")
        self.run_db_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.copy_btn = tk.Button(controls, text="Copy Table", command=self.copy_to_clipboard, state=tk.DISABLED)
        self.copy_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.export_btn = tk.Button(controls, text="Export CSV", command=self.export_csv, state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Treeview
        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

        columns = ("File Name","API","Wellbore","Full Well API","Initials","DiagramDate","WellStatus","AbandonmentDate")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=150)
        self.tree.column("File Name", width=260)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.pack(side="left", fill=tk.BOTH, expand=True)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")

        # Tag for highlight
        self.tree.tag_configure("highlight", background="goldenrod", foreground="black")

        # Status
        self.status = tk.StringVar(value="Ready. Scan a folder to begin.")
        tk.Label(self, textvariable=self.status, anchor="w", relief=tk.SUNKEN).pack(fill=tk.X, padx=10, pady=(0,10))

    def set_status(self, msg):
        self.status.set(msg)
        self.update_idletasks()

    # ---------- Step 1: Scan PDFs ----------
    def start_scan_thread(self):
        folder = filedialog.askdirectory(title="Select a folder of PDFs")
        if not folder:
            self.set_status("Folder selection cancelled.")
            return

        self.select_btn.configure(state=tk.DISABLED)
        self.run_db_btn.configure(state=tk.DISABLED)
        self.copy_btn.configure(state=tk.DISABLED)
        self.export_btn.configure(state=tk.DISABLED)
        self.clear_tree()

        t = threading.Thread(target=self.scan_folder, args=(folder,))
        t.daemon = True
        t.start()

    def scan_folder(self, folder):
        try:
            pdfs = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf")]
            total = len(pdfs)
            self.extracted_rows = []
            for i, path in enumerate(pdfs, 1):
                self.set_status(f"Scanning ({i}/{total}): {os.path.basename(path)}")
                data = extract_from_pdf(path)
                if data:
                    self.extracted_rows.append(data)
            self.set_status(f"Scan complete. Found data in {len(self.extracted_rows)} of {total} files.")
            self.populate_tree_from_extracts()
            if self.extracted_rows:
                self.run_db_btn.configure(state=tk.NORMAL)
                self.copy_btn.configure(state=tk.NORMAL)
                self.export_btn.configure(state=tk.NORMAL)
        except Exception as e:
            messagebox.showerror("Scan Error", str(e))
        finally:
            self.select_btn.configure(state=tk.NORMAL)

    def clear_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def populate_tree_from_extracts(self):
        self.clear_tree()
        # Create a base dataframe for later merge
        df = pd.DataFrame(self.extracted_rows)
        # Ensure DiagramDate is Timestamp
        if "DiagramDate" in df.columns:
            df["DiagramDate"] = pd.to_datetime(df["DiagramDate"], errors="coerce")

        # Normalize output columns now; WellStatus/AbandonmentDate blank until DB query
        df["WellStatus"] = ""
        df["AbandonmentDate"] = pd.NaT

        self.results_df = df
        for _, row in df.iterrows():
            self.tree.insert(
                "", "end",
                values=(
                    row.get("File Name",""),
                    row.get("API",""),
                    row.get("Wellbore",""),
                    row.get("Full Well API",""),
                    row.get("Initials",""),
                    row["DiagramDate"].strftime("%Y-%m-%d %H:%M:%S") if pd.notna(row.get("DiagramDate")) else row.get("DiagramDateStr",""),
                    "",
                    "",
                )
            )

    # ---------- Step 2: Oracle DB & Compare ----------
    def start_db_thread(self):
        if self.results_df.empty:
            messagebox.showinfo("No Data", "No extracted rows to check.")
            return
        self.run_db_btn.configure(state=tk.DISABLED)
        t = threading.Thread(target=self.query_and_compare)
        t.daemon = True
        t.start()

    def query_and_compare(self):
        try:
            # Unique 10-digit APIs
            apis = sorted(set(str(x) for x in self.results_df["API"] if pd.notna(x) and str(x).isdigit()))
            if not apis:
                messagebox.showinfo("No APIs", "No valid 10-digit API numbers were extracted.")
                return

            formatted = ", ".join([f"'{a}'" for a in apis])

            sql = f"""
                SELECT wd.well_api_nbr AS WELL_API_NBR,
                       cd.CMPL_STATE_TYPE_DESC AS CMPL_STATE_TYPE_DESC,
                       cd.CMPL_STATE_EFTV_DTTM AS CMPL_STATE_EFTV_DTTM
                FROM well_dmn wd
                JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
                WHERE cd.actv_indc = 'Y'
                  AND wd.actv_indc = 'Y'
                  AND cd.cmpl_state_type_cde NOT IN ('PRPO','FUTR')
                  AND wd.well_api_nbr IN ({formatted})
                  AND cd.CMPL_SEQ_NBR IS NOT NULL
            """

            self.set_status("Querying Oracle…")
            with self.conn_mgr.get_connection("odw") as conn:
                db_df = pd.read_sql(sql, conn)

            # Normalize & parse
            db_df["WELL_API_NBR"] = db_df["WELL_API_NBR"].astype(str)
            db_df["CMPL_STATE_EFTV_DTTM"] = pd.to_datetime(db_df["CMPL_STATE_EFTV_DTTM"], errors="coerce")

            # Prepare base with API key
            base = self.results_df.copy()
            base["WELL_API_NBR"] = base["API"].astype(str)

            merged = pd.merge(
                base,
                db_df.rename(columns={
                    "CMPL_STATE_TYPE_DESC": "WellStatus",
                    "CMPL_STATE_EFTV_DTTM": "AbandonmentDate"
                }),
                on="WELL_API_NBR",
                how="left"
            )

            # If there are multiple completion rows per API, keep the latest effective date per status row
            # (Commonly you'd group/select 'Permanently Abandoned' row; here we prefer latest per API)
            # Reduce by keeping the max AbandonmentDate per API+WellStatus row, then dedupe by API keeping the latest AbandonmentDate
            merged.sort_values(["WELL_API_NBR", "AbandonmentDate"], inplace=True)
            merged = merged.groupby(["WELL_API_NBR"], as_index=False).last()

            # Re-join with original (to keep file-level rows if there were multiple files per API)
            final = pd.merge(
                base.drop(columns=["WellStatus","AbandonmentDate"], errors="ignore"),
                merged[["WELL_API_NBR","WellStatus","AbandonmentDate"]],
                on="WELL_API_NBR",
                how="left"
            )

            # Highlight rule: AbandonmentDate AFTER DiagramDate
            # i.e., DiagramDate < AbandonmentDate
            self.results_df = final
            self.refresh_tree_with_results()
            self.set_status("Done. Rows highlighted where AbandonmentDate > DiagramDate.")
        except Exception as e:
            messagebox.showerror("Database Error", str(e))
        finally:
            self.run_db_btn.configure(state=tk.NORMAL)

    def refresh_tree_with_results(self):
        self.clear_tree()
        for _, row in self.results_df.iterrows():
            diagram = row.get("DiagramDate")
            abdn = row.get("AbandonmentDate")
            tag = ()
            if pd.notna(diagram) and pd.notna(abdn) and abdn > diagram:
                tag = ("highlight",)

            self.tree.insert(
                "", "end",
                values=(
                    row.get("File Name",""),
                    row.get("API",""),
                    row.get("Wellbore",""),
                    row.get("Full Well API",""),
                    row.get("Initials",""),
                    diagram.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(diagram) else row.get("DiagramDateStr",""),
                    row.get("WellStatus","") if pd.notna(row.get("WellStatus","")) else "",
                    abdn.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(abdn) else "",
                ),
                tags=tag
            )

        # Enable copy/export if we have anything
        if len(self.results_df) > 0:
            self.copy_btn.configure(state=tk.NORMAL)
            self.export_btn.configure(state=tk.NORMAL)

    # ---------- Utilities ----------
    def copy_to_clipboard(self):
        if self.tree.get_children() == ():
            messagebox.showinfo("No Data", "Nothing to copy.")
            return
        headers = self.tree["columns"]
        rows = ["\t".join(headers)]
        for iid in self.tree.get_children():
            vals = self.tree.item(iid)["values"]
            rows.append("\t".join(str(v) for v in vals))
        data = "\n".join(rows)
        self.clipboard_clear()
        self.clipboard_append(data)
        self.set_status("Table copied to clipboard.")

    def export_csv(self):
        if self.results_df.empty:
            messagebox.showinfo("No Data", "Nothing to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Results As",
            defaultextension=".csv",
            filetypes=[("CSV files","*.csv"),("All files","*.*")]
        )
        if not path:
            return

        out = self.results_df.copy()
        # Pretty date strings
        if "DiagramDate" in out.columns:
            out["DiagramDate"] = out["DiagramDate"].dt.strftime("%Y-%m-%d %H:%M:%S").fillna(out.get("DiagramDateStr",""))
        if "AbandonmentDate" in out.columns:
            out["AbandonmentDate"] = out["AbandonmentDate"].dt.strftime("%Y-%m-%d %H:%M:%S")

        # Reorder columns nicely if present
        cols = ["File Name","API","Wellbore","Full Well API","Initials","DiagramDate","WellStatus","AbandonmentDate"]
        cols = [c for c in cols if c in out.columns] + [c for c in out.columns if c not in cols]
        try:
            out.to_csv(path, index=False, columns=cols, encoding="utf-8")
            self.set_status(f"Exported to {os.path.basename(path)}")
            messagebox.showinfo("Success", f"Results exported to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))


if __name__ == "__main__":
    app = CombinedApp()
    app.mainloop()
