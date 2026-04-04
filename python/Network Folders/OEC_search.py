#!/usr/bin/env python3
"""
Injection Water Analysis PDF Scanner
=====================================
Scans folders for PDF files matching a filename keyword,
then checks PDF content for specified search keywords.

Progress tracking:
  - scan_progress.json tracks each folder (not_scanned / scanned).
    Saved every 10 folders completed.
  - OEC_output.json accumulates every matched PDF with its location
    and matched keywords. Saved immediately on each new match.
"""

import os
import sys
import json
import csv
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF not found. Installing...")
    os.system(f"{sys.executable} -m pip install PyMuPDF --break-system-packages -q")
    import fitz


# ─── Configuration ───────────────────────────────────────────────────────────

PROGRESS_FILENAME = "scan_progress.json"
OUTPUT_FILENAME = "OEC_output.json"
RESULTS_CSV_FILENAME = "scan_results.csv"

DEFAULT_FILENAME_KEYWORDS = [
    "OEC",
    "Water Analysis",
    "Injectate Analysis",
]
DEFAULT_CONTENT_KEYWORDS = [
    "UIC Water Testing",
    "Oilfield Environmental & Compliance",
]

FOLDER_SAVE_INTERVAL = 10  # save progress every N folders scanned


# ─── Progress Tracker (folder-level) ─────────────────────────────────────────

class ProgressTracker:
    """
    Tracks scan progress at the FOLDER level in a JSON file.

    Each subfolder stores which root folder it belongs to, so results
    can be grouped by root folder later.

    Structure:
    {
        "created": "...",
        "last_updated": "...",
        "filename_keywords": [...],
        "content_keywords": [...],
        "root_folders_completed": ["/path/to/root1", ...],
        "folders": {
            "/path/to/subfolder": {
                "status": "not_scanned" | "scanned",
                "root_folder": "/path/to/root"
            },
            ...
        }
    }
    """

    def __init__(self, progress_path: str):
        self.path = progress_path
        self.data = {
            "created": datetime.now().isoformat(),
            "last_updated": None,
            "filename_keywords": [],
            "content_keywords": [],
            "root_folders_completed": [],
            "folders": {},
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if "folders" in saved:
                    self.data = saved
                    if "root_folders_completed" not in self.data:
                        self.data["root_folders_completed"] = []
                    # Migration: convert old string status to dict format
                    for fp, val in list(self.data["folders"].items()):
                        if isinstance(val, str):
                            self.data["folders"][fp] = {
                                "status": val,
                                "root_folder": "",
                            }
            except (json.JSONDecodeError, IOError):
                pass

    def save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except IOError:
            pass

    def check_keywords_changed(self, filename_keywords: list,
                                content_keywords: list) -> bool:
        saved_fn = sorted(self.data.get("filename_keywords", []))
        saved_ct = sorted(self.data.get("content_keywords", []))
        new_fn = sorted(filename_keywords)
        new_ct = sorted(content_keywords)

        if saved_fn != new_fn or saved_ct != new_ct:
            for fp in self.data["folders"]:
                if isinstance(self.data["folders"][fp], dict):
                    self.data["folders"][fp]["status"] = "not_scanned"
                else:
                    self.data["folders"][fp] = {
                        "status": "not_scanned", "root_folder": ""
                    }
            self.data["root_folders_completed"] = []
            self.data["filename_keywords"] = filename_keywords
            self.data["content_keywords"] = content_keywords
            self.save()
            return True
        return False

    def set_keywords(self, filename_keywords: list, content_keywords: list):
        self.data["filename_keywords"] = filename_keywords
        self.data["content_keywords"] = content_keywords

    # ── Root folder tracking ─────────────────────────────────────────────

    def is_root_completed(self, root_folder: str) -> bool:
        norm = os.path.normpath(root_folder)
        return norm in self.data["root_folders_completed"]

    def mark_root_completed(self, root_folder: str):
        norm = os.path.normpath(root_folder)
        if norm not in self.data["root_folders_completed"]:
            self.data["root_folders_completed"].append(norm)

    def get_new_root_folders(self, requested_roots: list) -> list:
        return [
            r for r in requested_roots
            if not self.is_root_completed(os.path.normpath(r.strip()))
        ]

    # ── Subfolder tracking ───────────────────────────────────────────────

    def add_folder(self, folder_path: str, root_folder: str = ""):
        """Register a folder as not_scanned with its root_folder."""
        if folder_path not in self.data["folders"]:
            self.data["folders"][folder_path] = {
                "status": "not_scanned",
                "root_folder": root_folder,
            }

    def get_root_for_folder(self, folder_path: str) -> str:
        entry = self.data["folders"].get(folder_path)
        if isinstance(entry, dict):
            return entry.get("root_folder", "")
        return ""

    def is_folder_scanned(self, folder_path: str) -> bool:
        entry = self.data["folders"].get(folder_path)
        if isinstance(entry, dict):
            return entry.get("status") == "scanned"
        return False

    def mark_folder_scanned(self, folder_path: str):
        if isinstance(self.data["folders"].get(folder_path), dict):
            self.data["folders"][folder_path]["status"] = "scanned"
        else:
            self.data["folders"][folder_path] = {
                "status": "scanned", "root_folder": ""
            }

    def get_unscanned_folders(self) -> list:
        result = []
        for fp, val in self.data["folders"].items():
            if isinstance(val, dict):
                if val.get("status") == "not_scanned":
                    result.append(fp)
            elif val == "not_scanned":
                result.append(fp)
        return result

    def get_stats(self) -> dict:
        total = 0
        scanned = 0
        for val in self.data["folders"].values():
            total += 1
            if isinstance(val, dict):
                if val.get("status") == "scanned":
                    scanned += 1
            elif val == "scanned":
                scanned += 1
        return {
            "total_folders": total,
            "scanned_folders": scanned,
            "remaining_folders": total - scanned,
        }

    def reset(self):
        self.data["folders"] = {}
        self.data["filename_keywords"] = []
        self.data["content_keywords"] = []
        self.data["root_folders_completed"] = []
        self.data["created"] = datetime.now().isoformat()
        self.save()


# ─── Output Tracker (matched PDFs) ──────────────────────────────────────────

class OutputTracker:
    """
    Maintains OEC_output.json — a list of every PDF whose filename matched.

    Each entry includes root_folder so results can be filtered/grouped
    by root folder in the future.

    Save strategy: caller is responsible for calling save() at the right
    intervals (e.g. every 5% of folders scanned). This avoids disk I/O
    on every single file match.

    Structure:
    {
        "created": "...",
        "last_updated": "...",
        "total_entries": 5,
        "entries": [
            {
                "filename": "...",
                "folder": "...",
                "filepath": "...",
                "root_folder": "...",
                "match_status": "content_confirmed" | "filename_match_only",
                "content_keywords_found": [...],
                "found_at": "..."
            },
            ...
        ]
    }
    """

    def __init__(self, output_path: str):
        self.path = output_path
        self.data = {
            "created": datetime.now().isoformat(),
            "last_updated": None,
            "total_entries": 0,
            "entries": [],
        }
        self._index = {}  # filepath -> index in entries list
        self._dirty = False
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if "entries" in saved:
                    self.data = saved
                elif "matches" in saved:
                    saved["entries"] = saved.pop("matches")
                    saved["total_entries"] = saved.pop("total_matches",
                                                        len(saved["entries"]))
                    self.data = saved
                else:
                    return

                # Migration: ensure all entries have current fields
                for entry in self.data["entries"]:
                    if "match_status" not in entry:
                        # Old format had "matched_keywords" instead
                        old_kws = entry.pop("matched_keywords", [])
                        if old_kws:
                            entry["match_status"] = "content_confirmed"
                            entry["content_keywords_found"] = old_kws
                        else:
                            entry["match_status"] = "filename_match_only"
                            entry["content_keywords_found"] = []
                    if "content_keywords_found" not in entry:
                        entry["content_keywords_found"] = []
                    if "root_folder" not in entry:
                        entry["root_folder"] = ""

                self._index = {
                    m["filepath"]: i
                    for i, m in enumerate(self.data["entries"])
                }
            except (json.JSONDecodeError, IOError):
                pass

    def save(self):
        """Write to disk. Call this at save intervals, not on every add."""
        if not self._dirty:
            return
        self.data["last_updated"] = datetime.now().isoformat()
        self.data["total_entries"] = len(self.data["entries"])
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            self._dirty = False
        except IOError:
            pass

    def force_save(self):
        """Force save even if not dirty."""
        self._dirty = True
        self.save()

    def add_entry(self, filepath: str, match_status: str,
                  root_folder: str = "",
                  content_keywords_found: list = None):
        """
        Add or update an entry in memory. Does NOT save to disk —
        caller must call save() at the appropriate interval.
        """
        if content_keywords_found is None:
            content_keywords_found = []

        if filepath in self._index:
            idx = self._index[filepath]
            existing = self.data["entries"][idx]
            if (existing.get("match_status") == "filename_match_only"
                    and match_status == "content_confirmed"):
                existing["match_status"] = "content_confirmed"
                existing["content_keywords_found"] = content_keywords_found
                if root_folder:
                    existing["root_folder"] = root_folder
                self._dirty = True
            return

        self._index[filepath] = len(self.data["entries"])
        self.data["entries"].append({
            "filename": os.path.basename(filepath),
            "folder": os.path.dirname(filepath),
            "filepath": filepath,
            "root_folder": root_folder,
            "match_status": match_status,
            "content_keywords_found": content_keywords_found,
            "found_at": datetime.now().isoformat(),
        })
        self._dirty = True

    def get_entries(self) -> list:
        return self.data["entries"]

    def get_stats(self) -> dict:
        total = len(self.data["entries"])
        confirmed = sum(
            1 for e in self.data["entries"]
            if e.get("match_status") == "content_confirmed"
        )
        return {
            "total_entries": total,
            "content_confirmed": confirmed,
            "needs_review": total - confirmed,
        }

    def reset(self):
        self.data["entries"] = []
        self.data["total_entries"] = 0
        self.data["created"] = datetime.now().isoformat()
        self._index.clear()
        self._dirty = True
        self.save()


# ─── Scanner Engine ──────────────────────────────────────────────────────────

class ScannerEngine:
    """Handles folder discovery, PDF matching, and content scanning."""

    def __init__(self, tracker: ProgressTracker, output: OutputTracker):
        self.tracker = tracker
        self.output = output
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    # ── Phase 1: Discover all subfolders ─────────────────────────────────

    def discover_folders(self, root_folders: list, on_progress=None) -> int:
        """
        Walk only NEW root folders (skip roots already completed).
        Register every directory as 'not_scanned' with its root_folder.
        Save every 10 folders. Returns total number of new folders discovered.
        """
        new_roots = self.tracker.get_new_root_folders(root_folders)
        count = 0
        for root_folder in new_roots:
            root_folder = root_folder.strip()
            if not root_folder or not os.path.isdir(root_folder):
                continue
            norm_root = os.path.normpath(root_folder)
            for dirpath, dirnames, _files in os.walk(root_folder):
                if self._stop_flag:
                    self.tracker.save()
                    return count
                norm_path = os.path.normpath(dirpath)
                self.tracker.add_folder(norm_path, root_folder=norm_root)
                count += 1
                if on_progress:
                    on_progress(norm_path, count)
                if count % FOLDER_SAVE_INTERVAL == 0:
                    self.tracker.save()

        self.tracker.save()
        return count

    # ── Phase 2: Scan each unscanned folder ──────────────────────────────

    def scan_folders(self, filename_keywords: list, content_keywords: list,
                     root_folders: list,
                     on_folder_start=None, on_folder_done=None,
                     on_file_match=None) -> dict:
        """
        For each unscanned folder:
          1. Find PDFs whose filename contains ANY of the filename_keywords
          2. Open each PDF and check for content_keywords
          3. Record in OutputTracker with root_folder
          4. Mark folder as scanned
          5. Save progress every 10 folders
          6. Save output every 5% of total folders
          7. After scanning, check if any root folder is now fully complete

        Returns stats dict.
        """
        fn_kws_lower = [kw.strip().lower() for kw in filename_keywords if kw.strip()]
        content_kws_lower = [kw.strip().lower() for kw in content_keywords if kw.strip()]
        folders_to_scan = self.tracker.get_unscanned_folders()
        total_folders = len(folders_to_scan)
        folders_done = 0
        files_scanned = 0
        files_matched = 0
        files_errored = 0

        # Calculate 5% save interval (minimum 1 folder)
        output_save_interval = max(1, total_folders // 20)

        for idx, folder_path in enumerate(folders_to_scan, 1):
            if self._stop_flag:
                self.tracker.save()
                self.output.force_save()
                break

            if on_folder_start:
                on_folder_start(folder_path, idx, total_folders)

            # Get the root folder this subfolder belongs to
            root_folder = self.tracker.get_root_for_folder(folder_path)
            # Fallback: derive root from the root_folders list
            if not root_folder:
                norm_fp = os.path.normpath(folder_path)
                for rf in root_folders:
                    norm_rf = os.path.normpath(rf.strip())
                    if norm_fp == norm_rf or norm_fp.startswith(norm_rf + os.sep):
                        root_folder = norm_rf
                        break

            # Find matching PDFs in this specific folder (not recursive)
            try:
                entries = os.listdir(folder_path)
            except (PermissionError, OSError):
                self.tracker.mark_folder_scanned(folder_path)
                folders_done += 1
                if folders_done % FOLDER_SAVE_INTERVAL == 0:
                    self.tracker.save()
                if folders_done % output_save_interval == 0:
                    self.output.force_save()
                continue

            for fname in entries:
                if self._stop_flag:
                    break
                if not fname.lower().endswith(".pdf"):
                    continue
                fname_lower = fname.lower()
                if not any(kw in fname_lower for kw in fn_kws_lower):
                    continue

                full_path = os.path.normpath(os.path.join(folder_path, fname))
                files_scanned += 1

                # Level 1: filename matched — add to output
                self.output.add_entry(full_path, "filename_match_only",
                                      root_folder=root_folder)
                if on_file_match:
                    on_file_match(full_path, [], "filename_match_only")

                # Level 2: try to read content and check for keywords
                try:
                    text = self._extract_text(full_path)
                    text_lower = text.lower()

                    if not text_lower.strip():
                        continue

                    found_keywords = [
                        kw for kw, kw_low in zip(content_keywords, content_kws_lower)
                        if kw_low in text_lower
                    ]
                    if found_keywords:
                        self.output.add_entry(full_path, "content_confirmed",
                                              root_folder=root_folder,
                                              content_keywords_found=found_keywords)
                        files_matched += 1
                        if on_file_match:
                            on_file_match(full_path, found_keywords,
                                          "content_confirmed")
                except Exception:
                    files_errored += 1

            # Folder is done
            self.tracker.mark_folder_scanned(folder_path)
            folders_done += 1

            # Save progress every 10 folders scanned
            if folders_done % FOLDER_SAVE_INTERVAL == 0:
                self.tracker.save()

            # Save output every 5% of total folders scanned
            if folders_done % output_save_interval == 0:
                self.output.force_save()

            if on_folder_done:
                on_folder_done(folder_path, idx, total_folders,
                               files_scanned, files_matched, files_errored)

        # Final save — both progress and output
        self.tracker.save()
        self.output.force_save()

        # Check if any root folders are now fully complete
        for root in root_folders:
            root = root.strip()
            if not root or self.tracker.is_root_completed(root):
                continue
            norm_root = os.path.normpath(root)
            all_done = True
            for fp, val in self.tracker.data["folders"].items():
                if fp == norm_root or fp.startswith(norm_root + os.sep):
                    status = val.get("status") if isinstance(val, dict) else val
                    if status != "scanned":
                        all_done = False
                        break
            if all_done:
                self.tracker.mark_root_completed(root)
        self.tracker.save()

        return {
            "folders_scanned": folders_done,
            "files_scanned": files_scanned,
            "files_matched": files_matched,
            "files_errored": files_errored,
        }

    def _extract_text(self, filepath: str) -> str:
        """Extract all text from a PDF using PyMuPDF."""
        text_parts = []
        doc = fitz.open(filepath)
        try:
            for page in doc:
                text_parts.append(page.get_text())
        finally:
            doc.close()
        return "\n".join(text_parts)


# ─── GUI Application ─────────────────────────────────────────────────────────

class PDFScannerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Injection Water Analysis — PDF Scanner")
        self.root.geometry("1100x780")
        self.root.minsize(900, 650)

        self.tracker = None
        self.output = None
        self.engine = None
        self._scan_thread = None
        self._is_scanning = False

        self._build_ui()

    # ── UI Construction ──────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 9))

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # ── Row 0-1: Folder input ────────────────────────────────────────
        ttk.Label(main, text="Folders to Scan (one per line):",
                  style="Header.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 2))

        folder_frame = ttk.Frame(main)
        folder_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        folder_frame.columnconfigure(0, weight=1)

        self.folder_text = tk.Text(folder_frame, height=4, width=80,
                                   font=("Consolas", 10), wrap="none")
        self.folder_text.grid(row=0, column=0, sticky="nsew")

        folder_scroll = ttk.Scrollbar(folder_frame, orient="vertical",
                                       command=self.folder_text.yview)
        folder_scroll.grid(row=0, column=1, sticky="ns")
        self.folder_text.configure(yscrollcommand=folder_scroll.set)

        btn_frame = ttk.Frame(folder_frame)
        btn_frame.grid(row=0, column=2, sticky="n", padx=(6, 0))
        ttk.Button(btn_frame, text="Add Folder…",
                   command=self._browse_folder).pack(fill="x", pady=(0, 4))
        ttk.Button(btn_frame, text="Clear",
                   command=lambda: self.folder_text.delete("1.0", "end")).pack(fill="x")

        # ── Row 2-3: Filename keywords ────────────────────────────────────
        ttk.Label(main, text="Filename Keywords (one per line — scan PDF if ANY is in filename):",
                  style="Header.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 2))

        fkw_frame = ttk.Frame(main)
        fkw_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        fkw_frame.columnconfigure(0, weight=1)

        self.filename_kw_text = tk.Text(fkw_frame, height=3, width=80,
                                         font=("Consolas", 10), wrap="none")
        self.filename_kw_text.grid(row=0, column=0, sticky="nsew")
        self.filename_kw_text.insert("1.0", "\n".join(DEFAULT_FILENAME_KEYWORDS))

        fkw_scroll = ttk.Scrollbar(fkw_frame, orient="vertical",
                                    command=self.filename_kw_text.yview)
        fkw_scroll.grid(row=0, column=1, sticky="ns")
        self.filename_kw_text.configure(yscrollcommand=fkw_scroll.set)

        # ── Row 4-5: Content keywords ────────────────────────────────────
        ttk.Label(main, text="Content Keywords (one per line — match if ANY is found):",
                  style="Header.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 2))

        ckw_frame = ttk.Frame(main)
        ckw_frame.grid(row=5, column=0, sticky="nsew", pady=(0, 8))
        ckw_frame.columnconfigure(0, weight=1)

        self.content_kw_text = tk.Text(ckw_frame, height=3, width=80,
                                        font=("Consolas", 10), wrap="none")
        self.content_kw_text.grid(row=0, column=0, sticky="nsew")
        self.content_kw_text.insert("1.0", "\n".join(DEFAULT_CONTENT_KEYWORDS))

        ckw_scroll = ttk.Scrollbar(ckw_frame, orient="vertical",
                                    command=self.content_kw_text.yview)
        ckw_scroll.grid(row=0, column=1, sticky="ns")
        self.content_kw_text.configure(yscrollcommand=ckw_scroll.set)

        # ── Row 6: Output file location ─────────────────────────────────
        loc_frame = ttk.Frame(main)
        loc_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        loc_frame.columnconfigure(1, weight=1)

        ttk.Label(loc_frame, text="Output File:",
                  style="Header.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.output_path_var = tk.StringVar(
            value=os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data", OUTPUT_FILENAME
            )
        )
        ttk.Entry(loc_frame, textvariable=self.output_path_var,
                  font=("Consolas", 9)).grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(loc_frame, text="…",
                   command=self._browse_output_file, width=3).grid(row=0, column=2)

        # ── Row 7: Action buttons ────────────────────────────────────────
        action_frame = ttk.Frame(main)
        action_frame.grid(row=7, column=0, sticky="ew", pady=(0, 8))

        self.scan_btn = ttk.Button(action_frame, text="▶  Start Scan",
                                    command=self._start_scan)
        self.scan_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = ttk.Button(action_frame, text="■  Stop",
                                    command=self._stop_scan, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 6))

        self.export_btn = ttk.Button(action_frame, text="Export Results to CSV",
                                      command=self._export_csv, state="disabled")
        self.export_btn.pack(side="left", padx=(0, 6))

        self.reset_btn = ttk.Button(action_frame, text="Reset Progress",
                                     command=self._reset_progress)
        self.reset_btn.pack(side="left", padx=(0, 6))

        # ── Row 8: Status bar ────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(main, textvariable=self.status_var,
                  style="Status.TLabel").grid(row=8, column=0, sticky="w", pady=(0, 4))

        # ── Row 9: Progress bar ──────────────────────────────────────────
        self.progress_bar = ttk.Progressbar(main, mode="determinate")
        self.progress_bar.grid(row=9, column=0, sticky="ew", pady=(0, 8))

        # ── Row 10-11: Results table ─────────────────────────────────────
        ttk.Label(main, text="Matched Files:",
                  style="Header.TLabel").grid(row=10, column=0, sticky="w", pady=(0, 2))

        tree_frame = ttk.Frame(main)
        tree_frame.grid(row=11, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("filename", "folder", "status", "keywords")
        self.tree = ttk.Treeview(tree_frame, columns=columns,
                                  show="headings", selectmode="browse")
        self.tree.heading("filename", text="File Name")
        self.tree.heading("folder", text="Folder")
        self.tree.heading("status", text="Status")
        self.tree.heading("keywords", text="Content Keywords Found")
        self.tree.column("filename", width=220, minwidth=150)
        self.tree.column("folder", width=350, minwidth=200)
        self.tree.column("status", width=150, minwidth=120)
        self.tree.column("keywords", width=280, minwidth=150)
        self.tree.grid(row=0, column=0, sticky="nsew")

        tree_vscroll = ttk.Scrollbar(tree_frame, orient="vertical",
                                      command=self.tree.yview)
        tree_vscroll.grid(row=0, column=1, sticky="ns")
        tree_hscroll = ttk.Scrollbar(tree_frame, orient="horizontal",
                                      command=self.tree.xview)
        tree_hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=tree_vscroll.set,
                            xscrollcommand=tree_hscroll.set)

        # ── Row 12: Stats summary ────────────────────────────────────────
        self.stats_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.stats_var,
                  style="Status.TLabel").grid(row=12, column=0, sticky="w", pady=(4, 0))

        # Grid weights
        main.columnconfigure(0, weight=1)
        main.rowconfigure(11, weight=1)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select Folder to Scan")
        if path:
            current = self.folder_text.get("1.0", "end").strip()
            if current:
                self.folder_text.insert("end", "\n" + path)
            else:
                self.folder_text.insert("1.0", path)

    def _browse_output_file(self):
        path = filedialog.asksaveasfilename(
            title="Output File Location",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=OUTPUT_FILENAME,
        )
        if path:
            self.output_path_var.set(path)

    def _get_folders(self) -> list:
        raw = self.folder_text.get("1.0", "end").strip()
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _get_filename_keywords(self) -> list:
        raw = self.filename_kw_text.get("1.0", "end").strip()
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _get_content_keywords(self) -> list:
        raw = self.content_kw_text.get("1.0", "end").strip()
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _update_status(self, msg: str):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _update_progress(self, value: float, maximum: float = 100):
        def _do():
            self.progress_bar["maximum"] = maximum
            self.progress_bar["value"] = value
        self.root.after(0, _do)

    def _update_stats(self):
        if self.tracker and self.output:
            s = self.tracker.get_stats()
            o = self.output.get_stats()
            self.stats_var.set(
                f"Folders: {s['scanned_folders']}/{s['total_folders']} scanned  |  "
                f"Remaining: {s['remaining_folders']}  |  "
                f"PDFs found: {o['total_entries']}  |  "
                f"Content confirmed: {o['content_confirmed']}  |  "
                f"Needs review: {o['needs_review']}"
            )

    def _populate_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.output:
            return
        for m in self.output.get_entries():
            status = m.get("match_status", "filename_match_only")
            status_display = ("✓ Content Confirmed" if status == "content_confirmed"
                              else "⚠ Needs Review")
            keywords = m.get("content_keywords_found", [])
            self.tree.insert("", "end", values=(
                m["filename"],
                m["folder"],
                status_display,
                "; ".join(keywords) if keywords else "",
            ))
        self._update_stats()

    # ── Scan Logic ───────────────────────────────────────────────────────

    def _start_scan(self):
        folders = self._get_folders()
        if not folders:
            messagebox.showwarning("No Folders", "Please add at least one folder to scan.")
            return

        filename_kws = self._get_filename_keywords()
        if not filename_kws:
            messagebox.showwarning("No Keyword", "Please enter at least one filename keyword.")
            return

        content_kws = self._get_content_keywords()
        if not content_kws:
            messagebox.showwarning("No Content Keywords",
                                   "Please enter at least one content keyword.")
            return

        # Validate folders exist
        bad = [f for f in folders if not os.path.isdir(f)]
        if bad:
            messagebox.showwarning(
                "Invalid Folders",
                "These folders do not exist:\n\n" + "\n".join(bad)
            )
            return

        self._is_scanning = True
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.reset_btn.configure(state="disabled")
        self.export_btn.configure(state="disabled")

        # Progress file lives in data/ subfolder next to the script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        progress_path = os.path.join(data_dir, PROGRESS_FILENAME)
        self.tracker = ProgressTracker(progress_path)

        # If keywords changed since last run, reset all folders to not_scanned
        keywords_changed = self.tracker.check_keywords_changed(
            filename_kws, content_kws
        )
        # Store keywords on first run (when progress file is new)
        if not self.tracker.data.get("filename_keywords"):
            self.tracker.set_keywords(filename_kws, content_kws)

        output_path = self.output_path_var.get().strip()
        self.output = OutputTracker(output_path)
        self.engine = ScannerEngine(self.tracker, self.output)

        def _run():
            try:
                if keywords_changed:
                    self._update_status(
                        "Keywords changed — rescanning all folders…"
                    )
                # ── Phase 1: Discover all subfolders ─────────────────────
                new_roots = self.tracker.get_new_root_folders(folders)
                skipped_roots = len(folders) - len(new_roots)
                if skipped_roots > 0:
                    self._update_status(
                        f"Phase 1/2 — {skipped_roots} root folder(s) already "
                        f"completed, discovering new ones…"
                    )
                else:
                    self._update_status("Phase 1/2 — Discovering folders…")
                self._update_progress(0, 1)

                def on_discover(folder_path, count):
                    self._update_status(
                        f"Discovering folders… ({count} found) — {folder_path}"
                    )

                total_folders = self.engine.discover_folders(
                    folders, on_progress=on_discover
                )

                stats = self.tracker.get_stats()
                remaining = stats["remaining_folders"]
                self._update_status(
                    f"Discovery complete. {stats['total_folders']} folders found "
                    f"({remaining} to scan, "
                    f"{stats['scanned_folders']} already done)."
                )
                self.root.after(0, self._update_stats)

                if remaining == 0:
                    self._update_status(
                        "No new folders to scan. All folders already processed."
                    )
                    self.root.after(0, self._scan_finished)
                    return

                # ── Phase 2: Scan each unscanned folder ──────────────────
                self._update_status("Phase 2/2 — Scanning PDF content…")

                def on_folder_start(fp, idx, total):
                    self._update_status(
                        f"Scanning folder [{idx}/{total}]: {fp}"
                    )
                    self._update_progress(idx, total)

                def on_folder_done(fp, idx, total, f_scanned, f_matched, f_err):
                    if idx % 5 == 0 or idx == total:
                        self.root.after(0, self._populate_results)

                def on_file_match(filepath, keywords, status):
                    # Output JSON is already saved inside OutputTracker.add_entry
                    self.root.after(0, self._update_stats)

                result = self.engine.scan_folders(
                    filename_kws, content_kws,
                    root_folders=folders,
                    on_folder_start=on_folder_start,
                    on_folder_done=on_folder_done,
                    on_file_match=on_file_match,
                )

                self._update_status(
                    f"Scan complete! "
                    f"{result['folders_scanned']} folders scanned, "
                    f"{result['files_scanned']} PDFs checked, "
                    f"{result['files_matched']} matched, "
                    f"{result['files_errored']} errors."
                )
            except Exception as e:
                self._update_status(f"Unexpected error: {e}")
            finally:
                self.root.after(0, self._scan_finished)

        self._scan_thread = threading.Thread(target=_run, daemon=True)
        self._scan_thread.start()

    def _stop_scan(self):
        if self.engine:
            self.engine.stop()
        self._update_status("Stopping… (will finish current folder)")

    def _scan_finished(self):
        self._is_scanning = False
        self.scan_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.reset_btn.configure(state="normal")
        self.export_btn.configure(state="normal")
        self._populate_results()

    def _reset_progress(self):
        if self._is_scanning:
            return
        if messagebox.askyesno("Reset Progress",
                               "This will delete all scan progress AND output results."
                               "\nAre you sure?"):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = os.path.join(script_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            progress_path = os.path.join(data_dir, PROGRESS_FILENAME)
            output_path = self.output_path_var.get().strip()
            self.tracker = ProgressTracker(progress_path)
            self.tracker.reset()
            self.output = OutputTracker(output_path)
            self.output.reset()
            self._populate_results()
            self._update_status("Progress and output reset.")
            self.stats_var.set("")
            self._update_progress(0)

    def _export_csv(self):
        if not self.output:
            return
        results = self.output.get_entries()
        if not results:
            messagebox.showinfo("No Results", "No files found to export.")
            return

        path = filedialog.asksaveasfilename(
            title="Export Results",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=RESULTS_CSV_FILENAME,
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["File Name", "Folder", "Full Path",
                                 "Root Folder", "Match Status",
                                 "Content Keywords Found"])
                for r in results:
                    status = r.get("match_status", "filename_match_only")
                    keywords = r.get("content_keywords_found", [])
                    writer.writerow([
                        r["filename"],
                        r["folder"],
                        r["filepath"],
                        r.get("root_folder", ""),
                        status,
                        "; ".join(keywords) if keywords else "",
                    ])
            self._update_status(f"Exported {len(results)} results to {path}")
            messagebox.showinfo("Export Complete",
                                f"Saved {len(results)} results to:\n{path}")
        except IOError as e:
            messagebox.showerror("Export Error", str(e))


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = PDFScannerApp(root)
    root.mainloop()