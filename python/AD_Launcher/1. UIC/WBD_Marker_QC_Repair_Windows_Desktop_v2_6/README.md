# WBD Marker QC and Repair - Windows Desktop GUI v2.6

This native Windows desktop application checks searchable WellView-style casing-diagram PDFs against an authoritative Excel marker-pick workbook and rebuilds the marker table automatically.

## What changed in v2.6

- Added an automatic **TVD-to-MD placement fallback**. If the authoritative TVD picks cannot be mapped completely to the PDF TVD scale, the program now uses the corresponding MD survey rows to position every marker.
- This fixes older sidetrack diagrams where the PDF TVD column uses a different reference or range from the authoritative spreadsheet.
- The fallback is recorded in the Activity log and `repair_summary.txt`.
- Batch processing continues normally, and post-repair verification is still required before an original is moved to the `repaired` subfolder.


## What changed in v2.5

The repair engine now handles horizontal and highly deviated wells whose TVD
survey is non-monotonic. When TVD decreases while MD and the PDF survey rows
continue downward, a pure TVD-to-page mapping is ambiguous. The program now
automatically uses the marker's authoritative MD to select the correct survey
row, records a warning in the Activity log and repair summary, and completes the
repair instead of leaving the PDF unchanged.

## Existing v2.3 and v2.2 behavior

CSV reports include both:

- **Can fix** - whether the page is technically repairable.
- **Will repair** - whether the page is included in the current repair scope.

Partial marker mismatches are included in automatic repair by default. A partial mismatch is repaired by removing the current text in the `Zone` and `Top Zone Depth` data columns and rebuilding the complete marker list from the authoritative spreadsheet for that API.

## Main workflow

1. Select the authoritative marker-pick `.xlsx` workbook.
2. Add one or more searchable wellbore-diagram PDFs, or add every PDF in a folder.
3. Select an output parent folder.
4. Click **Check PDFs**.
5. Review the color-coded report for duplicate markers, all markers missing, partial mismatches, and blocked pages.
6. Confirm that **Include partial mismatches** is selected when those pages should be repaired.
7. Leave **Move repaired originals to a 'repaired' subfolder** selected to archive successfully processed originals.
8. Click **Fix all eligible flagged pages**.
9. Review the automatic post-repair verification and output folder.

## Default depth behavior

- Marker vertical plot location: **TVD**.
- Value printed in `Top Zone Depth`: **MD**.
- Printed precision: **1 decimal place**.

For a horizontal or up-dip well whose TVD column is non-monotonic, the engine
automatically uses the authoritative MD survey row to select the unique page
position. The requested display basis is unchanged, and the fallback is logged.

The GUI lets the user change these settings before repair.

## Quick Windows installation

Python 3.10 or newer, 64-bit, is recommended.

1. Extract the ZIP to a normal local folder.
2. Double-click `Setup_and_Run.bat`.
3. The script creates `.venv`, installs dependencies locally, and opens the GUI.
4. On later runs, double-click `Run_WBD_Marker_Tool.bat`.

The application does not require Microsoft Excel and does not upload files to a server.

## Build a standalone Windows EXE

On a Windows computer, double-click:

```text
Build_Windows_EXE.bat
```

The script creates:

```text
dist\WBD_Marker_QC_Repair.exe
```

PyInstaller must be run on Windows to create a Windows executable.

## Repair outputs

By default, each repair run creates a timestamped output folder containing:

- One `_markers_fixed.pdf` file for each repaired input PDF
- `scan_report_before.csv`
- `scan_report_after.csv`
- `repair_summary.txt`
- `WBD_Marker_Repair_Output.zip`, when the ZIP option is selected

When original archiving is enabled, each successfully processed source PDF is moved separately into a `repaired` subfolder beside that source file. The archived originals are not included in the output ZIP.

`repair_summary.txt` records the repaired output paths, archived original paths, warnings, and errors.

## Spreadsheet format

The workbook must be `.xlsx` and contain logical columns equivalent to:

| Well Name | Well API | Marker | MD (ft) | TVD (ft) |
|---|---|---|---:|---:|

Required fields are API, marker name, and MD. TVD is required when plotting by TVD. Common header aliases such as `API`, `Well API`, `Formation`, `Zone`, `MD`, and `TVD` are recognized.

The core retains the project-specific same-depth alias safeguard: where a coincident legacy `Freeman` row and the canonical `BOFW and Base USDW` row both exist for an API, the canonical row is retained. The rule is editable in `SAME_DEPTH_CANONICAL_ALIASES` near the top of `wbd_marker_core.py`.

Distinct authoritative formations at the same depth are preserved. The program separates their printed baselines slightly so both labels remain legible.

## Color coding

- Red: duplicate markers or all markers missing
- Yellow: partial marker mismatch
- Gray: automatic repair is blocked
- Green: no marker issue detected

## Current limitations

- The automatic layout logic targets searchable WellView-style diagrams with `Zone`, `Top Zone Depth`, `MD`, and `TVD` columns.
- Image-only PDFs are flagged; OCR is not run automatically.
- Rotated pages are reported but not edited automatically.
- A Windows file that is open in another program may not be movable; the application reports this and leaves the original in place.
- Repaired PDFs should receive a final visual review before regulatory submission.
