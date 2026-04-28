#Help: Periodic Project Review using ODW data
import os
import oracledb
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk
import tkinter.font
import ttkbootstrap as tb
import pandas as pd
from datetime import datetime, date, timedelta

# --- CONNECTION FIX: Enable Thick Mode ---
try:
    oracledb.init_oracle_client()
except Exception as e:
    print(f"Warning: Could not initialize Oracle thick client. {e}")
# -----------------------------------------


# Oracle Connection Manager
class OracleConnectionManager:
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"
            },
            "sandbox": {
                "user": os.getenv("DB_USER_SANDBOX", "engsb"),
                "password": os.getenv("DB_PASSWORD_SANDBOX", "Engine33r_SB"),
                "dsn": "odw"
            },
            "openwells": {
                "user": os.getenv("DB_USER_OW", "gen_user"),
                "password": os.getenv("DB_PASSWORD_OW", "allusers"),
                "dsn": "owdb1"
            }
        }

    def get_connection(self, name):
        if name not in self._connections:
            raise ValueError(f"Unknown DB connection name: {name}")
        config = self._connections[name]
        try:
            return oracledb.connect(
                user=config['user'],
                password=config['password'],
                dsn=config['dsn']
            )
        except oracledb.Error as e:
            error_obj, = e.args
            raise ConnectionError(f"Failed to connect to Oracle DB '{name}': {error_obj.message}") from e

    def available_connections(self):
        return list(self._connections.keys())


def format_well_api_list(raw_api_list):
    """Convert list of user-supplied APIs to a safe SQL IN list string."""
    cleaned = []
    for item in raw_api_list:
        if item is None:
            continue
        api = str(item).strip()
        if not api:
            continue
        if api not in cleaned:
            cleaned.append(api)
    if not cleaned:
        return None
    escaped = [f"'{x.replace(chr(39), chr(39)+chr(39))}'" for x in cleaned]
    return ", ".join(escaped)


# --------------------------------------------------------------------------
# Mixin with common Treeview display / clear / copy methods
# --------------------------------------------------------------------------
class TreeviewMixin:
    """Provides display_results, clear_results, copy_to_clipboard for any
    frame that has a `self.result_tree` Treeview and `self.current_data` attr."""

    def display_results(self, df, apply_global_sort=True):
        for i in self.result_tree.get_children():
            self.result_tree.delete(i)

        if df.empty:
            messagebox.showinfo("No Results", "No data found for the query.")
            self.result_tree["columns"] = []
            return

        if apply_global_sort:
            sort_columns = ['PRIM_PURP_TYPE_CDE', 'WELL_API_NBR']
            available_sort_columns = [col for col in sort_columns if col in df.columns]
            if available_sort_columns:
                try:
                    if 'WELL_API_NBR' in df.columns:
                        df['WELL_API_NBR'] = df['WELL_API_NBR'].astype(str)
                    df.sort_values(by=available_sort_columns, inplace=True)
                except Exception as e:
                    messagebox.showwarning("Sorting Warning",
                                           f"Error during sorting: {e}. Displaying unsorted data.")

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
            self.result_tree.column(col, width=tree_font.measure(col) + 20, stretch=False)

        for _, row in df.iterrows():
            display_values = []
            for item in row:
                if isinstance(item, pd.Timestamp):
                    display_values.append(item.strftime('%Y-%m-%d') if not pd.isna(item) else '')
                elif pd.isna(item):
                    display_values.append('')
                else:
                    display_values.append(item)
            self.result_tree.insert("", "end", values=display_values)

            for i, item in enumerate(display_values):
                col_width = tree_font.measure(str(item)) + 10
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
                messagebox.showinfo("Copy Success", "Results copied to clipboard (Excel format).")
            except Exception as e:
                messagebox.showerror("Copy Error", f"Failed to copy to clipboard: {e}")
        else:
            messagebox.showwarning("No Data", "No results to copy to clipboard.")


# --------------------------------------------------------------------------
# Helper: build a standard treeview + scrollbars inside a parent frame
# --------------------------------------------------------------------------
def build_treeview(parent):
    """Returns (tree_frame, result_tree) with horizontal & vertical scrollbars."""
    tree_frame = tb.Frame(parent)
    tree_scroll_y = tb.Scrollbar(tree_frame, orient="vertical")
    tree_scroll_y.pack(side="right", fill="y")
    tree_scroll_x = tb.Scrollbar(tree_frame, orient="horizontal")
    tree_scroll_x.pack(side="bottom", fill="x")

    result_tree = ttk.Treeview(tree_frame, show="headings",
                                yscrollcommand=tree_scroll_y.set,
                                xscrollcommand=tree_scroll_x.set)
    result_tree.pack(fill="both", expand=True)
    tree_scroll_y.config(command=result_tree.yview)
    tree_scroll_x.config(command=result_tree.xview)
    return tree_frame, result_tree


# =========================================================================
#  TAB 1 – Well API Input
# =========================================================================
class WellAPITab(tb.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self):
        label = tb.Label(self,
                         text="Enter Well APIs (one per line — all other tabs will use these):",
                         font=("Helvetica", 14))
        label.pack(pady=10)

        self.well_api_text = scrolledtext.ScrolledText(self, wrap=tk.WORD,
                                                        width=50, height=25,
                                                        font=("Courier New", 10))
        self.well_api_text.pack(pady=10)

        default_apis = ["0401920171", "0401922081", "0401922236"]
        self.well_api_text.insert(tk.END, "\n".join(default_apis))

        tb.Button(self, text="Set Well APIs", command=self._set_apis,
                  bootstyle="primary").pack(pady=10)

    def _set_apis(self):
        raw = self.well_api_text.get("1.0", tk.END).strip().split('\n')
        apis = list(dict.fromkeys(a.strip() for a in raw if a.strip()))
        if not apis:
            messagebox.showwarning("Input Error", "Please enter at least one Well API number.")
            return
        self.app.shared_data["well_apis"] = apis
        messagebox.showinfo("APIs Set", f"{len(apis)} unique Well API(s) saved. You can now pull data on any tab.")

    def get_apis(self):
        """Read current text box content (called by other tabs before querying)."""
        raw = self.well_api_text.get("1.0", tk.END).strip().split('\n')
        apis = list(dict.fromkeys(a.strip() for a in raw if a.strip()))
        self.app.shared_data["well_apis"] = apis
        return apis


# =========================================================================
#  TAB 2 – Well Basic Data
# =========================================================================
class WellBasicDataTab(tb.Frame, TreeviewMixin):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self._build()

    def _build(self):
        tb.Label(self, text="Well Basic Data", font=("Helvetica", 16, "bold")).pack(pady=10)
        tb.Button(self, text="Pull Basic Well Data", command=self.pull_basic_data,
                  bootstyle="info").pack(pady=10)

        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard,
                  bootstyle="secondary").pack(pady=10)

    def pull_basic_data(self):
        apis = self.app.api_tab.get_apis()
        if not apis:
            messagebox.showwarning("Input Error", "No Well APIs entered on the Well APIs tab.")
            self.clear_results()
            return

        formatted = format_well_api_list(apis)
        if not formatted:
            self.clear_results(); return

        sql_query = f"""
SELECT
    wd.wlbr_nme                    AS well_name,
    cd.opnl_fld                    AS field_name,
    cd.cmpl_nme                    AS completion_name,
    cd.well_api_nbr                AS api_number,
    wd.wlbr_api_suff_nbr           AS wellbore_suffix,
    wd.wlbr_incl_type_desc         AS wellbore_type,
    cd.prim_purp_type_cde          AS well_type,
    cd.cmpl_state_type_cde         AS status,
    cd.in_svc_indc                 AS in_service,
    cd.init_prod_dte               AS initial_prod_date
FROM dwrptg.cmpl_dmn cd
JOIN dwrptg.wlbr_dmn wd ON cd.well_fac_id = wd.well_fac_id
WHERE cd.actv_indc = 'Y' AND cd.well_api_nbr IN ({formatted})
ORDER BY cd.well_api_nbr, wd.wlbr_api_suff_nbr, cd.cmpl_nme
"""
        self._execute(sql_query, date_cols=['INITIAL_PROD_DATE'])

    def _execute(self, sql, date_cols=None):
        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql)
            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
                for col in (date_cols or []):
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')
                self.display_results(df)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "No results to display.")
                self.clear_results()
            cursor.close(); conn.close()
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e)); self.clear_results()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}"); self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}"); self.clear_results()


# =========================================================================
#  TAB 3 – Top Perf Data
# =========================================================================
class TopPerfTab(tb.Frame, TreeviewMixin):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self._build()

    def _build(self):
        tb.Label(self, text="Top Perf Query", font=("Helvetica", 16, "bold")).pack(pady=10)
        tb.Button(self, text="Run Top Perf Query", command=self.execute_query,
                  bootstyle="info").pack(pady=10)

        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard,
                  bootstyle="secondary").pack(pady=10)

    def execute_query(self):
        apis = self.app.api_tab.get_apis()
        if not apis:
            messagebox.showwarning("Input Error", "No Well APIs entered on the Well APIs tab.")
            self.clear_results(); return

        formatted = format_well_api_list(apis)
        if not formatted:
            self.clear_results(); return

        sql_query = f"""
WITH T AS (
    SELECT cd.cmpl_nme, cd.cmpl_fac_id, cd.well_fac_id,
           wd.well_api_nbr, cd.engr_strg_nme
    FROM dwrptg.cmpl_dmn cd
    JOIN dwrptg.well_dmn wd ON cd.well_fac_id = wd.well_fac_id
    WHERE cd.actv_indc = 'Y'
      AND wd.actv_indc = 'Y'
      AND wd.well_api_nbr IN ({formatted})
      AND cd.cmpl_state_type_cde IN ('OPNL', 'TA', 'ABND')
),
perfs AS (
    SELECT t.well_api_nbr, t.cmpl_nme, t.cmpl_fac_id,
           t.well_fac_id, t.engr_strg_nme,
           MIN(opg.top_md_qty) AS top_perf,
           MAX(opg.btm_md_qty) AS btm_perf
    FROM T
    JOIN dwrptg.wlbr_dmn wd ON t.well_fac_id = wd.well_fac_id
    JOIN dwrptg.actl_wlbr_opg_ntvl_dmn opg ON wd.wlbr_fac_id = opg.wlbr_fac_id
    GROUP BY t.well_api_nbr, t.cmpl_nme, t.cmpl_fac_id,
             t.well_fac_id, t.engr_strg_nme
),
surveys AS (
    SELECT wd.well_fac_id,
           d.md_qty AS svy_md,
           d.tvd_qty AS svy_tvd
    FROM dwrptg.dsvy_pt_dmn d
    JOIN dwrptg.wlbr_dmn wd ON d.wlbr_fac_id = wd.wlbr_fac_id
    WHERE wd.well_fac_id IN (SELECT well_fac_id FROM T)
      AND d.tvd_qty IS NOT NULL
      AND d.md_qty IS NOT NULL
      AND d.tvd_qty <= d.md_qty
),
top_above AS (
    SELECT p.cmpl_fac_id, s.svy_md, s.svy_tvd,
           ROW_NUMBER() OVER (PARTITION BY p.cmpl_fac_id ORDER BY s.svy_md DESC) AS rn
    FROM perfs p
    JOIN surveys s ON s.well_fac_id = p.well_fac_id
    WHERE s.svy_md <= p.top_perf
),
top_below AS (
    SELECT p.cmpl_fac_id, s.svy_md, s.svy_tvd,
           ROW_NUMBER() OVER (PARTITION BY p.cmpl_fac_id ORDER BY s.svy_md ASC) AS rn
    FROM perfs p
    JOIN surveys s ON s.well_fac_id = p.well_fac_id
    WHERE s.svy_md > p.top_perf
),
btm_above AS (
    SELECT p.cmpl_fac_id, s.svy_md, s.svy_tvd,
           ROW_NUMBER() OVER (PARTITION BY p.cmpl_fac_id ORDER BY s.svy_md DESC) AS rn
    FROM perfs p
    JOIN surveys s ON s.well_fac_id = p.well_fac_id
    WHERE s.svy_md <= p.btm_perf
),
btm_below AS (
    SELECT p.cmpl_fac_id, s.svy_md, s.svy_tvd,
           ROW_NUMBER() OVER (PARTITION BY p.cmpl_fac_id ORDER BY s.svy_md ASC) AS rn
    FROM perfs p
    JOIN surveys s ON s.well_fac_id = p.well_fac_id
    WHERE s.svy_md > p.btm_perf
)
SELECT p.well_api_nbr,
       p.cmpl_nme,
       p.engr_strg_nme,
       p.top_perf,
       LEAST(
           p.top_perf,
           ROUND(
               CASE
                   WHEN ta.svy_md IS NOT NULL AND tb.svy_md IS NOT NULL
                        AND tb.svy_md != ta.svy_md
                   THEN ta.svy_tvd +
                        (p.top_perf - ta.svy_md) *
                        (tb.svy_tvd - ta.svy_tvd) /
                        (tb.svy_md - ta.svy_md)
                   WHEN ta.svy_md IS NOT NULL
                   THEN ta.svy_tvd
                   ELSE p.top_perf
               END, 1)
       ) AS top_perf_tvd,
       p.btm_perf,
       LEAST(
           p.btm_perf,
           ROUND(
               CASE
                   WHEN ba.svy_md IS NOT NULL AND bb.svy_md IS NOT NULL
                        AND bb.svy_md != ba.svy_md
                   THEN ba.svy_tvd +
                        (p.btm_perf - ba.svy_md) *
                        (bb.svy_tvd - ba.svy_tvd) /
                        (bb.svy_md - ba.svy_md)
                   WHEN ba.svy_md IS NOT NULL
                   THEN ba.svy_tvd
                   ELSE p.btm_perf
               END, 1)
       ) AS btm_perf_tvd
FROM perfs p
LEFT JOIN top_above ta ON p.cmpl_fac_id = ta.cmpl_fac_id AND ta.rn = 1
LEFT JOIN top_below tb ON p.cmpl_fac_id = tb.cmpl_fac_id AND tb.rn = 1
LEFT JOIN btm_above ba ON p.cmpl_fac_id = ba.cmpl_fac_id AND ba.rn = 1
LEFT JOIN btm_below bb ON p.cmpl_fac_id = bb.cmpl_fac_id AND bb.rn = 1
ORDER BY p.cmpl_nme
"""
        self._execute(sql_query)

    def _execute(self, sql):
        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql)
            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
                self.display_results(df)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "No results to display.")
                self.clear_results()
            cursor.close(); conn.close()
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e)); self.clear_results()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}"); self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}"); self.clear_results()


# =========================================================================
#  TAB 4 – Performance Summary
# =========================================================================
class PerformanceSummaryTab(tb.Frame, TreeviewMixin):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self.calculation_labels = {}
        self._build()

    def _build(self):
        tb.Label(self, text="Performance Summary by Field",
                 font=("Helvetica", 16, "bold")).pack(pady=10)

        # Top row: date entry + pull button
        top_row = tb.Frame(self)
        top_row.pack(pady=5, padx=20, fill="x")

        date_frame = tb.Frame(top_row)
        date_frame.pack(side="left")
        tb.Label(date_frame, text="Last Project Update Date:").pack(side="left", padx=5)
        self.project_update_date_entry = tb.DateEntry(date_frame, dateformat="%Y-%m-%d")
        self.project_update_date_entry.pack(side="left", padx=5)
        two_years_ago = date.today() - timedelta(days=730)
        self.project_update_date_entry.entry.delete(0, tk.END)
        self.project_update_date_entry.entry.insert(0, two_years_ago.strftime("%Y-%m-%d"))

        tb.Button(top_row, text="Pull Performance Summary Data",
                  command=self.pull_summary_data, bootstyle="info").pack(side="right")

        # Calculations label frame
        calc_lf = ttk.LabelFrame(self, text="Calculations", style="info.TLabelframe")
        calc_lf.pack(pady=10, padx=20, fill="x")
        calc_lf.columnconfigure(1, weight=1)

        calc_items = [
            ("Total INJ wells (Active/TA):", "total_inj"),
            ("Total PROD wells:", "total_prod"),
            ("Active Injectors (Last 2 yrs):", "active_injectors"),
            ("Idle Injectors (Last 2 yrs):", "idle_injectors"),
            ("Injectors Drilled Since Last Update:", "injectors_drilled"),
            ("Active Producers (Last 2 yrs):", "active_producers"),
            ("Idle Producers (Last 2 yrs):", "idle_producers"),
            ("Producers Drilled Since Last Update:", "producers_drilled"),
            ("Producers Abandoned Since Last Update:", "producers_abandoned"),
        ]
        for row_idx, (text, key) in enumerate(calc_items):
            tb.Label(calc_lf, text=text, anchor="w").grid(row=row_idx, column=0, sticky="ew", padx=5, pady=2)
            lbl = tb.Label(calc_lf, text="N/A", bootstyle="success", font=("TkDefaultFont", 10, "bold"))
            lbl.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=2)
            self.calculation_labels[key] = lbl

        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard,
                  bootstyle="secondary").pack(pady=10)

    def pull_summary_data(self):
        apis = self.app.api_tab.get_apis()
        if not apis:
            messagebox.showwarning("Input Error", "No Well APIs entered on the Well APIs tab.")
            self.clear_calculations(); return

        formatted = format_well_api_list(apis)
        if not formatted:
            self.clear_calculations(); return

        sql_query = f"""
        WITH T1 AS (
            SELECT cmpl_fac_id, eftv_dttm AS last_inj_dte FROM
            (
                SELECT cmpl_fac_id, eftv_dttm, aloc_stm_inj_dly_rte_qty, aloc_wtr_inj_dly_rte_qty,
                       DENSE_RANK() OVER (PARTITION BY cmpl_fac_id ORDER BY eftv_dttm DESC) AS rnk
                FROM cmpl_mnly_fact
                WHERE aloc_wtr_inj_dly_rte_qty > 0 OR aloc_stm_inj_dly_rte_qty > 0
            ) WHERE rnk = 1
        ),
        T2 AS (
            SELECT cmpl_fac_id, eftv_dttm AS last_prod_dte FROM
            (
                SELECT cmpl_fac_id, eftv_dttm, aloc_gros_prod_dly_rte_qty,
                       DENSE_RANK() OVER (PARTITION BY cmpl_fac_id ORDER BY eftv_dttm DESC) AS rnk
                FROM cmpl_mnly_fact
                WHERE aloc_gros_prod_dly_rte_qty > 0
            ) WHERE rnk = 1
        )
        SELECT wd.well_nme, wd.well_api_nbr, wd.fld_nme,
               cd.init_prod_dte, cd.init_inj_dte, cd.prim_purp_type_cde,
               cd.ENGR_STRG_NME,
               t1.last_inj_dte, t2.last_prod_dte,
               cd.CMPL_STATE_TYPE_DESC, cd.CMPL_STATE_EFTV_DTTM
        FROM well_dmn wd
        JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
        LEFT JOIN cmpl_non_ver_dmn cnd ON cd.cmpl_fac_id = cnd.cmpl_fac_id
        LEFT JOIN curr_cmpl_opnl_stat os ON cd.cmpl_fac_id = os.cmpl_fac_id
        LEFT JOIN T1 ON cd.cmpl_fac_id = T1.cmpl_fac_id
        LEFT JOIN T2 ON cd.cmpl_fac_id = T2.cmpl_fac_id
        WHERE
            cd.actv_indc = 'Y'
            AND wd.actv_indc = 'Y'
            AND wd.well_api_nbr IN ({formatted})
            AND cd.prim_purp_type_cde IN ('PROD', 'INJ')
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)
            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
                date_cols = ['LAST_INJ_DTE', 'LAST_PROD_DTE', 'INIT_INJ_DTE',
                             'INIT_PROD_DTE', 'CMPL_STATE_EFTV_DTTM']
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors='coerce')
                self.display_results(df)
                self.current_data = df
                self.perform_calculations(df)
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "No results to display.")
                self.clear_results(); self.clear_calculations()
            cursor.close(); conn.close()
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.clear_results(); self.clear_calculations()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}")
            self.clear_results(); self.clear_calculations()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
            self.clear_results(); self.clear_calculations()

    def perform_calculations(self, df):
        try:
            project_update_date_str = self.project_update_date_entry.entry.get()
            project_update_date = datetime.strptime(project_update_date_str, "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("Date Error", "Invalid date format. Please use YYYY-MM-DD.")
            self.clear_calculations(); return

        today = date.today()
        project_update_date_ts = pd.Timestamp(project_update_date)
        two_years_ago_ts = pd.Timestamp(today - timedelta(days=730))

        inj_df = df[df['PRIM_PURP_TYPE_CDE'] == 'INJ']
        prod_df = df[df['PRIM_PURP_TYPE_CDE'] == 'PROD']

        def _set(key, val):
            if key in self.calculation_labels:
                self.calculation_labels[key].config(text=str(val))

        _set("total_inj", len(inj_df[inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned']))
        _set("total_prod", len(prod_df))
        _set("active_injectors", len(inj_df[inj_df['LAST_INJ_DTE'] >= two_years_ago_ts]))
        _set("idle_injectors", len(inj_df[(inj_df['LAST_INJ_DTE'] < two_years_ago_ts) &
                                           (inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]))
        _set("injectors_drilled", len(inj_df[(inj_df['INIT_INJ_DTE'] > project_update_date_ts) &
                                              (inj_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]))
        _set("active_producers", len(prod_df[prod_df['LAST_PROD_DTE'] >= two_years_ago_ts]))
        _set("idle_producers", len(prod_df[(prod_df['LAST_PROD_DTE'] < two_years_ago_ts) &
                                            (prod_df['CMPL_STATE_TYPE_DESC'] != 'Permanently Abandoned')]))
        _set("producers_drilled", len(prod_df[prod_df['INIT_PROD_DTE'] > project_update_date_ts]))
        _set("producers_abandoned", len(prod_df[(prod_df['CMPL_STATE_TYPE_DESC'] == 'Permanently Abandoned') &
                                                 (prod_df['CMPL_STATE_EFTV_DTTM'] > project_update_date_ts)]))

    def clear_calculations(self):
        for lbl in self.calculation_labels.values():
            lbl.config(text="N/A")


# =========================================================================
#  TAB 5 – Avg Tubing Pressure & Inj Vol
# =========================================================================
class TubingPressureTab(tb.Frame, TreeviewMixin):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self._build()

    def _build(self):
        tb.Label(self, text="Avg Injection Volume and Wlhd Tubing Pressure",
                 font=("Helvetica", 16, "bold")).pack(pady=10)

        control = tb.Frame(self)
        control.pack(pady=10, padx=20, fill="x")

        tb.Button(control, text="Pull Averages Data", command=self.pull_data,
                  bootstyle="info").pack(side="left")

        avg_frame = tb.Frame(control)
        avg_frame.pack(side="right")
        tb.Label(avg_frame, text="Overall Avg Tubing Pressure:").pack(side="left", padx=5)
        self.avg_pressure_label = tb.Label(avg_frame, text="N/A", bootstyle="success",
                                            font=("TkDefaultFont", 10, "bold"))
        self.avg_pressure_label.pack(side="left", padx=5)

        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard,
                  bootstyle="secondary").pack(pady=10)

    def pull_data(self):
        apis = self.app.api_tab.get_apis()
        if not apis:
            messagebox.showwarning("Input Error", "No Well APIs entered on the Well APIs tab.")
            self.clear_results(); self._clear_avg(); return

        formatted = format_well_api_list(apis)
        if not formatted:
            self.clear_results(); self._clear_avg(); return

        sql_query = f"""
        SELECT wd.well_nme, wd.well_api_nbr, cd.cmpl_nme, cd.cmpl_fac_id,
            AVG(CASE WHEN cf.aloc_stm_inj_vol_qty > 0 THEN cf.aloc_stm_inj_vol_qty END) AS avg_stm_inj_vol,
            ROUND(AVG(CASE WHEN cf.aloc_wtr_inj_vol_qty > 0 THEN cf.aloc_wtr_inj_vol_qty END), 2) AS avg_wtr_inj_vol,
            ROUND(AVG(CASE WHEN cf.wlhd_tbg_prsr_qty > 0 THEN cf.wlhd_tbg_prsr_qty END), 2) AS avg_wlhd_tbg_prsr
        FROM well_dmn wd
        JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
        JOIN cmpl_dly_fact cf ON cd.cmpl_fac_id = cf.cmpl_fac_id
        WHERE wd.actv_indc = 'Y' AND cd.actv_indc = 'Y'
            AND cf.eftv_dttm >= TRUNC(SYSDATE) - 60
            AND wd.well_api_nbr IN ({formatted})
        GROUP BY wd.well_nme, wd.well_api_nbr, cd.cmpl_nme, cd.cmpl_fac_id
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)
            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
                self.display_results(df)
                self.current_data = df
                self._calc_avg(df)
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "No results to display.")
                self.clear_results(); self._clear_avg()
            cursor.close(); conn.close()
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e)); self.clear_results(); self._clear_avg()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}"); self.clear_results(); self._clear_avg()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}"); self.clear_results(); self._clear_avg()

    def _calc_avg(self, df):
        col = next((c for c in df.columns if c.upper() == 'AVG_WLHD_TBG_PRSR'), None)
        if col is None:
            self.avg_pressure_label.config(text="Column not found"); return
        vals = pd.to_numeric(df[col], errors='coerce').dropna()
        if not vals.empty:
            self.avg_pressure_label.config(text=f"{vals.mean():.1f}")
        else:
            self.avg_pressure_label.config(text="No valid data")

    def _clear_avg(self):
        self.avg_pressure_label.config(text="N/A")


# =========================================================================
#  TAB 6 – Production and Injection (Monthly)
# =========================================================================
class ProductionInjectionTab(tb.Frame, TreeviewMixin):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self._build()

    def _build(self):
        tb.Label(self, text="Production and Injection Data",
                 font=("Helvetica", 16, "bold")).pack(pady=10)
        tb.Button(self, text="Pull Production/Injection Data", command=self.pull_data,
                  bootstyle="info").pack(pady=10)

        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard,
                  bootstyle="secondary").pack(pady=10)

    def pull_data(self):
        apis = self.app.api_tab.get_apis()
        if not apis:
            messagebox.showwarning("Input Error", "No Well APIs entered on the Well APIs tab.")
            self.clear_results(); return

        formatted = format_well_api_list(apis)
        if not formatted:
            self.clear_results(); return

        sql_query = f"""
        SELECT
            wd.well_nme AS "WELL NAME",
            wd.well_api_nbr AS "WELL API",
            cf.eftv_dttm AS "DATE",
            cf.aloc_oil_prod_dly_rte_qty AS "OIL PROD BOPD",
            cf.aloc_wtr_prod_dly_rte_qty AS "WATER PROD BWPD",
            cf.aloc_gas_prod_dly_rte_qty AS "GAS PROD MCFD",
            cf.aloc_stm_inj_dly_rte_qty AS "STEAM INJ Per Day",
            cf.aloc_wtr_inj_dly_rte_qty AS "WATER INJ Per Day",
            cf.aloc_gas_inj_dly_rte_qty AS "GAS INJ Per Day"
        FROM well_dmn wd
        JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
        JOIN cmpl_mnly_fact cf ON cd.cmpl_fac_id = cf.cmpl_fac_id
        WHERE cd.actv_indc = 'Y' AND wd.actv_indc = 'Y'
            AND wd.well_api_nbr IN ({formatted})
            AND cf.eftv_dttm >= ADD_MONTHS(TRUNC(SYSDATE), -62)
            AND cf.eftv_dttm <= TRUNC(SYSDATE)
        ORDER BY wd.well_api_nbr, cf.eftv_dttm
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)
            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
                if 'DATE' in df.columns:
                    df['DATE'] = pd.to_datetime(df['DATE'], errors='coerce')
                self.display_results(df, apply_global_sort=False)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "No results to display.")
                self.clear_results()
            cursor.close(); conn.close()
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e)); self.clear_results()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}"); self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}"); self.clear_results()


# =========================================================================
#  TAB 7 – Daily Injection and Tubing Pressure
# =========================================================================
class DailyInjectionPressureTab(tb.Frame, TreeviewMixin):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.conn_manager = OracleConnectionManager()
        self.current_data = None
        self._build()

    def _build(self):
        tb.Label(self, text="Daily Injection and Tubing Pressure (Full Data)",
                 font=("Helvetica", 16, "bold")).pack(pady=10)
        tb.Button(self, text="Pull Daily Data", command=self.pull_data,
                  bootstyle="info").pack(pady=10)

        self.tree_frame, self.result_tree = build_treeview(self)
        self.tree_frame.pack(pady=10, padx=20, fill="both", expand=True)

        tb.Button(self, text="Copy Results to Clipboard", command=self.copy_to_clipboard,
                  bootstyle="secondary").pack(pady=10)

    def pull_data(self):
        apis = self.app.api_tab.get_apis()
        if not apis:
            messagebox.showwarning("Input Error", "No Well APIs entered on the Well APIs tab.")
            self.clear_results(); return

        formatted = format_well_api_list(apis)
        if not formatted:
            self.clear_results(); return

        sql_query = f"""
        SELECT wd.well_nme, wd.well_api_nbr, cd.cmpl_nme, cd.cmpl_fac_id,
            cf.eftv_dttm,
            cf.aloc_stm_inj_vol_qty,
            cf.aloc_wtr_inj_vol_qty,
            cf.wlhd_tbg_prsr_qty
        FROM well_dmn wd
        JOIN cmpl_dmn cd ON wd.well_fac_id = cd.well_fac_id
        JOIN cmpl_dly_fact cf ON cd.cmpl_fac_id = cf.cmpl_fac_id
        WHERE wd.actv_indc = 'Y' AND cd.actv_indc = 'Y'
            AND cf.eftv_dttm >= TRUNC(SYSDATE) - 60
            AND wd.well_api_nbr IN ({formatted})
        ORDER BY cf.eftv_dttm
        """

        try:
            conn = self.conn_manager.get_connection('odw')
            cursor = conn.cursor()
            cursor.execute(sql_query)
            if cursor.description:
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
                if 'EFTV_DTTM' in df.columns:
                    df['EFTV_DTTM'] = pd.to_datetime(df['EFTV_DTTM'], errors='coerce')
                self.display_results(df, apply_global_sort=False)
                self.current_data = df
            else:
                conn.commit()
                messagebox.showinfo("Query Executed", "No results to display.")
                self.clear_results()
            cursor.close(); conn.close()
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e)); self.clear_results()
        except oracledb.Error as e:
            error_obj, = e.args
            messagebox.showerror("Database Error", f"Oracle Error: {error_obj.message}"); self.clear_results()
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {e}"); self.clear_results()


# =========================================================================
#  MAIN APPLICATION  –  single window with ttk.Notebook
# =========================================================================
class MainApplication(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Periodic Project Review")
        self.geometry("1600x1200")

        s = ttk.Style()
        s.configure("TButton", font=("Helvetica", 12, "bold"))
        # Make notebook tab text larger and bolder
        s.configure("TNotebook.Tab", font=("Helvetica", 11, "bold"), padding=[12, 6])

        self.shared_data = {}

        # Create Notebook (tab container)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Build each tab — order matches original page flow
        self.api_tab = WellAPITab(self.notebook, self)
        self.notebook.add(self.api_tab, text="  Well APIs  ")

        self.basic_tab = WellBasicDataTab(self.notebook, self)
        self.notebook.add(self.basic_tab, text="  Basic Data  ")

        self.perf_tab = TopPerfTab(self.notebook, self)
        self.notebook.add(self.perf_tab, text="  Top Perf  ")

        self.summary_tab = PerformanceSummaryTab(self.notebook, self)
        self.notebook.add(self.summary_tab, text="  Summary  ")

        self.tubing_tab = TubingPressureTab(self.notebook, self)
        self.notebook.add(self.tubing_tab, text="  Avg Tubing Pres  ")

        self.prod_inj_tab = ProductionInjectionTab(self.notebook, self)
        self.notebook.add(self.prod_inj_tab, text="  Monthly Prod/Inj  ")

        self.daily_tab = DailyInjectionPressureTab(self.notebook, self)
        self.notebook.add(self.daily_tab, text="  Daily Inj/Pres  ")


if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()