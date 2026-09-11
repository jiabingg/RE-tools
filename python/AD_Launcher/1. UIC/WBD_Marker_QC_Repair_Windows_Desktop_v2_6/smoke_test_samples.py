import importlib.machinery
import importlib.util
import queue
import shutil
import sys
import tempfile
from pathlib import Path

from wbd_marker_core import analyse_pdf_bytes, load_marker_database, repair_pdf_bytes

SHEET = Path("/mnt/data/62809014 Marker Picks_202606.xlsx")
PDFS = [
    Path("/mnt/data/All_WBD_combined_Part1.pdf"),
    Path("/mnt/data/All_WBD_combined_Part2.pdf"),
    Path("/mnt/data/All_WBD_combined_Part10.pdf"),
    Path("/mnt/data/All_WBD_combined_Part125.pdf"),
    Path("/mnt/data/All_WBD_combined_Part153.pdf"),
    Path("/mnt/data/All_WBD_combined_Part223.pdf"),
]


def load_gui_module():
    path = Path(__file__).with_name("wbd_marker_windows_gui.pyw")
    loader = importlib.machinery.SourceFileLoader("wbd_marker_windows_gui", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("Could not load GUI module for smoke test.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def main() -> None:
    gui = load_gui_module()

    # File-archiving helper test, including collision-safe naming.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "sample.pdf"
        source.write_bytes(b"sample")
        archived = gui.archive_original_pdf(source)
        assert archived == root / "repaired" / "sample.pdf"
        assert archived.read_bytes() == b"sample"
        assert not source.exists()

        second = root / "sample.pdf"
        second.write_bytes(b"second")
        archived_second = gui.archive_original_pdf(second)
        assert archived_second == root / "repaired" / "sample_2.pdf"
        assert archived_second.read_bytes() == b"second"

    if not SHEET.exists():
        print("Sample workbook is not present; archive helper and syntax tests passed.")
        return

    available = [path for path in PDFS if path.exists()]
    if not available:
        print("Sample PDFs are not present; archive helper and syntax tests passed.")
        return

    database = load_marker_database(SHEET.read_bytes())
    for path in available:
        before = analyse_pdf_bytes(path.read_bytes(), path.name, database)
        print(path.name, "before", [analysis.flags for analysis in before])
        print(
            path.name,
            "default repair scope",
            [analysis.should_fix() for analysis in before],
        )

        result = repair_pdf_bytes(path.read_bytes(), path.name, database)
        print(
            path.name,
            "fixed pages",
            result.fixed_pages,
            "after",
            [analysis.flags for analysis in result.after],
        )

        remaining = [
            analysis
            for analysis in result.after
            if analysis.has_duplicates
            or analysis.all_markers_missing
            or analysis.has_mismatch
        ]
        if remaining:
            raise AssertionError(
                f"Post-repair QC still found marker issues in {path.name}: "
                f"{[analysis.flags for analysis in remaining]}"
            )
        if result.fixed_pages and not gui.repaired_pages_pass_verification(result):
            raise AssertionError(f"Verification helper rejected repaired file {path.name}.")

    # Full worker test: repaired output is written and original is moved only after
    # successful verification. Use one known repairable sample.
    sample = available[-1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_dir = root / "source"
        output_dir = root / "output"
        source_dir.mkdir()
        copied = source_dir / sample.name
        shutil.copy2(sample, copied)
        data = copied.read_bytes()
        analyses = analyse_pdf_bytes(data, copied.name, database)
        pdf_input = gui.PdfInput(copied, copied.name, data, analyses)

        app = object.__new__(gui.WbdMarkerApp)
        app._events = queue.Queue()
        app._repair_worker(
            database,
            [pdf_input],
            output_dir,
            True,
            "TVD",
            "MD",
            1,
            False,
            False,
            True,
        )
        events = []
        while not app._events.empty():
            events.append(app._events.get_nowait())
        fatals = [event for event in events if event[0] == "fatal"]
        if fatals:
            raise AssertionError(f"Worker smoke test failed: {fatals}")
        done = [event[1] for event in events if event[0] == "repair_done"]
        if len(done) != 1:
            raise AssertionError("Worker did not return one repair_done event.")
        payload = done[0]
        assert len(payload.repaired_files) == 1
        assert payload.repaired_files[0].is_file()
        assert len(payload.archived_originals) == 1
        assert payload.archived_originals[0].is_file()
        assert payload.archived_originals[0].parent == source_dir / "repaired"
        assert not copied.exists()

    print("All v2.6 smoke tests passed.")


if __name__ == "__main__":
    main()
