#!/usr/bin/env python3
"""
ppr_datasheet.py -- Fill a CalGEM Periodic Project Review (PPR) / PxP summary
data sheet from a well + production data pull, and write a full audit trail
into the same workbook.

Three files go in:

  INPUT   <proj>_PPR_SF_input.xlsx    Well List (API + Type), Project info (PAL date)
  DATA    <proj>_PPR_SF_data.xlsx     Basic Data, Top Perf, Summary, Avg Tubing Pres,
                                      Monthly Prod Inj, Daily Inj Pres, WellSTAR
  OUTPUT  <proj>_PPR_SF_output.xlsx   the blank data sheet to fill

One file comes out: a copy of OUTPUT with every value filled as a live formula,
a description beside each value, and four supporting tabs --

  Calculations      inputs, basis, line-by-line derivation, review checklist
  Well Data         one row per API, with the Active/Idle and since-PAL tests
  Injector Detail   one row per injection completion, perforations and gradients
  Monthly Volumes   one row per month, rates and volumes

Run with no arguments for the file-picker window; run with --input/--data/--output
for the command line.  Requires openpyxl.

    pip install openpyxl
    python ppr_datasheet.py
    python ppr_datasheet.py --input in.xlsx --data data.xlsx --output out.xlsx \
        --window 24 --active-basis volume --abandoned-basis pa_only \
        --gradient-source avg_tubing
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import os
import re
import sys
import traceback
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.properties import PageSetupProperties
except ImportError:  # pragma: no cover
    sys.exit("openpyxl is required.  Install it with:  pip install openpyxl")


# ----------------------------------------------------------------------------
# configuration
# ----------------------------------------------------------------------------

WINDOW_CHOICES = {
    "12": "Last 12 complete months",
    "24": "Last 24 complete months (annualized)",
    "all": "Full available history (annualized)",
}
ACTIVE_CHOICES = {
    "volume": "Actual injected/produced volume over the window (PRC 3008(d))",
    "wellstar": "WellSTAR WELL_STATUS",
}
ABANDON_CHOICES = {
    "pa_only": "Plugged & abandoned only",
    "abandon_dte": "Any ABANDON_DTE after the PAL date (includes TA)",
}
GRADIENT_CHOICES = {
    "avg_tubing": "Period-average wellhead tubing pressure",
    "daily_max": "Daily injection pressure, peak day",
    "daily_avg": "Daily injection pressure, per-well average",
}


@dataclass
class Config:
    input_path: str = ""
    data_path: str = ""
    output_path: str = ""
    save_as: str = ""            # blank -> overwrite a copy beside the output file
    window: str = "24"
    active_basis: str = "volume"
    abandoned_basis: str = "pa_only"
    gradient_source: str = "avg_tubing"
    idle_test_months: int = 24   # PRC 3008(d) look-back for the volume test


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def norm(s) -> str:
    """Lowercase, strip punctuation and runs of whitespace -- for fuzzy matching."""
    if s is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def as_date(v):
    """Coerce a cell value to date, whether it arrives as datetime, date or string."""
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def as_num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def api10(v) -> str:
    """Normalize an API number to its printed form, keeping leading zeros."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"[^0-9]", "", s)
    return s.zfill(10) if 0 < len(s) <= 10 else s


def month_of(v):
    d = as_date(v)
    return (d.year, d.month) if d else None


def mlabel(m) -> str:
    return f"{calendar.month_abbr[m[1]]}-{m[0]}"


class SheetError(Exception):
    """Raised when a required sheet or column cannot be located."""


def find_sheet(wb, *keywords, required=True, exclude=()):
    """Locate a worksheet whose name contains all of `keywords` (normalized)."""
    for ws in wb.worksheets:
        n = norm(ws.title)
        if all(k in n for k in keywords) and not any(x in n for x in exclude):
            return ws
    if required:
        raise SheetError(
            f"No sheet matching {' + '.join(keywords)!r}. "
            f"Sheets present: {', '.join(ws.title for ws in wb.worksheets)}"
        )
    return None


def header_row(ws, must_have, scan=8):
    """Find the header row -- data pulls sometimes carry a banner row above it."""
    want = norm(must_have)
    for r in range(1, min(scan, ws.max_row) + 1):
        for c in ws[r]:
            if norm(c.value) == want:
                return r
    for r in range(1, min(scan, ws.max_row) + 1):
        for c in ws[r]:
            if want and want in norm(c.value):
                return r
    raise SheetError(f"'{ws.title}': no header row containing {must_have!r} in the first {scan} rows.")


def col_map(ws, hrow):
    return {norm(c.value): c.column for c in ws[hrow] if c.value is not None}


def pick(cmap, aliases, sheet, required=True):
    """Resolve a column index from a list of acceptable header names."""
    for a in aliases:
        n = norm(a)
        if n in cmap:
            return cmap[n]
    for a in aliases:                      # then loosen to substring
        n = norm(a)
        for k, v in cmap.items():
            if n and n in k:
                return v
    if required:
        raise SheetError(
            f"'{sheet}': could not find a column for {aliases[0]!r} "
            f"(tried {aliases}).  Headers present: {sorted(cmap)}"
        )
    return None


def read_table(ws, hrow, spec):
    """Yield dict rows.  `spec` maps output key -> (aliases, required)."""
    cmap = col_map(ws, hrow)
    idx = {k: pick(cmap, al, ws.title, req) for k, (al, req) in spec.items()}
    for row in ws.iter_rows(min_row=hrow + 1):
        out = {k: (row[i - 1].value if i else None) for k, i in idx.items()}
        if any(v is not None for v in out.values()):
            yield out


# ----------------------------------------------------------------------------
# reading the three workbooks
# ----------------------------------------------------------------------------

# Column aliases.  Add to these lists when a pull uses a new header name --
# nothing else in the program needs to change.
A_API = ["API_NUMBER", "WELL_API_NBR", "WELL API", "API_10", "API"]
A_NAME = ["WELL_NAME", "CMPL_NME", "WELL NAME", "COMPLETION"]

SPEC_BASIC = {
    "api": (A_API, True), "name": (A_NAME, False),
    "wtype": (["WELL_TYPE"], False), "wstate": (["WELL_STATE"], False),
    "eff": (["STATE_EFFECTIVE_DATE"], False), "initprod": (["INITIAL_PROD_DATE"], False),
}
SPEC_PERF = {
    "api": (A_API, True), "cmpl": (A_NAME, True), "zone": (["ENGR_STRG_NME", "STRING"], False),
    "tmd": (["TOP_PERF", "TOP_PERF_MD"], False), "ttvd": (["TOP_PERF_TVD"], True),
    "bmd": (["BTM_PERF", "BOT_PERF"], False), "btvd": (["BTM_PERF_TVD", "BOT_PERF_TVD"], False),
}
SPEC_SUMMARY = {
    "api": (A_API, True), "cmpl": (A_NAME, False), "zone": (["ENGR_STRG_NME"], False),
    "spud": (["SPUD_DTE", "SPUD_DATE"], False),
    "initprod": (["INIT_PROD_DTE"], False), "initinj": (["INIT_INJ_DTE"], False),
    "lastinj": (["LAST_INJ_DTE"], False), "lastprod": (["LAST_PROD_DTE"], False),
    "state": (["CMPL_STATE_TYPE_DESC", "CMPL_STATE"], False),
    "stateeff": (["CMPL_STATE_EFTV_DTTM"], False),
    "abandon": (["ABANDON_DTE", "ABANDON_DATE"], False),
}
SPEC_TUBING = {
    "api": (A_API, True), "cmpl": (A_NAME, False),
    "prsr": (["AVG_WLHD_TBG_PRSR", "WLHD_TBG_PRSR", "TUBING PRESSURE"], True),
}
SPEC_MONTHLY = {
    "api": (["WELL API", "WELL_API_NBR", "API_10", "API_NUMBER"], True),
    "date": (["DATE", "PROD_DATE", "EFTV_DTTM"], True),
    "oil": (["OIL PROD BOPD", "OIL"], True), "water": (["WATER PROD BWPD", "WATER PROD"], True),
    "gas": (["GAS PROD MCFD", "GAS PROD"], False),
    "steam": (["STEAM INJ Per Day", "STEAM INJ"], False),
    "winj": (["WATER INJ Per Day", "WATER INJ"], False),
    "ginj": (["GAS INJ Per Day", "GAS INJ"], False),
}
SPEC_DAILY = {
    "api": (A_API, True), "cmpl": (A_NAME, False),
    "date": (["EFTV_DTTM", "DATE"], True),
    "steam": (["ALOC_STM_INJ_VOL_QTY", "STM_INJ"], False),
    "prsr": (["WLHD_TBG_PRSR_QTY", "WLHD_TBG_PRSR"], True),
}
SPEC_WELLSTAR = {
    "api": (A_API, True), "wtype": (["WELL_TYPE"], False), "status": (["WELL_STATUS"], True),
}


@dataclass
class Pull:
    """Everything read off disk, keyed and normalized."""
    pal: dt.date | None = None
    injectors: set = field(default_factory=set)
    producers: set = field(default_factory=set)
    role_label: dict = field(default_factory=dict)
    name: dict = field(default_factory=dict)
    zone: dict = field(default_factory=dict)
    cstate: dict = field(default_factory=lambda: defaultdict(set))
    spud: dict = field(default_factory=dict)
    spud_proxy: set = field(default_factory=set)
    pa_date: dict = field(default_factory=dict)
    abandon_date: dict = field(default_factory=dict)
    ws_status: dict = field(default_factory=dict)
    ws_type: dict = field(default_factory=dict)
    perf: "OrderedDict" = field(default_factory=OrderedDict)
    tubing: dict = field(default_factory=dict)
    daily: dict = field(default_factory=lambda: defaultdict(list))
    monthly: list = field(default_factory=list)
    months: list = field(default_factory=list)


def load(cfg: Config, log) -> Pull:
    p = Pull()

    # ---- input workbook: well list + PAL date --------------------------------
    inp = openpyxl.load_workbook(cfg.input_path, data_only=True)
    wls = find_sheet(inp, "well", "list")
    hr = header_row(wls, "API")
    cmap = col_map(wls, hr)
    c_api = pick(cmap, A_API, wls.title)
    c_type = pick(cmap, ["TYPE", "ROLE", "PURPOSE"], wls.title)
    for row in wls.iter_rows(min_row=hr + 1):
        a = api10(row[c_api - 1].value)
        t = row[c_type - 1].value
        if not a or not t:
            continue
        t = str(t).strip()
        if norm(t).startswith("inject"):
            p.injectors.add(a)
        else:
            p.producers.add(a)
            p.role_label.setdefault(a, t)
    p.producers -= p.injectors
    for a in p.injectors:
        p.role_label[a] = "Injector"
    log(f"  well list: {len(p.injectors)} injectors, {len(p.producers)} other wells")

    pis = find_sheet(inp, "project", "info", required=False)
    if pis:
        for row in pis.iter_rows(values_only=True):
            for i, v in enumerate(row):
                if norm(v).startswith("pal date") and i + 1 < len(row):
                    p.pal = as_date(row[i + 1])
    if p.pal:
        log(f"  PAL date: {p.pal}")

    # ---- data workbook -------------------------------------------------------
    d = openpyxl.load_workbook(cfg.data_path, data_only=True)

    ws = find_sheet(d, "basic", "data", required=False)
    if ws:
        hr = header_row(ws, "WELL_TYPE")
        for r in read_table(ws, hr, SPEC_BASIC):
            a = api10(r["api"])
            if not a:
                continue
            p.name.setdefault(a, r["name"])
            if r["wstate"]:
                p.cstate[a].add(str(r["wstate"]))
            # Proxy spud date, used only when SPUD_DTE is absent: the EARLIEST date
            # on record for the well.  Taking only the state-effective date would
            # pick up a later TA date and wrongly read as "drilled since PAL".
            for cand in (as_date(r["initprod"]), as_date(r["eff"])):
                if cand and (a not in p.spud or cand < p.spud[a]):
                    p.spud[a] = cand
                    p.spud_proxy.add(a)

    ws = find_sheet(d, "top", "perf")
    hr = header_row(ws, "TOP_PERF_TVD")
    for r in read_table(ws, hr, SPEC_PERF):
        a = api10(r["api"])
        if not a or as_num(r["ttvd"]) is None:
            continue
        p.perf[(a, r["cmpl"])] = {
            "zone": r["zone"], "tmd": as_num(r["tmd"]), "ttvd": as_num(r["ttvd"]),
            "bmd": as_num(r["bmd"]), "btvd": as_num(r["btvd"]),
        }
        p.zone.setdefault(a, r["zone"])
    log(f"  perforations: {len(p.perf)} completions")

    ws = find_sheet(d, "summary", required=False)
    if ws:
        hr = header_row(ws, "CMPL_NME")
        for r in read_table(ws, hr, SPEC_SUMMARY):
            a = api10(r["api"])
            if not a:
                continue
            p.name.setdefault(a, r["cmpl"])
            if r["zone"]:
                p.zone.setdefault(a, r["zone"])
            if r["state"]:
                p.cstate[a].add(str(r["state"]))
            sp = as_date(r["spud"])
            if sp and (a not in p.spud or a in p.spud_proxy):
                p.spud[a] = sp                     # a real spud date always wins
                p.spud_proxy.discard(a)
            elif a in p.spud_proxy or a not in p.spud:
                for cand in (as_date(r["initinj"]), as_date(r["initprod"])):
                    if cand and (a not in p.spud or cand < p.spud[a]):
                        p.spud[a] = cand
                        p.spud_proxy.add(a)
            ab = as_date(r["abandon"]) or as_date(r["stateeff"])
            if ab:
                if a not in p.abandon_date or ab < p.abandon_date[a]:
                    p.abandon_date[a] = ab
                if norm(r["state"]).startswith("permanently abandoned"):
                    p.pa_date[a] = ab

    ws = find_sheet(d, "wellstar", required=False)
    if ws:
        hr = header_row(ws, "WELL_STATUS")
        for r in read_table(ws, hr, SPEC_WELLSTAR):
            a = api10(r["api"])
            if a:
                p.ws_status[a] = r["status"]
                p.ws_type[a] = r["wtype"]
        log(f"  WellSTAR: {len(p.ws_status)} wells")

    ws = find_sheet(d, "tubing", required=False) or find_sheet(d, "tbg", required=False)
    if ws:
        hr = header_row(ws, "WLHD_TBG_PRSR")
        for r in read_table(ws, hr, SPEC_TUBING):
            a = api10(r["api"])
            if a:
                p.tubing[(a, r["cmpl"])] = as_num(r["prsr"])

    ws = find_sheet(d, "daily", required=False)
    if ws:
        hr = header_row(ws, "WLHD_TBG_PRSR")
        for r in read_table(ws, hr, SPEC_DAILY):
            a = api10(r["api"])
            pr = as_num(r["prsr"])
            if a and pr is not None:
                p.daily[(a, r["cmpl"])].append((as_date(r["date"]), as_num(r["steam"]), pr))
        if p.daily:
            log(f"  daily injection pressures: {sum(len(v) for v in p.daily.values())} readings")

    ws = (find_sheet(d, "monthly", required=False)
          or find_sheet(d, "prod", "inj", required=False)
          or find_sheet(d, "inj", "rate", required=False))
    if ws is None:
        raise SheetError("No monthly production/injection sheet found.")
    hr = header_row(ws, "OIL")
    for r in read_table(ws, hr, SPEC_MONTHLY):
        a = api10(r["api"])
        m = month_of(r["date"])
        if not a or not m:
            continue
        p.monthly.append({
            "api": a, "m": m,
            "oil": as_num(r["oil"]) or 0.0, "water": as_num(r["water"]) or 0.0,
            "gas": as_num(r["gas"]) or 0.0, "steam": as_num(r["steam"]) or 0.0,
            "winj": as_num(r["winj"]) or 0.0, "ginj": as_num(r["ginj"]) or 0.0,
        })
    p.months = sorted({r["m"] for r in p.monthly})
    log(f"  monthly rates: {len(p.monthly)} rows, {mlabel(p.months[0])} to {mlabel(p.months[-1])}")

    missing_inj = [a for a in p.injectors if a not in {k[0] for k in p.perf}]
    if missing_inj:
        log(f"  ! {len(missing_inj)} injector(s) have no perforation record: {', '.join(sorted(missing_inj)[:5])}")
    return p


# ----------------------------------------------------------------------------
# the calculation
# ----------------------------------------------------------------------------

@dataclass
class Result:
    window: list = field(default_factory=list)
    partial: tuple | None = None
    act_months: list = field(default_factory=list)
    mrate: dict = field(default_factory=dict)
    wellsum: dict = field(default_factory=dict)
    inj_active: set = field(default_factory=set)
    prd_active: set = field(default_factory=set)
    inj_detail: list = field(default_factory=list)   # (api, cmpl, perf, pressure, injecting)
    n_inj_active_cmpl: int = 0
    notes: list = field(default_factory=list)


def compute(cfg: Config, p: Pull, log) -> Result:
    R = Result()

    R.partial = p.months[-1]
    complete = p.months[:-1]
    if not complete:
        raise SheetError("Only one month of rate data -- nothing to report on.")
    n = len(complete) if cfg.window == "all" else min(int(cfg.window), len(complete))
    R.window = complete[-n:]
    R.act_months = [m for m in p.months if m >= complete[-min(cfg.idle_test_months, len(complete)):][0]]
    log(f"  reporting window: {mlabel(R.window[0])} to {mlabel(R.window[-1])} "
        f"({len(R.window)} months); {mlabel(R.partial)} excluded as partial")

    mrate = defaultdict(lambda: dict(oil=0.0, water=0.0, gas=0.0, steam=0.0, winj=0.0, ginj=0.0))
    wellsum = defaultdict(lambda: {"inj": 0.0, "prod": 0.0})
    win = set(R.window)
    act = set(R.act_months)
    for r in p.monthly:
        if r["m"] in act:
            w = wellsum[r["api"]]
            w["inj"] += r["steam"] + r["winj"] + r["ginj"]
            w["prod"] += r["oil"] + r["water"]
        if r["m"] in win:
            v = mrate[r["m"]]
            for k in ("oil", "water", "gas", "steam", "winj", "ginj"):
                v[k] += r[k]
    R.mrate = {m: mrate[m] for m in R.window}
    R.wellsum = wellsum

    if cfg.active_basis == "wellstar":
        R.inj_active = {a for a in p.injectors if norm(p.ws_status.get(a)) == "active"}
        R.prd_active = {a for a in p.producers if norm(p.ws_status.get(a)) == "active"}
    else:
        R.inj_active = {a for a in p.injectors if wellsum[a]["inj"] > 0}
        R.prd_active = {a for a in p.producers if wellsum[a]["prod"] > 0}
    log(f"  injectors {len(R.inj_active)} active / {len(p.injectors) - len(R.inj_active)} idle; "
        f"producers {len(R.prd_active)} active / {len(p.producers) - len(R.prd_active)} idle")

    # injection completions, currently-injecting first so AVERAGE/MAX can use a
    # contiguous range on the Injector Detail tab
    inj_by_volume = {a for a in p.injectors if wellsum[a]["inj"] > 0}
    rows = []
    for (a, cmpl), v in p.perf.items():
        if a not in p.injectors:
            continue
        rows.append([a, cmpl, v, pressure_for(cfg, p, a, cmpl), a in inj_by_volume])
    rows.sort(key=lambda x: (not x[4], x[0], str(x[1])))
    R.inj_detail = rows
    R.n_inj_active_cmpl = sum(1 for x in rows if x[4])
    if R.n_inj_active_cmpl == 0:
        R.notes.append("No injection completion shows volume in the window -- gradient lines "
                       "fall back to every injector with a recorded pressure.")
        for x in rows:
            x[4] = True
        R.n_inj_active_cmpl = len(rows)

    missing = [x[1] for x in rows[:R.n_inj_active_cmpl] if x[3] is None]
    if missing:
        R.notes.append(f"{len(missing)} injecting completion(s) have no wellhead pressure on "
                       f"record ({', '.join(str(m) for m in missing[:6])}) -- they are left out "
                       "of the gradient average.")
    return R


def pressure_for(cfg: Config, p: Pull, a: str, cmpl):
    """Wellhead injection pressure for one completion, per the chosen source."""
    if cfg.gradient_source.startswith("daily"):
        rows = p.daily.get((a, cmpl)) or []
        inj = [(d, s, pr) for d, s, pr in rows if s]     # days with injection volume
        use = inj or rows
        vals = [pr for _, _, pr in use if pr is not None]
        if vals:
            return max(vals) if cfg.gradient_source == "daily_max" else sum(vals) / len(vals)
    v = p.tubing.get((a, cmpl))
    if v is None:                                        # fall back to any completion on the API
        for (aa, _), vv in p.tubing.items():
            if aa == a and vv is not None:
                return vv
    return v


# ----------------------------------------------------------------------------
# locating the labelled cells on the blank data sheet
# ----------------------------------------------------------------------------

SECTIONS = [
    ("inj_wells", "injection wells associated with project"),
    ("prod_wells", "production wells associated with project"),
    ("inj_data", "injection data"),
    ("prod_data", "production data"),
]
LINES = [
    # key,            section,      label fragment,                            number format
    ("inj_active",    "inj_wells",  "count of active wells",                   "0"),
    ("inj_idle",      "inj_wells",  "count of idle wells",                     "0"),
    ("inj_drilled",   "inj_wells",  "drilled since pal",                       "0"),
    ("inj_abandoned", "inj_wells",  "abandoned since pal",                     "0"),
    ("inj_total",     "inj_wells",  "present total number of wells",           "0"),
    ("prd_active",    "prod_wells", "active",                                  "0"),
    ("prd_idle",      "prod_wells", "idle",                                    "0"),
    ("prd_drilled",   "prod_wells", "drilled since pal",                       "0"),
    ("prd_abandoned", "prod_wells", "abandoned since pal",                     "0"),
    ("prd_total",     "prod_wells", "present total number of wells",           "0"),
    ("depth",         "inj_data",   "depth of injection",                      "@"),
    ("masp",          "inj_data",   "masp",                                    "#,##0"),
    ("grad_avg",      "inj_data",   "average pressure gradient",               "0.000"),
    ("grad_max",      "inj_data",   "maximum pressure gradient",               "0.000"),
    ("inj_annual",    "inj_data",   "total annual injection volume",           "#,##0"),
    ("inj_avg_mo",    "inj_data",   "average monthly volume",                  "#,##0"),
    ("inj_max_mo",    "inj_data",   "maximum monthly volume",                  "#,##0"),
    ("oil_avg",       "prod_data",  "average produced oil rate",               "#,##0.0"),
    ("oil_max",       "prod_data",  "maximum produced oil rate",               "#,##0.0"),
    ("wat_avg",       "prod_data",  "average produced water rate",             "#,##0.0"),
    ("wat_max",       "prod_data",  "maximum produced water rate",             "#,##0.0"),
    ("wcut",          "prod_data",  "average percent water cut",               "0.0%"),
]


def map_datasheet(ws):
    """Find the row of every labelled line, and the PAL-date cell if present.

    Rows are located by label text, not by position, because the blank form gets
    re-issued with rows shifted.
    """
    labels = {r: norm(ws.cell(r, 1).value) for r in range(1, ws.max_row + 1)}
    bounds = {}
    for key, frag in SECTIONS:
        for r, t in labels.items():
            if t.startswith(frag):
                bounds[key] = r
                break
    if not bounds:
        raise SheetError("This does not look like a PPR data sheet -- none of the section "
                         "headings ('Injection wells associated with project', etc.) were found "
                         "in column A.")
    starts = sorted(bounds.items(), key=lambda kv: kv[1])
    span = {}
    for i, (key, r) in enumerate(starts):
        end = starts[i + 1][1] - 1 if i + 1 < len(starts) else ws.max_row
        span[key] = (r + 1, end)

    rows, used = {}, set()
    for key, sect, frag, _ in LINES:
        if sect not in span:
            continue
        lo, hi = span[sect]
        for r in range(lo, hi + 1):
            if r in used or not labels.get(r):
                continue
            if frag in labels[r]:
                rows[key] = r
                used.add(r)
                break

    pal_cell = None
    for r, t in labels.items():
        if t.startswith("pal date"):
            pal_cell = f"B{r}"
            break

    missing = [k for k, _, _, _ in LINES if k not in rows]
    return rows, pal_cell, missing


# ----------------------------------------------------------------------------
# writing the workbook
# ----------------------------------------------------------------------------

FONT = "Aptos Narrow"
BLUE = Font(name=FONT, sz=11, color="0000FF")
BLUE_I = Font(name=FONT, sz=11, color="0000FF", italic=True)
BLK = Font(name=FONT, sz=11)
BOLD = Font(name=FONT, sz=11, bold=True)
HDR = Font(name=FONT, sz=11, bold=True, color="FFFFFF")
TITLE = Font(name=FONT, sz=14, bold=True)
SEC = Font(name=FONT, sz=12, bold=True, color="1F3864")
RED = Font(name=FONT, sz=11, color="C00000", bold=True)
GREY = Font(name=FONT, sz=10, italic=True, color="595959")
NOTE = Font(name=FONT, sz=10, italic=True, color="404040")
NOTE_RED = Font(name=FONT, sz=10, italic=True, color="C00000")
HFILL = PatternFill("solid", fgColor="1F3864")
YFILL = PatternFill("solid", fgColor="FFFFCC")
_thin = Side(style="thin", color="BFBFBF")
BOX = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def put(ws, coord, val, font=BLK, fmt=None, fill=None, align=None, border=False, wrap=False):
    c = ws[coord]
    c.value = val
    c.font = font
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if align or wrap:
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    if border:
        c.border = BOX
    return c


def state_str(s):
    order = ["Operational", "Temporarily Abandoned", "Permanently Abandoned", "Canceled"]
    known = [x for x in order if x in s]
    return " / ".join(known or sorted(str(x) for x in s if x))


def build(cfg: Config, p: Pull, R: Result, log) -> str:
    wb = openpyxl.load_workbook(cfg.output_path)
    DS = wb.worksheets[0]
    for name in ("Calculations", "Well Data", "Injector Detail", "Monthly Volumes"):
        if name in wb.sheetnames:
            del wb[name]

    rows, pal_cell, missing = map_datasheet(DS)
    if missing:
        log(f"  ! no row found for: {', '.join(missing)} -- those lines are left blank")
    pal = p.pal or as_date(DS[pal_cell].value) if pal_cell else p.pal
    if pal is None:
        raise SheetError("No PAL date found on the Project info tab or the data sheet. "
                         "Add it before running.")
    if pal_cell is None:                       # park it somewhere the formulas can point at
        pal_cell = "B1"
        put(DS, "A1", "PAL date", BOLD)
        put(DS, pal_cell, dt.datetime.combine(pal, dt.time()), BLUE, "yyyy-mm-dd")
    else:
        DS[pal_cell].value = dt.datetime.combine(pal, dt.time())
        DS[pal_cell].number_format = "yyyy-mm-dd"

    win_lbl = f"{mlabel(R.window[0])} to {mlabel(R.window[-1])}"
    act_lbl = f"{mlabel(R.act_months[0])} to {mlabel(R.act_months[-1])}"
    days = {m: calendar.monthrange(*m)[1] for m in R.window}
    tot_days = sum(days.values())

    # ---------------- Monthly Volumes ----------------------------------------
    mv = wb.create_sheet("Monthly Volumes")
    put(mv, "A1", f"Monthly roll-up of the rate data in {os.path.basename(cfg.data_path)}", TITLE)
    put(mv, "A2", f"Reporting window: {win_lbl} ({len(R.window)} months, {tot_days} days). "
                  f"{mlabel(R.partial)} is excluded as a partial month at the date of the pull. "
                  "Blue = summed straight from the source (monthly average daily rates, all wells). "
                  "Black = calculated here.", GREY)
    heads = ["Month", "Days in\nmonth", "Sum of well oil\nrates (BOPD)",
             "Sum of well water\nrates (BWPD)", "Sum of well gas\nrates (MCFD)",
             "Sum of well steam\ninj rates (B/D)", "Oil volume\n(bbl)", "Water volume\n(bbl)",
             "Gas volume\n(mcf)", "Injection volume\n(bbl water eq.)", "Field oil rate\n(BOPD)",
             "Field water rate\n(BWPD)"]
    for j, h in enumerate(heads, 1):
        put(mv, f"{get_column_letter(j)}4", h, HDR, fill=HFILL, align="center", border=True, wrap=True)
    r0 = 5
    for i, m in enumerate(R.window):
        r = r0 + i
        v = R.mrate[m]
        put(mv, f"A{r}", mlabel(m), BLK, border=True)
        put(mv, f"B{r}", days[m], BLUE, "0", border=True)
        put(mv, f"C{r}", round(v["oil"], 2), BLUE, "#,##0.00", border=True)
        put(mv, f"D{r}", round(v["water"], 2), BLUE, "#,##0.00", border=True)
        put(mv, f"E{r}", round(v["gas"], 2), BLUE, "#,##0.00", border=True)
        put(mv, f"F{r}", round(v["steam"] + v["winj"] + v["ginj"], 2), BLUE, "#,##0.00", border=True)
        for out, src in zip("GHIJ", "CDEF"):
            put(mv, f"{out}{r}", f"={src}{r}*B{r}", BLK, "#,##0", border=True)
        put(mv, f"K{r}", f"=G{r}/B{r}", BLK, "#,##0.0", border=True)
        put(mv, f"L{r}", f"=H{r}/B{r}", BLK, "#,##0.0", border=True)
    rl = r0 + len(R.window) - 1
    tr = rl + 1
    put(mv, f"A{tr}", f"TOTAL ({len(R.window)} months)", BOLD, border=True)
    for col in "BGHIJ":
        put(mv, f"{col}{tr}", f"=SUM({col}{r0}:{col}{rl})", BOLD, "#,##0", border=True)
    mv.freeze_panes = "A5"
    for col, w in zip("ABCDEFGHIJKL", [12, 8, 15, 15, 15, 15, 13, 13, 13, 16, 13, 13]):
        mv.column_dimensions[col].width = w
    mv.row_dimensions[4].height = 34
    MV_F, MV_L = r0, rl

    # ---------------- Well Data ----------------------------------------------
    wd = wb.create_sheet("Well Data")
    basis_txt = ("CalGEM's WellSTAR WELL_STATUS"
                 if cfg.active_basis == "wellstar" else
                 f"actual injection or production volume over {act_lbl} (PRC 3008(d) idle-well test)")
    put(wd, "A1", f"Well-by-well classification -- every API on the Well List tab of "
                  f"{os.path.basename(cfg.input_path)}", TITLE)
    put(wd, "A2", f"Blue = from the data pull. Black = calculated here. Active/Idle is decided by "
                  f"{basis_txt}; the other basis is carried alongside so the two can be compared, "
                  f"and column O flags every well where they disagree. 'Drilled since PAL' and "
                  f"'Abandoned since PAL' test against the PAL date in '{DS.title}'!{pal_cell}. "
                  "A spud date shown in italics is a proxy (the state-effective date) for a well "
                  "with no SPUD_DTE on record.", GREY)
    h2 = ["API (10)", "Well name", "Role in project", "Reservoir\nstring",
          "Completion state\n(company records)", "WellSTAR\nwell type", "WellSTAR\nstatus",
          "Spud date", "Perm. abandon\ndate", "Any abandon\ndate",
          f"Sum of monthly inj\nrates in window (b/d)", f"Sum of monthly prod\nrates in window (b/d)",
          "Status", "Drilled\nsince PAL", "Abandoned\nsince PAL", "Bases\nagree?"]
    for j, h in enumerate(h2, 1):
        put(wd, f"{get_column_letter(j)}4", h, HDR, fill=HFILL, align="center", border=True, wrap=True)
    r = 5
    for a in sorted(p.injectors) + sorted(p.producers):
        inj = a in p.injectors
        put(wd, f"A{r}", a, BLUE, "@", border=True)
        put(wd, f"B{r}", p.name.get(a, ""), BLUE, border=True)
        put(wd, f"C{r}", "Injector" if inj else p.role_label.get(a, "Area of Review"), BLUE, border=True)
        put(wd, f"D{r}", p.zone.get(a, ""), BLUE, border=True)
        put(wd, f"E{r}", state_str(p.cstate.get(a, set())), BLUE, border=True)
        put(wd, f"F{r}", p.ws_type.get(a, ""), BLUE, border=True, align="center")
        put(wd, f"G{r}", p.ws_status.get(a, ""), BLUE, border=True, align="center")
        sp = p.spud.get(a)
        put(wd, f"H{r}", dt.datetime.combine(sp, dt.time()) if sp else None,
            BLUE_I if a in p.spud_proxy else BLUE, "yyyy-mm-dd", border=True)
        pa = p.pa_date.get(a)
        put(wd, f"I{r}", dt.datetime.combine(pa, dt.time()) if pa else None, BLUE, "yyyy-mm-dd", border=True)
        ab = p.abandon_date.get(a)
        put(wd, f"J{r}", dt.datetime.combine(ab, dt.time()) if ab else None, BLUE, "yyyy-mm-dd", border=True)
        put(wd, f"K{r}", round(R.wellsum[a]["inj"], 2), BLUE, "#,##0.00", border=True)
        put(wd, f"L{r}", round(R.wellsum[a]["prod"], 2), BLUE, "#,##0.00", border=True)
        if cfg.active_basis == "wellstar":
            put(wd, f"M{r}", f'=IF(EXACT(LOWER(G{r}),"active"),"Active","Idle")', BLK, border=True, align="center")
        else:
            put(wd, f"M{r}", f'=IF(C{r}="Injector",IF(K{r}>0,"Active","Idle"),IF(L{r}>0,"Active","Idle"))',
                BLK, border=True, align="center")
        put(wd, f"N{r}", f"=IF(N(H{r})=0,\"Unknown\",IF(H{r}>'{DS.title}'!${pal_cell[0]}${pal_cell[1:]},\"Yes\",\"No\"))",
            BLK, border=True, align="center")
        abcol = "I" if cfg.abandoned_basis == "pa_only" else "J"
        put(wd, f"O{r}", f"=IF(N({abcol}{r})=0,\"No\",IF({abcol}{r}>'{DS.title}'!${pal_cell[0]}${pal_cell[1:]},\"Yes\",\"No\"))",
            BLK, border=True, align="center")
        other = ('=IF(OR(G{r}="",G{r}=M{r}),"yes","NO")' if cfg.active_basis != "wellstar"
                 else '=IF(M{r}=IF(C{r}="Injector",IF(K{r}>0,"Active","Idle"),IF(L{r}>0,"Active","Idle")),"yes","NO")')
        put(wd, f"P{r}", other.format(r=r), BLK, border=True, align="center")
        r += 1
    WD_F, WD_L = 5, r - 1
    wd.freeze_panes = "C5"
    for col, w in zip("ABCDEFGHIJKLMNOP",
                      [13, 15, 15, 14, 22, 10, 10, 12, 14, 13, 19, 19, 10, 10, 11, 9]):
        wd.column_dimensions[col].width = w
    wd.row_dimensions[4].height = 44

    # ---------------- Injector Detail ----------------------------------------
    idt = wb.create_sheet("Injector Detail")
    gsrc = GRADIENT_CHOICES[cfg.gradient_source]
    put(idt, "A1", "Injection completions -- perforated interval and surface injection pressure", TITLE)
    put(idt, "A2", f"One row per injection completion ({len(R.inj_detail)} completions across "
                   f"{len(p.injectors)} API numbers). Pressure source: {gsrc.lower()}. "
                   "Surface pressure gradient = wellhead injection pressure / top-perf TVD. "
                   "Rows are sorted so the currently-injecting completions come first, and only "
                   "those feed the gradient lines -- an idle injector's recorded pressure is a "
                   "shut-in reading near zero and would pull the average down. A bottom-perf TVD "
                   "shown in italics is the top-perf TVD repeated, for a pull with no bottom-perf "
                   "column.", GREY)
    h3 = ["API (10)", "Completion", "Reservoir\nstring", "Top perf\nMD (ft)", "Top perf\nTVD (ft)",
          "Btm perf\nMD (ft)", "Btm perf\nTVD (ft)", "Wellhead inj\npressure (psi)",
          "Surface pressure\ngradient (psi/ft)", "Injecting in\nwindow?"]
    for j, h in enumerate(h3, 1):
        put(idt, f"{get_column_letter(j)}4", h, HDR, fill=HFILL, align="center", border=True, wrap=True)
    r = 5
    for a, cmpl, v, pr, yes in R.inj_detail:
        put(idt, f"A{r}", a, BLUE, "@", border=True)
        put(idt, f"B{r}", cmpl, BLUE, border=True)
        put(idt, f"C{r}", v["zone"], BLUE, border=True)
        for col, k in zip("DEF", ("tmd", "ttvd", "bmd")):
            put(idt, f"{col}{r}", v[k], BLUE, "#,##0.0", border=True)
        # No bottom-perf column in the pull -> fall back to top-perf TVD, in italics,
        # so the depth-of-injection formula never reads a blank as zero.
        put(idt, f"G{r}", v["btvd"] if v["btvd"] is not None else v["ttvd"],
            BLUE if v["btvd"] is not None else BLUE_I, "#,##0.0", border=True)
        put(idt, f"H{r}", pr, BLUE, "#,##0.00", border=True)
        put(idt, f"I{r}", f'=IF(OR(N(H{r})=0,N(E{r})=0),"",H{r}/E{r})', BLK, "0.000", border=True)
        put(idt, f"J{r}", "Yes" if yes else "No", BLK, border=True, align="center")
        r += 1
    ID_F, ID_L = 5, r - 1
    ID_A = ID_F + R.n_inj_active_cmpl - 1
    idt.freeze_panes = "C5"
    for col, w in zip("ABCDEFGHIJ", [13, 15, 14, 11, 11, 11, 11, 17, 18, 13]):
        idt.column_dimensions[col].width = w
    idt.row_dimensions[4].height = 34
    put(idt, f"A{r + 1}", f"Rows {ID_F}-{ID_A} are the {R.n_inj_active_cmpl} completions that "
                          f"injected during the window; rows {ID_A + 1}-{ID_L} did not.", GREY)

    # ---------------- figures used in the prose ------------------------------
    T = {k: sum(R.mrate[m][k] * days[m] for m in R.window)
         for k in ("oil", "water", "gas", "steam", "winj", "ginj")}
    T["inj"] = T["steam"] + T["winj"] + T["ginj"]
    max_inj_m = max(R.window, key=lambda m: (R.mrate[m]["steam"] + R.mrate[m]["winj"] + R.mrate[m]["ginj"]) * days[m])
    max_oil_m = max(R.window, key=lambda m: R.mrate[m]["oil"])
    max_wat_m = max(R.window, key=lambda m: R.mrate[m]["water"])
    grads = [(pr / v["ttvd"], cmpl, pr, v["ttvd"])
             for a, cmpl, v, pr, yes in R.inj_detail[:R.n_inj_active_cmpl]
             if pr is not None and v["ttvd"]]
    grads.sort()
    g_lo, g_hi = (grads[0][0], grads[-1][0]) if grads else (0, 0)
    g_top = grads[-1] if grads else ("", "", 0, 0)
    p_avg = sum(g[2] for g in grads) / len(grads) if grads else 0
    ttvds = [v["ttvd"] for _, _, v, _, _ in R.inj_detail if v["ttvd"]]
    btvds = [v["btvd"] or v["ttvd"] for _, _, v, _, _ in R.inj_detail if (v["btvd"] or v["ttvd"])]
    a_ttvds = [v["ttvd"] for _, _, v, _, y in R.inj_detail[:R.n_inj_active_cmpl] if v["ttvd"]]
    a_btvds = [v["btvd"] or v["ttvd"] for _, _, v, _, y in R.inj_detail[:R.n_inj_active_cmpl]]
    fluid = ("steam" if T["steam"] > max(T["winj"], T["ginj"])
             else "water" if T["winj"] >= T["ginj"] else "gas")

    def count(role, col, val):
        return f'COUNTIFS(\'Well Data\'!$C${WD_F}:$C${WD_L},"{role}",' \
               f'\'Well Data\'!${col}${WD_F}:${col}${WD_L},"{val}")'

    n_inj_idle = len(p.injectors) - len(R.inj_active)
    n_prd_idle = len(p.producers) - len(R.prd_active)
    ws_inj_act = sum(1 for a in p.injectors if norm(p.ws_status.get(a)) == "active")
    ws_prd_act = sum(1 for a in p.producers if norm(p.ws_status.get(a)) == "active")
    drilled_inj = [p.name.get(a, a) for a in p.injectors if p.spud.get(a) and p.spud[a] > pal]
    drilled_prd = [p.name.get(a, a) for a in p.producers if p.spud.get(a) and p.spud[a] > pal]
    abcol = "I" if cfg.abandoned_basis == "pa_only" else "J"
    ab_src = ("the permanent-abandonment date" if cfg.abandoned_basis == "pa_only"
              else "ABANDON_DTE (any abandonment, temporary or permanent)")
    ta_inj = sum(1 for a in p.injectors if p.abandon_date.get(a) and p.abandon_date[a] > pal
                 and a not in p.pa_date)
    ta_prd = sum(1 for a in p.producers if p.abandon_date.get(a) and p.abandon_date[a] > pal
                 and a not in p.pa_date)
    role_prd = next(iter({p.role_label.get(a) for a in p.producers} - {None}), "Area of Review")

    half = len(R.window) // 2
    h1 = sum((R.mrate[m]["steam"] + R.mrate[m]["winj"] + R.mrate[m]["ginj"]) * days[m] for m in R.window[:half])
    h2v = sum((R.mrate[m]["steam"] + R.mrate[m]["winj"] + R.mrate[m]["ginj"]) * days[m] for m in R.window[half:])
    trend = ""
    if half and h1:
        trend = (f" Injection {'fell' if h2v < h1 else 'rose'} from {h1:,.0f} bbl in the first half "
                 f"of the window to {h2v:,.0f} bbl in the second, a change of {abs(h2v - h1) / h1:.1%}.")

    MVS, IDS = "'Monthly Volumes'", "'Injector Detail'"
    spec = {
        "inj_active": (
            f"={count('Injector', 'M', 'Active')}",
            f"Injector API numbers judged active on the chosen basis ({basis_txt}). "
            + (f"WellSTAR shows {ws_inj_act} active injectors." if p.ws_status and cfg.active_basis != "wellstar" else ""),
            f"Well Data C/M rows {WD_F}-{WD_L}"),
        "inj_idle": (
            f"={count('Injector', 'M', 'Idle')}",
            f"The remaining {n_inj_idle} injectors. Temporarily abandoned wells are counted here, "
            "not on the abandoned line -- they are still on the books.",
            f"Well Data C/M rows {WD_F}-{WD_L}"),
        "inj_drilled": (
            f"={count('Injector', 'N', 'Yes')}",
            f"Injectors whose spud date falls after the PAL date ({pal})"
            + (f": {', '.join(str(x) for x in drilled_inj[:8])}." if drilled_inj else " -- none."),
            f"Well Data C/N rows {WD_F}-{WD_L}; PAL date in '{DS.title}'!{pal_cell}"),
        "inj_abandoned": (
            f"={count('Injector', 'O', 'Yes')}",
            f"Injectors abandoned after the PAL date, tested against {ab_src}."
            + (f" {ta_inj} injector(s) were temporarily abandoned after the PAL date but are not "
               "plugged, so they are reported as idle instead." if cfg.abandoned_basis == 'pa_only' and ta_inj else ""),
            f"Well Data C/O rows {WD_F}-{WD_L}"),
        "inj_total": (
            f'=COUNTIF(\'Well Data\'!$C${WD_F}:$C${WD_L},"Injector")',
            f"Every API tagged as an injector on the Well List. Counted by API, not by completion -- "
            f"the {len(p.injectors)} wells carry {len(R.inj_detail)} completions.",
            f"Well Data C rows {WD_F}-{WD_L}"),
        "prd_active": (
            f"={count(role_prd, 'M', 'Active')}",
            f"Non-injector wells judged active on the chosen basis."
            + (f" WellSTAR shows {ws_prd_act} active." if p.ws_status and cfg.active_basis != "wellstar" else ""),
            f"Well Data C/M rows {WD_F}-{WD_L}"),
        "prd_idle": (
            f"={count(role_prd, 'M', 'Idle')}",
            f"The balance -- {n_prd_idle} wells. Observation wells never report production, so a "
            "volume test will always classify them idle; check column P before submitting."
            if cfg.active_basis != "wellstar" else "The balance of the non-injector wells.",
            f"Well Data C/M rows {WD_F}-{WD_L}"),
        "prd_drilled": (
            f"={count(role_prd, 'N', 'Yes')}",
            f"Same spud-date test as the injector line"
            + (f": {', '.join(str(x) for x in drilled_prd[:8])}." if drilled_prd else " -- none."),
            f"Well Data C/N rows {WD_F}-{WD_L}"),
        "prd_abandoned": (
            f"={count(role_prd, 'O', 'Yes')}",
            f"Tested against {ab_src}."
            + (f" {ta_prd} were temporarily abandoned after the PAL date and are counted as idle."
               if cfg.abandoned_basis == 'pa_only' and ta_prd else ""),
            f"Well Data C/O rows {WD_F}-{WD_L}"),
        "prd_total": (
            f'=COUNTIF(\'Well Data\'!$C${WD_F}:$C${WD_L},"{role_prd}")',
            f"All {len(p.producers)} non-injector API numbers on the Well List, counted as project "
            "production wells.",
            f"Well Data C rows {WD_F}-{WD_L}"),
        "depth": (
            f'=TEXT(MIN({IDS}!$E${ID_F}:$E${ID_L}),"#,##0")&" - "&'
            f'TEXT(MAX({IDS}!$G${ID_F}:$G${ID_L}),"#,##0")',
            f"Top of the shallowest perforation to the bottom of the deepest, TVD, across all "
            f"{len(R.inj_detail)} injection completions. Across the {R.n_inj_active_cmpl} injecting "
            f"completions only: {min(a_ttvds):,.0f} - {max(a_btvds):,.0f} ft."
            if a_ttvds else "Perforated interval across the injection completions.",
            f"Injector Detail E and G, rows {ID_F}-{ID_L}"),
        "masp": (
            None,
            "NOT IN THE DATA PULL. MASP comes from the project approval letter or the most recent "
            "MASP demonstration. Enter it here, then read the gradients below against "
            "MASP / depth of injection.",
            "Manual entry from the PAL"),
        "grad_avg": (
            f"=AVERAGE({IDS}!$I${ID_F}:$I${ID_A})",
            f"Mean of the per-well surface gradient (wellhead injection pressure / top-perf TVD) "
            f"across the {R.n_inj_active_cmpl} injecting completions. Pressure source: "
            f"{gsrc.lower()}. Well gradients run {g_lo:.3f} to {g_hi:.3f} psi/ft; the average "
            f"wellhead pressure behind this number is {p_avg:,.0f} psi.",
            f"Injector Detail I rows {ID_F}-{ID_A}"),
        "grad_max": (
            f"=MAX({IDS}!$I${ID_F}:$I${ID_A})",
            f"Highest single-well surface gradient among the injecting completions: {g_top[1]}, "
            f"{g_top[2]:,.1f} psi over {g_top[3]:,.0f} ft."
            if grads else "Highest single-well surface gradient.",
            f"Injector Detail I rows {ID_F}-{ID_A}"),
        "inj_annual": (
            f"=SUM({MVS}!$J${MV_F}:$J${MV_L})/SUM({MVS}!$B${MV_F}:$B${MV_L})*365",
            f"Monthly volume = summed well injection rate x days in month. Over the window that "
            f"totals {T['inj']:,.0f} bbl across {tot_days} days; normalised to 365 days to express "
            f"it annually. Injection fluid is {fluid}"
            + (", reported in barrels of water equivalent." if fluid == "steam" else ".") + trend,
            f"Monthly Volumes J and B, rows {MV_F}-{MV_L}"),
        "inj_avg_mo": (
            f"=AVERAGE({MVS}!$J${MV_F}:$J${MV_L})",
            f"Total injected over the window divided by {len(R.window)} months.",
            f"Monthly Volumes J rows {MV_F}-{MV_L}"),
        "inj_max_mo": (
            f"=MAX({MVS}!$J${MV_F}:$J${MV_L})",
            f"Largest single month in the window: {mlabel(max_inj_m)}.",
            f"Monthly Volumes J rows {MV_F}-{MV_L}"),
        "oil_avg": (
            f"=SUM({MVS}!$G${MV_F}:$G${MV_L})/SUM({MVS}!$B${MV_F}:$B${MV_L})",
            f"Total oil over the window divided by total days ({T['oil']:,.0f} bbl / {tot_days} days). "
            "Day-weighted, so a 28-day month is not given the same weight as a 31-day one.",
            f"Monthly Volumes G and B, rows {MV_F}-{MV_L}"),
        "oil_max": (
            f"=MAX({MVS}!$K${MV_F}:$K${MV_L})",
            f"Highest monthly average daily field oil rate in the window: {mlabel(max_oil_m)}. "
            "A monthly average, not a peak single day.",
            f"Monthly Volumes K rows {MV_F}-{MV_L}"),
        "wat_avg": (
            f"=SUM({MVS}!$H${MV_F}:$H${MV_L})/SUM({MVS}!$B${MV_F}:$B${MV_L})",
            f"Total produced water over the window divided by total days "
            f"({T['water']:,.0f} bbl / {tot_days} days).",
            f"Monthly Volumes H and B, rows {MV_F}-{MV_L}"),
        "wat_max": (
            f"=MAX({MVS}!$L${MV_F}:$L${MV_L})",
            f"Highest monthly average daily field water rate: {mlabel(max_wat_m)}.",
            f"Monthly Volumes L rows {MV_F}-{MV_L}"),
        "wcut": (
            f"=SUM({MVS}!$H${MV_F}:$H${MV_L})/(SUM({MVS}!$H${MV_F}:$H${MV_L})"
            f"+SUM({MVS}!$G${MV_F}:$G${MV_L}))",
            f"Total produced water / total produced liquid over the whole window -- volume-weighted, "
            f"not the average of the {len(R.window)} monthly cuts. "
            f"{T['water']:,.0f} / {T['water'] + T['oil']:,.0f}.",
            f"Monthly Volumes G and H, rows {MV_F}-{MV_L}"),
    }

    # ---------------- Calculations -------------------------------------------
    cs = wb.create_sheet("Calculations", 1)
    put(cs, "A1", "Periodic Project Review data sheet: basis of every number", TITLE)
    put(cs, "A2", f"Every value on the {DS.title} tab is a live formula pointing at the three "
                  "supporting tabs in this workbook. Change an input and the data sheet updates. "
                  f"Generated {dt.date.today()} by ppr_datasheet.py.", GREY)
    put(cs, "A4", "1. INPUTS AND REPORTING BASIS", SEC)
    basis_rows = [
        ("Well list / project info", os.path.basename(cfg.input_path) +
         f" -- {len(p.injectors)} injectors, {len(p.producers)} other wells"),
        ("Source data file", os.path.basename(cfg.output_path.replace(os.path.basename(cfg.output_path),
                                                                      os.path.basename(cfg.data_path)))),
        ("Rate data available", f"{mlabel(p.months[0])} through {mlabel(p.months[-1])} "
                                f"({len(p.months)} months) of monthly average daily rates per well"),
        ("Reporting window used", f"{win_lbl} -- {len(R.window)} complete months, {tot_days} days"),
        (f"{mlabel(R.partial)} excluded", "Partial month at the date of the pull; including it would "
                                          "bias every average and the water cut downward"),
        ("PAL / last project update date", "__PAL__"),
        ("Active vs. Idle basis", basis_txt[0].upper() + basis_txt[1:]),
        ('"Abandoned since PAL" basis',
         "Permanently abandoned (plugged) wells only; temporarily abandoned wells are reported on "
         "the idle lines instead" if cfg.abandoned_basis == "pa_only"
         else "Any ABANDON_DTE after the PAL date, temporary abandonments included"),
        ("Pressure gradient basis", gsrc + ", over the injecting completions only"),
        ("Injection fluid", f"{fluid.capitalize()} -- "
                            f"{T['steam']:,.0f} bbl steam, {T['winj']:,.0f} bbl water, "
                            f"{T['ginj']:,.0f} gas over the window"),
    ]
    r = 5
    pal_row = None
    for k, v in basis_rows:
        put(cs, f"A{r}", k, BOLD, border=True, wrap=True)
        if v == "__PAL__":
            pal_row = r
            put(cs, f"B{r}", f"='{DS.title}'!{pal_cell}", BLUE, "yyyy-mm-dd", fill=YFILL,
                border=True, align="center")
            put(cs, f"C{r}", f"Read from '{DS.title}'!{pal_cell}. Columns N and O of the Well Data "
                             "tab test against it -- change that cell and every 'since PAL' count "
                             "updates.", GREY, wrap=True)
            cs.merge_cells(f"C{r}:E{r}")
        else:
            put(cs, f"B{r}", v, BLK, border=True, wrap=True)
        r += 1
    r += 1
    put(cs, f"A{r}", "2. LINE-BY-LINE DERIVATION", SEC)
    r += 1
    hr_ = r
    for j, h in enumerate([f"{DS.title}\ncell", "Line item", "Value", "How it is calculated",
                           "Cells / range used"], 1):
        put(cs, f"{get_column_letter(j)}{hr_}", h, HDR, fill=HFILL, align="center", border=True, wrap=True)
    r += 1
    for key, sect, frag, fmt in LINES:
        if key not in rows or key not in spec:
            continue
        formula, how, rng = spec[key]
        cell = f"B{rows[key]}"
        put(cs, f"A{r}", cell, BOLD, border=True, align="center")
        put(cs, f"B{r}", str(DS.cell(rows[key], 1).value).strip().rstrip(":"), BLK, border=True, wrap=True)
        if formula is None:
            put(cs, f"C{r}", "not available", RED, border=True, align="center")
        else:
            put(cs, f"C{r}", f"='{DS.title}'!{cell}", BLK, fmt, border=True, align="center")
        put(cs, f"D{r}", how, BLK, border=True, wrap=True)
        put(cs, f"E{r}", rng, GREY, border=True, wrap=True)
        cs.row_dimensions[r].height = 62
        r += 1
    r += 1
    put(cs, f"A{r}", "3. THINGS TO CHECK BEFORE THIS GOES OUT", SEC)
    r += 1
    checks = list(R.notes)
    checks.append("MASP is blank -- it has to come from the project approval letter. Once entered, "
                  "read the injecting-well gradients on the Injector Detail tab against "
                  "MASP / depth of injection.")
    if p.ws_status and cfg.active_basis != "wellstar":
        checks.append("Column P of the Well Data tab flags every well where the volume test and "
                      "WellSTAR disagree. Filter on 'NO' and reconcile before submitting -- CalGEM "
                      "reads the sheet against their own system. Observation wells never report "
                      "production, so a volume test always calls them idle.")
    op_no_vol = sum(1 for a in (p.injectors | p.producers)
                    if "Operational" in p.cstate.get(a, set())
                    and R.wellsum[a]["inj"] + R.wellsum[a]["prod"] == 0)
    if op_no_vol:
        checks.append(f"{op_no_vol} well(s) show 'Operational' in company records but have had no "
                      "volume across the idle-test window. Confirm their idle-well status and IWMP "
                      "coverage, and consider whether the completion states need updating.")
    if cfg.abandoned_basis == "pa_only" and (ta_inj + ta_prd):
        checks.append(f"{ta_inj + ta_prd} well(s) were temporarily abandoned after the PAL date "
                      f"({ta_inj} injectors, {ta_prd} producers). They are not counted as abandoned "
                      "because they are not plugged, but that is a lot of TA activity since the last "
                      "project update and CalGEM may ask about it directly.")
    if p.daily and cfg.gradient_source == "avg_tubing":
        peaks = []
        for a, cmpl, v, _, yes in R.inj_detail[:R.n_inj_active_cmpl]:
            rowsd = [x for x in p.daily.get((a, cmpl), []) if x[1]]
            if rowsd and v["ttvd"]:
                mx = max(x[2] for x in rowsd)
                peaks.append((mx / v["ttvd"], cmpl, mx, v["ttvd"]))
        if peaks:
            peaks.sort()
            g, nm, mx, dp = peaks[-1]
            checks.append(f"Gradients use period-average pressures. The daily injection-pressure "
                          f"data shows higher instantaneous peaks -- {nm} reaches {mx:,.0f} psi "
                          f"({g:.3f} psi/ft). If CalGEM asks for compliance at maximum injection "
                          "pressure, those are the numbers they will want.")
    if trend:
        checks.append("Check the injection and produced-water trends over the window before writing "
                      "the narrative -- a falling injection rate against rising water production is "
                      "the first thing a reviewer asks about.")
    for c in checks:
        put(cs, f"A{r}", "•", BLK)
        put(cs, f"B{r}", c, BLK, wrap=True)
        cs.merge_cells(f"B{r}:E{r}")
        cs.row_dimensions[r].height = 30
        r += 1
    for col, w in zip("ABCDE", [30, 40, 16, 74, 40]):
        cs.column_dimensions[col].width = w
    cs.row_dimensions[hr_].height = 32

    # ---------------- the data sheet itself ----------------------------------
    for key, sect, frag, fmt in LINES:
        if key not in rows or key not in spec:
            continue
        formula, how, rng = spec[key]
        rr = rows[key]
        c = DS.cell(rr, 2)
        if formula is not None:
            c.value = formula
            c.number_format = fmt
        c.alignment = Alignment(horizontal="center", vertical="center")
        d = DS.cell(rr, 3)
        d.value = how
        d.font = NOTE_RED if formula is None else NOTE
        d.alignment = Alignment(wrap_text=True, vertical="center", horizontal="left")
    DS.column_dimensions["B"].width = 17
    DS.column_dimensions["C"].width = 85
    lo = min(rows.values())
    hi = max(rows.values())
    for rr in range(lo, hi + 1):
        DS.row_dimensions[rr].height = 40
        DS.cell(rr, 1).alignment = Alignment(vertical="center", wrap_text=True)
    hint = 'How each value was calculated -- full derivation on the "Calculations" tab'
    put(DS, "C1" if DS["C1"].value is None else f"C{lo - 1}" if lo > 1 else "D1",
        hint, Font(name=FONT, sz=10, italic=True, color="1F3864"))
    foot = hi + 2
    put(DS, f"A{foot}",
        f"Reporting window {win_lbl} ({len(R.window)} complete months, {tot_days} days); "
        f"{mlabel(R.partial)} excluded as a partial month. Active/Idle by {basis_txt}. "
        f"'Abandoned' = {'plugged & abandoned only' if cfg.abandoned_basis == 'pa_only' else 'any abandonment date'}. "
        f"Gradients from {gsrc.lower()}. Sources: {os.path.basename(cfg.data_path)} and "
        f"{os.path.basename(cfg.input_path)}. Generated {dt.date.today()} by ppr_datasheet.py.",
        GREY, wrap=True)
    DS.merge_cells(f"A{foot}:C{foot}")
    DS.row_dimensions[foot].height = 40

    for sh, rep in ((DS, None), (cs, None), (wd, "4:4"), (idt, "4:4"), (mv, "4:4")):
        sh.page_setup.orientation = "landscape"
        sh.page_setup.fitToWidth = 1
        sh.page_setup.fitToHeight = 0
        sh.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        if rep:
            sh.print_title_rows = rep
    DS.print_area = f"A1:C{foot}"

    dest = cfg.save_as or default_dest(cfg.output_path)
    wb.save(dest)
    return dest


def default_dest(output_path):
    base, ext = os.path.splitext(output_path)
    return f"{base}_FILLED{ext}"


# ----------------------------------------------------------------------------
# orchestration
# ----------------------------------------------------------------------------

def run(cfg: Config, log=print) -> str:
    for label, path in (("Input", cfg.input_path), ("Data", cfg.data_path), ("Output", cfg.output_path)):
        if not path:
            raise SheetError(f"{label} file not selected.")
        if not os.path.isfile(path):
            raise SheetError(f"{label} file not found: {path}")
    log("Reading workbooks...")
    p = load(cfg, log)
    log("Calculating...")
    R = compute(cfg, p, log)
    log("Writing...")
    dest = build(cfg, p, R, log)
    log("")
    log(f"Saved: {dest}")
    log("")
    log("Open it in Excel and press Ctrl+Alt+F9 to force a full recalculation -- openpyxl writes")
    log("formulas without cached values, so cells look blank until Excel evaluates them once.")
    return dest


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

def gui(cfg: Config):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("CalGEM PPR data sheet")
    root.geometry("880x620")
    root.columnconfigure(1, weight=1)

    vars_ = {
        "input": tk.StringVar(value=cfg.input_path),
        "data": tk.StringVar(value=cfg.data_path),
        "output": tk.StringVar(value=cfg.output_path),
        "window": tk.StringVar(value=WINDOW_CHOICES[cfg.window]),
        "active": tk.StringVar(value=ACTIVE_CHOICES[cfg.active_basis]),
        "abandon": tk.StringVar(value=ABANDON_CHOICES[cfg.abandoned_basis]),
        "gradient": tk.StringVar(value=GRADIENT_CHOICES[cfg.gradient_source]),
    }

    def browse(key, title):
        d = os.path.dirname(vars_[key].get() or vars_["data"].get() or vars_["input"].get() or "")
        path = filedialog.askopenfilename(
            title=title, initialdir=d or None,
            filetypes=[("Excel workbooks", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if path:
            vars_[key].set(path)
            autofill(path)

    def autofill(path):
        """Selecting one file offers to fill the other two from the same folder."""
        folder = os.path.dirname(path)
        for key, tag in (("input", "input"), ("data", "data"), ("output", "output")):
            if vars_[key].get():
                continue
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$") \
                        and f"_{tag}." in f.lower():
                    vars_[key].set(os.path.join(folder, f))
                    break

    rowi = 0
    for key, label, title in (("input", "Input file  (well list + PAL date)", "Select the INPUT workbook"),
                              ("data", "Data file  (the pull)", "Select the DATA workbook"),
                              ("output", "Output file  (blank data sheet)", "Select the OUTPUT workbook")):
        ttk.Label(root, text=label).grid(row=rowi, column=0, sticky="w", padx=10, pady=(10, 0))
        ttk.Entry(root, textvariable=vars_[key]).grid(row=rowi, column=1, sticky="ew", padx=6, pady=(10, 0))
        ttk.Button(root, text="Browse...", command=lambda k=key, t=title: browse(k, t)) \
            .grid(row=rowi, column=2, padx=10, pady=(10, 0))
        rowi += 1

    ttk.Separator(root, orient="horizontal").grid(row=rowi, column=0, columnspan=3, sticky="ew", pady=12)
    rowi += 1

    for key, label, choices in (("window", "Reporting window", WINDOW_CHOICES),
                                ("active", "Active vs. Idle basis", ACTIVE_CHOICES),
                                ("abandon", '"Abandoned since PAL" means', ABANDON_CHOICES),
                                ("gradient", "Pressure gradient from", GRADIENT_CHOICES)):
        ttk.Label(root, text=label).grid(row=rowi, column=0, sticky="w", padx=10, pady=3)
        ttk.Combobox(root, textvariable=vars_[key], values=list(choices.values()),
                     state="readonly").grid(row=rowi, column=1, columnspan=2, sticky="ew",
                                            padx=(6, 10), pady=3)
        rowi += 1

    ttk.Separator(root, orient="horizontal").grid(row=rowi, column=0, columnspan=3, sticky="ew", pady=12)
    rowi += 1

    log_box = tk.Text(root, height=16, wrap="word", font=("Consolas", 9))
    log_box.grid(row=rowi, column=0, columnspan=3, sticky="nsew", padx=10)
    root.rowconfigure(rowi, weight=1)
    rowi += 1

    def log(msg=""):
        log_box.insert("end", str(msg) + "\n")
        log_box.see("end")
        root.update_idletasks()

    def go():
        log_box.delete("1.0", "end")
        c = Config(
            input_path=vars_["input"].get().strip(),
            data_path=vars_["data"].get().strip(),
            output_path=vars_["output"].get().strip(),
            window=inv(WINDOW_CHOICES, vars_["window"].get()),
            active_basis=inv(ACTIVE_CHOICES, vars_["active"].get()),
            abandoned_basis=inv(ABANDON_CHOICES, vars_["abandon"].get()),
            gradient_source=inv(GRADIENT_CHOICES, vars_["gradient"].get()),
        )
        try:
            dest = run(c, log)
        except SheetError as e:
            log(f"\nSTOPPED: {e}")
            messagebox.showerror("Cannot continue", str(e))
        except PermissionError:
            log("\nSTOPPED: the destination file is open in Excel. Close it and run again.")
            messagebox.showerror("File is open", "The destination workbook is open in Excel. "
                                                 "Close it and run again.")
        except Exception as e:
            log("\n" + traceback.format_exc())
            messagebox.showerror("Unexpected error", f"{type(e).__name__}: {e}")
        else:
            if messagebox.askyesno("Done", f"Saved:\n{dest}\n\nOpen the folder?"):
                open_folder(dest)

    bar = ttk.Frame(root)
    bar.grid(row=rowi, column=0, columnspan=3, sticky="e", padx=10, pady=10)
    ttk.Button(bar, text="Quit", command=root.destroy).pack(side="right", padx=4)
    ttk.Button(bar, text="Fill data sheet", command=go).pack(side="right", padx=4)

    log("Select the three workbooks, check the four options, then press Fill data sheet.")
    log("The result is saved beside the output file with _FILLED on the end -- the original")
    log("is never overwritten.")
    root.mainloop()


def inv(choices, label, default=None):
    for k, v in choices.items():
        if v == label:
            return k
    return default or next(iter(choices))


def open_folder(path):
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)                       # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{folder}"')
        else:
            os.system(f'xdg-open "{folder}"')
    except Exception:
        pass


# ----------------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", dest="input_path", help="well list + PAL date workbook")
    ap.add_argument("--data", dest="data_path", help="data pull workbook")
    ap.add_argument("--output", dest="output_path", help="blank data sheet workbook")
    ap.add_argument("--save-as", dest="save_as", help="where to write the filled copy "
                                                      "(default: <output>_FILLED.xlsx)")
    ap.add_argument("--window", choices=list(WINDOW_CHOICES), default="24")
    ap.add_argument("--active-basis", choices=list(ACTIVE_CHOICES), default="volume")
    ap.add_argument("--abandoned-basis", choices=list(ABANDON_CHOICES), default="pa_only")
    ap.add_argument("--gradient-source", choices=list(GRADIENT_CHOICES), default="avg_tubing")
    ap.add_argument("--no-gui", action="store_true", help="never open the picker window")
    a = ap.parse_args(argv)

    cfg = Config(input_path=a.input_path or "", data_path=a.data_path or "",
                 output_path=a.output_path or "", save_as=a.save_as or "",
                 window=a.window, active_basis=a.active_basis,
                 abandoned_basis=a.abandoned_basis, gradient_source=a.gradient_source)

    if cfg.input_path and cfg.data_path and cfg.output_path:
        try:
            run(cfg)
        except SheetError as e:
            sys.exit(f"STOPPED: {e}")
        return 0
    if a.no_gui:
        ap.error("--input, --data and --output are all required with --no-gui")
    gui(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
