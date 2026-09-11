from __future__ import annotations

import io
import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk

from wbd_marker_core import (
    MarkerDatabase,
    MarkerToolError,
    PageAnalysis,
    RepairResult,
    analyses_to_csv,
    analyse_pdf_bytes,
    format_depth,
    load_marker_database,
    render_page_png,
    repair_pdf_bytes,
)


APP_TITLE = "WBD Marker QC and Repair"
APP_VERSION = "2.6"


@dataclass
class PdfInput:
    path: Path
    display_name: str
    data: bytes
    analyses: list[PageAnalysis] = field(default_factory=list)


@dataclass
class ScanPayload:
    database: MarkerDatabase
    pdf_inputs: list[PdfInput]
    analyses: list[PageAnalysis]
    errors: list[str]


@dataclass
class RepairPayload:
    results: list[RepairResult]
    after_analyses: list[PageAnalysis]
    preview_sources: dict[str, bytes]
    output_dir: Path
    repaired_files: list[Path]
    archived_originals: list[Path]
    next_pdf_paths: list[Path]
    warnings: list[str]
    errors: list[str]


def resource_path(filename: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / filename


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not create a unique output name for {path.name!r}.")


def archive_original_pdf(path: Path, folder_name: str = "repaired") -> Path:
    """Move an original PDF into a sibling archive subfolder.

    A unique suffix is added if the destination already exists. If the file is
    already inside the requested archive folder, it is left in place.
    """
    if path.parent.name.casefold() == folder_name.casefold():
        return path
    archive_dir = path.parent / folder_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_path(archive_dir / path.name)
    shutil.move(str(path), str(destination))
    return destination


def repaired_pages_pass_verification(result: RepairResult) -> bool:
    fixed_pages = set(result.fixed_pages)
    fixed_after = [
        analysis for analysis in result.after if analysis.page_number in fixed_pages
    ]
    return bool(fixed_after) and all(
        not analysis.all_markers_missing
        and not analysis.has_duplicates
        and not analysis.has_mismatch
        for analysis in fixed_after
    )


def issue_status(analysis: PageAnalysis) -> tuple[str, str]:
    if "NO_SEARCHABLE_TEXT" in analysis.flags:
        return "NO SEARCHABLE TEXT", "blocked"
    if "API_NOT_FOUND" in analysis.flags:
        return "API NOT FOUND", "blocked"
    if "NO_SPREADSHEET_MARKERS" in analysis.flags:
        return "NO SPREADSHEET PICKS", "blocked"
    if "SPREADSHEET_MARKER_CONFLICT" in analysis.flags:
        return "SPREADSHEET CONFLICT", "blocked"
    if "MARKER_TABLE_NOT_FOUND" in analysis.flags:
        return "MARKER TABLE NOT FOUND", "blocked"
    if "ROTATED_PAGE_UNSUPPORTED" in analysis.flags:
        return "ROTATED PAGE", "blocked"
    if analysis.all_markers_missing:
        return "ALL MARKERS MISSING", "critical"
    if analysis.has_duplicates:
        return "DUPLICATE MARKERS", "critical"
    if analysis.has_mismatch:
        return "PARTIAL MISMATCH", "warning"
    return "OK", "ok"


def issue_details(analysis: PageAnalysis) -> str:
    details: list[str] = []
    if analysis.duplicate_labels:
        details.append("Duplicate label(s): " + ", ".join(analysis.duplicate_labels))
    if analysis.duplicate_depths:
        details.append(
            "Duplicate row depth(s): "
            + ", ".join(format_depth(value) for value in analysis.duplicate_depths)
        )
    if analysis.all_markers_missing:
        details.append("The Zone / Top Zone Depth area contains no marker rows.")
    if analysis.missing_expected and not analysis.all_markers_missing:
        details.append("Missing: " + "; ".join(analysis.missing_expected))
    if analysis.unexpected_existing:
        details.append("Unexpected: " + "; ".join(analysis.unexpected_existing))
    if analysis.flags:
        details.append("Flags: " + "; ".join(analysis.flags))
    return " | ".join(details) or "No marker issue found."


class PreviewWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        png_bytes: bytes,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("980x760")
        self.minsize(650, 500)

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        self._original = image
        self._scale = 1.0
        self._tk_image: ImageTk.PhotoImage | None = None
        self._canvas_image_id: int | None = None

        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="-", width=4, command=lambda: self._zoom(0.8)).pack(
            side="left"
        )
        ttk.Button(toolbar, text="Fit", command=self._fit).pack(side="left", padx=4)
        ttk.Button(toolbar, text="100%", command=self._actual_size).pack(side="left")
        ttk.Button(toolbar, text="+", width=4, command=lambda: self._zoom(1.25)).pack(
            side="left", padx=(4, 0)
        )
        self._zoom_label = ttk.Label(toolbar, text="")
        self._zoom_label.pack(side="left", padx=12)
        ttk.Button(toolbar, text="Close", command=self.destroy).pack(side="right")

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(container, background="#4b4b4b", highlightthickness=0)
        xbar = ttk.Scrollbar(container, orient="horizontal", command=self._canvas.xview)
        ybar = ttk.Scrollbar(container, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.bind("<Control-plus>", lambda _event: self._zoom(1.25))
        self.bind("<Control-minus>", lambda _event: self._zoom(0.8))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(100, self._fit)

    def _fit(self) -> None:
        self.update_idletasks()
        width = max(self._canvas.winfo_width() - 24, 100)
        height = max(self._canvas.winfo_height() - 24, 100)
        self._scale = min(width / self._original.width, height / self._original.height, 1.5)
        self._redraw()

    def _actual_size(self) -> None:
        self._scale = 1.0
        self._redraw()

    def _zoom(self, factor: float) -> None:
        self._scale = min(max(self._scale * factor, 0.15), 4.0)
        self._redraw()

    def _redraw(self) -> None:
        width = max(1, int(self._original.width * self._scale))
        height = max(1, int(self._original.height * self._scale))
        resized = self._original.resize((width, height), Image.Resampling.LANCZOS)
        self._tk_image = ImageTk.PhotoImage(resized)
        if self._canvas_image_id is None:
            self._canvas_image_id = self._canvas.create_image(
                0, 0, anchor="nw", image=self._tk_image
            )
        else:
            self._canvas.itemconfigure(self._canvas_image_id, image=self._tk_image)
        self._canvas.configure(scrollregion=(0, 0, width, height))
        self._zoom_label.configure(text=f"{self._scale * 100:.0f}%")


class WbdMarkerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} {APP_VERSION}")
        self.root.geometry("1260x820")
        self.root.minsize(980, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        icon_path = resource_path("app_icon.ico")
        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except Exception:
                pass

        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy = False
        self._database: MarkerDatabase | None = None
        self._pdf_inputs: list[PdfInput] = []
        self._analyses: list[PageAnalysis] = []
        self._preview_sources: dict[str, bytes] = {}
        self._analysis_by_iid: dict[str, PageAnalysis] = {}
        self._last_output_dir: Path | None = None
        self._pdf_paths: list[Path] = []

        self.spreadsheet_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.plot_basis_var = tk.StringVar(value="TVD")
        self.display_basis_var = tk.StringVar(value="MD")
        self.decimals_var = tk.IntVar(value=1)
        self.fix_partial_var = tk.BooleanVar(value=True)
        self.timestamp_folder_var = tk.BooleanVar(value=True)
        self.zip_output_var = tk.BooleanVar(value=True)
        self.archive_originals_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar(value="No PDFs checked yet.")

        self._configure_styles()
        self._build_ui()
        self.root.after(100, self._drain_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 9))
        style.configure("Heading.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.configure("Action.TButton", padding=(10, 6))
        style.configure("Summary.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Native Windows desktop workflow for checking duplicate marker rows, "
                "flagging diagrams with all markers missing, and rebuilding marker columns "
                "from an authoritative XLSX workbook."
            ),
            style="Subtitle.TLabel",
            wraplength=1150,
        ).pack(anchor="w", pady=(2, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self.files_tab = ttk.Frame(self.notebook, padding=14)
        self.results_tab = ttk.Frame(self.notebook, padding=10)
        self.log_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.files_tab, text="1. Files and settings")
        self.notebook.add(self.results_tab, text="2. QC results")
        self.notebook.add(self.log_tab, text="Activity log")

        self._build_files_tab()
        self._build_results_tab()
        self._build_log_tab()

        status = ttk.Frame(self.root, padding=(14, 4, 14, 10))
        status.pack(fill="x")
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100)
        self.progress.pack(side="right", fill="x", expand=True, padx=(20, 0))
        ttk.Label(status, textvariable=self.status_var).pack(side="left")

    def _build_files_tab(self) -> None:
        self.files_tab.columnconfigure(0, weight=1)
        self.files_tab.rowconfigure(1, weight=1)

        inputs = ttk.LabelFrame(self.files_tab, text="Input files", padding=12)
        inputs.grid(row=0, column=0, sticky="ew")
        inputs.columnconfigure(1, weight=1)

        ttk.Label(inputs, text="Marker spreadsheet (.xlsx):").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.spreadsheet_entry = ttk.Entry(
            inputs, textvariable=self.spreadsheet_var, state="readonly"
        )
        self.spreadsheet_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.browse_sheet_button = ttk.Button(
            inputs, text="Browse...", command=self._browse_spreadsheet
        )
        self.browse_sheet_button.grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(inputs, text="Output parent folder:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.output_entry = ttk.Entry(inputs, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.browse_output_button = ttk.Button(
            inputs, text="Browse...", command=self._browse_output
        )
        self.browse_output_button.grid(row=1, column=2, padx=(8, 0), pady=4)

        pdf_frame = ttk.LabelFrame(self.files_tab, text="Wellbore diagram PDFs", padding=10)
        pdf_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        pdf_frame.columnconfigure(0, weight=1)
        pdf_frame.rowconfigure(0, weight=1)

        list_container = ttk.Frame(pdf_frame)
        list_container.grid(row=0, column=0, sticky="nsew")
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)
        self.pdf_list = tk.Listbox(
            list_container,
            selectmode="extended",
            activestyle="dotbox",
            font=("Segoe UI", 9),
            borderwidth=1,
            relief="solid",
        )
        ybar = ttk.Scrollbar(list_container, orient="vertical", command=self.pdf_list.yview)
        xbar = ttk.Scrollbar(list_container, orient="horizontal", command=self.pdf_list.xview)
        self.pdf_list.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.pdf_list.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")

        buttons = ttk.Frame(pdf_frame)
        buttons.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        self.add_pdf_button = ttk.Button(buttons, text="Add PDFs...", command=self._add_pdfs)
        self.add_pdf_button.pack(fill="x", pady=(0, 6))
        self.add_folder_button = ttk.Button(
            buttons, text="Add folder...", command=self._add_pdf_folder
        )
        self.add_folder_button.pack(fill="x", pady=6)
        self.remove_pdf_button = ttk.Button(
            buttons, text="Remove selected", command=self._remove_selected_pdfs
        )
        self.remove_pdf_button.pack(fill="x", pady=6)
        self.clear_pdf_button = ttk.Button(
            buttons, text="Clear list", command=self._clear_pdfs
        )
        self.clear_pdf_button.pack(fill="x", pady=6)
        self.pdf_count_label = ttk.Label(buttons, text="0 files")
        self.pdf_count_label.pack(fill="x", pady=(16, 0))

        settings = ttk.LabelFrame(self.files_tab, text="Repair settings", padding=12)
        settings.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        for index in range(4):
            settings.columnconfigure(index, weight=1)

        plot_group = ttk.Frame(settings)
        plot_group.grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Label(plot_group, text="Plot vertical position using:").pack(anchor="w")
        ttk.Radiobutton(
            plot_group, text="TVD (recommended)", value="TVD", variable=self.plot_basis_var
        ).pack(anchor="w")
        ttk.Radiobutton(
            plot_group, text="MD", value="MD", variable=self.plot_basis_var
        ).pack(anchor="w")

        display_group = ttk.Frame(settings)
        display_group.grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Label(display_group, text="Print Top Zone Depth using:").pack(anchor="w")
        ttk.Radiobutton(
            display_group, text="MD (diagram convention)", value="MD", variable=self.display_basis_var
        ).pack(anchor="w")
        ttk.Radiobutton(
            display_group, text="TVD", value="TVD", variable=self.display_basis_var
        ).pack(anchor="w")

        precision_group = ttk.Frame(settings)
        precision_group.grid(row=0, column=2, sticky="nw", padx=(0, 20))
        ttk.Label(precision_group, text="Printed depth decimals:").pack(anchor="w")
        self.decimal_spin = ttk.Spinbox(
            precision_group,
            from_=0,
            to=3,
            textvariable=self.decimals_var,
            width=6,
            state="readonly",
        )
        self.decimal_spin.pack(anchor="w", pady=(4, 0))

        scope_group = ttk.Frame(settings)
        scope_group.grid(row=0, column=3, sticky="nw")
        self.partial_scope_files_check = ttk.Checkbutton(
            scope_group,
            text="Repair partial mismatches automatically (recommended)",
            variable=self.fix_partial_var,
            command=self._on_fix_scope_changed,
        )
        self.partial_scope_files_check.pack(anchor="w")
        ttk.Checkbutton(
            scope_group,
            text="Create timestamped output folder",
            variable=self.timestamp_folder_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            scope_group,
            text="Create ZIP package",
            variable=self.zip_output_var,
        ).pack(anchor="w")
        self.archive_originals_check = ttk.Checkbutton(
            scope_group,
            text="Move repaired originals to a 'repaired' subfolder",
            variable=self.archive_originals_var,
        )
        self.archive_originals_check.pack(anchor="w")

        actions = ttk.Frame(self.files_tab)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.check_button = ttk.Button(
            actions,
            text="Check PDFs",
            style="Primary.TButton",
            command=self._start_scan,
        )
        self.check_button.pack(side="left")
        self.reset_button = ttk.Button(
            actions, text="Reset results", style="Action.TButton", command=self._reset_results
        )
        self.reset_button.pack(side="left", padx=8)
        ttk.Label(
            actions,
            text="Repaired outputs are saved separately; optional original-file archiving is enabled by default.",
        ).pack(side="right")

    def _build_results_tab(self) -> None:
        self.results_tab.columnconfigure(0, weight=1)
        self.results_tab.rowconfigure(1, weight=1)
        self.results_tab.rowconfigure(3, weight=1)

        summary = ttk.Frame(self.results_tab)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(summary, textvariable=self.summary_var, style="Summary.TLabel").pack(
            side="left"
        )
        self.view_label = ttk.Label(summary, text="")
        self.view_label.pack(side="right")

        tree_frame = ttk.Frame(self.results_tab)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = (
            "file",
            "page",
            "api",
            "well",
            "status",
            "expected",
            "pdfmarkers",
            "willrepair",
            "details",
        )
        self.results_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "file": "PDF",
            "page": "Page",
            "api": "API",
            "well": "Well",
            "status": "QC status",
            "expected": "XLSX",
            "pdfmarkers": "PDF",
            "willrepair": "Will repair",
            "details": "Issue details",
        }
        widths = {
            "file": 210,
            "page": 55,
            "api": 105,
            "well": 140,
            "status": 165,
            "expected": 55,
            "pdfmarkers": 55,
            "willrepair": 85,
            "details": 430,
        }
        anchors = {
            "page": "center",
            "expected": "center",
            "pdfmarkers": "center",
            "willrepair": "center",
        }
        for column in columns:
            self.results_tree.heading(column, text=headings[column])
            self.results_tree.column(
                column,
                width=widths[column],
                minwidth=45,
                stretch=column in {"file", "well", "details"},
                anchor=anchors.get(column, "w"),
            )
        ybar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.results_tree.yview)
        xbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.results_tree.xview)
        self.results_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")

        self.results_tree.tag_configure("critical", background="#ffe3e0")
        self.results_tree.tag_configure("warning", background="#fff2cc")
        self.results_tree.tag_configure("blocked", background="#ececec")
        self.results_tree.tag_configure("ok", background="#e8f5e9")
        self.results_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.results_tree.bind("<Double-1>", lambda _event: self._preview_selected())

        action_bar = ttk.Frame(self.results_tab)
        action_bar.grid(row=2, column=0, sticky="ew", pady=8)
        self.preview_button = ttk.Button(
            action_bar, text="Preview selected page", command=self._preview_selected, state="disabled"
        )
        self.preview_button.pack(side="left")
        self.export_button = ttk.Button(
            action_bar, text="Export current scan CSV...", command=self._export_scan_csv, state="disabled"
        )
        self.export_button.pack(side="left", padx=6)
        self.partial_scope_results_check = ttk.Checkbutton(
            action_bar,
            text="Include partial mismatches",
            variable=self.fix_partial_var,
            command=self._on_fix_scope_changed,
        )
        self.partial_scope_results_check.pack(side="left", padx=(10, 0))
        self.fix_button = ttk.Button(
            action_bar,
            text="Fix all eligible flagged pages",
            style="Primary.TButton",
            command=self._start_repair,
            state="disabled",
        )
        self.fix_button.pack(side="right")
        self.open_output_button = ttk.Button(
            action_bar,
            text="Open output folder",
            command=self._open_output_folder,
            state="disabled",
        )
        self.open_output_button.pack(side="right", padx=6)

        details_frame = ttk.LabelFrame(
            self.results_tab, text="Selected-page details", padding=8
        )
        details_frame.grid(row=3, column=0, sticky="nsew")
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        self.details_text = ScrolledText(
            details_frame,
            height=9,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        self.details_text.grid(row=0, column=0, sticky="nsew")

    def _build_log_tab(self) -> None:
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)
        self.log_text = ScrolledText(
            self.log_tab, wrap="word", font=("Consolas", 9), state="disabled"
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        controls = ttk.Frame(self.log_tab)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(controls, text="Clear log", command=self._clear_log).pack(side="left")
        ttk.Button(controls, text="Copy log", command=self._copy_log).pack(
            side="left", padx=6
        )

    def _browse_spreadsheet(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select authoritative marker workbook",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")],
        )
        if filename:
            self.spreadsheet_var.set(filename)
            self._invalidate_results("Spreadsheet selection changed.")

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Select output parent folder")
        if folder:
            self.output_var.set(folder)

    def _add_pdfs(self) -> None:
        filenames = filedialog.askopenfilenames(
            title="Select wellbore diagram PDFs",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if filenames:
            self._add_pdf_paths(Path(name) for name in filenames)

    def _add_pdf_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select folder containing PDFs")
        if not folder:
            return
        paths = sorted(Path(folder).glob("*.pdf"), key=lambda value: value.name.lower())
        if not paths:
            messagebox.showinfo(APP_TITLE, "No PDF files were found in that folder.")
            return
        self._add_pdf_paths(paths)

    def _add_pdf_paths(self, paths: Iterable[Path]) -> None:
        existing = {str(path.resolve()).lower() for path in self._pdf_paths}
        added = 0
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            key = str(resolved).lower()
            if key in existing:
                continue
            if path.suffix.lower() != ".pdf":
                continue
            self._pdf_paths.append(path)
            existing.add(key)
            added += 1
        self._pdf_paths.sort(key=lambda value: (value.name.lower(), str(value).lower()))
        self._refresh_pdf_list()
        if added:
            if not self.output_var.get() and self._pdf_paths:
                self.output_var.set(str(self._pdf_paths[0].parent / "WBD_Marker_Output"))
            self._invalidate_results("PDF selection changed.")

    def _remove_selected_pdfs(self) -> None:
        selected = set(self.pdf_list.curselection())
        if not selected:
            return
        self._pdf_paths = [
            path for index, path in enumerate(self._pdf_paths) if index not in selected
        ]
        self._refresh_pdf_list()
        self._invalidate_results("PDF selection changed.")

    def _clear_pdfs(self) -> None:
        if not self._pdf_paths:
            return
        self._pdf_paths.clear()
        self._refresh_pdf_list()
        self._invalidate_results("PDF selection changed.")

    def _refresh_pdf_list(self) -> None:
        self.pdf_list.delete(0, tk.END)
        for path in self._pdf_paths:
            self.pdf_list.insert(tk.END, str(path))
        count = len(self._pdf_paths)
        self.pdf_count_label.configure(text=f"{count} file{'s' if count != 1 else ''}")

    def _invalidate_results(self, reason: str) -> None:
        if not self._analyses:
            return
        self._log(reason)
        self._reset_results()

    def _reset_results(self) -> None:
        self._database = None
        self._pdf_inputs = []
        self._analyses = []
        self._preview_sources = {}
        self._analysis_by_iid = {}
        self._last_output_dir = None
        self.summary_var.set("No PDFs checked yet.")
        self.view_label.configure(text="")
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self._set_details("")
        self.fix_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.open_output_button.configure(state="disabled")

    def _validate_inputs(self) -> tuple[Path, list[Path], Path] | None:
        spreadsheet = Path(self.spreadsheet_var.get().strip())
        if not spreadsheet.is_file():
            messagebox.showerror(APP_TITLE, "Select a valid .xlsx marker spreadsheet.")
            return None
        if spreadsheet.suffix.lower() != ".xlsx":
            messagebox.showerror(APP_TITLE, "The marker spreadsheet must be an .xlsx file.")
            return None
        if not self._pdf_paths:
            messagebox.showerror(APP_TITLE, "Add at least one PDF file.")
            return None
        missing = [path for path in self._pdf_paths if not path.is_file()]
        if missing:
            messagebox.showerror(
                APP_TITLE,
                "One or more PDF files no longer exist:\n\n"
                + "\n".join(str(path) for path in missing[:10]),
            )
            return None
        output = Path(self.output_var.get().strip()) if self.output_var.get().strip() else spreadsheet.parent / "WBD_Marker_Output"
        return spreadsheet, list(self._pdf_paths), output

    def _start_scan(self) -> None:
        validated = self._validate_inputs()
        if validated is None or self._busy:
            return
        spreadsheet, pdf_paths, _output = validated
        self._reset_results()
        self._set_busy(True, "Loading spreadsheet...")
        self._log("-" * 72)
        self._log(f"Starting QC scan at {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._log(f"Spreadsheet: {spreadsheet}")
        self._log(f"PDF count: {len(pdf_paths)}")
        thread = threading.Thread(
            target=self._scan_worker,
            args=(spreadsheet, pdf_paths),
            daemon=True,
        )
        thread.start()

    def _scan_worker(self, spreadsheet: Path, pdf_paths: list[Path]) -> None:
        try:
            database = load_marker_database(spreadsheet.read_bytes())
            self._events.put(
                (
                    "log",
                    f"Loaded {database.marker_count} marker picks for {len(database.by_api)} APIs "
                    f"from sheet {database.sheet_name!r}.",
                )
            )
            for warning in database.warnings:
                self._events.put(("log", "Spreadsheet warning: " + warning))

            name_counts: Counter[str] = Counter()
            pdf_inputs: list[PdfInput] = []
            all_analyses: list[PageAnalysis] = []
            errors: list[str] = []
            total = len(pdf_paths)

            for index, path in enumerate(pdf_paths, start=1):
                name_counts[path.name.lower()] += 1
                duplicate_number = name_counts[path.name.lower()]
                display_name = path.name
                if duplicate_number > 1:
                    display_name = f"{path.stem}_{duplicate_number}{path.suffix}"
                self._events.put(
                    (
                        "progress",
                        (index - 1) / total * 100,
                        f"Checking {index} of {total}: {path.name}",
                    )
                )
                try:
                    data = path.read_bytes()
                    analyses = analyse_pdf_bytes(data, display_name, database)
                    pdf_inputs.append(
                        PdfInput(
                            path=path,
                            display_name=display_name,
                            data=data,
                            analyses=analyses,
                        )
                    )
                    all_analyses.extend(analyses)
                    self._events.put(
                        (
                            "log",
                            f"Checked {path.name}: {len(analyses)} page(s).",
                        )
                    )
                except (OSError, MarkerToolError) as exc:
                    message = f"{path}: {exc}"
                    errors.append(message)
                    self._events.put(("log", "ERROR: " + message))

            self._events.put(("progress", 100.0, "QC scan complete."))
            self._events.put(
                (
                    "scan_done",
                    ScanPayload(
                        database=database,
                        pdf_inputs=pdf_inputs,
                        analyses=all_analyses,
                        errors=errors,
                    ),
                )
            )
        except Exception as exc:
            self._events.put(("fatal", self._format_exception(exc)))

    def _on_scan_done(self, payload: ScanPayload) -> None:
        self._set_busy(False, "QC scan complete")
        self._database = payload.database
        self._pdf_inputs = payload.pdf_inputs
        self._analyses = payload.analyses
        self._preview_sources = {item.display_name: item.data for item in payload.pdf_inputs}
        self._populate_results(payload.analyses, "Before repair")
        self.notebook.select(self.results_tab)

        if not payload.analyses:
            messagebox.showerror(
                APP_TITLE,
                "No PDF pages could be checked. Review the Activity log for details.",
            )
            return
        if payload.errors:
            messagebox.showwarning(
                APP_TITLE,
                f"The scan completed, but {len(payload.errors)} PDF file(s) could not be processed. "
                "See the Activity log.",
            )

    def _populate_results(self, analyses: Sequence[PageAnalysis], view_name: str) -> None:
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self._analysis_by_iid.clear()

        fix_partial = self.fix_partial_var.get()
        for index, analysis in enumerate(analyses):
            status, tag = issue_status(analysis)
            will_repair = "Yes" if analysis.should_fix(fix_partial) else "No"
            iid = f"row_{index}"
            self.results_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    analysis.filename,
                    analysis.page_number,
                    analysis.api or "",
                    analysis.well_name or "",
                    status,
                    len(analysis.expected_markers),
                    len(analysis.existing_markers),
                    will_repair,
                    issue_details(analysis),
                ),
                tags=(tag,),
            )
            self._analysis_by_iid[iid] = analysis

        total = len(analyses)
        duplicates = sum(analysis.has_duplicates for analysis in analyses)
        all_missing = sum(analysis.all_markers_missing for analysis in analyses)
        partial = sum(
            analysis.has_mismatch
            and not analysis.all_markers_missing
            and not analysis.has_duplicates
            for analysis in analyses
        )
        blocked = sum(
            issue_status(analysis)[1] == "blocked" for analysis in analyses
        )
        eligible = sum(analysis.should_fix(fix_partial) for analysis in analyses)
        clean = sum(issue_status(analysis)[0] == "OK" for analysis in analyses)
        self.summary_var.set(
            f"Pages: {total}   |   Duplicates: {duplicates}   |   All missing: {all_missing}   |   "
            f"Partial mismatch: {partial}   |   Blocked: {blocked}   |   Clean: {clean}   |   "
            f"Eligible to repair: {eligible}"
        )
        self.view_label.configure(text=f"View: {view_name}")
        self.fix_button.configure(state="normal" if eligible and self._database else "disabled")
        self.export_button.configure(state="normal" if analyses else "disabled")
        self.preview_button.configure(state="normal" if analyses else "disabled")
        if analyses:
            first = self.results_tree.get_children()[0]
            self.results_tree.selection_set(first)
            self.results_tree.focus(first)
            self._show_analysis_details(self._analysis_by_iid[first])

    def _on_fix_scope_changed(self) -> None:
        if self._analyses:
            self._populate_results(self._analyses, self.view_label.cget("text").replace("View: ", "") or "Current")

    def _on_tree_select(self, _event: tk.Event[Any]) -> None:
        selected = self.results_tree.selection()
        if not selected:
            return
        analysis = self._analysis_by_iid.get(selected[0])
        if analysis is not None:
            self._show_analysis_details(analysis)

    def _show_analysis_details(self, analysis: PageAnalysis) -> None:
        expected = [
            f"  - {pick.marker}: MD {format_depth(pick.md)}, "
            + (f"TVD {format_depth(pick.tvd)}" if pick.tvd is not None else "TVD blank")
            + f" (sheet row {pick.source_row})"
            for pick in analysis.expected_markers
        ]
        existing = [
            f"  - {row.name or '(blank label)'}: "
            + (format_depth(row.depth) if row.depth is not None else "blank depth")
            for row in analysis.existing_markers
        ]
        lines = [
            f"PDF: {analysis.filename}",
            f"Page: {analysis.page_number}",
            f"API: {analysis.api or 'Not found'}",
            f"Well: {analysis.well_name or 'Not found'}",
            f"Status: {issue_status(analysis)[0]}",
            f"Technically repairable: {'Yes' if analysis.can_fix else 'No'}",
            f"Included in current repair scope: {'Yes' if analysis.should_fix(self.fix_partial_var.get()) else 'No'}",
            "",
            "Authoritative spreadsheet markers:",
            *(expected or ["  (none available for this API)"]),
            "",
            "Markers currently read from the PDF:",
            *(existing or ["  (none)"]),
            "",
            "Duplicate labels: " + (", ".join(analysis.duplicate_labels) or "None"),
            "Duplicate row depths: "
            + (
                ", ".join(format_depth(value) for value in analysis.duplicate_depths)
                or "None"
            ),
            "Missing from PDF: " + ("; ".join(analysis.missing_expected) or "None"),
            "Unexpected in PDF: " + ("; ".join(analysis.unexpected_existing) or "None"),
            "Flags: " + ("; ".join(analysis.flags) or "OK"),
            "Notes: " + ("; ".join(analysis.notes) or "None"),
        ]
        self._set_details("\n".join(lines))

    def _set_details(self, text: str) -> None:
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert("1.0", text)
        self.details_text.configure(state="disabled")

    def _preview_selected(self) -> None:
        selected = self.results_tree.selection()
        if not selected:
            messagebox.showinfo(APP_TITLE, "Select a page in the results table first.")
            return
        analysis = self._analysis_by_iid.get(selected[0])
        if analysis is None:
            return
        pdf_bytes = self._preview_sources.get(analysis.filename)
        if pdf_bytes is None:
            messagebox.showerror(APP_TITLE, "The PDF preview source is no longer available.")
            return
        try:
            png = render_page_png(pdf_bytes, analysis.page_number, dpi=135)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not render the preview:\n\n{exc}")
            return
        PreviewWindow(
            self.root,
            f"{analysis.filename} - page {analysis.page_number} - API {analysis.api or 'N/A'}",
            png,
        )

    def _export_scan_csv(self) -> None:
        if not self._analyses:
            return
        filename = filedialog.asksaveasfilename(
            title="Save scan report",
            defaultextension=".csv",
            initialfile="wbd_marker_scan_report.csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            Path(filename).write_bytes(
                analyses_to_csv(
                    self._analyses,
                    fix_partial_mismatches=self.fix_partial_var.get(),
                )
            )
            self._log(f"Saved scan report: {filename}")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save the report:\n\n{exc}")

    def _start_repair(self) -> None:
        if self._busy or self._database is None or not self._pdf_inputs:
            return
        fix_partial = self.fix_partial_var.get()
        eligible_pages = sum(
            analysis.should_fix(fix_partial) for analysis in self._analyses
        )
        if not eligible_pages:
            messagebox.showinfo(APP_TITLE, "No pages are currently included in the repair scope. For a PARTIAL MISMATCH, enable 'Repair partial mismatches automatically' on the Files and Settings tab.")
            return
        output_parent = Path(self.output_var.get().strip() or "WBD_Marker_Output")
        scope_text = (
            "duplicate-marker pages, pages with all markers missing, and partial mismatches"
            if fix_partial
            else "duplicate-marker pages and pages with all markers missing"
        )
        archive_originals = self.archive_originals_var.get()
        archive_text = (
            "After successful repair and verification, each original PDF will be moved "
            "to a 'repaired' subfolder beside the source file."
            if archive_originals
            else "Original PDFs will remain in their current folders."
        )
        confirmed = messagebox.askyesno(
            APP_TITLE,
            f"Repair {eligible_pages} eligible page(s)?\n\n"
            f"Scope: {scope_text}.\n"
            f"Output parent: {output_parent}\n\n"
            "The current marker/depth text on each repaired page will be replaced by the complete "
            f"authoritative marker set for that API.\n\n{archive_text}",
        )
        if not confirmed:
            return

        self._set_busy(True, "Preparing repair...")
        self.notebook.select(self.results_tab)
        thread = threading.Thread(
            target=self._repair_worker,
            args=(
                self._database,
                list(self._pdf_inputs),
                output_parent,
                fix_partial,
                self.plot_basis_var.get(),
                self.display_basis_var.get(),
                int(self.decimals_var.get()),
                self.timestamp_folder_var.get(),
                self.zip_output_var.get(),
                archive_originals,
            ),
            daemon=True,
        )
        thread.start()

    def _repair_worker(
        self,
        database: MarkerDatabase,
        pdf_inputs: list[PdfInput],
        output_parent: Path,
        fix_partial: bool,
        plot_basis: str,
        display_basis: str,
        decimals: int,
        timestamp_folder: bool,
        zip_output: bool,
        archive_originals: bool,
    ) -> None:
        try:
            if timestamp_folder:
                output_dir = output_parent / f"WBD_Marker_Repair_{datetime.now():%Y%m%d_%H%M%S}"
            else:
                output_dir = output_parent
            output_dir.mkdir(parents=True, exist_ok=True)

            results: list[RepairResult] = []
            after_analyses: list[PageAnalysis] = []
            preview_sources: dict[str, bytes] = {}
            repaired_files: list[Path] = []
            archived_originals: list[Path] = []
            next_pdf_paths: list[Path] = []
            warnings: list[str] = []
            errors: list[str] = []
            before_all: list[PageAnalysis] = []
            total = len(pdf_inputs)

            for index, item in enumerate(pdf_inputs, start=1):
                before_all.extend(item.analyses)
                selected_count = sum(
                    analysis.should_fix(fix_partial) for analysis in item.analyses
                )
                self._events.put(
                    (
                        "progress",
                        (index - 1) / total * 100,
                        f"Repairing {index} of {total}: {item.path.name}",
                    )
                )
                if not selected_count:
                    after_analyses.extend(item.analyses)
                    preview_sources[item.display_name] = item.data
                    next_pdf_paths.append(item.path)
                    self._events.put(
                        ("log", f"Skipped {item.path.name}: no eligible pages.")
                    )
                    continue

                try:
                    result = repair_pdf_bytes(
                        item.data,
                        item.display_name,
                        database,
                        fix_partial_mismatches=fix_partial,
                        plot_basis=plot_basis,
                        display_basis=display_basis,
                        decimals=decimals,
                    )
                    results.append(result)
                    after_analyses.extend(result.after)
                    preview_sources[result.filename] = result.output_bytes
                    warnings.extend(result.warnings)
                    if result.fixed_pages:
                        output_path = unique_path(output_dir / result.filename)
                        output_path.write_bytes(result.output_bytes)
                        repaired_files.append(output_path)
                        next_pdf_paths.append(output_path)
                        self._events.put(
                            (
                                "log",
                                f"Wrote {output_path.name}; repaired page(s) "
                                + ", ".join(str(page) for page in result.fixed_pages)
                                + ".",
                            )
                        )

                        if archive_originals:
                            if repaired_pages_pass_verification(result):
                                try:
                                    archived_path = archive_original_pdf(item.path)
                                    archived_originals.append(archived_path)
                                    self._events.put(
                                        (
                                            "log",
                                            f"Moved original {item.path.name} to {archived_path.parent}.",
                                        )
                                    )
                                except OSError as exc:
                                    warning = (
                                        f"Repaired output was saved, but the original could not be moved "
                                        f"to the 'repaired' subfolder: {item.path} ({exc})"
                                    )
                                    warnings.append(warning)
                                    self._events.put(("log", "Warning: " + warning))
                            else:
                                warning = (
                                    f"Original was not archived because post-repair verification still "
                                    f"reported a marker issue: {item.path}"
                                )
                                warnings.append(warning)
                                self._events.put(("log", "Warning: " + warning))
                    else:
                        next_pdf_paths.append(item.path)
                        self._events.put(
                            ("log", f"No pages changed in {item.path.name}.")
                        )
                    for warning in result.warnings:
                        self._events.put(("log", "Warning: " + warning))
                except (OSError, MarkerToolError, ValueError) as exc:
                    message = f"{item.path}: {exc}"
                    errors.append(message)
                    after_analyses.extend(item.analyses)
                    preview_sources[item.display_name] = item.data
                    next_pdf_paths.append(item.path)
                    self._events.put(("log", "REPAIR ERROR: " + message))

            before_report = output_dir / "scan_report_before.csv"
            after_report = output_dir / "scan_report_after.csv"
            before_report.write_bytes(
                analyses_to_csv(
                    before_all,
                    fix_partial_mismatches=fix_partial,
                )
            )
            after_report.write_bytes(
                analyses_to_csv(
                    after_analyses,
                    fix_partial_mismatches=fix_partial,
                )
            )

            summary_path = output_dir / "repair_summary.txt"
            summary_lines = [
                f"{APP_TITLE} {APP_VERSION}",
                f"Run time: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"Plot basis: {plot_basis}",
                f"Printed depth basis: {display_basis}",
                f"Printed decimals: {decimals}",
                f"Partial mismatches included: {'Yes' if fix_partial else 'No'}",
                f"Move repaired originals to subfolder: {'Yes' if archive_originals else 'No'}",
                f"Input PDFs: {len(pdf_inputs)}",
                f"Repaired PDF files: {len(repaired_files)}",
                f"Archived original PDFs: {len(archived_originals)}",
                f"Repaired pages: {sum(len(result.fixed_pages) for result in results)}",
                "",
                "Repaired output files:",
                *([f"- {path}" for path in repaired_files] or ["- None"]),
                "",
                "Archived original files:",
                *([f"- {path}" for path in archived_originals] or ["- None"]),
                "",
                "Warnings:",
                *([f"- {warning}" for warning in warnings] or ["- None"]),
                "",
                "Errors:",
                *([f"- {error}" for error in errors] or ["- None"]),
            ]
            summary_path.write_text("\n".join(summary_lines), encoding="utf-8")

            if zip_output:
                zip_path = unique_path(output_dir / "WBD_Marker_Repair_Output.zip")
                with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in [*repaired_files, before_report, after_report, summary_path]:
                        archive.write(path, arcname=path.name)
                self._events.put(("log", f"Created ZIP package: {zip_path.name}"))

            self._events.put(("progress", 100.0, "Repair and verification complete."))
            self._events.put(
                (
                    "repair_done",
                    RepairPayload(
                        results=results,
                        after_analyses=after_analyses,
                        preview_sources=preview_sources,
                        output_dir=output_dir,
                        repaired_files=repaired_files,
                        archived_originals=archived_originals,
                        next_pdf_paths=next_pdf_paths,
                        warnings=warnings,
                        errors=errors,
                    ),
                )
            )
        except Exception as exc:
            self._events.put(("fatal", self._format_exception(exc)))

    def _on_repair_done(self, payload: RepairPayload) -> None:
        self._set_busy(False, "Repair and verification complete")
        self._analyses = payload.after_analyses
        self._preview_sources = payload.preview_sources
        self._last_output_dir = payload.output_dir
        self._pdf_paths = payload.next_pdf_paths
        self._refresh_pdf_list()
        self._populate_results(payload.after_analyses, "After repair verification")
        # Require a fresh scan before another write operation, so a second repair
        # can never accidentally run against the original in-memory inputs.
        self.fix_button.configure(state="disabled")
        self.open_output_button.configure(state="normal")

        repaired_page_count = sum(len(result.fixed_pages) for result in payload.results)
        remaining_problem_pages = sum(
            analysis.all_markers_missing
            or analysis.has_duplicates
            or analysis.has_mismatch
            for analysis in payload.after_analyses
        )
        message = (
            f"Repair complete.\n\n"
            f"Repaired pages: {repaired_page_count}\n"
            f"Repaired PDF files: {len(payload.repaired_files)}\n"
            f"Original PDFs moved to 'repaired' subfolders: {len(payload.archived_originals)}\n"
            f"Marker-issue pages remaining in verification: {remaining_problem_pages}\n"
            f"Output folder:\n{payload.output_dir}"
        )
        if payload.errors:
            message += f"\n\n{len(payload.errors)} file(s) had repair errors. See the Activity log."
            messagebox.showwarning(APP_TITLE, message)
        else:
            messagebox.showinfo(APP_TITLE, message)

    def _open_output_folder(self) -> None:
        if self._last_output_dir is None or not self._last_output_dir.exists():
            messagebox.showinfo(APP_TITLE, "No output folder is available yet.")
            return
        try:
            open_path(self._last_output_dir)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not open the output folder:\n\n{exc}")

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self.status_var.set(status)
        state = "disabled" if busy else "normal"
        for widget in (
            self.browse_sheet_button,
            self.browse_output_button,
            self.add_pdf_button,
            self.add_folder_button,
            self.remove_pdf_button,
            self.clear_pdf_button,
            self.check_button,
            self.reset_button,
            self.partial_scope_files_check,
            self.partial_scope_results_check,
            self.archive_originals_check,
        ):
            widget.configure(state=state)
        if busy:
            self.fix_button.configure(state="disabled")
            self.progress.configure(value=0)
        else:
            eligible = bool(
                self._database
                and any(
                    analysis.should_fix(self.fix_partial_var.get())
                    for analysis in self._analyses
                )
            )
            self.fix_button.configure(state="normal" if eligible else "disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._log(str(event[1]))
                elif kind == "progress":
                    self.progress.configure(value=float(event[1]))
                    self.status_var.set(str(event[2]))
                elif kind == "scan_done":
                    self._on_scan_done(event[1])
                elif kind == "repair_done":
                    self._on_repair_done(event[1])
                elif kind == "fatal":
                    self._set_busy(False, "Operation failed")
                    self._log("FATAL ERROR:\n" + str(event[1]))
                    messagebox.showerror(
                        APP_TITLE,
                        "The operation failed. Review the Activity log for technical details.",
                    )
                    self.notebook.select(self.log_tab)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _copy_log(self) -> None:
        text = self.log_text.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        if isinstance(exc, (MarkerToolError, OSError, ValueError)):
            return str(exc)
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    def _on_close(self) -> None:
        if self._busy:
            close = messagebox.askyesno(
                APP_TITLE,
                "A scan or repair is still running. Close the application anyway?",
            )
            if not close:
                return
        self.root.destroy()


def main() -> int:
    enable_high_dpi()
    root = tk.Tk()
    WbdMarkerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
