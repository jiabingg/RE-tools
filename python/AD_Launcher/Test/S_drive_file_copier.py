#!/usr/bin/env python3
"""
CSV File Copier
===============
Reads a CSV that contains a column of full file paths, copies every file to a
target folder, and when two rows point at files with the same name, keeps only
the newer one.

Standard library only (tkinter + csv + shutil) -- no pip install required.

Usage:
    python file_copier.py            # opens the GUI
    python file_copier.py --cli ...  # see --help for the headless mode
"""

import argparse
import csv
import os
import queue
import shutil
import sys
import threading
import time
from datetime import datetime

# --------------------------------------------------------------------------
# Core logic (no GUI dependencies -- importable and testable on its own)
# --------------------------------------------------------------------------

PATH_HINTS = ("path", "file", "full", "location", "source", "index_level")


def long_path(p):
    """Wrap a Windows path so it can exceed the 260 character MAX_PATH limit."""
    if os.name != "nt":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):                 # UNC share  \\server\share\...
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def normalize(p):
    """Turn a path from the CSV into something the local OS understands."""
    p = str(p).strip().strip('"')
    if os.name != "nt":
        # Handy for testing on Linux/Mac: treat backslashes as separators.
        p = p.replace("\\", "/")
    return p


def read_csv_rows(csv_path):
    """Read the CSV with encoding fallbacks. Returns (headers, list-of-dicts)."""
    last_err = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(csv_path, "r", encoding=enc, newline="") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
                return (reader.fieldnames or []), rows
        except UnicodeDecodeError as err:
            last_err = err
    raise last_err


def looks_like_path(value):
    v = str(value or "")
    if len(v) < 4:
        return False
    has_sep = ("\\" in v) or ("/" in v)
    ext = os.path.splitext(v)[1]
    has_ext = 1 < len(ext) <= 6
    return has_sep and has_ext


def detect_path_column(headers, rows, sample=200):
    """Pick the column most likely to hold file paths. Returns (name, scores)."""
    scores = {}
    subset = rows[:sample]
    for col in headers:
        if not subset:
            scores[col] = 0.0
            continue
        hits = sum(1 for r in subset if looks_like_path(r.get(col)))
        score = hits / len(subset)
        # Small nudge for column names that sound like a path column.
        if any(h in col.lower() for h in PATH_HINTS):
            score += 0.05
        scores[col] = score
    best = max(scores, key=scores.get) if scores else None
    if best is not None and scores[best] < 0.5:
        best = None
    return best, scores


def parse_csv_timestamp(value):
    """Best-effort conversion of a CSV date cell to a POSIX timestamp."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    try:
        n = float(s)
        if n > 1e17:        # nanoseconds
            return n / 1e9
        if n > 1e14:        # microseconds
            return n / 1e6
        if n > 1e11:        # milliseconds
            return n / 1e3
        if n > 1e8:         # seconds
            return n
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:19], fmt).timestamp()
        except ValueError:
            continue
    return None


def fmt_time(ts):
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


class PlannedFile:
    __slots__ = ("source", "name", "mtime", "size", "rel", "exists", "row")

    def __init__(self, source, name, mtime, size, rel, exists, row):
        self.source = source
        self.name = name
        self.mtime = mtime
        self.size = size
        self.rel = rel
        self.exists = exists
        self.row = row


def build_plan(rows, path_col, flatten=True, date_col=None, prefer_csv_date=False):
    """
    Inspect every path in the CSV and decide what to copy.

    Returns a dict with:
        to_copy  - list of PlannedFile
        missing  - list of (path, reason)
        dupes    - list of (loser PlannedFile, winner PlannedFile)
        blank    - count of empty path cells
    """
    seen = {}
    missing = []
    dupes = []
    blank = 0

    # When preserving structure, mirror everything below the common root.
    common_root = ""
    if not flatten:
        paths = [normalize(r.get(path_col, "")) for r in rows if r.get(path_col)]
        dirs = [os.path.dirname(p) for p in paths]
        if dirs:
            try:
                common_root = os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]
            except ValueError:
                common_root = ""

    for row in rows:
        raw = row.get(path_col, "")
        if not str(raw).strip():
            blank += 1
            continue

        src = normalize(raw)
        try:
            st = os.stat(long_path(src))
            mtime, size, exists = st.st_mtime, st.st_size, True
        except OSError as err:
            mtime, size, exists = None, None, False
            missing.append((src, err.strerror or str(err)))

        if prefer_csv_date and date_col:
            csv_ts = parse_csv_timestamp(row.get(date_col))
            if csv_ts:
                mtime = csv_ts

        name = os.path.basename(src)
        if flatten:
            rel = name
        else:
            rel = os.path.relpath(src, common_root) if common_root else name

        pf = PlannedFile(src, name, mtime, size, rel, exists, row)
        if not exists:
            continue

        key = rel.lower() if os.name == "nt" else rel
        prior = seen.get(key)
        if prior is None:
            seen[key] = pf
        else:
            newer, older = (pf, prior) if (pf.mtime or 0) > (prior.mtime or 0) else (prior, pf)
            seen[key] = newer
            dupes.append((older, newer))

    return {
        "to_copy": list(seen.values()),
        "missing": missing,
        "dupes": dupes,
        "blank": blank,
        "common_root": common_root,
    }


def copy_files(plan, target, on_existing="newer", dry_run=False,
               progress=None, should_stop=None):
    """
    Copy the planned files into `target`.

    on_existing: "newer" (overwrite only if source is newer), "overwrite", "skip"
    progress(done, total, message) is called after each file.
    should_stop() may return True to abort early.
    """
    results = {"copied": [], "replaced": [], "skipped": [], "failed": []}
    files = plan["to_copy"]
    total = len(files)

    for i, pf in enumerate(files, 1):
        if should_stop and should_stop():
            break

        dest = os.path.join(target, pf.rel)
        note = ""
        try:
            dest_exists = os.path.exists(long_path(dest))
            if dest_exists:
                if on_existing == "skip":
                    results["skipped"].append((pf, "already in target"))
                    note = "skip (exists)"
                elif on_existing == "newer":
                    dst_mtime = os.stat(long_path(dest)).st_mtime
                    if (pf.mtime or 0) <= dst_mtime + 1:
                        results["skipped"].append((pf, "target copy is same age or newer"))
                        note = "skip (target newer)"
                    else:
                        if not dry_run:
                            os.makedirs(os.path.dirname(dest) or target, exist_ok=True)
                            shutil.copy2(long_path(pf.source), long_path(dest))
                        results["replaced"].append(pf)
                        note = "replaced (source newer)"
                else:  # overwrite
                    if not dry_run:
                        os.makedirs(os.path.dirname(dest) or target, exist_ok=True)
                        shutil.copy2(long_path(pf.source), long_path(dest))
                    results["replaced"].append(pf)
                    note = "overwritten"
            else:
                if not dry_run:
                    os.makedirs(os.path.dirname(dest) or target, exist_ok=True)
                    shutil.copy2(long_path(pf.source), long_path(dest))
                results["copied"].append(pf)
                note = "copied"
        except Exception as err:            # noqa: BLE001 - surfaced in the log
            results["failed"].append((pf, str(err)))
            note = f"FAILED: {err}"

        if progress:
            progress(i, total, f"{note}: {pf.name}")

    return results


def write_report(path, plan, results):
    """Write a CSV audit trail of everything that happened."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "file_name", "source_path", "source_modified",
                    "size_bytes", "detail"])
        for pf in results["copied"]:
            w.writerow(["copied", pf.name, pf.source, fmt_time(pf.mtime), pf.size, ""])
        for pf in results["replaced"]:
            w.writerow(["replaced", pf.name, pf.source, fmt_time(pf.mtime), pf.size,
                        "source was newer than the file already in the target"])
        for pf, why in results["skipped"]:
            w.writerow(["skipped", pf.name, pf.source, fmt_time(pf.mtime), pf.size, why])
        for older, newer in plan["dupes"]:
            w.writerow(["duplicate_not_copied", older.name, older.source,
                        fmt_time(older.mtime), older.size,
                        f"newer copy used instead: {newer.source} ({fmt_time(newer.mtime)})"])
        for pf, err in results["failed"]:
            w.writerow(["failed", pf.name, pf.source, fmt_time(pf.mtime), pf.size, err])
        for src, why in plan["missing"]:
            w.writerow(["not_found", os.path.basename(src), src, "", "", why])


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App:
        def __init__(self, root):
            self.root = root
            root.title("CSV File Copier")
            root.geometry("880x640")
            root.minsize(760, 560)

            self.csv_path = tk.StringVar()
            self.target = tk.StringVar()
            self.path_col = tk.StringVar()
            self.date_col = tk.StringVar()
            self.flatten = tk.BooleanVar(value=True)
            self.prefer_csv_date = tk.BooleanVar(value=False)
            self.on_existing = tk.StringVar(value="newer")
            self.dry_run = tk.BooleanVar(value=False)
            self.status = tk.StringVar(value="Choose a CSV file to begin.")

            self.headers, self.rows = [], []
            self.plan = None
            self.msgq = queue.Queue()
            self.worker = None
            self.stop_flag = False

            self._build()
            self.root.after(100, self._drain_queue)

        # ---------------- layout ----------------
        def _build(self):
            pad = {"padx": 10, "pady": 6}

            top = ttk.LabelFrame(self.root, text="1. Input")
            top.pack(fill="x", **pad)
            ttk.Label(top, text="CSV file:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
            ttk.Entry(top, textvariable=self.csv_path).grid(row=0, column=1, sticky="ew", pady=6)
            ttk.Button(top, text="Browse...", command=self.pick_csv).grid(row=0, column=2, padx=8)

            ttk.Label(top, text="Path column:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
            self.col_combo = ttk.Combobox(top, textvariable=self.path_col, state="readonly")
            self.col_combo.grid(row=1, column=1, sticky="ew", pady=6)
            self.col_combo.bind("<<ComboboxSelected>>", lambda e: self.invalidate())

            ttk.Label(top, text="Target folder:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
            ttk.Entry(top, textvariable=self.target).grid(row=2, column=1, sticky="ew", pady=6)
            ttk.Button(top, text="Browse...", command=self.pick_target).grid(row=2, column=2, padx=8)
            top.columnconfigure(1, weight=1)

            opts = ttk.LabelFrame(self.root, text="2. Options")
            opts.pack(fill="x", **pad)
            ttk.Checkbutton(opts, text="Flatten everything into one folder",
                            variable=self.flatten, command=self.invalidate
                            ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
            ttk.Checkbutton(opts, text="Preview only (don't copy anything)",
                            variable=self.dry_run).grid(row=0, column=1, sticky="w", padx=8, pady=4)

            ttk.Label(opts, text="If the file already exists in the target:").grid(
                row=1, column=0, sticky="w", padx=8, pady=4)
            box = ttk.Frame(opts)
            box.grid(row=1, column=1, sticky="w")
            for label, val in (("Keep newer", "newer"), ("Always overwrite", "overwrite"),
                               ("Always skip", "skip")):
                ttk.Radiobutton(box, text=label, value=val,
                                variable=self.on_existing).pack(side="left", padx=6)

            ttk.Checkbutton(opts, text="Judge 'newer' using this CSV date column instead of the file's timestamp:",
                            variable=self.prefer_csv_date, command=self.invalidate
                            ).grid(row=2, column=0, sticky="w", padx=8, pady=4)
            self.date_combo = ttk.Combobox(opts, textvariable=self.date_col, state="readonly", width=24)
            self.date_combo.grid(row=2, column=1, sticky="w", padx=8)
            self.date_combo.bind("<<ComboboxSelected>>", lambda e: self.invalidate())
            opts.columnconfigure(1, weight=1)

            act = ttk.Frame(self.root)
            act.pack(fill="x", **pad)
            self.analyze_btn = ttk.Button(act, text="Analyze", command=self.analyze)
            self.analyze_btn.pack(side="left")
            self.copy_btn = ttk.Button(act, text="Copy Files", command=self.start_copy, state="disabled")
            self.copy_btn.pack(side="left", padx=8)
            self.stop_btn = ttk.Button(act, text="Stop", command=self.stop, state="disabled")
            self.stop_btn.pack(side="left")
            ttk.Button(act, text="Clear Log", command=lambda: self.log_box.delete("1.0", "end")
                       ).pack(side="right")

            self.bar = ttk.Progressbar(self.root, mode="determinate")
            self.bar.pack(fill="x", padx=10)
            ttk.Label(self.root, textvariable=self.status).pack(anchor="w", padx=12, pady=4)

            logf = ttk.LabelFrame(self.root, text="Log")
            logf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            self.log_box = tk.Text(logf, wrap="none", height=14)
            ybar = ttk.Scrollbar(logf, orient="vertical", command=self.log_box.yview)
            self.log_box.configure(yscrollcommand=ybar.set)
            ybar.pack(side="right", fill="y")
            self.log_box.pack(side="left", fill="both", expand=True)

        # ---------------- helpers ----------------
        def log(self, text):
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")

        def invalidate(self):
            self.plan = None
            self.copy_btn.config(state="disabled")

        def pick_csv(self):
            path = filedialog.askopenfilename(
                title="Select the CSV file",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
            if not path:
                return
            self.csv_path.set(path)
            self.load_csv(path)

        def pick_target(self):
            path = filedialog.askdirectory(title="Select the target folder")
            if path:
                self.target.set(path)
                self.invalidate()

        def load_csv(self, path):
            try:
                self.headers, self.rows = read_csv_rows(path)
            except Exception as err:                        # noqa: BLE001
                messagebox.showerror("Could not read CSV", str(err))
                return
            self.col_combo["values"] = self.headers
            self.date_combo["values"] = [""] + self.headers

            best, scores = detect_path_column(self.headers, self.rows)
            if best:
                self.path_col.set(best)
                self.log(f"Loaded {len(self.rows):,} rows. Detected path column: "
                         f"'{best}' ({scores[best]*100:.0f}% of sampled values look like paths).")
            else:
                self.path_col.set(self.headers[-1] if self.headers else "")
                self.log(f"Loaded {len(self.rows):,} rows. Could not confidently detect the "
                         f"path column -- please pick it from the dropdown.")

            for cand in self.headers:
                if "date" in cand.lower() or "time" in cand.lower():
                    self.date_col.set(cand)
                    break
            self.invalidate()
            self.status.set("CSV loaded. Pick a target folder, then click Analyze.")

        # ---------------- actions ----------------
        def analyze(self):
            if not self.rows:
                messagebox.showwarning("No CSV", "Load a CSV file first.")
                return
            if not self.path_col.get():
                messagebox.showwarning("No column", "Select the column that holds the file paths.")
                return

            self.status.set("Checking every path... this can be slow on a network drive.")
            self.root.update_idletasks()
            t0 = time.time()

            self.plan = build_plan(
                self.rows, self.path_col.get(),
                flatten=self.flatten.get(),
                date_col=self.date_col.get() or None,
                prefer_csv_date=self.prefer_csv_date.get())

            p = self.plan
            total_size = sum(pf.size or 0 for pf in p["to_copy"])
            self.log("")
            self.log("=" * 72)
            self.log(f"ANALYSIS  ({time.time() - t0:.1f}s)")
            self.log(f"  Rows in CSV .................. {len(self.rows):,}")
            if p["blank"]:
                self.log(f"  Blank path cells ............. {p['blank']:,}")
            self.log(f"  Files that will be copied .... {len(p['to_copy']):,}  ({fmt_size(total_size)})")
            self.log(f"  Duplicate names dropped ...... {len(p['dupes']):,}  (older copy skipped)")
            self.log(f"  Paths not reachable .......... {len(p['missing']):,}")

            for older, newer in p["dupes"][:15]:
                self.log(f"    dup '{older.name}': keeping {fmt_time(newer.mtime)} "
                         f"over {fmt_time(older.mtime)}")
            if len(p["dupes"]) > 15:
                self.log(f"    ... and {len(p['dupes']) - 15} more duplicates")
            for src, why in p["missing"][:10]:
                self.log(f"    missing: {src}  [{why}]")
            if len(p["missing"]) > 10:
                self.log(f"    ... and {len(p['missing']) - 10} more unreachable paths")
            self.log("=" * 72)

            self.status.set(f"Ready: {len(p['to_copy']):,} files ({fmt_size(total_size)}) to copy.")
            if p["to_copy"] and self.target.get():
                self.copy_btn.config(state="normal")
            elif not self.target.get():
                self.status.set("Analysis done -- now choose a target folder.")

        def start_copy(self):
            target = self.target.get()
            if not target:
                messagebox.showwarning("No target", "Choose a target folder first.")
                return
            if not self.plan:
                self.analyze()
                if not self.plan:
                    return
            try:
                os.makedirs(target, exist_ok=True)
            except Exception as err:                        # noqa: BLE001
                messagebox.showerror("Bad target folder", str(err))
                return

            n = len(self.plan["to_copy"])
            verb = "Preview" if self.dry_run.get() else "Copy"
            if not messagebox.askyesno(f"{verb} {n} files?",
                                       f"{verb} {n:,} files to:\n{target}\n\nContinue?"):
                return

            self.stop_flag = False
            self.analyze_btn.config(state="disabled")
            self.copy_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.bar["maximum"] = n
            self.bar["value"] = 0
            self.log("")
            self.log(f"--- {'DRY RUN' if self.dry_run.get() else 'COPY'} started "
                     f"{datetime.now():%Y-%m-%d %H:%M:%S} ---")

            self.worker = threading.Thread(target=self._run_copy, args=(target,), daemon=True)
            self.worker.start()

        def _run_copy(self, target):
            def progress(done, total, msg):
                self.msgq.put(("progress", (done, total, msg)))

            try:
                results = copy_files(self.plan, target,
                                     on_existing=self.on_existing.get(),
                                     dry_run=self.dry_run.get(),
                                     progress=progress,
                                     should_stop=lambda: self.stop_flag)
                report = None
                if not self.dry_run.get():
                    report = os.path.join(
                        target, f"_copy_report_{datetime.now():%Y%m%d_%H%M%S}.csv")
                    try:
                        write_report(report, self.plan, results)
                    except Exception as err:                # noqa: BLE001
                        self.msgq.put(("log", f"Could not write report: {err}"))
                        report = None
                self.msgq.put(("done", (results, report)))
            except Exception as err:                        # noqa: BLE001
                self.msgq.put(("error", str(err)))

        def stop(self):
            self.stop_flag = True
            self.status.set("Stopping after the current file...")

        def _drain_queue(self):
            try:
                while True:
                    kind, payload = self.msgq.get_nowait()
                    if kind == "progress":
                        done, total, msg = payload
                        self.bar["value"] = done
                        self.status.set(f"{done:,} / {total:,}  -  {msg}")
                        if done <= 400 or done % 25 == 0 or msg.startswith("FAILED"):
                            self.log(f"  [{done}/{total}] {msg}")
                    elif kind == "log":
                        self.log(payload)
                    elif kind == "error":
                        self.log(f"ERROR: {payload}")
                        messagebox.showerror("Copy failed", payload)
                        self._finish()
                    elif kind == "done":
                        results, report = payload
                        self._summarize(results, report)
                        self._finish()
            except queue.Empty:
                pass
            self.root.after(100, self._drain_queue)

        def _summarize(self, results, report):
            copied = len(results["copied"])
            replaced = len(results["replaced"])
            skipped = len(results["skipped"])
            failed = len(results["failed"])
            moved_bytes = sum((pf.size or 0) for pf in results["copied"] + results["replaced"])

            self.log("")
            self.log("=" * 72)
            self.log("RESULT" + ("  (dry run -- nothing was written)" if self.dry_run.get() else ""))
            self.log(f"  Copied ....................... {copied:,}")
            self.log(f"  Replaced (source newer) ...... {replaced:,}")
            self.log(f"  Skipped ...................... {skipped:,}")
            self.log(f"  Duplicates dropped ........... {len(self.plan['dupes']):,}")
            self.log(f"  Failed ....................... {failed:,}")
            self.log(f"  Not found .................... {len(self.plan['missing']):,}")
            self.log(f"  Data transferred ............. {fmt_size(moved_bytes)}")
            for pf, err in results["failed"][:20]:
                self.log(f"    FAILED {pf.name}: {err}")
            if report:
                self.log(f"  Report written to: {report}")
            self.log("=" * 72)

            self.status.set(f"Done. {copied + replaced:,} files written, "
                            f"{skipped:,} skipped, {failed:,} failed.")
            messagebox.showinfo(
                "Finished",
                f"Copied: {copied:,}\nReplaced: {replaced:,}\nSkipped: {skipped:,}\n"
                f"Duplicates dropped: {len(self.plan['dupes']):,}\nFailed: {failed:,}\n"
                f"Not found: {len(self.plan['missing']):,}"
                + (f"\n\nReport: {os.path.basename(report)}" if report else ""))

        def _finish(self):
            self.analyze_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.copy_btn.config(state="normal" if self.plan and self.plan["to_copy"] else "disabled")

    root = tk.Tk()
    try:
        ttk_style = __import__("tkinter.ttk", fromlist=["Style"]).Style()
        if "vista" in ttk_style.theme_names():
            ttk_style.theme_use("vista")
        elif "clam" in ttk_style.theme_names():
            ttk_style.theme_use("clam")
    except Exception:                                       # noqa: BLE001
        pass
    App(root)
    root.mainloop()


# --------------------------------------------------------------------------
# Headless mode
# --------------------------------------------------------------------------

def run_cli(args):
    headers, rows = read_csv_rows(args.csv)
    col = args.column or detect_path_column(headers, rows)[0]
    if not col:
        print("Could not detect the path column. Pass --column NAME.")
        print("Available columns:", ", ".join(headers))
        return 1
    print(f"Path column: {col}   Rows: {len(rows):,}")

    plan = build_plan(rows, col, flatten=not args.keep_structure)
    print(f"To copy: {len(plan['to_copy']):,}   "
          f"Duplicates dropped: {len(plan['dupes']):,}   "
          f"Not found: {len(plan['missing']):,}")

    if args.analyze_only:
        for older, newer in plan["dupes"][:20]:
            print(f"  dup {older.name}: keep {fmt_time(newer.mtime)} over {fmt_time(older.mtime)}")
        return 0

    os.makedirs(args.target, exist_ok=True)
    results = copy_files(plan, args.target, on_existing=args.on_existing,
                         dry_run=args.dry_run,
                         progress=lambda d, t, m: print(f"[{d}/{t}] {m}"))
    print(f"\nCopied {len(results['copied'])}, replaced {len(results['replaced'])}, "
          f"skipped {len(results['skipped'])}, failed {len(results['failed'])}")
    if not args.dry_run:
        report = os.path.join(args.target, f"_copy_report_{datetime.now():%Y%m%d_%H%M%S}.csv")
        write_report(report, plan, results)
        print("Report:", report)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Copy files listed in a CSV to a target folder.")
    ap.add_argument("--cli", action="store_true", help="run without the GUI")
    ap.add_argument("--csv", help="path to the CSV file")
    ap.add_argument("--target", help="destination folder")
    ap.add_argument("--column", help="name of the column holding file paths")
    ap.add_argument("--keep-structure", action="store_true",
                    help="mirror the source folder tree instead of flattening")
    ap.add_argument("--on-existing", choices=["newer", "overwrite", "skip"], default="newer")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args()

    if args.cli:
        if not args.csv or not (args.target or args.analyze_only):
            ap.error("--cli needs --csv and --target (or --analyze-only)")
        return run_cli(args)
    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
