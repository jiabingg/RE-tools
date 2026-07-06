#Help: Input 10-digit APIs and output Well information table in AOR format
"""
===========================
Input: 10-digit API numbers (one per line, or comma/space separated)
Output: Well information table matching CalGEM AOR format

Each API may have multiple completions (original + sidetracks).
Each completion gets its own row with the derived 12-digit UWI,
well name, translated well type and status.

Translations:
  well_type:  INJ+Steam -> "Steam Injector", INJ+Water -> "Water Injector",
              PROD -> "Oil Producer", OBSN -> "Observer"
  status:     ABND -> "Permanently Abandoned", OPNL -> "Operational",
              TA -> "Temporarily Abandoned"
"""

import os
import oracledb
import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd

# --- CONNECTION FIX: Enable Thick Mode ---
try:
    oracledb.init_oracle_client()
except Exception as e:
    print(f"Warning: Could not initialize Oracle thick client. {e}")
# -----------------------------------------


# ── Translation Maps ─────────────────────────────────────────────────────

STATUS_MAP = {
    "ABND": "Permanently Abandoned",
    "OPNL": "Operational",
    "TA":   "Temporarily Abandoned",
}

def translate_well_type(purpose_code, material):
    """Convert prim_purp_type_cde + prim_matl_desc to display well type."""
    if purpose_code == "PROD":
        return "Oil Producer"
    elif purpose_code == "OBSN":
        return "Observer"
    elif purpose_code == "INJ":
        if material and "steam" in str(material).lower():
            return "Steam Injector"
        elif material and "water" in str(material).lower():
            return "Water Injector"
        return "Injector"
    return purpose_code or ""

def translate_status(status_code):
    """Convert cmpl_state_type_cde to display status."""
    return STATUS_MAP.get(status_code, status_code or "")


# ── Oracle Connection ────────────────────────────────────────────────────

class OracleConnectionManager:
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"
            },
        }

    def get_connection(self, name="odw"):
        config = self._connections[name]
        try:
            return oracledb.connect(
                user=config['user'],
                password=config['password'],
                dsn=config['dsn']
            )
        except oracledb.Error as e:
            error_obj, = e.args
            raise ConnectionError(
                f"Failed to connect to Oracle DB '{name}': {error_obj.message}"
            ) from e


# ── Treeview Mixin ───────────────────────────────────────────────────────

class TreeviewMixin:
    """Provides display_results, clear_results, copy_to_clipboard."""

    def display_results(self, df):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df.empty:
            messagebox.showinfo("No Results", "No data found for the provided APIs.")
            self.result_tree["columns"] = []
            return

        columns = list(df.columns)
        self.result_tree["columns"] = columns
        self.result_tree["displaycolumns"] = columns

        try:
            treeview_font_name = ttk.Style().lookup("Treeview", "font")
            tree_font = tkinter.font.Font(font=treeview_font_name)
        except Exception:
            tree_font = tkinter.font.Font(family="TkDefaultFont", size=10)

        for col in columns:
            self.result_tree.heading(col, text=col, anchor="w")
            self.result_tree.column(col, width=tree_font.measure(col) + 30, stretch=False)

        for _, row in df.iterrows():
            vals = []
            for item in row:
                if isinstance(item, pd.Timestamp):
                    vals.append(item.strftime('%Y-%m-%d') if not pd.isna(item) else '')
                elif pd.isna(item):
                    vals.append('')
                else:
                    vals.append(item)
            self.result_tree.insert("", "end", values=vals)

            for i, item in enumerate(vals):
                col_width = tree_font.measure(str(item)) + 20
                current_col_id = columns[i]
                if self.result_tree.column(current_col_id, width=None) < col_width:
                    self.result_tree.column(current_col_id, width=col_width)

    def clear_results(self):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)
        self.result_tree["columns"] = []
        self.current_data = None

    def copy_to_clipboard(self):
        if self.current_data is not None and not self.current_data.empty:
            try:
                self.current_data.to_clipboard(excel=True, index=False, header=True)
                messagebox.showinfo("Copy Success",
                                    "Results copied to clipboard (Excel format).")
            except Exception as e:
                messagebox.showerror("Copy Error", f"Failed to copy: {e}")
        else:
            messagebox.showwarning("No Data", "No results to copy.")


# ── Helper: build treeview with scrollbars ───────────────────────────────

def build_treeview(parent):
    tree_frame = tb.Frame(parent)
    tree_scroll_y = tb.Scrollbar(tree_frame, orient="vertical")
    tree_scroll_y.pack(side="right", fill="y")
    tree_scroll_x = tb.Scrollbar(tree_frame, orient="horizontal")
    tree_scroll_x.pack(side="bottom", fill="x")

    result_tree = ttk.Treeview(
        tree_frame, show="headings",
        yscrollcommand=tree_scroll_y.set,
        xscrollcommand=tree_scroll_x.set
    )
    result_tree.pack(fill="both", expand=True)
    tree_scroll_y.config(command=result_tree.yview)
    tree_scroll_x.config(command=result_tree.xview)
    return tree_frame, result_tree


# ── API Parsing ──────────────────────────────────────────────────────────

def parse_apis(raw_text):
    """Parse raw text into a list of clean 10-digit API strings."""
    tokens = raw_text.replace(",", " ").replace(";", " ").split()
    apis = []
    seen = set()
    for tok in tokens:
        cleaned = ''.join(c for c in tok.strip() if c.isdigit())
        # Accept 10-digit APIs; also accept 12-digit (strip last 2)
        if len(cleaned) == 12:
            cleaned = cleaned[:10]
        if len(cleaned) == 10 and cleaned not in seen:
            apis.append(cleaned)
            seen.add(cleaned)
    return apis


def build_aor_sql(apis):
    """Build Oracle SQL for AOR well lookup from 10-digit APIs.

    For each API, pulls all active completions. Each completion is
    paired with exactly one wellbore (highest suffix) to derive the
    12-digit UWI. Returns Well Name (from wlbr_dmn) alongside
    Completion Name (from cmpl_dmn).
    """
    api_in = ", ".join(f"'{a}'" for a in apis)

    return f"""
WITH ranked AS (
    SELECT
        cd.well_api_nbr                                          AS API,
        cd.well_api_nbr || LPAD(wd.wlbr_api_suff_nbr, 2, '0')  AS UWI,
        wd.well_nme                                              AS WELL_NAME,
        cd.cmpl_nme                                              AS COMPLETION_NAME,
        cd.prim_purp_type_cde                                    AS WELL_TYPE_CODE,
        cd.prim_matl_desc                                        AS MATERIAL,
        cd.cmpl_state_type_cde                                   AS STATUS_CODE,
        cd.opnl_fld                                              AS FIELD,
        cd.in_svc_indc                                           AS IN_SERVICE,
        cd.cmpl_fac_id,
        ROW_NUMBER() OVER (
            PARTITION BY cd.cmpl_fac_id
            ORDER BY wd.wlbr_api_suff_nbr DESC
        ) AS rn
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE cd.well_api_nbr IN ({api_in})
      AND cd.actv_indc = 'Y'
)
SELECT API, UWI, WELL_NAME, COMPLETION_NAME,
       WELL_TYPE_CODE, MATERIAL, STATUS_CODE, FIELD, IN_SERVICE
FROM ranked
WHERE rn = 1
ORDER BY API, cmpl_fac_id
"""


def transform_aor_results(df):
    """Apply translations and format the DataFrame to match the AOR output table."""
    if df.empty:
        return df

    # Translate well type
    df["Well Type"] = df.apply(
        lambda r: translate_well_type(r.get("WELL_TYPE_CODE", ""),
                                       r.get("MATERIAL", "")),
        axis=1
    )

    # Translate status
    df["Well Status"] = df["STATUS_CODE"].apply(translate_status)

    # Add operator
    df["Operator"] = "Aera Energy LLC"

    # Select and rename to final output columns
    output = df[["UWI", "API", "WELL_NAME", "COMPLETION_NAME", "Well Type",
                 "Well Status", "FIELD", "MATERIAL", "Operator"]].copy()
    output.rename(columns={
        "WELL_NAME": "Well Name",
        "COMPLETION_NAME": "Completion Name",
        "FIELD": "Field",
        "MATERIAL": "Material",
    }, inplace=True)

    return output


# ═════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═════════════════════════════════════════════════════════════════════════

class AORWellLookupApp(tb.Window, TreeviewMixin):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("AOR Well Information Lookup")
        self.geometry("1400x800")

        self.conn_manager = OracleConnectionManager()
        self.current_data = None

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 11, "bold"))

        self._build_ui()

    def _build_ui(self):
        # ── Top Frame: Input + Controls ──────────────────────────────────
        top = tb.Frame(self)
        top.pack(fill="x", padx=15, pady=(15, 5))

        # Left: API input
        input_frame = tb.LabelFrame(top, text="Enter 10-Digit API Numbers")
        input_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        self.api_text = scrolledtext.ScrolledText(
            input_frame, wrap=tk.WORD, width=40, height=10,
            font=("Courier New", 10)
        )
        self.api_text.pack(fill="both", expand=True)

        # Pre-populate with example
        self.api_text.insert(tk.END, "0405322030\n0403059234")

        hint = tb.Label(
            input_frame,
            text="One API per line, or comma/space separated. "
                 "10-digit format (e.g. 0405322030). "
                 "12-digit UWIs also accepted (suffix stripped).",
            font=("Helvetica", 9), foreground="gray"
        )
        hint.pack(anchor="w", pady=(5, 0))

        # Right: Buttons
        btn_frame = tb.Frame(top, padding=(15, 0, 0, 0))
        btn_frame.pack(side="right", fill="y")

        tb.Button(
            btn_frame, text="🔍  Run Lookup",
            command=self.run_lookup, bootstyle="warning",
            width=22
        ).pack(pady=5)

        tb.Button(
            btn_frame, text="📋  Copy to Clipboard",
            command=self.copy_to_clipboard, bootstyle="secondary",
            width=22
        ).pack(pady=5)

        tb.Button(
            btn_frame, text="💾  Export to Excel",
            command=self.export_to_excel, bootstyle="success",
            width=22
        ).pack(pady=5)

        tb.Button(
            btn_frame, text="🗑  Clear All",
            command=self.clear_all, bootstyle="danger-outline",
            width=22
        ).pack(pady=5)

        # ── Status Bar ───────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready — enter API numbers and click Run Lookup")
        status_bar = tb.Label(
            self, textvariable=self.status_var,
            font=("Helvetica", 10), anchor="w", padding=(15, 5)
        )
        status_bar.pack(fill="x")

        # ── Results Table ────────────────────────────────────────────────
        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(fill="both", expand=True, padx=15, pady=(5, 15))

    # ── Core Logic ───────────────────────────────────────────────────────

    def run_lookup(self):
        raw = self.api_text.get("1.0", tk.END)
        apis = parse_apis(raw)

        if not apis:
            messagebox.showwarning(
                "Input Error",
                "No valid API numbers found.\n\n"
                "API format: 10-digit number\n"
                "Example: 0405322030\n\n"
                "12-digit UWIs are also accepted (last 2 digits stripped)."
            )
            return

        self.status_var.set(f"Querying {len(apis)} API(s)...")
        self.update_idletasks()

        sql = build_aor_sql(apis)

        try:
            conn = self.conn_manager.get_connection("odw")
            cursor = conn.cursor()
            cursor.execute(sql)

            if not cursor.description:
                messagebox.showinfo("No Results", "Query returned no data.")
                self.clear_results()
                self.status_var.set("No results.")
                cursor.close()
                conn.close()
                return

            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            df = pd.DataFrame(rows, columns=columns)
            cursor.close()
            conn.close()

            # Apply translations
            df_out = transform_aor_results(df)

            self.current_data = df_out
            self.display_results(df_out)
            self.status_var.set(
                f"Done — {len(df_out)} completion(s) found for "
                f"{len(apis)} API(s)"
            )

        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.status_var.set("Connection failed.")
            self.clear_results()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error",
                                 f"Oracle Error: {error_obj.message}")
            self.status_var.set("Query failed.")
            self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {e}")
            self.status_var.set("Error occurred.")
            self.clear_results()

    # ── Export ────────────────────────────────────────────────────────────

    def export_to_excel(self):
        if self.current_data is None or self.current_data.empty:
            messagebox.showwarning("No Data", "No results to export.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("CSV files", "*.csv")],
            title="Save AOR Well Data"
        )
        if not filepath:
            return

        try:
            if filepath.endswith(".csv"):
                self.current_data.to_csv(filepath, index=False)
            else:
                self.current_data.to_excel(filepath, index=False, sheet_name="AOR Wells")
            messagebox.showinfo("Export Success", f"Saved to:\n{filepath}")
            self.status_var.set(f"Exported {len(self.current_data)} rows to {filepath}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to save: {e}")

    # ── Clear ────────────────────────────────────────────────────────────

    def clear_all(self):
        self.api_text.delete("1.0", tk.END)
        self.clear_results()
        self.status_var.set("Cleared — ready for new input")


# ═════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = AORWellLookupApp()
    app.mainloop()