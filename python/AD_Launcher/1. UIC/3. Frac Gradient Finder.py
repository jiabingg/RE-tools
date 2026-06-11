"""
=============================================================================
  Keyword File Scanner — Filename Search Tool  (High-Performance Edition)
  
  Searches network folders for files with names matching reservoir engineering
  keywords (step rate test, fracture gradient, mini frac, etc.)
  
  Designed for: tens of thousands of subfolders, millions of files
  - Single-pass scan (no pre-counting)
  - Live progress updates as it runs
  - Batched UI updates to stay responsive
  - os.scandir for faster directory traversal
  
  Output: On-screen summary + CSV file of results
  Requirements: NONE — uses only Python standard library
=============================================================================
"""

import os
import re
import csv
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
from collections import deque


# =============================================================================
#  DEFAULT KEYWORDS — Edit this list to add/remove search terms
# =============================================================================
DEFAULT_KEYWORDS = [
    "step rate test",
    "step rate",
    "step-rate test",
    "SRT",
    "fracture gradient",
    "frac gradient",
    "fracture pressure",
    "mini frac",
    "mini-frac",
    "minifrac",
    "DFIT",
    "diagnostic fracture injection test",
    "breakdown pressure",
    "closure pressure",
    "instantaneous shut-in pressure",
    "ISIP",
    "formation parting pressure",
    "injection pressure",
    "pressure falloff",
    "falloff test",
    "pressure vs rate",
    "psi/ft",
]


# =============================================================================
#  KEYWORD MATCHER — Pre-compiled for speed
# =============================================================================

class KeywordMatcher:
    """Pre-compiles keyword patterns for fast matching."""

    def __init__(self, keywords):
        self.keywords = keywords
        self.short_patterns = []  # (keyword, compiled_regex) for len <= 4
        self.long_keywords = []   # (keyword, keyword_lower) for len > 4

        for kw in keywords:
            if len(kw) <= 4:
                pattern = re.compile(
                    r'(?:^|[\s_\-\.])' + re.escape(kw.lower()) + r'(?:[\s_\-\.]|$)',
                    re.IGNORECASE
                )
                self.short_patterns.append((kw, pattern))
            else:
                self.long_keywords.append((kw, kw.lower()))

    def find_matches(self, filename_lower):
        """Return list of matched keywords for a filename."""
        matches = []
        for kw, kw_lower in self.long_keywords:
            if kw_lower in filename_lower:
                matches.append(kw)
        for kw, pattern in self.short_patterns:
            if pattern.search(filename_lower):
                matches.append(kw)
        return matches


# =============================================================================
#  MAIN SCANNER CLASS — Single-pass, os.scandir, live progress
# =============================================================================

class FileScanner:
    """Scans directories for keyword matches in filenames."""

    def __init__(self, matcher):
        self.matcher = matcher
        self.results = []
        self.files_scanned = 0
        self.dirs_scanned = 0
        self.match_count = 0
        self.current_dir = ""
        self.is_running = False
        self.cancel_requested = False
        self.errors = []

    def scan_folder(self, folder_path, extensions):
        """Single-pass recursive scan using os.scandir for speed."""
        self.results = []
        self.errors = []
        self.files_scanned = 0
        self.dirs_scanned = 0
        self.match_count = 0
        self.is_running = True
        self.cancel_requested = False

        # Use iterative stack instead of os.walk for better control
        dir_stack = deque([folder_path])

        while dir_stack and not self.cancel_requested:
            current_dir = dir_stack.popleft()
            self.current_dir = current_dir
            self.dirs_scanned += 1

            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        if self.cancel_requested:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if not entry.name.startswith('.'):
                                    dir_stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                self.files_scanned += 1
                                name = entry.name
                                ext = os.path.splitext(name)[1].lower()

                                # Filter by extension if specified
                                if extensions and ext not in extensions:
                                    continue

                                # Check filename for keywords
                                name_lower = name.lower()
                                matched_keywords = self.matcher.find_matches(name_lower)

                                if matched_keywords:
                                    try:
                                        stat = entry.stat()
                                        size_kb = round(stat.st_size / 1024, 1)
                                        mod_date = datetime.fromtimestamp(
                                            stat.st_mtime
                                        ).strftime("%Y-%m-%d %H:%M")
                                    except OSError:
                                        size_kb = 0
                                        mod_date = "N/A"

                                    ext_label = ext.upper().replace(".", "")
                                    folder = current_dir

                                    for kw in matched_keywords:
                                        self.results.append({
                                            "file_path": entry.path,
                                            "file_name": name,
                                            "folder": folder,
                                            "file_type": ext_label,
                                            "file_size_kb": size_kb,
                                            "keyword": kw,
                                            "modified_date": mod_date,
                                        })
                                        self.match_count += 1

                        except (PermissionError, OSError):
                            pass  # Skip inaccessible entries silently

            except PermissionError:
                self.errors.append(f"Access denied: {current_dir}")
            except OSError as e:
                self.errors.append(f"{current_dir}: {e}")

        self.is_running = False


# =============================================================================
#  CSV REPORT WRITER
# =============================================================================

def write_results_to_csv(results, output_path):
    """Write scan results to a CSV file."""
    headers = ["File Name", "Keyword Matched", "Type", "Size (KB)",
               "Date Modified", "Folder", "Full Path"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in results:
            writer.writerow([
                r["file_name"],
                r["keyword"],
                r["file_type"],
                r["file_size_kb"],
                r["modified_date"],
                r["folder"],
                r["file_path"],
            ])


# =============================================================================
#  GUI APPLICATION
# =============================================================================

class ScannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Keyword File Scanner — Filename Search")
        self.root.geometry("1050x750")
        self.root.minsize(850, 600)
        self.root.configure(bg="#f5f5f5")

        self.scanner = None
        self.scan_thread = None
        self._build_ui()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"),
                        foreground="#1a1a2e", background="#f5f5f5")
        style.configure("TLabel", font=("Segoe UI", 10), background="#f5f5f5")
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"),
                        foreground="#ffffff", background="#0f3460")
        style.map("Accent.TButton",
                  background=[("active", "#16213e"), ("disabled", "#999999")])
        style.configure("Stop.TButton", font=("Segoe UI", 10, "bold"),
                        foreground="#ffffff", background="#c62828")
        style.map("Stop.TButton", background=[("active", "#b71c1c")])

        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        # --- TITLE ---
        ttk.Label(main, text="Keyword File Scanner — Filename Search",
                  style="Title.TLabel").pack(anchor="w")
        ttk.Label(main,
                  text="High-performance scanner for large network drives. "
                       "Handles millions of files."
                  ).pack(anchor="w", pady=(0, 10))

        # --- FOLDER ---
        folder_frame = ttk.LabelFrame(main, text="Folder to Search", padding=8)
        folder_frame.pack(fill="x", pady=(0, 8))

        self.folder_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self.folder_var,
                  font=("Consolas", 10)).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(folder_frame, text="Browse...",
                   command=self._browse_folder).pack(side="left")

        # --- KEYWORDS ---
        kw_frame = ttk.LabelFrame(main, text="Keywords (one per line)", padding=8)
        kw_frame.pack(fill="x", pady=(0, 8))

        self.kw_text = scrolledtext.ScrolledText(kw_frame, height=5,
                                                  font=("Consolas", 10), wrap="word")
        self.kw_text.pack(fill="x")
        self.kw_text.insert("1.0", "\n".join(DEFAULT_KEYWORDS))

        # --- FILE TYPE FILTER ---
        ft_frame = ttk.LabelFrame(main, text="File Types to Include", padding=8)
        ft_frame.pack(fill="x", pady=(0, 8))

        self.ft_vars = {}
        file_types = [
            (".pdf", "PDF"), (".pptx", "PowerPoint"), (".xlsx", "Excel (.xlsx)"),
            (".xls", "Excel (.xls)"), (".docx", "Word"), (".doc", "Word (.doc)"),
            (".txt", "Text"), (".csv", "CSV"),
        ]
        self.ft_all_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ft_frame, text="All file types",
                        variable=self.ft_all_var,
                        command=self._toggle_all_types).pack(side="left", padx=(0, 15))

        for ext, label in file_types:
            var = tk.BooleanVar(value=True)
            self.ft_vars[ext] = var
            ttk.Checkbutton(ft_frame, text=label, variable=var).pack(side="left", padx=(0, 10))

        # --- BUTTONS ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(0, 8))

        self.scan_btn = ttk.Button(btn_frame, text="▶  Start Scan",
                                   style="Accent.TButton", command=self._start_scan)
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ttk.Button(btn_frame, text="■  Stop",
                                   style="Stop.TButton", command=self._stop_scan,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 8))

        self.export_btn = ttk.Button(btn_frame, text="Export to CSV",
                                     command=self._export_csv, state="disabled")
        self.export_btn.pack(side="left")

        # --- LIVE STATS BAR ---
        stats_frame = ttk.LabelFrame(main, text="Live Progress", padding=8)
        stats_frame.pack(fill="x", pady=(0, 8))

        stats_inner = ttk.Frame(stats_frame)
        stats_inner.pack(fill="x")

        self.lbl_files = ttk.Label(stats_inner, text="Files: 0",
                                    font=("Consolas", 11, "bold"))
        self.lbl_files.pack(side="left", padx=(0, 30))

        self.lbl_dirs = ttk.Label(stats_inner, text="Folders: 0",
                                   font=("Consolas", 11))
        self.lbl_dirs.pack(side="left", padx=(0, 30))

        self.lbl_matches = ttk.Label(stats_inner, text="Matches: 0",
                                      font=("Consolas", 11, "bold"),
                                      foreground="#e94560")
        self.lbl_matches.pack(side="left", padx=(0, 30))

        self.lbl_errors = ttk.Label(stats_inner, text="Errors: 0",
                                     font=("Consolas", 11))
        self.lbl_errors.pack(side="left")

        self.status_var = tk.StringVar(value="Ready. Select a folder and click Start Scan.")
        ttk.Label(stats_frame, textvariable=self.status_var,
                  font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(4, 0))

        # --- PROGRESS BAR (indeterminate during scan) ---
        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 4))

        # --- RESULTS ---
        results_frame = ttk.LabelFrame(main, text="Results", padding=8)
        results_frame.pack(fill="both", expand=True)

        self.results_text = scrolledtext.ScrolledText(
            results_frame, font=("Consolas", 9), wrap="word",
            state="disabled", bg="#1a1a2e", fg="#e0e0e0"
        )
        self.results_text.pack(fill="both", expand=True)

        self.results_text.tag_config("header", foreground="#53d8fb",
                                      font=("Consolas", 10, "bold"))
        self.results_text.tag_config("keyword", foreground="#e94560",
                                      font=("Consolas", 9, "bold"))
        self.results_text.tag_config("match", foreground="#00e676")
        self.results_text.tag_config("info", foreground="#90a4ae")
        self.results_text.tag_config("success", foreground="#00e676",
                                      font=("Consolas", 10, "bold"))

        # Track how many results we've already printed (for live streaming)
        self._printed_results = 0

    def _toggle_all_types(self):
        val = self.ft_all_var.get()
        for var in self.ft_vars.values():
            var.set(val)

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder to Search")
        if folder:
            self.folder_var.set(folder)

    def _log(self, text, tag=None):
        self.results_text.config(state="normal")
        if tag:
            self.results_text.insert("end", text + "\n", tag)
        else:
            self.results_text.insert("end", text + "\n")
        self.results_text.see("end")
        self.results_text.config(state="disabled")

    def _clear_log(self):
        self.results_text.config(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.config(state="disabled")

    def _get_keywords(self):
        raw = self.kw_text.get("1.0", "end")
        return [line.strip() for line in raw.strip().split("\n") if line.strip()]

    def _get_extensions(self):
        if self.ft_all_var.get():
            return set()
        return {ext for ext, var in self.ft_vars.items() if var.get()}

    def _start_scan(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("No Folder", "Please select a valid folder to search.")
            return

        keywords = self._get_keywords()
        if not keywords:
            messagebox.showwarning("No Keywords", "Please enter at least one keyword.")
            return

        extensions = self._get_extensions()

        # Pre-compile keyword matcher
        matcher = KeywordMatcher(keywords)

        self.scan_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.export_btn.config(state="disabled")
        self._clear_log()
        self._printed_results = 0

        self._log("=" * 65, "header")
        self._log("  KEYWORD FILE SCANNER — Filename Search", "header")
        self._log(f"  Folder : {folder}", "info")
        self._log(f"  Keywords: {len(keywords)}", "info")
        ext_label = "All" if not extensions else ", ".join(sorted(extensions))
        self._log(f"  File types: {ext_label}", "info")
        self._log("=" * 65, "header")
        self._log("")
        self._log("  Scanning... matches appear here in real time:", "info")
        self._log("-" * 65)

        # Start indeterminate progress bar
        self.progress.config(mode="indeterminate")
        self.progress.start(15)

        self.scanner = FileScanner(matcher)
        self.scan_thread = threading.Thread(
            target=self.scanner.scan_folder,
            args=(folder, extensions),
            daemon=True
        )
        self.scan_thread.start()
        self._poll_progress()

    def _stop_scan(self):
        if self.scanner:
            self.scanner.cancel_requested = True
            self._log("\n  ** Scan cancelled by user **\n", "keyword")

    def _poll_progress(self):
        if self.scanner is None:
            return

        # Update live stats
        self.lbl_files.config(text=f"Files: {self.scanner.files_scanned:,}")
        self.lbl_dirs.config(text=f"Folders: {self.scanner.dirs_scanned:,}")
        self.lbl_matches.config(text=f"Matches: {self.scanner.match_count:,}")
        self.lbl_errors.config(text=f"Errors: {len(self.scanner.errors):,}")

        # Truncate long paths for status bar
        cur = self.scanner.current_dir
        if len(cur) > 80:
            cur = "..." + cur[-77:]
        self.status_var.set(f"Scanning: {cur}")

        # Stream new matches to the results log in real time
        results = self.scanner.results
        new_results = results[self._printed_results:]
        if new_results:
            # Group new results by file for cleaner display
            batch = {}
            for r in new_results:
                fp = r["file_path"]
                if fp not in batch:
                    batch[fp] = []
                batch[fp].append(r["keyword"])

            for fp, kws in batch.items():
                fname = os.path.basename(fp)
                folder = os.path.dirname(fp)
                kw_str = ", ".join(sorted(set(kws)))
                self._log(f"  {fname}", "match")
                self._log(f"    Keywords: {kw_str}", "keyword")
                self._log(f"    Folder  : {folder}", "info")

            self._printed_results = len(results)

        if self.scanner.is_running:
            self.root.after(250, self._poll_progress)
        else:
            self._scan_complete()

    def _scan_complete(self):
        self.progress.stop()
        self.progress.config(mode="determinate")
        self.progress["value"] = 100
        self.scan_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

        results = self.scanner.results

        # Final stats update
        self.lbl_files.config(text=f"Files: {self.scanner.files_scanned:,}")
        self.lbl_dirs.config(text=f"Folders: {self.scanner.dirs_scanned:,}")
        self.lbl_matches.config(text=f"Matches: {self.scanner.match_count:,}")
        self.lbl_errors.config(text=f"Errors: {len(self.scanner.errors):,}")

        self._log("")
        self._log("=" * 65, "header")
        self._log(f"  SCAN COMPLETE", "success")
        self._log(f"  Files scanned  : {self.scanner.files_scanned:,}", "info")
        self._log(f"  Folders scanned: {self.scanner.dirs_scanned:,}", "info")
        self._log(f"  Matches found  : {self.scanner.match_count:,}", "info")
        self._log(f"  Errors         : {len(self.scanner.errors):,}", "info")

        if results:
            self.export_btn.config(state="normal")

            by_file = {}
            for r in results:
                fp = r["file_path"]
                if fp not in by_file:
                    by_file[fp] = []
                by_file[fp].append(r["keyword"])

            self._log(f"  Unique files   : {len(by_file):,}", "info")
            self._log("=" * 65, "header")

            # Keyword summary
            self._log("\n  KEYWORD SUMMARY:", "header")
            kw_counts = {}
            for r in results:
                kw_counts[r["keyword"]] = kw_counts.get(r["keyword"], 0) + 1
            for kw, count in sorted(kw_counts.items(), key=lambda x: -x[1]):
                self._log(f"    {kw:40s}  {count} files", "info")
        else:
            self._log("=" * 65, "header")
            self._log("\n  No matches found. Try broadening your keywords or "
                       "checking a different folder.", "info")

        if self.scanner.errors:
            self._log(f"\n  ACCESS ERRORS ({len(self.scanner.errors)}):", "keyword")
            for err in self.scanner.errors[:10]:
                self._log(f"    {err}", "info")
            if len(self.scanner.errors) > 10:
                self._log(f"    ... and {len(self.scanner.errors) - 10} more", "info")

        self.status_var.set(
            f"Done. {self.scanner.files_scanned:,} files, "
            f"{self.scanner.dirs_scanned:,} folders, "
            f"{self.scanner.match_count:,} matches. "
            f"{'Click Export to save.' if results else ''}"
        )

    def _export_csv(self):
        if not self.scanner or not self.scanner.results:
            return

        output_path = filedialog.asksaveasfilename(
            title="Save Results As",
            defaultextension=".csv",
            filetypes=[("CSV File", "*.csv")],
            initialfile=f"filename_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not output_path:
            return

        try:
            write_results_to_csv(self.scanner.results, output_path)
            self._log(f"\n  CSV saved: {output_path}", "success")
            messagebox.showinfo("Export Complete", f"Results saved to:\n{output_path}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))


# =============================================================================
#  MAIN
# =============================================================================

def main():
    root = tk.Tk()
    app = ScannerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()