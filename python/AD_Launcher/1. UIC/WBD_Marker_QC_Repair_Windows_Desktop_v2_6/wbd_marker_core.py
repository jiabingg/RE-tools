from __future__ import annotations

import bisect
import csv
import io
import math
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, Iterator, Mapping, Sequence
from xml.etree import ElementTree as ET

import fitz  # PyMuPDF


XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class MarkerToolError(RuntimeError):
    """Raised for a user-correctable input or processing problem."""


@dataclass(frozen=True)
class MarkerPick:
    well_name: str
    api: str
    marker: str
    md: float
    tvd: float | None
    source_sheet: str
    source_row: int


@dataclass
class MarkerDatabase:
    by_api: dict[str, list[MarkerPick]]
    sheet_name: str
    header_row: int
    warnings: list[str] = field(default_factory=list)
    conflicts_by_api: dict[str, list[str]] = field(default_factory=dict)

    @property
    def marker_count(self) -> int:
        return sum(len(v) for v in self.by_api.values())


@dataclass(frozen=True)
class RectData:
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_rect(cls, rect: fitz.Rect) -> "RectData":
        return cls(rect.x0, rect.y0, rect.x1, rect.y1)

    def to_rect(self) -> fitz.Rect:
        return fitz.Rect(self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class TableGeometry:
    zone: RectData
    depth: RectData
    md: RectData
    tvd: RectData
    data_top: float
    data_bottom: float
    source: str


@dataclass(frozen=True)
class ExistingMarker:
    name: str
    depth: float | None
    baseline_y: float
    label_rects: tuple[RectData, ...]
    depth_rects: tuple[RectData, ...]


@dataclass
class PageAnalysis:
    filename: str
    page_number: int
    api: str | None
    well_name: str | None
    expected_markers: list[MarkerPick]
    existing_markers: list[ExistingMarker]
    duplicate_labels: list[str]
    duplicate_depths: list[float]
    missing_expected: list[str]
    unexpected_existing: list[str]
    all_markers_missing: bool
    geometry: TableGeometry | None
    searchable_text: bool
    flags: list[str]
    notes: list[str]

    @property
    def has_duplicates(self) -> bool:
        return bool(self.duplicate_labels or self.duplicate_depths)

    @property
    def has_mismatch(self) -> bool:
        return bool(self.missing_expected or self.unexpected_existing)

    @property
    def can_fix(self) -> bool:
        return (
            self.searchable_text
            and self.api is not None
            and bool(self.expected_markers)
            and self.geometry is not None
            and "ROTATED_PAGE_UNSUPPORTED" not in self.flags
            and "SPREADSHEET_MARKER_CONFLICT" not in self.flags
        )

    def should_fix(self, fix_partial_mismatches: bool = True) -> bool:
        if not self.can_fix:
            return False
        return (
            self.all_markers_missing
            or self.has_duplicates
            or (fix_partial_mismatches and self.has_mismatch)
        )

    def report_row(self, fix_partial_mismatches: bool = True) -> dict[str, object]:
        return {
            "File": self.filename,
            "Page": self.page_number,
            "API": self.api or "",
            "Well": self.well_name or "",
            "Expected markers": len(self.expected_markers),
            "PDF markers": len(self.existing_markers),
            "All missing": "Yes" if self.all_markers_missing else "No",
            "Duplicate labels": "; ".join(self.duplicate_labels),
            "Duplicate depths": "; ".join(format_depth(v) for v in self.duplicate_depths),
            "Missing from PDF": "; ".join(self.missing_expected),
            "Unexpected in PDF": "; ".join(self.unexpected_existing),
            "Flags": "; ".join(self.flags) if self.flags else "OK",
            "Can fix": "Yes" if self.can_fix else "No",
            "Will repair": "Yes" if self.should_fix(fix_partial_mismatches) else "No",
            "Notes": "; ".join(self.notes),
        }


@dataclass
class RepairResult:
    filename: str
    output_bytes: bytes
    before: list[PageAnalysis]
    after: list[PageAnalysis]
    fixed_pages: list[int]
    warnings: list[str]


@dataclass(frozen=True)
class _Span:
    text: str
    rect: RectData
    origin_x: float
    origin_y: float
    size: float


@dataclass(frozen=True)
class _TextLine:
    text: str
    baseline_y: float
    rects: tuple[RectData, ...]
    spans: tuple[_Span, ...]


@dataclass(frozen=True)
class _DepthScale:
    values: tuple[float, ...]
    baselines: tuple[float, ...]

    @property
    def minimum(self) -> float:
        return self.values[0]

    @property
    def maximum(self) -> float:
        return self.values[-1]

    def map(self, value: float, allow_small_extrapolation: bool = True) -> float:
        if len(self.values) < 2:
            raise MarkerToolError("The PDF depth scale does not contain enough points.")

        values = self.values
        ys = self.baselines
        if value < values[0]:
            span = max(values[-1] - values[0], 1.0)
            allowance = max(50.0, 0.03 * span)
            if not allow_small_extrapolation or values[0] - value > allowance:
                raise MarkerToolError(
                    f"Marker depth {value:g} is above the plotted scale ({values[0]:g} to {values[-1]:g})."
                )
            i0, i1 = 0, 1
        elif value > values[-1]:
            span = max(values[-1] - values[0], 1.0)
            allowance = max(50.0, 0.03 * span)
            if not allow_small_extrapolation or value - values[-1] > allowance:
                raise MarkerToolError(
                    f"Marker depth {value:g} is below the plotted scale ({values[0]:g} to {values[-1]:g})."
                )
            i0, i1 = len(values) - 2, len(values) - 1
        else:
            i1 = bisect.bisect_left(values, value)
            if i1 == 0:
                return ys[0]
            if i1 == len(values):
                return ys[-1]
            if math.isclose(values[i1], value, abs_tol=1e-9):
                return ys[i1]
            i0 = i1 - 1

        v0, v1 = values[i0], values[i1]
        y0, y1 = ys[i0], ys[i1]
        if math.isclose(v0, v1, abs_tol=1e-12):
            return (y0 + y1) / 2.0
        fraction = (value - v0) / (v1 - v0)
        return y0 + fraction * (y1 - y0)


# ---------------------------------------------------------------------------
# Spreadsheet loading (.xlsx, no Excel installation required)
# ---------------------------------------------------------------------------

# Same-depth legacy labels that should be suppressed when the canonical
# CalGEM-facing label is also present. Edit this mapping for another project.
SAME_DEPTH_CANONICAL_ALIASES: dict[str, set[str]] = {
    "bofwandbaseusdw": {
        "freeman",
        "freemanbaseoffw",
        "freemanbaseoffreshwater",
        "baseoffw",
        "bfw",
    }
}


_HEADER_ALIASES: dict[str, set[str]] = {
    "well_name": {
        "wellname",
        "legalwellname",
        "well",
    },
    "api": {
        "wellapi",
        "api",
        "apiuwi",
        "apiuwi10",
        "api10",
    },
    "marker": {
        "marker",
        "formation",
        "formationtop",
        "zone",
        "zonename",
    },
    "md": {
        "md",
        "mdft",
        "measureddepth",
        "measureddepthft",
        "markermd",
        "topmd",
    },
    "tvd": {
        "tvd",
        "tvdft",
        "trueverticaldepth",
        "trueverticaldepthft",
        "markertvd",
        "toptvd",
    },
}


def _normalise_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _normalise_marker(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def normalise_api(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Excel may expose a numeric API as 402918516.0.
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        text = text.split(".", 1)[0]
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if len(digits) < 10:
        digits = digits.zfill(10)
    elif len(digits) > 10:
        # WellView diagrams in this workflow use the 10-digit API/UWI field.
        digits = digits[:10]
    return digits


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return result


def _xlsx_column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        raise MarkerToolError(f"Invalid Excel cell reference: {cell_ref!r}")
    result = 0
    for character in match.group(1):
        result = result * 26 + (ord(character) - ord("A") + 1)
    return result - 1


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    strings: list[str] = []
    for item in root.findall(f"{{{XLSX_MAIN_NS}}}si"):
        strings.append(
            "".join(
                text_node.text or ""
                for text_node in item.iter(f"{{{XLSX_MAIN_NS}}}t")
            )
        )
    return strings


def _xlsx_sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets: dict[str, str] = {}
    for rel in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        rel_targets[rel.attrib["Id"]] = rel.attrib["Target"]

    result: list[tuple[str, str]] = []
    sheets = workbook.find(f"{{{XLSX_MAIN_NS}}}sheets")
    if sheets is None:
        return result
    for sheet in sheets:
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(f"{{{XLSX_REL_NS}}}id")
        if not rel_id or rel_id not in rel_targets:
            continue
        target = rel_targets[rel_id]
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = str(PurePosixPath("xl") / target)
        # Normalise paths such as xl/worksheets/../worksheets/sheet1.xml.
        parts: list[str] = []
        for part in PurePosixPath(path).parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        result.append((name, "/".join(parts)))
    return result


def _xlsx_cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> object:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{XLSX_MAIN_NS}}}is")
        if inline is None:
            return ""
        return "".join(
            node.text or "" for node in inline.iter(f"{{{XLSX_MAIN_NS}}}t")
        )

    value_node = cell.find(f"{{{XLSX_MAIN_NS}}}v")
    if value_node is None:
        return ""
    raw = value_node.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type == "b":
        return raw == "1"
    return raw


def _iter_xlsx_rows(
    archive: zipfile.ZipFile, sheet_path: str, shared_strings: Sequence[str]
) -> Iterator[tuple[int, dict[int, object]]]:
    root = ET.fromstring(archive.read(sheet_path))
    sheet_data = root.find(f"{{{XLSX_MAIN_NS}}}sheetData")
    if sheet_data is None:
        return
    for row in sheet_data.findall(f"{{{XLSX_MAIN_NS}}}row"):
        row_number = int(row.attrib.get("r", "0") or 0)
        values: dict[int, object] = {}
        for cell in row.findall(f"{{{XLSX_MAIN_NS}}}c"):
            ref = cell.attrib.get("r")
            if not ref:
                continue
            values[_xlsx_column_index(ref)] = _xlsx_cell_value(cell, shared_strings)
        yield row_number, values


def _identify_header(row: Mapping[int, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for column, value in row.items():
        normalised = _normalise_header(value)
        for role, aliases in _HEADER_ALIASES.items():
            if normalised in aliases and role not in result:
                result[role] = column
    return result


def load_marker_database(xlsx_bytes: bytes) -> MarkerDatabase:
    """Load authoritative marker picks from an .xlsx workbook.

    Required logical columns are API, Marker, and MD. TVD is strongly
    recommended and is required when the app is configured to plot by TVD.
    Header names are matched through common aliases.
    """

    try:
        archive = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    except zipfile.BadZipFile as exc:
        raise MarkerToolError(
            "The marker file is not a valid .xlsx workbook. Convert legacy .xls files to .xlsx first."
        ) from exc

    with archive:
        try:
            shared_strings = _xlsx_shared_strings(archive)
            sheet_paths = _xlsx_sheet_paths(archive)
        except (KeyError, ET.ParseError) as exc:
            raise MarkerToolError("The .xlsx workbook structure could not be read.") from exc

        chosen_sheet: str | None = None
        chosen_header_row: int | None = None
        chosen_header: dict[str, int] | None = None
        chosen_rows: list[tuple[int, dict[int, object]]] | None = None

        for sheet_name, sheet_path in sheet_paths:
            rows = list(_iter_xlsx_rows(archive, sheet_path, shared_strings))
            for row_number, row_values in rows[:30]:
                header = _identify_header(row_values)
                if {"api", "marker", "md"}.issubset(header):
                    chosen_sheet = sheet_name
                    chosen_header_row = row_number
                    chosen_header = header
                    chosen_rows = rows
                    break
            if chosen_header is not None:
                break

        if chosen_header is None or chosen_rows is None or chosen_header_row is None:
            raise MarkerToolError(
                "No worksheet contains the required API, Marker, and MD columns. "
                "Expected headers similar to 'Well API', 'Marker', and 'MD (ft)'."
            )

        warnings: list[str] = []
        by_api: dict[str, list[MarkerPick]] = defaultdict(list)
        for row_number, values in chosen_rows:
            if row_number <= chosen_header_row:
                continue
            api = normalise_api(values.get(chosen_header["api"]))
            marker = str(values.get(chosen_header["marker"], "") or "").strip()
            md = _parse_float(values.get(chosen_header["md"]))
            if not api and not marker and md is None:
                continue
            if not api or not marker or md is None:
                warnings.append(
                    f"Skipped incomplete marker row {row_number} on '{chosen_sheet}'."
                )
                continue
            tvd = (
                _parse_float(values.get(chosen_header["tvd"]))
                if "tvd" in chosen_header
                else None
            )
            well_name = (
                str(values.get(chosen_header["well_name"], "") or "").strip()
                if "well_name" in chosen_header
                else ""
            )
            by_api[api].append(
                MarkerPick(
                    well_name=well_name,
                    api=api,
                    marker=marker,
                    md=md,
                    tvd=tvd,
                    source_sheet=chosen_sheet,
                    source_row=row_number,
                )
            )

        # Preserve spreadsheet authority while removing exact duplicate rows.
        # The supplied workbook contains a legacy Freeman pick for essentially
        # every BOFW / Base USDW pick. When both occur at the same API and depth,
        # the CalGEM-facing canonical label wins. Distinct formations at the same
        # depth are preserved.
        exact_duplicate_count = 0
        legacy_alias_count = 0
        conflicts_by_api: dict[str, list[str]] = {}
        for api, picks in list(by_api.items()):
            unique: list[MarkerPick] = []
            seen: set[tuple[str, float, float | None]] = set()
            for pick in picks:
                key = (
                    _normalise_marker(pick.marker),
                    round(pick.md, 4),
                    round(pick.tvd, 4) if pick.tvd is not None else None,
                )
                if key in seen:
                    exact_duplicate_count += 1
                    continue
                seen.add(key)
                unique.append(pick)

            depth_groups: dict[tuple[float, float | None], list[MarkerPick]] = defaultdict(list)
            for pick in unique:
                depth_groups[(
                    round(pick.md, 2),
                    round(pick.tvd, 2) if pick.tvd is not None else None,
                )].append(pick)

            resolved: list[MarkerPick] = []
            for depth_group in depth_groups.values():
                names = {_normalise_marker(pick.marker) for pick in depth_group}
                suppressed_aliases: set[str] = set()
                for canonical_name, aliases in SAME_DEPTH_CANONICAL_ALIASES.items():
                    if canonical_name in names:
                        suppressed_aliases.update(aliases)
                for pick in depth_group:
                    normalised_name = _normalise_marker(pick.marker)
                    if normalised_name in suppressed_aliases:
                        legacy_alias_count += 1
                        continue
                    resolved.append(pick)

            resolved.sort(key=lambda item: (item.md, item.marker.lower()))
            by_api[api] = resolved

            marker_counts = Counter(_normalise_marker(p.marker) for p in resolved)
            repeated_names = sorted({
                p.marker
                for p in resolved
                if marker_counts[_normalise_marker(p.marker)] > 1
            })
            if repeated_names:
                conflicts_by_api[api] = [
                    "Authoritative spreadsheet repeats marker name(s): "
                    + ", ".join(repeated_names)
                ]

        if exact_duplicate_count:
            warnings.append(
                f"Ignored {exact_duplicate_count:,} exact duplicate spreadsheet row(s)."
            )
        if legacy_alias_count:
            warnings.append(
                f"Resolved {legacy_alias_count:,} legacy Freeman alias row(s) in favor of "
                "'BOFW and Base USDW' at the same API and depth."
            )
        if conflicts_by_api:
            warnings.append(
                f"{len(conflicts_by_api):,} API(s) still contain repeated marker names in the spreadsheet; "
                "those pages will be flagged and not auto-repaired."
            )

        if not by_api:
            raise MarkerToolError("The workbook did not contain any usable marker rows.")

        return MarkerDatabase(
            by_api=dict(by_api),
            sheet_name=chosen_sheet,
            header_row=chosen_header_row,
            warnings=warnings,
            conflicts_by_api=conflicts_by_api,
        )


# ---------------------------------------------------------------------------
# PDF analysis
# ---------------------------------------------------------------------------


def _page_spans(page: fitz.Page) -> list[_Span]:
    spans: list[_Span] = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text", ""))
                if not text.strip():
                    continue
                bbox = fitz.Rect(span["bbox"])
                origin = span.get("origin", (bbox.x0, bbox.y1))
                spans.append(
                    _Span(
                        text=text,
                        rect=RectData.from_rect(bbox),
                        origin_x=float(origin[0]),
                        origin_y=float(origin[1]),
                        size=float(span.get("size", 0.0)),
                    )
                )
    return spans


def _join_line_text(spans: Sequence[_Span]) -> str:
    ordered = sorted(spans, key=lambda item: item.origin_x)
    return " ".join(item.text.strip() for item in ordered if item.text.strip()).strip()


def _group_spans_by_baseline(
    spans: Sequence[_Span], tolerance: float = 1.5
) -> list[_TextLine]:
    groups: list[list[_Span]] = []
    for span in sorted(spans, key=lambda item: (item.origin_y, item.origin_x)):
        if not groups:
            groups.append([span])
            continue
        current_y = statistics.median(s.origin_y for s in groups[-1])
        if abs(span.origin_y - current_y) <= tolerance:
            groups[-1].append(span)
        else:
            groups.append([span])

    result: list[_TextLine] = []
    for group in groups:
        ordered = tuple(sorted(group, key=lambda item: item.origin_x))
        result.append(
            _TextLine(
                text=_join_line_text(ordered),
                baseline_y=float(statistics.median(item.origin_y for item in ordered)),
                rects=tuple(item.rect for item in ordered),
                spans=ordered,
            )
        )
    return result


def _extract_api_from_page(page: fitz.Page, spans: Sequence[_Span]) -> str | None:
    page_height = page.rect.height
    header_spans = [
        span
        for span in spans
        if span.rect.y0 < page_height * 0.35
        and "apiuwi" in _normalise_header(span.text)
    ]
    for header in header_spans:
        candidates = [
            span
            for span in spans
            if header.rect.x0 - 5 <= span.rect.x0 <= header.rect.x1 + 60
            and header.rect.y0 <= span.rect.y0 <= header.rect.y1 + 35
            and span is not header
        ]
        for candidate in sorted(candidates, key=lambda item: (item.rect.y0, item.rect.x0)):
            api = normalise_api(candidate.text)
            if api and len(re.sub(r"\D", "", candidate.text)) >= 9:
                return api

    # Conservative fallback: any 10-digit value in the top third of the page.
    for span in spans:
        if span.rect.y0 > page_height * 0.35:
            continue
        for match in re.finditer(r"(?<!\d)\d{10}(?!\d)", span.text):
            api = normalise_api(match.group(0))
            if api:
                return api
    return None


def _extract_well_name(page: fitz.Page, spans: Sequence[_Span]) -> str | None:
    page_height = page.rect.height
    headers = [
        span
        for span in spans
        if span.rect.y0 < page_height * 0.35
        and _normalise_header(span.text) in {"legalwellname", "wellname"}
    ]
    for header in headers:
        same_header_row = [
            span
            for span in spans
            if span is not header
            and abs(span.origin_y - header.origin_y) <= 2.0
            and span.rect.x0 > header.rect.x1 + 1.0
        ]
        cell_right = (
            min(span.rect.x0 for span in same_header_row) - 1.0
            if same_header_row
            else header.rect.x0 + 110.0
        )
        candidates = [
            span
            for span in spans
            if header.rect.x0 - 3 <= span.rect.x0
            and span.rect.x1 <= cell_right
            and header.rect.y1 - 2 <= span.rect.y0 <= header.rect.y1 + 30
            and span is not header
        ]
        if candidates:
            first_line_y = min(candidate.origin_y for candidate in candidates)
            line = [c for c in candidates if abs(c.origin_y - first_line_y) <= 1.5]
            value = _join_line_text(line)
            if value:
                return value
    return None


def _unique_sorted(values: Iterable[float], tolerance: float = 0.6) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > tolerance:
            result.append(value)
        else:
            result[-1] = (result[-1] + value) / 2.0
    return result


def _long_vertical_boundaries(page: fitz.Page) -> list[float]:
    xs: list[float] = []
    minimum_height = page.rect.height * 0.45
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if rect.height >= minimum_height:
            xs.extend([rect.x0, rect.x1])
        for item in drawing.get("items", []):
            if not item or item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            if abs(p0.x - p1.x) <= 0.8 and abs(p0.y - p1.y) >= minimum_height:
                xs.append(float((p0.x + p1.x) / 2.0))
    return [
        x
        for x in _unique_sorted(xs)
        if page.rect.x0 + 5 < x < page.rect.x1 - 5
    ]


def _find_header_span(
    spans: Sequence[_Span], page: fitz.Page, predicate
) -> _Span | None:
    candidates = [
        span
        for span in spans
        if span.rect.y0 < page.rect.height * 0.32 and predicate(span.text)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.rect.y0)


def _surrounding_boundaries(
    boundaries: Sequence[float], rect: RectData
) -> tuple[float, float] | None:
    left_candidates = [x for x in boundaries if x < rect.x0 - 0.5]
    right_candidates = [x for x in boundaries if x > rect.x1 + 0.5]
    if not left_candidates or not right_candidates:
        return None
    return max(left_candidates), min(right_candidates)


def find_table_geometry(page: fitz.Page, spans: Sequence[_Span] | None = None) -> TableGeometry | None:
    if spans is None:
        spans = _page_spans(page)
    zone_header = _find_header_span(
        spans,
        page,
        lambda text: _normalise_header(text) == "zone",
    )
    depth_header = _find_header_span(
        spans,
        page,
        lambda text: "topzonedepth" in _normalise_header(text),
    )
    boundaries = _long_vertical_boundaries(page)

    if zone_header and depth_header and len(boundaries) >= 6:
        zone_pair = _surrounding_boundaries(boundaries, zone_header.rect)
        depth_pair = _surrounding_boundaries(boundaries, depth_header.rect)
        if zone_pair and depth_pair:
            zone_left, zone_right = zone_pair
            depth_left, depth_right = depth_pair
            # The two detected columns should share a boundary.
            if abs(zone_right - depth_left) <= 2.0:
                next_boundaries = [x for x in boundaries if x > depth_right + 0.5]
                if len(next_boundaries) >= 2:
                    md_right = next_boundaries[0]
                    tvd_right = next_boundaries[1]
                    data_top = max(
                        zone_header.rect.y1 + 4.0,
                        depth_header.rect.y1 + 4.0,
                        page.rect.height * 0.217,
                    )
                    data_bottom = page.rect.height * 0.977
                    return TableGeometry(
                        zone=RectData(zone_left, data_top, zone_right, data_bottom),
                        depth=RectData(depth_left, data_top, depth_right, data_bottom),
                        md=RectData(depth_right, data_top, md_right, data_bottom),
                        tvd=RectData(md_right, data_top, tvd_right, data_bottom),
                        data_top=data_top,
                        data_bottom=data_bottom,
                        source="detected",
                    )

    # Fallback for the standard WellView export used in the supplied files.
    width = page.rect.width
    height = page.rect.height
    if width <= 0 or height <= 0:
        return None
    return TableGeometry(
        zone=RectData(width * (298.8009948730469 / 612.0), height * 0.217, width * (442.8009948730469 / 612.0), height * 0.977),
        depth=RectData(width * (442.8009948730469 / 612.0), height * 0.217, width * (494.6409912109375 / 612.0), height * 0.977),
        md=RectData(width * (494.6409912109375 / 612.0), height * 0.217, width * (518.3989868164062 / 612.0), height * 0.977),
        tvd=RectData(width * (518.3980102539062 / 612.0), height * 0.217, width * (542.1600341796875 / 612.0), height * 0.977),
        data_top=height * 0.217,
        data_bottom=height * 0.977,
        source="fallback-standard-layout",
    )


def _span_in_rect(span: _Span, rect: RectData, x_margin: float = 0.5) -> bool:
    center_x = (span.rect.x0 + span.rect.x1) / 2.0
    return (
        rect.x0 + x_margin <= center_x <= rect.x1 - x_margin
        and rect.y0 <= span.origin_y <= rect.y1
    )


def _parse_pdf_number(text: str) -> float | None:
    compact = text.strip().replace(" ", "").replace(",", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", compact):
        return None
    try:
        value = float(compact)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def extract_existing_markers(
    spans: Sequence[_Span], geometry: TableGeometry
) -> tuple[list[ExistingMarker], list[_Span], list[_Span]]:
    zone_spans = [span for span in spans if _span_in_rect(span, geometry.zone)]
    depth_spans = [span for span in spans if _span_in_rect(span, geometry.depth)]
    zone_lines = [line for line in _group_spans_by_baseline(zone_spans) if line.text]
    depth_lines = _group_spans_by_baseline(depth_spans)

    depth_records: list[tuple[_TextLine, float | None]] = [
        (line, _parse_pdf_number(line.text)) for line in depth_lines
    ]
    used_depth_indices: set[int] = set()
    markers: list[ExistingMarker] = []

    for label_line in zone_lines:
        best_index: int | None = None
        best_distance = float("inf")
        for index, (depth_line, _) in enumerate(depth_records):
            if index in used_depth_indices:
                continue
            distance = abs(depth_line.baseline_y - label_line.baseline_y)
            if distance < best_distance:
                best_distance = distance
                best_index = index
        if best_index is not None and best_distance <= 5.0:
            depth_line, depth_value = depth_records[best_index]
            used_depth_indices.add(best_index)
            depth_rects = depth_line.rects
            baseline = statistics.mean([label_line.baseline_y, depth_line.baseline_y])
        else:
            depth_value = None
            depth_rects = ()
            baseline = label_line.baseline_y
        markers.append(
            ExistingMarker(
                name=label_line.text,
                depth=depth_value,
                baseline_y=float(baseline),
                label_rects=label_line.rects,
                depth_rects=depth_rects,
            )
        )

    # Depth-only entries are still treated as existing marker rows for diagnostics.
    for index, (depth_line, depth_value) in enumerate(depth_records):
        if index in used_depth_indices:
            continue
        if depth_value is None:
            continue
        markers.append(
            ExistingMarker(
                name="",
                depth=depth_value,
                baseline_y=depth_line.baseline_y,
                label_rects=(),
                depth_rects=depth_line.rects,
            )
        )

    markers.sort(key=lambda item: item.baseline_y)
    return markers, zone_spans, depth_spans


def _marker_match(expected: MarkerPick, actual: ExistingMarker, tolerance: float = 1.0) -> bool:
    if _normalise_marker(expected.marker) != _normalise_marker(actual.name):
        return False
    if actual.depth is None:
        return False
    return abs(expected.md - actual.depth) <= tolerance


def _compare_markers(
    expected: Sequence[MarkerPick], actual: Sequence[ExistingMarker]
) -> tuple[list[str], list[str]]:
    used_actual: set[int] = set()
    missing: list[str] = []
    for pick in expected:
        match_index: int | None = None
        for index, row in enumerate(actual):
            if index in used_actual:
                continue
            if _marker_match(pick, row):
                match_index = index
                break
        if match_index is None:
            missing.append(f"{pick.marker} @ {format_depth(pick.md)}")
        else:
            used_actual.add(match_index)

    unexpected: list[str] = []
    for index, row in enumerate(actual):
        if index in used_actual:
            continue
        label = row.name or "(blank label)"
        if row.depth is None:
            unexpected.append(label)
        else:
            unexpected.append(f"{label} @ {format_depth(row.depth)}")
    return missing, unexpected


def analyse_pdf_bytes(
    pdf_bytes: bytes,
    filename: str,
    marker_database: MarkerDatabase,
) -> list[PageAnalysis]:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise MarkerToolError(f"Could not open PDF '{filename}'.") from exc

    analyses: list[PageAnalysis] = []
    try:
        for page_index, page in enumerate(document):
            spans = _page_spans(page)
            searchable = bool(spans)
            api = _extract_api_from_page(page, spans) if searchable else None
            well_name = _extract_well_name(page, spans) if searchable else None
            expected = marker_database.by_api.get(api or "", [])
            geometry = find_table_geometry(page, spans) if searchable else None
            existing: list[ExistingMarker] = []
            notes: list[str] = []
            flags: list[str] = []

            if page.rotation % 360 != 0:
                flags.append("ROTATED_PAGE_UNSUPPORTED")
                notes.append("Rotate the page to 0 degrees before automatic repair.")
            if not searchable:
                flags.append("NO_SEARCHABLE_TEXT")
                notes.append("Image-only PDFs require OCR before this tool can inspect markers.")
            if searchable and api is None:
                flags.append("API_NOT_FOUND")
            if api is not None and not expected:
                flags.append("NO_SPREADSHEET_MARKERS")
            if api is not None and api in marker_database.conflicts_by_api:
                flags.append("SPREADSHEET_MARKER_CONFLICT")
                notes.extend(marker_database.conflicts_by_api[api])
            if searchable and geometry is None:
                flags.append("MARKER_TABLE_NOT_FOUND")

            if geometry is not None:
                existing, _, _ = extract_existing_markers(spans, geometry)

            label_counts = Counter(
                _normalise_marker(row.name) for row in existing if row.name.strip()
            )
            duplicate_labels = [
                next(
                    row.name
                    for row in existing
                    if _normalise_marker(row.name) == normalised
                )
                for normalised, count in label_counts.items()
                if normalised and count > 1
            ]
            row_counts = Counter(
                (_normalise_marker(row.name), round(row.depth, 1))
                for row in existing
                if row.name.strip() and row.depth is not None
            )
            duplicate_depths = sorted({
                depth
                for (name, depth), count in row_counts.items()
                if name and count > 1
            })
            all_missing = bool(expected) and len(existing) == 0
            missing, unexpected = _compare_markers(expected, existing)

            if all_missing:
                flags.append("ALL_MARKERS_MISSING")
            if duplicate_labels:
                flags.append("DUPLICATE_MARKER_LABELS")
            if duplicate_depths:
                flags.append("DUPLICATE_MARKER_DEPTHS")
            if expected and not all_missing and (missing or unexpected):
                flags.append("MARKER_MISMATCH")
            if geometry and geometry.source != "detected":
                notes.append("Used the standard-layout coordinate fallback.")

            analyses.append(
                PageAnalysis(
                    filename=filename,
                    page_number=page_index + 1,
                    api=api,
                    well_name=well_name,
                    expected_markers=list(expected),
                    existing_markers=existing,
                    duplicate_labels=duplicate_labels,
                    duplicate_depths=duplicate_depths,
                    missing_expected=missing,
                    unexpected_existing=unexpected,
                    all_markers_missing=all_missing,
                    geometry=geometry,
                    searchable_text=searchable,
                    flags=flags,
                    notes=notes,
                )
            )
    finally:
        document.close()
    return analyses


# ---------------------------------------------------------------------------
# PDF repair
# ---------------------------------------------------------------------------


def _extract_depth_scale(
    spans: Sequence[_Span], column: RectData
) -> _DepthScale:
    raw_points: list[tuple[float, float]] = []
    for span in spans:
        if not _span_in_rect(span, column):
            continue
        value = _parse_pdf_number(span.text)
        if value is None:
            continue
        raw_points.append((value, span.origin_y))

    if len(raw_points) < 2:
        raise MarkerToolError("Could not read a usable survey depth scale from the PDF.")

    # Group repeated numeric values, then sort by value for interpolation.
    grouped: dict[float, list[float]] = defaultdict(list)
    for value, y in raw_points:
        grouped[round(value, 4)].append(y)
    points = sorted(
        (value, statistics.median(ys)) for value, ys in grouped.items()
    )

    # Survey scales should increase with page Y. Remove isolated outliers rather
    # than allowing a badly parsed number to invert the mapping.
    monotonic: list[tuple[float, float]] = []
    for value, y in points:
        if not monotonic or (value > monotonic[-1][0] and y > monotonic[-1][1] - 0.5):
            monotonic.append((value, y))
    if len(monotonic) < 2:
        raise MarkerToolError("The PDF survey depth scale is not monotonic.")

    return _DepthScale(
        values=tuple(point[0] for point in monotonic),
        baselines=tuple(point[1] for point in monotonic),
    )


def _depth_column_is_non_decreasing(
    spans: Sequence[_Span], column: RectData, tolerance: float = 1.0
) -> bool:
    """Return whether a plotted survey column is invertible by depth.

    Horizontal and highly deviated wells can have TVD values that flatten or
    decrease while MD and page Y continue to increase. A pure TVD-to-Y mapping
    is ambiguous for those wells, so the repair engine must use the MD row
    position to disambiguate marker placement.
    """
    raw_points: list[tuple[float, float]] = []
    for span in spans:
        if not _span_in_rect(span, column):
            continue
        value = _parse_pdf_number(span.text)
        if value is None:
            continue
        raw_points.append((span.origin_y, value))

    if len(raw_points) < 2:
        return False

    # Collapse text fragments sharing the same survey-row baseline.
    grouped: dict[float, list[float]] = defaultdict(list)
    for y, value in raw_points:
        grouped[round(y, 1)].append(value)
    ordered_values = [
        statistics.median(grouped_y_values)
        for _, grouped_y_values in sorted(grouped.items())
    ]
    return all(
        current >= previous - tolerance
        for previous, current in zip(ordered_values, ordered_values[1:])
    )


def _fit_font_size(text: str, maximum_size: float, width: float, minimum_size: float = 4.2) -> float:
    if not text:
        return maximum_size
    unit_length = fitz.get_text_length(text, fontname="helv", fontsize=1.0)
    if unit_length <= 0:
        return maximum_size
    fitted = min(maximum_size, width / unit_length)
    return max(minimum_size, fitted)


def format_depth(value: float, decimals: int = 1) -> str:
    return f"{value:,.{decimals}f}"


def _expanded_redaction_rect(rect: RectData, page: fitz.Page) -> fitz.Rect:
    expanded = fitz.Rect(rect.x0 - 0.6, rect.y0 - 0.35, rect.x1 + 0.6, rect.y1 + 0.35)
    return expanded & page.rect


def _estimate_marker_font_size(existing: Sequence[ExistingMarker], spans: Sequence[_Span], geometry: TableGeometry, page: fitz.Page) -> float:
    sizes = [
        span.size
        for span in spans
        if _span_in_rect(span, geometry.zone) and span.size >= 4.0
    ]
    if sizes:
        return float(statistics.median(sizes))
    return 6.23 * (page.rect.width / 612.0)


def _expected_pick_for_existing(
    row: ExistingMarker, expected: Sequence[MarkerPick], tolerance: float = 1.1
) -> MarkerPick | None:
    if row.depth is None:
        return None
    by_depth = [pick for pick in expected if abs(pick.md - row.depth) <= tolerance]
    if not by_depth:
        return None
    exact_name = [
        pick
        for pick in by_depth
        if _normalise_marker(pick.marker) == _normalise_marker(row.name)
    ]
    return exact_name[0] if exact_name else by_depth[0]


def _estimate_baseline_offset(
    existing: Sequence[ExistingMarker],
    expected: Sequence[MarkerPick],
    scale: _DepthScale,
    plot_basis: str,
    page: fitz.Page,
) -> float:
    offsets: list[float] = []
    for row in existing:
        pick = _expected_pick_for_existing(row, expected)
        if pick is None:
            continue
        plot_value = pick.tvd if plot_basis == "TVD" else pick.md
        if plot_value is None:
            continue
        try:
            mapped = scale.map(plot_value)
        except MarkerToolError:
            continue
        offsets.append(row.baseline_y - mapped)
    if offsets:
        return float(statistics.median(offsets))
    return 0.75 * (page.rect.height / 792.0)




def _resolve_vertical_positions(
    raw_positions: Sequence[float],
    minimum: float,
    maximum: float,
    minimum_gap: float,
) -> list[float]:
    """Separate colliding marker rows while staying inside the table.

    The true plotted location is retained whenever rows are already far enough
    apart. Coincident or nearly coincident formations are nudged only as much as
    needed for readable labels; their exact depths remain printed in the depth
    column.
    """
    if not raw_positions:
        return []
    if len(raw_positions) == 1:
        return [min(max(raw_positions[0], minimum), maximum)]

    available = max(maximum - minimum, 0.0)
    gap = min(minimum_gap, available / (len(raw_positions) - 1))
    assigned = [float(value) for value in raw_positions]

    for index in range(1, len(assigned)):
        assigned[index] = max(assigned[index], assigned[index - 1] + gap)

    if assigned[-1] > maximum:
        assigned[-1] = maximum
        for index in range(len(assigned) - 2, -1, -1):
            assigned[index] = min(assigned[index], assigned[index + 1] - gap)

    if assigned[0] < minimum:
        assigned[0] = minimum
        for index in range(1, len(assigned)):
            assigned[index] = max(assigned[index], assigned[index - 1] + gap)

    # Final clamp for floating-point noise.
    return [min(max(value, minimum), maximum) for value in assigned]
def _repair_page(
    page: fitz.Page,
    analysis: PageAnalysis,
    *,
    plot_basis: str,
    display_basis: str,
    decimals: int,
) -> list[str]:
    if analysis.geometry is None:
        raise MarkerToolError("Marker table geometry is unavailable.")
    geometry = analysis.geometry
    spans = _page_spans(page)
    existing, zone_spans, depth_spans = extract_existing_markers(spans, geometry)

    if plot_basis == "TVD":
        missing_tvd = [pick for pick in analysis.expected_markers if pick.tvd is None]
        if missing_tvd:
            rows = ", ".join(str(pick.source_row) for pick in missing_tvd[:10])
            raise MarkerToolError(
                f"API {analysis.api} has blank TVD values in spreadsheet row(s) {rows}; "
                "nothing was changed on this page."
            )
    if display_basis == "TVD":
        missing_display_tvd = [pick for pick in analysis.expected_markers if pick.tvd is None]
        if missing_display_tvd:
            rows = ", ".join(str(pick.source_row) for pick in missing_display_tvd[:10])
            raise MarkerToolError(
                f"API {analysis.api} has blank TVD values in spreadsheet row(s) {rows}; "
                "nothing was changed on this page."
            )

    warnings: list[str] = []
    marker_font_size = _estimate_marker_font_size(existing, spans, geometry, page)

    zone_x = geometry.zone.x0 + 1.68 * (page.rect.width / 612.0)
    depth_x = geometry.depth.x0 + 1.68 * (page.rect.width / 612.0)
    zone_width = geometry.zone.x1 - zone_x - 1.5
    depth_width = geometry.depth.x1 - depth_x - 1.5

    minimum_baseline = geometry.data_top + marker_font_size + 1.0
    maximum_baseline = geometry.data_bottom - 1.5

    def attempt_placements(basis: str) -> tuple[list[tuple[MarkerPick, float, float]], list[str]]:
        plot_column = geometry.tvd if basis == "TVD" else geometry.md
        try:
            scale = _extract_depth_scale(spans, plot_column)
        except MarkerToolError as exc:
            return [], [str(exc)]

        baseline_offset = _estimate_baseline_offset(
            existing, analysis.expected_markers, scale, basis, page
        )
        rows: list[tuple[MarkerPick, float, float]] = []
        errors: list[str] = []
        for pick in analysis.expected_markers:
            plot_value = pick.tvd if basis == "TVD" else pick.md
            display_value = pick.tvd if display_basis == "TVD" else pick.md
            if plot_value is None or display_value is None:
                errors.append(
                    f"{pick.marker} (spreadsheet row {pick.source_row}) has a blank required depth"
                )
                continue
            try:
                raw_baseline = scale.map(plot_value) + baseline_offset
            except MarkerToolError as exc:
                errors.append(f"{pick.marker}: {exc}")
                continue
            if raw_baseline < minimum_baseline - 1.0 or raw_baseline > maximum_baseline + 1.0:
                errors.append(f"{pick.marker}: plotted position is outside the marker table")
                continue
            rows.append((pick, display_value, raw_baseline))
        return rows, errors

    # TVD is preferred when requested, but MD is the unique survey-row key in
    # these WellView diagrams. Use MD automatically when TVD is non-monotonic,
    # incomplete, outside the plotted TVD range, or otherwise cannot place the
    # complete authoritative marker set. This also handles older sidetrack
    # diagrams whose displayed TVD column is referenced differently from the
    # authoritative marker workbook.
    candidate_bases: list[str]
    non_monotonic_tvd = False
    if plot_basis == "TVD":
        non_monotonic_tvd = not _depth_column_is_non_decreasing(spans, geometry.tvd)
        candidate_bases = ["MD"] if non_monotonic_tvd else ["TVD", "MD"]
    else:
        candidate_bases = ["MD"]

    placement_rows: list[tuple[MarkerPick, float, float]] = []
    placement_errors_by_basis: list[tuple[str, list[str]]] = []
    effective_plot_basis: str | None = None
    for basis in candidate_bases:
        candidate_rows, candidate_errors = attempt_placements(basis)
        if not candidate_errors and len(candidate_rows) == len(analysis.expected_markers):
            placement_rows = candidate_rows
            effective_plot_basis = basis
            break
        placement_errors_by_basis.append((basis, candidate_errors))

    if effective_plot_basis is None:
        details: list[str] = []
        for basis, errors in placement_errors_by_basis:
            basis_detail = "; ".join(errors[:6]) or "incomplete marker placement"
            if len(errors) > 6:
                basis_detail += f"; and {len(errors) - 6} more"
            details.append(f"{basis}: {basis_detail}")
        raise MarkerToolError(
            f"Could not place the complete authoritative marker set for API {analysis.api}: "
            + " | ".join(details)
            + ". Nothing was changed on this page."
        )

    if plot_basis == "TVD" and effective_plot_basis == "MD":
        if non_monotonic_tvd:
            warnings.append(
                f"Page {analysis.page_number}, API {analysis.api}: the PDF TVD survey is "
                "non-monotonic, so marker positions were disambiguated using the MD "
                "survey rows."
            )
        else:
            warnings.append(
                f"Page {analysis.page_number}, API {analysis.api}: the authoritative TVD "
                "picks could not be mapped completely to the PDF TVD scale, so marker "
                "positions were placed using the corresponding MD survey rows."
            )

    placement_rows.sort(key=lambda item: (item[2], item[0].md, item[0].marker.lower()))
    raw_positions = [row[2] for row in placement_rows]
    assigned_positions = _resolve_vertical_positions(
        raw_positions,
        minimum_baseline,
        maximum_baseline,
        marker_font_size + 1.5,
    )
    adjusted = [
        abs(raw - assigned)
        for raw, assigned in zip(raw_positions, assigned_positions)
        if abs(raw - assigned) > 0.5
    ]
    if adjusted:
        warnings.append(
            f"Page {analysis.page_number}, API {analysis.api}: adjusted {len(adjusted)} "
            f"closely spaced marker row(s) by up to {max(adjusted):.1f} PDF point(s) for readability."
        )

    # Rebuild the whole marker/depth set only after all placements pass validation.
    for span in [*zone_spans, *depth_spans]:
        page.add_redact_annot(
            _expanded_redaction_rect(span.rect, page),
            fill=(1, 1, 1),
            cross_out=False,
        )
    if zone_spans or depth_spans:
        page.apply_redactions(images=0, graphics=0, text=0)

    for (pick, display_value, _), baseline_y in zip(placement_rows, assigned_positions):
        label_size = _fit_font_size(pick.marker, marker_font_size, zone_width)
        depth_text = format_depth(display_value, decimals)
        depth_size = _fit_font_size(depth_text, marker_font_size, depth_width)
        page.insert_text(
            fitz.Point(zone_x, baseline_y),
            pick.marker,
            fontname="helv",
            fontsize=label_size,
            color=(0, 0, 0),
            overlay=True,
        )
        page.insert_text(
            fitz.Point(depth_x, baseline_y),
            depth_text,
            fontname="helv",
            fontsize=depth_size,
            color=(0, 0, 0),
            overlay=True,
        )

    return warnings


def repair_pdf_bytes(
    pdf_bytes: bytes,
    filename: str,
    marker_database: MarkerDatabase,
    *,
    fix_partial_mismatches: bool = True,
    plot_basis: str = "TVD",
    display_basis: str = "MD",
    decimals: int = 1,
) -> RepairResult:
    plot_basis = plot_basis.upper()
    display_basis = display_basis.upper()
    if plot_basis not in {"MD", "TVD"}:
        raise ValueError("plot_basis must be 'MD' or 'TVD'.")
    if display_basis not in {"MD", "TVD"}:
        raise ValueError("display_basis must be 'MD' or 'TVD'.")
    if not 0 <= decimals <= 3:
        raise ValueError("decimals must be between 0 and 3.")

    before = analyse_pdf_bytes(pdf_bytes, filename, marker_database)
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise MarkerToolError(f"Could not open PDF '{filename}' for repair.") from exc

    fixed_pages: list[int] = []
    warnings: list[str] = []
    try:
        for page_index, analysis in enumerate(before):
            if not analysis.should_fix(fix_partial_mismatches):
                continue
            page = document[page_index]
            page_warnings = _repair_page(
                page,
                analysis,
                plot_basis=plot_basis,
                display_basis=display_basis,
                decimals=decimals,
            )
            warnings.extend(page_warnings)
            fixed_pages.append(page_index + 1)

        output_bytes = document.tobytes(garbage=4, deflate=True, clean=True)
    finally:
        document.close()

    output_filename = _fixed_filename(filename)
    after = analyse_pdf_bytes(output_bytes, output_filename, marker_database)
    return RepairResult(
        filename=output_filename,
        output_bytes=output_bytes,
        before=before,
        after=after,
        fixed_pages=fixed_pages,
        warnings=warnings,
    )


def _fixed_filename(filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        return filename[:-4] + "_markers_fixed.pdf"
    return filename + "_markers_fixed.pdf"


def analyses_to_csv(
    analyses: Sequence[PageAnalysis],
    fix_partial_mismatches: bool = True,
) -> bytes:
    rows = [
        analysis.report_row(fix_partial_mismatches=fix_partial_mismatches)
        for analysis in analyses
    ]
    if not rows:
        return b""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def render_page_png(pdf_bytes: bytes, page_number: int, dpi: int = 120) -> bytes:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = document[page_number - 1]
        scale = dpi / 72.0
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()
