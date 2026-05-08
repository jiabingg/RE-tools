"""
CRC Wellbore Diagram Generator — GUI Application
=================================================
Uses Windows Credential Manager (keyring) to connect to ODW —
the exact same method as the Claude MCP tool and SQL Developer.

INSTALL (run once in PowerShell):
    pip install oracledb keyring

RUN:
    python CRC_WBD_App.py
"""

import sys, os, csv, time, threading, webbrowser, subprocess, traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════
SCHEMA      = "JH214323"
OUTPUT_DIR  = Path(r"S:\asset development\Tools\WELLBORE DIAGRAMS\Python Export Location")
PREFIX      = "CRC_WBD_"

# Oracle Client — confirmed on your machine
ORACLE_CLIENT = r"C:\oracle\product\12.1.0\client_2"

# TNS alias — confirmed working from cmdkey output
TNS_ALIASES = ["ODW", "ODW.WORLD", "ODW_VIP.WORLD", "MCP_USER@ODW"]

# Windows Credential Manager targets — from cmdkey /list output
KEYRING_TARGETS = ["MCP_USER@ODW", "ODW"]

# CRC Colors
NAVY  = "#1B2F5B"
GOLD  = "#C8860A"
GREEN = "#1a7a4a"
WHITE = "#FFFFFF"
LGRAY = "#f0eeea"
DGRAY = "#2a2a2a"
DBLUE = "#0e1a2b"

try:
    import oracledb
    ORACLE_OK = True
except ImportError:
    ORACLE_OK = False

try:
    import keyring
    KEYRING_OK = True
except ImportError:
    KEYRING_OK = False


# ───────────────────────────────────────────────────────────────
# CREDENTIAL RETRIEVAL — Windows Credential Manager
# ───────────────────────────────────────────────────────────────
def get_credentials():
    """
    Read credentials from Windows Credential Manager.
    Same store used by MCP tool and SQL Developer.
    Returns list of (username, password, target) tuples to try.
    """
    creds = []

    # Method 1 — keyring library
    if KEYRING_OK:
        for target in KEYRING_TARGETS:
            try:
                import keyring.backend
                # Try common Oracle usernames stored under this target
                for user in ["rptuser", "rptguser", "MCP_USER"]:
                    try:
                        pwd = keyring.get_password(target, user)
                        if pwd:
                            creds.append((user, pwd, f"keyring:{target}"))
                    except Exception:
                        continue
            except Exception:
                continue

    # Method 2 — Windows DPAPI via ctypes (reads cmdkey entries directly)
    try:
        import ctypes
        import ctypes.wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags",              ctypes.wintypes.DWORD),
                ("Type",               ctypes.wintypes.DWORD),
                ("TargetName",         ctypes.wintypes.LPWSTR),
                ("Comment",            ctypes.wintypes.LPWSTR),
                ("LastWritten",        ctypes.wintypes.FILETIME),
                ("CredentialBlobSize", ctypes.wintypes.DWORD),
                ("CredentialBlob",     ctypes.POINTER(ctypes.wintypes.BYTE)),
                ("Persist",            ctypes.wintypes.DWORD),
                ("AttributeCount",     ctypes.wintypes.DWORD),
                ("Attributes",         ctypes.c_void_p),
                ("TargetAlias",        ctypes.wintypes.LPWSTR),
                ("UserName",           ctypes.wintypes.LPWSTR),
            ]

        advapi32 = ctypes.windll.advapi32

        for target in KEYRING_TARGETS:
            try:
                cred_ptr = ctypes.POINTER(CREDENTIAL)()
                result = advapi32.CredReadW(
                    target, 1, 0, ctypes.byref(cred_ptr))
                if result:
                    cred = cred_ptr.contents
                    username = cred.UserName or "rptuser"
                    blob_size = cred.CredentialBlobSize
                    blob = bytes(ctypes.cast(
                        cred.CredentialBlob,
                        ctypes.POINTER(ctypes.wintypes.BYTE * blob_size)
                    ).contents)
                    password = blob.decode("utf-16-le").rstrip("\x00")
                    if password:
                        creds.append((username, password, f"cmdkey:{target}"))
                    advapi32.CredFree(cred_ptr)
            except Exception:
                continue
    except Exception:
        pass

    # Method 3 — fallback known credentials
    creds.append(("rptuser",    "allusers", "fallback"))
    creds.append(("rptguser",   "allusers", "fallback"))

    return creds


# ───────────────────────────────────────────────────────────────
# DATABASE CONNECTION
# ───────────────────────────────────────────────────────────────
def connect():
    """
    Connect using Windows Credential Manager — same as MCP tool.
    Tries every credential + TNS alias combination.
    Returns (connection, description)
    """
    if not ORACLE_OK:
        raise ImportError("Run:  pip install oracledb")

    creds = get_credentials()
    last_error = None

    # Init thick mode with known Oracle Client
    thick_ok = False
    try:
        oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT)
        thick_ok = True
    except Exception:
        try:
            oracledb.init_oracle_client()
            thick_ok = True
        except Exception:
            pass

    # Full DSN descriptor (most reliable — bypasses TNS lookup)
    full_dsn = (
        "(DESCRIPTION=(ADDRESS_LIST="
        "(ADDRESS=(PROTOCOL=TCP)(HOST=10.20.240.102)(PORT=1521))"
        "(ADDRESS=(PROTOCOL=TCP)(HOST=bkx9dbadm01)(PORT=1521)))"
        "(CONNECT_DATA=(SERVICE_NAME=ODW)(INSTANCE_NAME=ODW1)))"
    )

    for username, password, source in creds:
        # Try thick mode with each TNS alias
        if thick_ok:
            for alias in TNS_ALIASES + [full_dsn]:
                try:
                    conn = oracledb.connect(
                        user=username,
                        password=password,
                        dsn=alias,
                    )
                    return conn, f"{username} via {alias} [thick/{source}]"
                except Exception as e:
                    last_error = e
                    continue

        # Try thin mode with full DSN
        for alias in [full_dsn] + TNS_ALIASES:
            try:
                conn = oracledb.connect(
                    user=username,
                    password=password,
                    dsn=alias,
                    disable_oob=True,
                )
                return conn, f"{username} via thin/{alias[:20]}"
            except Exception as e:
                last_error = e
                continue

    raise Exception(
        f"Connection failed.\n\n"
        f"Last error: {last_error}\n\n"
        f"Windows Credential Manager has:\n"
        f"  MCP_USER@ODW\n"
        f"  ODW\n\n"
        f"Credentials found: {len(creds)} combinations tried.\n"
        f"Make sure you are on the CRC network."
    )


def qall(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ───────────────────────────────────────────────────────────────
# WELL DATA QUERIES  (same tables Claude uses)
# ───────────────────────────────────────────────────────────────
def to_md(v, kb):
    if v is None: return None
    return round(float(v) + float(kb), 1)

def fmt(v, d=0):
    if v is None: return "N/A"
    try:    return f"{float(v):,.{d}f}"
    except: return str(v)

def na(v):
    if v is None or str(v).strip() in ("","None"): return "N/A"
    return str(v)

def find_well(conn, identifier):
    api_clean = str(identifier).strip().lstrip("0")
    rows = qall(conn, f"""
        SELECT ws.WELL_ID, ws.WELL_COMMON_NAME, ws.API_NO,
               ws.FIELD_NAME, ws.SPUD_DATE, ws.WATER_DEPTH,
               ws.GEO_LATITUDE, ws.GEO_LONGITUDE,
               ws.GEO_OFFSET_NORTH, ws.GEO_OFFSET_EAST,
               ws.WELL_PURPOSE, ws.TARGET_FORMATION, ws.WELL_OPERATOR,
               wb.WELLBORE_ID, wb.WELLBORE_NAME,
               wb.BH_MD, wb.BH_TVD, wb.PLUGBACK_MD, wb.END_STATUS,
               d.DATUM_ELEVATION, d.DATUM_NAME
        FROM {SCHEMA}.CD_WELL_SOURCE ws
        JOIN {SCHEMA}.CD_WELLBORE_T wb ON ws.WELL_ID = wb.WELL_ID
        LEFT JOIN {SCHEMA}.CD_DATUM_T d
               ON ws.WELL_ID = d.WELL_ID AND d.IS_DEFAULT = 'Y'
        WHERE (TO_CHAR(ws.API_NO) LIKE :api_p
               OR UPPER(ws.WELL_COMMON_NAME) LIKE UPPER(:name_p))
        ORDER BY ws.SPUD_DATE DESC
    """, {"api_p": f"%{api_clean}%", "name_p": f"%{identifier}%"})
    return rows[0] if rows else {}

def get_casings(conn, well_id, kb):
    rows = qall(conn, f"""
        SELECT a.ASSEMBLY_NAME, a.ASSEMBLY_SIZE, a.ASSEMBLY_ID,
               a.MD_ASSEMBLY_TOP, a.MD_ASSEMBLY_BASE, s.DATE_STATUS,
               ac.SEQUENCE_NO, ac.COMP_NAME, ac.OD_BODY, ac.ID_BODY,
               ac.GRADE, ac.APPROXIMATE_WEIGHT, ac.CONNECTION_NAME,
               ac.MD_TOP, ac.MD_BASE, ac.LENGTH, ac.JOINTS
        FROM {SCHEMA}.CD_ASSEMBLY_T a
        JOIN {SCHEMA}.CD_ASSEMBLY_COMP_T ac ON a.ASSEMBLY_ID = ac.ASSEMBLY_ID
        LEFT JOIN (
            SELECT ASSEMBLY_ID, DATE_STATUS,
                   ROW_NUMBER() OVER (
                       PARTITION BY ASSEMBLY_ID ORDER BY DATE_STATUS DESC) RN
            FROM {SCHEMA}.CD_ASSEMBLY_STATUS_T
        ) s ON a.ASSEMBLY_ID = s.ASSEMBLY_ID AND s.RN = 1
        WHERE a.WELL_ID = :1
          AND (
              UPPER(a.ASSEMBLY_NAME) LIKE '%CASING%'
           OR UPPER(a.ASSEMBLY_NAME) LIKE '%LINER%'
          )
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '%PLUG%'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '%TUBING%'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE 'ROD %'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '% ROD'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '% ROD %'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '%DRILL%'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '%SRWD%'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '%ASSY%'
          AND UPPER(a.ASSEMBLY_NAME) NOT LIKE '%STRING%'
        ORDER BY a.MD_ASSEMBLY_TOP, ac.SEQUENCE_NO
    """, [well_id])
    asms = {}; order = []
    for r in rows:
        name = r["ASSEMBLY_NAME"]
        if name not in asms:
            order.append(name)
            asms[name] = {
                "name": name, "size": float(r["ASSEMBLY_SIZE"] or 0),
                "asm_id": na(r["ASSEMBLY_ID"]),
                "top_md": to_md(r["MD_ASSEMBLY_TOP"], kb),
                "base_md": to_md(r["MD_ASSEMBLY_BASE"], kb),
                "installed": str(r.get("DATE_STATUS") or "")[:10],
                "comps": [],
            }
        asms[name]["comps"].append({
            "seq": r.get("SEQUENCE_NO"), "name": na(r.get("COMP_NAME")),
            "od": r.get("OD_BODY"), "id_": r.get("ID_BODY"),
            "grade": na(r.get("GRADE")), "wt": na(r.get("APPROXIMATE_WEIGHT")),
            "conn": na(r.get("CONNECTION_NAME")),
            "top": to_md(r.get("MD_TOP"), kb), "base": to_md(r.get("MD_BASE"), kb),
            "len": r.get("LENGTH"), "jts": r.get("JOINTS"),
        })
    result = [asms[n] for n in order]
    # Sort by shoe depth (shallowest to deepest): Conductor→Surface→Prod→Liner
    csg_order = {"CONDUCTOR":0,"SURFACE":1,"INTERMEDIATE":2,"PRODUCTION":3,
                 "LINER":4,"GRAVEL":4,"SCREEN":4,"SLOTTED":4}
    def _csg_key(a):
        nm = a.get("name","").upper()
        for k,v in csg_order.items():
            if k in nm: return (v, float(a.get("base_md") or 0))
        return (9, float(a.get("base_md") or 0))
    result.sort(key=_csg_key)
    return result

def get_cement(conn, well_id, kb):
    rows = qall(conn, f"""
        SELECT cj.CEMENT_JOB_ID, cj.JOB_TYPE, cj.JOB_START_DATE,
               cj.JOB_END_DATE, cj.TOC_MD, cj.CONTRACTOR,
               cj.BOTTOM_HOLE_TEMPERATURE,
               cs.STAGE_NO, cs.STAGE_TYPE, cs.HOLE_SIZE AS STAGE_HOLE_SIZE,
               cs.MD_TOP AS STAGE_TOP, cs.MD_BASE AS STAGE_BASE,
               cs.RETURNS, cs.IS_TOP_PLUG_USED, cs.PRIMARY_PRESSURE_BUMP,
               cs.PRIMARY_VOLUME_RETURNS,
               cf.CEMENT_FLUID_ID, cf.FLUID_TYPE, cf.SLURRY_CLASS,
               cf.SLURRY_DENSITY, cf.SLURRY_YIELD, cf.MIX_WATER_RATIO,
               cf.SACKS_USED, cf.VOL_SLURRY, cf.VOL_SLURRY_IN_WELL,
               cf.EXCESS_SLURRY_PERCENT, cf.SLURRY_DESC,
               cf.SEQUENCE_NO AS FLUID_SEQ,
               a.ASSEMBLY_NAME, a.ASSEMBLY_SIZE AS CSG_OD
        FROM {SCHEMA}.CD_CEMENT_JOB_T cj
        LEFT JOIN {SCHEMA}.CD_CEMENT_STAGE_T cs
               ON cj.CEMENT_JOB_ID = cs.CEMENT_JOB_ID
        LEFT JOIN {SCHEMA}.CD_CEMENT_FLUID_T cf
               ON cj.CEMENT_JOB_ID = cf.CEMENT_JOB_ID
        LEFT JOIN {SCHEMA}.CD_ASSEMBLY_T a
               ON cj.ASSEMBLY_ID = a.ASSEMBLY_ID
        WHERE cj.WELL_ID = :1
        ORDER BY cj.JOB_START_DATE, cf.SEQUENCE_NO
    """, [well_id])
    jobs = {}; order = []
    for r in rows:
        jid = r["CEMENT_JOB_ID"]
        if jid not in jobs:
            order.append(jid)
            toc_raw = to_md(r.get("TOC_MD"), kb)
            rtns_bbl = float(r.get("PRIMARY_VOLUME_RETURNS") or 0)
            jobs[jid] = {
                "id": jid, "assembly": na(r.get("ASSEMBLY_NAME")),
                "type": na(r.get("JOB_TYPE")),
                "start": str(r.get("JOB_START_DATE") or "")[:16],
                "end":   str(r.get("JOB_END_DATE")   or "")[:16],
                "toc": max(0, toc_raw) if toc_raw is not None else 0,
                "contractor": na(r.get("CONTRACTOR")),
                "bht": r.get("BOTTOM_HOLE_TEMPERATURE"),
                "stage_no": r.get("STAGE_NO"),
                "stage_type": na(r.get("STAGE_TYPE")),
                "stage_top": to_md(r.get("STAGE_TOP"), kb),
                "stage_base": to_md(r.get("STAGE_BASE"), kb),
                "hole_size": r.get("STAGE_HOLE_SIZE"),
                "returns": na(r.get("RETURNS")),
                "returns_bbls": rtns_bbl,
                "top_plug": na(r.get("IS_TOP_PLUG_USED")),
                "bump_psi": r.get("PRIMARY_PRESSURE_BUMP"),
                "csg_od": r.get("CSG_OD"),  # ASSEMBLY_SIZE = casing OD
                "stage_id": jid, "fluids": [],
            }
        fid = r.get("CEMENT_FLUID_ID")
        if fid and not any(f["id"]==fid for f in jobs[jid]["fluids"]):
            jobs[jid]["fluids"].append({
                "id": fid, "type": na(r.get("FLUID_TYPE")),
                "class_": na(r.get("SLURRY_CLASS")),
                "density": r.get("SLURRY_DENSITY"),
                "yield_": r.get("SLURRY_YIELD"),
                "mix_water": r.get("MIX_WATER_RATIO"),
                "sacks": r.get("SACKS_USED"), "vol": r.get("VOL_SLURRY"),
                "vol_well": r.get("VOL_SLURRY_IN_WELL"),
                "excess": r.get("EXCESS_SLURRY_PERCENT"),
                "desc": na(r.get("SLURRY_DESC")), "seq": r.get("FLUID_SEQ", 0),
            })
    return [jobs[j] for j in order]

def get_formations(conn, well_id, kb):
    rows = qall(conn, f"""
        SELECT FORMATION_NAME, PROGNOSED_MD, PROGNOSED_TVD
        FROM {SCHEMA}.CD_WELLBORE_FORMATION_T
        WHERE WELL_ID = :1 AND FORMATION_NAME IS NOT NULL
        ORDER BY PROGNOSED_MD
    """, [well_id])
    return [{"name": r["FORMATION_NAME"],
             "top_md": to_md(r.get("PROGNOSED_MD"), kb),
             "top_tvd": to_md(r.get("PROGNOSED_TVD"), kb)} for r in rows]

def get_holes(conn, well_id, kb):
    rows = qall(conn, f"""
        SELECT hsg.HOLE_NAME, hsg.MD_HOLE_SECT_TOP, hsg.MD_HOLE_SECT_BASE,
               hsg.TVD_HOLE_SECT_TOP, hsg.TVD_HOLE_SECT_BASE,
               hsg.DATE_SECT_START, hs.EFFECTIVE_DIAMETER, hs.HOLE_SIZE
        FROM {SCHEMA}.CD_HOLE_SECT_GROUP_T hsg
        LEFT JOIN {SCHEMA}.CD_HOLE_SECT_T hs
               ON hsg.HOLE_SECT_GROUP_ID = hs.HOLE_SECT_GROUP_ID
        WHERE hsg.WELL_ID = :1
        ORDER BY hsg.MD_HOLE_SECT_TOP
    """, [well_id])
    seen = set(); result = []
    for r in rows:
        name = na(r.get("HOLE_NAME"))
        if name in seen: continue
        seen.add(name)
        result.append({
            "name": name,
            "diam": float(r.get("EFFECTIVE_DIAMETER") or r.get("HOLE_SIZE") or 0),
            "top_md": to_md(r.get("MD_HOLE_SECT_TOP"), kb),
            "base_md": to_md(r.get("MD_HOLE_SECT_BASE"), kb),
            "top_tvd": to_md(r.get("TVD_HOLE_SECT_TOP"), kb),
            "base_tvd": to_md(r.get("TVD_HOLE_SECT_BASE"), kb),
            "drilled": str(r.get("DATE_SECT_START") or "")[:10],
        })
    return result

def get_pa_data(conn, well_id, kb):
    """
    Parse P&A event data + daily reports to extract:
    - Cement plugs (from narrative text)
    - Squeeze intervals
    - Reported TOC/TTOC
    - Tagged depths
    Uses robust regex matching of oilfield report language.
    """
    import re
    try:
        events = qall(conn, f"""
            SELECT e.EVENT_ID, e.EVENT_CODE, e.EVENT_OBJECTIVE_1,
                   e.EVENT_OBJECTIVE_2, e.DATE_OPS_START, e.DATE_OPS_END,
                   e.STATUS_END, e.PRIMARY_SERVICE_PROVIDER, e.EVENT_TEAM,
                   d.DATE_REPORT, d.REPORT_NO, d.COMMENT_SUMMARY,
                   d.SUPERVISOR_NAME_1, d.DAILY_COST
            FROM {SCHEMA}.DM_EVENT_T e
            JOIN {SCHEMA}.DM_DAILY_T d
              ON e.WELL_ID = d.WELL_ID AND e.EVENT_ID = d.EVENT_ID
            WHERE e.WELL_ID = :1 AND e.EVENT_CODE = 'ABD'
            ORDER BY d.DATE_REPORT
        """, [well_id])
        if not events:
            return None

        ev = events[0]
        daily = []
        total_cost = 0
        for i, r in enumerate(events):
            cost = float(r.get("DAILY_COST") or 0)
            total_cost += cost
            daily.append({
                "day":        i + 1,
                "date":       str(r.get("DATE_REPORT") or "")[:10],
                "supervisor": na(r.get("SUPERVISOR_NAME_1")),
                "cost":       cost,
                "summary":    na(r.get("COMMENT_SUMMARY")),
            })

        # ── Parse plugs from all daily report text ──────────────
        # Builds a list of raw plug events keyed by date for cross-day tagging
        raw_events = {}   # date -> list of plug dicts
        all_plugs  = []

        def parse_vol(text):
            for pat in [
                r'(?:M/P|Mix\s*&\s*Pump|pump(?:ed)?)\s+(\d+)\s*(?:CuFt|CF|cf|cubic\s*feet)',
                r'(\d+)\s*(?:CuFt|CF|cf|cubic\s*feet)',
            ]:
                m = re.findall(pat, text, re.IGNORECASE)
                if m: return [int(v.replace(",","")) for v in m if 0 < int(v.replace(",","")) < 5000]
            return []

        def parse_depth(text, pattern):
            m = re.findall(pattern, text, re.IGNORECASE)
            if m:
                try: return int(m[0].replace(",",""))
                except: pass
            return None

        def parse_class(text):
            m = re.search(r'Class\s+([A-Z])', text, re.IGNORECASE)
            return m.group(1).upper() if m else "G"

        def parse_additives(text):
            adds = []
            if re.search(r'\d+%?\s*S/F|Silica\s*Flour', text, re.I): adds.append("35% S/F")
            if re.search(r'perlite', text, re.I):                       adds.append("Perlite")
            if re.search(r'diamix', text, re.I):                        adds.append("Diamix")
            if re.search(r'CaCl2', text, re.I):                         adds.append("CaCl2")
            if re.search(r'bentonite', text, re.I):                      adds.append("Bentonite")
            return ", ".join(adds) if adds else ""

        def parse_interval(text):
            """
            Parse 'From X to Y' intervals from daily report text.
            Per SOP: larger depth = base (bottom), smaller = top.
            Guards: Y must be > 50ft (not surface/zero) and both > 0.
            """
            m = re.search(
                r'From\s+([\d,]+)\'\s+to\s+([\d,]+)\'',
                text, re.IGNORECASE)
            if m:
                try:
                    a = int(m.group(1).replace(",",""))
                    b = int(m.group(2).replace(",",""))
                    # Skip if either depth is zero/invalid or identical
                    if a <= 0 or b <= 0 or a == b: return (None, None)
                    # Skip if "to 0" or "to surface" (nonsense interval)
                    if min(a,b) < 50: return (None, None)
                    # Return (top=shallower, base=deeper)
                    return (min(a,b), max(a,b))
                except: pass
            return (None, None)

        plug_no = 1
        squeeze_no = 1
        plugs   = []
        squeezes = []
        tagged_depths = {}   # maps approx_depth -> confirmed_tag_depth from next day

        # First pass: collect all tag depths per date for cross-referencing
        for dr in daily:
            summary = dr["summary"]
            date    = dr["date"]
            # "tag plug at 1,213'"  "tag well @ 2,115'"  "witnessed & approved tag at X"
            tag_depths = re.findall(
                r'(?:tag(?:ged)?\s+(?:plug\s+)?(?:at|@)|witnessed.*?tag\s+at)\s+([\d,]+)\'',
                summary, re.IGNORECASE)
            tagged_depths[date] = [int(d.replace(",","")) for d in tag_depths if d]

        # Second pass: build plugs and squeezes
        for dr in daily:
            summary = dr["summary"]
            date    = dr["date"]
            vols    = parse_vol(summary)
            if not vols:
                continue

            class_  = parse_class(summary)
            adds    = parse_additives(summary)
            interval_top, interval_btm = parse_interval(summary)

            # TTOC/TOC/ETOC reported — all equivalent
            ttoc_rep = (parse_depth(summary, r'TTOC\s+at\s+([\d,]+)\'')
                     or parse_depth(summary, r'ETOC\s*[@at]\s*([\d,]+)\'')
                     or parse_depth(summary, r'TTOC\s*[@]\s*([\d,]+)\''))
            toc_rep  = parse_depth(summary, r'TOC\s+at\s+([\d,]+)\'')

            # Tag depth in THIS report — various phrasings
            tag_local = (parse_depth(summary,
                             r'tag(?:ged)?\s+(?:(?:plug|well|c/o)\s+)?(?:at|@)\s+([\d,]+)\'')
                      or parse_depth(summary,
                             r'RIH\s+tag(?:ged)?\s+[@]?\s*([\d,]+)\'')
                      or parse_depth(summary,
                             r'C/O\s+tag\s+[@]\s*([\d,]+)\''))

            # Check if subsequent report has a confirmed tag that adjusts our plug top
            confirmed_tag = None
            next_day_tags = []
            for dr2 in daily:
                if dr2["date"] > date:
                    next_day_tags.extend(tagged_depths.get(dr2["date"],[]))
            if next_day_tags:
                confirmed_tag = next_day_tags[0]

            # Is this a squeeze?
            is_squeeze = bool(re.search(
                r'squeeze|TCP\s+gun|perforation.*cement|injection\s+rate',
                summary, re.IGNORECASE))

            for j, vol in enumerate(vols):
                # Determine plug top and base
                plug_top  = None
                plug_base = None

                if interval_top is not None:
                    plug_top  = interval_top
                    plug_base = interval_btm
                elif tag_local:
                    plug_base = tag_local
                    plug_top  = confirmed_tag if confirmed_tag else (tag_local - 1000 if tag_local > 1000 else 0)
                elif ttoc_rep:
                    plug_top = ttoc_rep

                if is_squeeze:
                    # Squeeze: log separately, never create a main plug entry
                    squeezes.append({
                        "squeeze_no": squeeze_no,
                        "date":       date,
                        "top_md":     plug_top or 0,
                        "base_md":    plug_base or (plug_top + 10 if plug_top else 0),
                        "volume_cf":  vol,
                        "class_":     class_,
                        "additives":  adds,
                        "ttoc_rep":   ttoc_rep,
                        "confidence": "HIGH" if (interval_top and ttoc_rep) else "MEDIUM",
                        "notes":      summary[:100],
                    })
                    squeeze_no += 1
                else:
                    # Determine plug top and base using SOP logic:
                    # tag_local = where they tagged (= plug BASE — deepest point)
                    # confirmed_tag = next-day tag (= verified plug TOP)
                    # interval from "From X to Y" = explicit pumped interval
                    if interval_top is not None and interval_btm is not None:
                        # Explicit "From X to Y" — top=shallower, base=deeper
                        p_top  = interval_top
                        p_base = interval_btm
                        # Override top if next-day tag confirms shallower top
                        if confirmed_tag and confirmed_tag < p_top:
                            p_top = confirmed_tag
                    elif ttoc_rep and tag_local:
                        # TTOC/ETOC = plug TOP (shallowest cement reached)
                        # tag depth   = plug BASE (deepest tagged point)
                        p_top  = min(ttoc_rep, tag_local)
                        p_base = max(ttoc_rep, tag_local)
                    elif tag_local and confirmed_tag:
                        # Next-day tag confirms the plug top
                        p_top  = min(tag_local, confirmed_tag)
                        p_base = max(tag_local, confirmed_tag)
                    elif tag_local:
                        # Only tag depth — that's the plug BASE
                        # No way to know top precisely — flag LOW confidence
                        p_top  = max(0, tag_local - int(vol * 0.18)) if vol else 0
                        p_base = tag_local
                    else:
                        p_top  = 0
                        p_base = 0

                    # Sanity check: top must be shallower than base
                    if p_top and p_base and p_top > p_base:
                        p_top, p_base = p_base, p_top

                    conf = ("HIGH"   if (interval_top is not None and interval_btm is not None) else
                            "HIGH"   if (ttoc_rep and tag_local) else
                            "MEDIUM" if confirmed_tag else
                            "LOW")

                    plugs.append({
                        "plug_no":    plug_no,
                        "name":       f"Plug #{plug_no}",
                        "top_md":     p_top,
                        "base_md":    p_base,
                        "volume_cf":  vol,
                        "method":     "CTU",
                        "date":       date,
                        "class_":     class_,
                        "additives":  adds,
                        "ttoc_rep":   ttoc_rep or toc_rep,
                        "returns":    "GOOD RETURNS TO SURFACE." if (
                            "surface" in summary.lower() and "return" in summary.lower()
                        ) else "N/A",
                        "confidence": conf,
                        "notes":      summary[:120],
                    })
                    plug_no += 1

        # fluid between plugs
        plugs_sorted = sorted(plugs, key=lambda x: x["top_md"] or 0)
        fluids_btw = []
        for j in range(len(plugs_sorted)-1):
            if plugs_sorted[j]["base_md"] and plugs_sorted[j+1]["top_md"]:
                fluids_btw.append({
                    "from_md":    plugs_sorted[j]["base_md"],
                    "to_md":      plugs_sorted[j+1]["top_md"],
                    "fluid_type": "KCL WATER",
                    "fluid_wt":   "8.5 ppg",
                })

        # Pull PLUG BACK assembly from CD_ASSEMBLY_T — these are authoritative depths
        # CEMENT component = actual cement plug; BRIDGE PLUG = mechanical plug
        try:
            plug_asm_rows = qall(conn, f"""
                SELECT a.ASSEMBLY_NAME, ac.COMP_NAME, ac.OD_BODY,
                       ac.MD_TOP, ac.MD_BASE, ac.LENGTH, s.DATE_STATUS
                FROM {SCHEMA}.CD_ASSEMBLY_T a
                JOIN {SCHEMA}.CD_ASSEMBLY_COMP_T ac ON a.ASSEMBLY_ID = ac.ASSEMBLY_ID
                LEFT JOIN (
                    SELECT ASSEMBLY_ID, DATE_STATUS,
                           ROW_NUMBER() OVER (PARTITION BY ASSEMBLY_ID ORDER BY DATE_STATUS DESC) RN
                    FROM {SCHEMA}.CD_ASSEMBLY_STATUS_T
                ) s ON a.ASSEMBLY_ID = s.ASSEMBLY_ID AND s.RN = 1
                WHERE a.WELL_ID = :1
                AND UPPER(a.ASSEMBLY_NAME) LIKE '%PLUG%'
                ORDER BY ac.MD_TOP
            """, [well_id])

            asm_plugs = []
            for pr in plug_asm_rows:
                comp = (pr.get("COMP_NAME") or "").upper()
                # Only cement and bridge plugs
                if not any(x in comp for x in ("CEMENT","BRIDGE PLUG","SAND PLUG")):
                    continue
                raw_top = pr.get("MD_TOP")
                raw_btm = pr.get("MD_BASE")
                if raw_top is None: continue
                # PLUG BACK depths are SUBSEA — apply KB
                p_top = to_md(raw_top, kb)
                p_btm = to_md(raw_btm, kb) if raw_btm is not None else p_top
                if p_top is None: continue
                p_top_f = float(p_top)
                p_btm_f = float(p_btm) if p_btm is not None else p_top_f + 10
                # SKIP: Assembly wrapper records where the span = full wellbore
                # These have top very near surface/negative AND base near TD
                # The CEMENT component in PLUG BACK often is just a placeholder
                # Real indicator: LENGTH > 1000ft for cement = full-bore wrapper, skip it
                length = float(pr.get("LENGTH") or 0)
                if comp == "CEMENT" and length > 500:
                    continue  # Skip — this is the full assembly wrapper, not a real plug
                if p_top_f < -20: continue  # Invalid depth
                p_top = max(0, round(p_top_f, 1))
                p_btm = round(p_btm_f, 1)
                if p_btm <= p_top: p_btm = p_top + 10
                p_date = str(pr.get("DATE_STATUS") or "")[:10]
                asm_plugs.append({
                    "plug_no":    0,  # renumbered below
                    "name":       comp,
                    "top_md":     p_top,
                    "base_md":    p_btm,
                    "volume_cf":  0,   # volume comes from daily reports
                    "method":     "CTU",
                    "date":       p_date,
                    "class_":     "G",
                    "additives":  "",
                    "ttoc_rep":   None,
                    "returns":    "N/A",
                    "confidence": "HIGH",
                    "notes":      f"Source: CD_ASSEMBLY_T PLUG BACK ({comp})",
                })

            # Strategy:
            # - CEMENT plugs: daily reports are authoritative (assembly CEMENT = wrapper)
            # - BRIDGE/MECHANICAL plugs: add from assembly if not already covered
            if asm_plugs:
                for ap in asm_plugs:
                    if "CEMENT" in ap["name"].upper():
                        continue  # skip - cement wrappers, use daily reports
                    # Only add if not already covered by a daily-report plug
                    already_covered = any(
                        abs((dp.get("top_md") or 0) - ap["top_md"]) < 100 or
                        abs((dp.get("base_md") or 0) - ap["base_md"]) < 100
                        for dp in plugs
                    )
                    if not already_covered:
                        plugs.append(ap)
        except Exception:
            pass

        # Re-sort all plugs by top depth, renumber
        plugs_sorted = sorted(plugs, key=lambda x: float(x.get("top_md") or 0))
        for i, p in enumerate(plugs_sorted):
            p["plug_no"] = i + 1
        fluids_btw = []
        for j in range(len(plugs_sorted)-1):
            if plugs_sorted[j]["base_md"] and plugs_sorted[j+1]["top_md"]:
                fluids_btw.append({
                    "from_md":    plugs_sorted[j]["base_md"],
                    "to_md":      plugs_sorted[j+1]["top_md"],
                    "fluid_type": "KCL WATER",
                    "fluid_wt":   "8.5 ppg",
                })

        return {
            "event_id":    ev.get("EVENT_ID"),
            "objective":   na(ev.get("EVENT_OBJECTIVE_1")),
            "reason":      na(ev.get("EVENT_OBJECTIVE_2")),
            "date_start":  str(ev.get("DATE_OPS_START") or "")[:10],
            "date_end":    str(ev.get("DATE_OPS_END")   or "")[:10],
            "status":      na(ev.get("STATUS_END")),
            "contractor":  na(ev.get("PRIMARY_SERVICE_PROVIDER")),
            "team":        na(ev.get("EVENT_TEAM")),
            "total_cost":  total_cost,
            "daily_reports": daily,
            "cement_plugs":  plugs_sorted,
            "squeezes":      squeezes,
            "fluid_between_plugs": fluids_btw,
        }
    except Exception as e:
        return None


def collect(conn, identifier):
    hdr = find_well(conn, identifier)
    if not hdr:
        raise ValueError(f"Well '{identifier}' not found in {SCHEMA}")
    well_id = hdr["WELL_ID"]
    kb = float(hdr.get("WATER_DEPTH") or 1248.4)
    if kb < 0: kb = 1248.4
    pb_edm = hdr.get("PLUGBACK_MD")
    pb_md  = to_md(pb_edm, kb)
    if pb_md is not None and pb_md < 0:
        pb_md = to_md(hdr.get("BH_MD"), kb)
    status = na(hdr.get("END_STATUS")) if hdr.get("END_STATUS") else "ACTIVE"
    casings    = get_casings(conn, well_id, kb)
    cement     = get_cement(conn, well_id, kb)
    formations = get_formations(conn, well_id, kb)
    holes      = get_holes(conn, well_id, kb)
    # Detect P&A: either status says so, OR PLUG BACK assembly exists with cement
    status_is_pa = ("P" in status.upper() and "A" in status.upper()) or status.upper() in ("ABANDONED","P&A","P & A")
    try:
        plug_check = qall(conn, f"SELECT COUNT(*) AS CNT FROM {SCHEMA}.CD_ASSEMBLY_T WHERE WELL_ID = :1 AND UPPER(ASSEMBLY_NAME) LIKE '%PLUG%'", [well_id])
        has_plug_asm = plug_check[0]["CNT"] > 0 if plug_check else False
    except:
        has_plug_asm = False
    is_pa = status_is_pa or has_plug_asm
    pa_data    = get_pa_data(conn, well_id, kb) if is_pa else None
    total_comps = sum(len(a["comps"]) for a in casings)
    return {
        "well_name":   hdr.get("WELL_COMMON_NAME", str(identifier)),
        "api":         na(hdr.get("API_NO")),
        "field":       na(hdr.get("FIELD_NAME")),
        "operator":    na(hdr.get("WELL_OPERATOR")),
        "spud":        str(hdr.get("SPUD_DATE") or "")[:10],
        "purpose":     na(hdr.get("WELL_PURPOSE")),
        "target_fm":   na(hdr.get("TARGET_FORMATION")),
        "kb":          kb,
        "td_md":       to_md(hdr.get("BH_MD"), kb),
        "td_tvd":      to_md(hdr.get("BH_TVD"), kb),
        "pb_md":       pb_md,
        "lat":         hdr.get("GEO_LATITUDE"),
        "lon":         hdr.get("GEO_LONGITUDE"),
        "northing":    hdr.get("GEO_OFFSET_NORTH"),
        "easting":     hdr.get("GEO_OFFSET_EAST"),
        "status":      status,
        "well_id":     well_id,
        "wellbore":    na(hdr.get("WELLBORE_NAME")),
        "wellbore_id": na(hdr.get("WELLBORE_ID")),
        "contractor":  next((c["contractor"] for c in cement if c["contractor"]!="N/A"), "N/A"),
        "bht":         next((str(int(c["bht"])) for c in cement if c.get("bht")), "N/A"),
        "casings":     casings, "cement": cement,
        "formations":  formations, "holes": holes,
        "total_comps": total_comps, "pa_data": pa_data,
        "perfs":       get_perfs(conn, well_id, kb),
        "openings":    get_openings(conn, well_id, kb),
        "base_usdw":   next((fm["top_md"] for fm in get_formations(conn, well_id, kb)
                             if any(x in fm["name"].upper() for x in ("WILM","USDW","FRESH"))), None),
        "ground_elev": round(float(hdr.get("DATUM_ELEVATION") or kb) - 14.0, 2),
        "datum_elev":  float(hdr.get("DATUM_ELEVATION") or (kb + 14)),
        "generated":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ───────────────────────────────────────────────────────────────
# SVG SCHEMATIC
# ───────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────
# TTOC CALCULATION
# ───────────────────────────────────────────────────────────────
def calc_ttoc(fluids, shoe_md, hole_diam_in, csg_od_in, returns="", returns_bbls=0, spud_year=None):
    """
    Calculate Theoretical Top of Cement (TTOC).
    Returns (ttoc_depth, toc_label, calc_notes)
    Uses SOP default yields when EDM data is missing:
      Pre-1970:  yield=1.18 cf/sk, density=15.6 ppg
      Post-1970: yield=1.81 cf/sk, density=14.9 ppg
    """
    import math
    if not hole_diam_in or not csg_od_in or not shoe_md:
        return None, "TOC: Unknown", "Missing inputs (hole size or csg OD)"
    if not fluids:
        return None, "TOC: Unknown", "No cement fluid data"
    # SOP default yields by era
    default_yield = 1.18 if (spud_year and spud_year < 1970) else 1.81
    total_vol_cf = 0.0
    for f in fluids:
        sacks  = float(f.get("sacks",0) or 0)
        yield_ = float(f.get("yield_",0) or f.get("yield",0) or 0)
        # Apply SOP default if yield missing
        if yield_ <= 0:
            yield_ = default_yield
        excess = float(f.get("excess",0) or 0)
        if sacks > 0:
            vol = sacks * yield_
            if excess > 0:
                vol *= (1 + excess / 100)
            total_vol_cf += vol
    if total_vol_cf <= 0:
        return None, "TOC: Unknown", "No slurry volume calculable"
    hole_area = (math.pi / 4) * (float(hole_diam_in) ** 2) / 144
    csg_area  = (math.pi / 4) * (float(csg_od_in)   ** 2) / 144
    ann_cap = hole_area - csg_area
    if ann_cap <= 0:
        return None, "TOC: Unknown", "Invalid annular capacity"
    cem_height = total_vol_cf / ann_cap
    ttoc = float(shoe_md) - cem_height
    notes = f"Calc: {total_vol_cf:.0f} cf ÷ {ann_cap:.4f} ft³/ft = {cem_height:.0f}ft height"
    if ttoc <= 0 or (returns == "Full" and returns_bbls > 0):
        return 0, "TOC: Surface", f"Full returns confirmed — {returns_bbls:.0f} bbls to surface"
    if ttoc > float(shoe_md):
        return None, "TOC: Unknown", "Calculated TTOC below shoe — check inputs"
    return round(ttoc), f"TTOC: {ttoc:,.0f}ft", notes


# ───────────────────────────────────────────────────────────────
# WELLBORE OPENINGS QUERY
# ───────────────────────────────────────────────────────────────
def get_openings(conn, well_id, kb):
    """
    Pull wellbore openings with most current status per depth.
    Source: CD_WELLBORE_OPENING_T + CD_OPENING_STATUS_T
    EDM stores depths subsea — KB correction applied here.
    """
    try:
        rows = qall(conn, f"""
            SELECT wo.OPENING_TYPE,
                   wo.MD_TOP  AS WO_TOP,
                   wo.MD_BASE AS WO_BASE,
                   wo.OPENING_REASON,
                   os.STATUS,
                   os.EFFECTIVE_DATE,
                   os.MD_TOP  AS OS_TOP,
                   os.MD_BASE AS OS_BASE
            FROM {SCHEMA}.CD_WELLBORE_OPENING_T wo
            LEFT JOIN (
                SELECT WELLBORE_OPENING_ID, STATUS, EFFECTIVE_DATE, MD_TOP, MD_BASE,
                       ROW_NUMBER() OVER (
                           PARTITION BY WELLBORE_OPENING_ID
                           ORDER BY EFFECTIVE_DATE DESC
                       ) RN
                FROM {SCHEMA}.CD_OPENING_STATUS_T
            ) os ON wo.WELLBORE_OPENING_ID = os.WELLBORE_OPENING_ID AND os.RN = 1
            WHERE wo.WELL_ID = :1
            ORDER BY NVL(os.MD_TOP, wo.MD_TOP)
        """, [well_id])
        result = []
        for r in rows:
            # Use opening_status depths if available, else wellbore_opening depths
            raw_top = r.get("OS_TOP") or r.get("WO_TOP")
            raw_btm = r.get("OS_BASE") or r.get("WO_BASE")
            if raw_top is None:
                continue
            # CD_OPENING_STATUS_T MD_TOP stores SURFACE depths (not subsea)
            # Verified: 12-35R openings at 1904ft are correct surface depths
            # No KB correction needed for opening status depths
            try:
                top_f = float(raw_top)
                btm_f = float(raw_btm) if raw_btm is not None else top_f
            except:
                continue
            if top_f < 0:
                continue
            top = round(top_f, 1)
            btm = round(btm_f, 1)
            result.append({
                "type":     na(r.get("OPENING_TYPE")),
                "top":      round(float(top), 1),
                "btm":      round(float(btm), 1),
                "status":   na(r.get("STATUS")),
                "eff_date": str(r.get("EFFECTIVE_DATE") or "")[:10],
                "reason":   na(r.get("OPENING_REASON")),
            })
        return result
    except Exception as e:
        return []


def get_perfs(conn, well_id, kb):
    """Pull perforation intervals from EDM."""
    try:
        rows = qall(conn, f"""
            SELECT pi.MD_TOP_SHOT, pi.MD_BOTTOM_SHOT,
                   pi.SHOT_DENSITY, pi.INTERVAL_TYPE,
                   pi.DATE_INTERVAL_SHOT, pi.COMMENTS,
                   p.FLUID_TYPE, p.CONTRACTOR
            FROM {SCHEMA}.CD_PERFORATE_T p
            JOIN {SCHEMA}.CD_PERF_INTERVAL_T pi ON p.PERF_ID = pi.PERF_ID
            WHERE p.WELL_ID = :1
            ORDER BY pi.MD_TOP_SHOT
        """, [well_id])
        result = []
        for r in rows:
            # CD_PERFORATE_T depths are already surface MD — NO KB correction
            raw_top = r.get("MD_TOP_SHOT")
            raw_btm = r.get("MD_BOTTOM_SHOT")
            if raw_top is None: continue
            top = round(float(raw_top), 1)
            btm = round(float(raw_btm or raw_top), 1)
            if top < 0: continue  # skip invalid
            result.append({
                "top":   top,
                "btm":   btm,
                "spf":   float(r.get("SHOT_DENSITY") or 0),
                "type":  na(r.get("INTERVAL_TYPE")),
                "date":  str(r.get("DATE_INTERVAL_SHOT") or "")[:10],
                "fluid": na(r.get("FLUID_TYPE")),
                "contractor": na(r.get("CONTRACTOR")),
            })
        return result
    except Exception:
        return []



# ───────────────────────────────────────────────────────────────
# OILFIELD NARRATIVE GENERATORS
# ───────────────────────────────────────────────────────────────
def cement_narrative(cem):
    """Generate lean oilfield-format cement job narrative."""
    fluids   = cem.get("fluids", [])
    lf       = fluids[0] if fluids else {}
    tf       = fluids[1] if len(fluids) > 1 else None
    parts    = []
    returns  = cem.get("returns","")
    rtns_bbl = float(cem.get("returns_bbls") or 0)
    end_time = cem.get("end_time","")
    date_str = cem.get("date","") or cem.get("start","")[:10] if cem.get("start") else ""
    if lf:
        lf_sacks  = float(lf.get("sacks",0) or 0)
        lf_yield  = float(lf.get("yield_",0) or lf.get("yield",0) or 0)
        lead_cf   = round(lf_sacks * lf_yield) if lf_sacks and lf_yield else 0
        lead_bbls = round(float(lf.get("vol",0) or lf.get("vol_slurry",0) or (lead_cf / 5.615 if lead_cf else 0)))
        parts.append(
            f'PUMPED {lead_bbls} BBLS ({fmt(lf.get("sacks",0),0)} SX '
            f'— {lead_cf} CF) OF LEAD SLURRY MIXED AT '
            f'{fmt(lf.get("density",0),1)} PPG CLASS "{lf.get("class_","")}" '
            f'CEMENT + {na(lf.get("desc",""))} WITH YIELD OF {fmt(lf.get("yield_",0) or lf.get("yield",0),2)} CF/SK.'
        )
    if tf:
        tail_cf   = round(float(tf.get("sacks",0)) * float(tf.get("yield_",0) or tf.get("yield",0) or 0))
        tail_bbls = round(float(tf.get("vol",0) or tf.get("vol_slurry",0) or 0))
        parts.append(
            f'FOLLOWED BY {tail_bbls} BBLS ({fmt(tf.get("sacks",0),0)} SX '
            f'— {tail_cf} CF) OF TAIL SLURRY MIXED AT '
            f'{fmt(tf.get("density",0),1)} PPG CLASS "{tf.get("class_","")}" '
            f'CEMENT + {na(tf.get("desc",""))} WITH YIELD OF {fmt(tf.get("yield_",0) or tf.get("yield",0),2)} CF/SK.'
        )
    if cem.get("float_held") == "Y":
        t = f" AT {end_time} ON {date_str}" if end_time and date_str else ""
        parts.append(f'THE FLOAT HELD AND C.I.P.{t}.')
    if returns == "Full":
        rtn = f' — {rtns_bbl:.0f} BBLS TO SURFACE.' if rtns_bbl else ' WITH FULL RETURNS.'
        parts.append(f'CEMENTED{rtn}')
    toc_lbl  = cem.get("toc_label","")
    toc_note = cem.get("ttoc_notes","")
    if toc_lbl:
        parts.append(f'{toc_lbl}. ({toc_note})')
    return " ".join(parts) if parts else "Cement details not available in EDM."


def plug_narrative(plug, plug_no):
    """Generate lean P&A plug narrative per SOP."""
    method = plug.get("method","CTU")
    top    = plug.get("top_md") or plug.get("top") or 0
    btm    = plug.get("base_md") or plug.get("btm") or 0
    vol    = plug.get("volume_cf") or plug.get("vol") or 0
    fluid  = plug.get("fluid_type") or plug.get("fluid") or "Neat Cement"
    wt     = plug.get("fluid_wt") or plug.get("wt") or ""
    cls_   = plug.get("class_","")
    adds   = plug.get("additives","")
    notes  = (plug.get("notes","") or "").strip()
    ttoc   = plug.get("ttoc_rep")
    conf   = plug.get("confidence","")
    ret    = plug.get("returns","") or ""
    # Build class/additives string
    cem_desc = f"CLASS {cls_}" if cls_ else "NEAT"
    if adds: cem_desc += f" W/ {adds}"
    wt_str  = f" AT {wt}" if wt else ""
    ret_str = f" {ret}" if ret and ret not in ("N/A","") else ""
    ttoc_str = f" REPORTED TTOC: {ttoc:,}\'." if ttoc else ""
    conf_str = f" ({conf} confidence)" if conf and conf != "HIGH" else ""
    top_i = int(top) if top else 0
    btm_i = int(btm) if btm else 0
    vol_i = int(vol) if vol else 0
    return (
        f"RIH W/ {method}. MIX AND PUMP {vol_i} CF OF {cem_desc}{wt_str}. "
        f"SET CEMENT PLUG FROM {top_i:,}\' TO {btm_i:,}\' MD."
        f"{ttoc_str}{ret_str} {notes}{conf_str}"
    ).strip()



# ───────────────────────────────────────────────────────────────
# SVG SCHEMATIC — CalGEM 8.5x11 style
# ───────────────────────────────────────────────────────────────
def build_svg(w):
    max_md = w["td_md"] or 5000
    H = 860; cx = 95; SW = 195
    kb = w.get("kb", 0) or 0

    def sy(d):
        if d is None or max_md <= 0: return 0
        return round((float(d)/max_md)*H, 1)

    s = [f'<svg viewBox="0 0 {SW} {H}" xmlns="http://www.w3.org/2000/svg" width="{SW}" height="{H}">',
         f'<rect width="{SW}" height="{H}" fill="white"/>']

    d = 0
    while d <= max_md:
        s.append(f'<line x1="28" y1="{sy(d)}" x2="{SW-2}" y2="{sy(d)}" stroke="#f0ede6" stroke-width="0.4"/>'); d+=500
    s.append(f'<line x1="30" y1="0" x2="30" y2="{H}" stroke="#aaa" stroke-width="0.6"/>')
    d = 0
    while d <= max_md:
        y=sy(d); tw="1.2" if d%1000==0 else "0.6"
        s.append(f'<line x1="27" y1="{y}" x2="33" y2="{y}" stroke="#999" stroke-width="{tw}"/>')
        if d%500==0:
            s.append(f'<text x="25" y="{y+3}" font-size="5.5" font-family="Courier New" fill="#777" text-anchor="end">{d:,}</text>')
        d+=500

    # Ground level — ~14ft below KB in surface MD
    gl_md = 14.0
    gy = sy(gl_md)
    ge = w.get("ground_elev")
    gl_label = f"Ground Level {ge:.0f}ft MSL" if ge else "Ground Level"
    s.append(f'<line x1="30" y1="{gy}" x2="{SW-2}" y2="{gy}" stroke="#8B4513" stroke-width="1" stroke-dasharray="4,2"/>')
    s.append(f'<text x="32" y="{gy-1}" font-size="5" font-family="Arial" fill="#8B4513" font-weight="bold">{gl_label}</text>')

    # Base USDW
    base_usdw = w.get("base_usdw")
    if base_usdw and base_usdw > 0:
        uy = sy(base_usdw)
        s.append(f'<line x1="30" y1="{uy}" x2="{SW-2}" y2="{uy}" stroke="#0066cc" stroke-width="0.8" stroke-dasharray="3,2"/>')
        s.append(f'<text x="32" y="{uy-1}" font-size="5" font-family="Arial" fill="#0066cc">Base USDW {base_usdw:.0f}ft</text>')

    # Hole sections
    hw_map = {18.0:50,13.5:40,9.875:32,8.75:26,24.0:60,12.25:44,6.0:20}
    for h in w.get("holes", []):
        diam = h.get("diam",0)
        if not diam or not h.get("base_md") or h.get("top_md") is None: continue
        hw = hw_map.get(round(diam,3), int((diam/18)*50))
        yt=sy(max(0,h["top_md"])); yb=sy(min(max_md,h["base_md"]))
        for side in [cx-hw,cx+hw]:
            s.append(f'<line x1="{side}" y1="{yt}" x2="{side}" y2="{yb}" stroke="#c4a060" stroke-width="0.9" stroke-dasharray="3,2"/>')

    # Cement fills — solid dark blue using TTOC or TOC
    cem_col = {"SURFACE CASING":"#1a4a8a","PRODUCTION CASING":"#0e2d5e"}
    for ci,cem in enumerate(w.get("cement",[])):
        asm = next((a for a in w.get("casings",[]) if a["name"]==cem.get("assembly","")),None)
        if not asm: continue
        od = asm["size"]; hw=int((od/18)*36)+4; wall=5
        # Use ttoc if available, else toc, else 0
        toc_depth = cem.get("ttoc") if cem.get("ttoc") is not None else max(0, cem.get("toc") or 0)
        if toc_depth is None: toc_depth = 0
        toc_depth = max(0, toc_depth)
        base = min(max_md, cem.get("stage_base") or asm.get("base_md") or max_md)
        if base <= toc_depth: continue
        yt=sy(toc_depth); yb=sy(base); h_=yb-yt
        col = cem_col.get(asm["name"],"#1a4a8a")
        # Solid fill
        s.append(f'<rect x="{cx-hw-wall}" y="{yt}" width="{wall}" height="{h_}" fill="{col}" opacity="0.85"/>')
        s.append(f'<rect x="{cx+hw}" y="{yt}" width="{wall}" height="{h_}" fill="{col}" opacity="0.85"/>')
        # TOC label
        toc_lbl = cem.get("toc_label","")
        if not toc_lbl:
            toc_lbl = "TOC: Surface" if toc_depth <= 0 else f"TOC: {toc_depth:,.0f}ft"
        s.append(f'<line x1="{cx-hw-wall-2}" y1="{yt}" x2="{cx-hw-wall-14}" y2="{yt}" stroke="{col}" stroke-width="0.7"/>')
        s.append(f'<text x="{cx-hw-wall-16}" y="{yt+4}" font-size="5.5" font-family="Courier New" fill="{col}" text-anchor="end" font-weight="bold">{toc_lbl}</text>')

    # Casings
    gray={"CONDUCTOR CASING":"#777","SURFACE CASING":"#999","PRODUCTION CASING":"#bbb","GRAVEL PACK LINER":"#aaa"}
    for asm in w.get("casings",[]):
        if not asm.get("size") or not asm.get("base_md") or asm.get("top_md") is None: continue
        od=asm["size"]; hw=int((od/18)*36); wall=3
        yt=sy(max(0,asm["top_md"])); yb=sy(min(max_md,asm["base_md"])); fc=gray.get(asm["name"],"#bbb")
        for x0 in [cx-hw-wall,cx+hw]:
            s.append(f'<rect x="{x0}" y="{yt}" width="{wall}" height="{yb-yt}" fill="{fc}" stroke="#555" stroke-width="0.7"/>')
        s.append(f'<polygon points="{cx-hw-wall},{yb} {cx-hw},{yb} {cx-hw-1},{yb+6}" fill="#555"/>')
        s.append(f'<polygon points="{cx+hw+wall},{yb} {cx+hw},{yb} {cx+hw+1},{yb+6}" fill="#555"/>')
        my=(yt+yb)/2
        s.append(f'<text x="{cx+hw+wall+4}" y="{my+3}" font-size="6" font-family="Arial Narrow,Arial" font-weight="bold" fill="#333">{od}"</text>')
        s.append(f'<text x="{cx+hw+wall+4}" y="{yb+5}" font-size="5" font-family="Courier New" fill="#666">@{fmt(asm["base_md"])}ft</text>')

    # Formation tops — lines only, no text labels
    for fm in w.get("formations",[]):
        if not fm.get("top_md"): continue
        fy=sy(fm["top_md"])
        s.append(f'<line x1="33" y1="{fy}" x2="{SW-2}" y2="{fy}" stroke="#1B2F5B" stroke-width="0.5" stroke-dasharray="3,2" opacity="0.6"/>')
        # Small formation name on left side only
        s.append(f'<text x="35" y="{fy-1}" font-size="5.5" font-family="Arial Narrow" font-weight="bold" fill="#1B2F5B">{fm["name"]}</text>')
        s.append(f'<text x="{SW-3}" y="{fy-1}" font-size="5" font-family="Courier New" fill="#1B2F5B" text-anchor="end">{fmt(fm.get("top_md",""))}ft</text>')

    # ── PERFORATIONS — draw on right side of deepest/smallest casing ──
    perfs = w.get("perfs", [])
    if perfs:
        # Find the best casing to attach ticks to
        prod_csg = None
        for name_key in ["PRODUCTION CASING", "GRAVEL PACK LINER", "SURFACE CASING"]:
            prod_csg = next((a for a in w.get("casings",[]) if name_key.upper() in a.get("name","").upper()), None)
            if prod_csg: break
        if not prod_csg and w.get("casings"):
            prod_csg = w["casings"][-1]
        prod_hw = int((float(prod_csg.get("size",7))/18)*36) if prod_csg else 26
        for p in perfs:
            pt = p.get("top", 0); pb = p.get("btm", pt)
            if not pt or pt <= 0: continue
            yt = sy(pt); yb = sy(pb); mid = (yt+yb)/2
            # Draw dense ticks across the interval
            n_ticks = max(3, int((yb - yt) / 3) + 1)
            step = (yb - yt) / max(n_ticks - 1, 1)
            for k in range(n_ticks):
                ty = yt + k * step
                s.append(f'<line x1="{cx+prod_hw+3}" y1="{ty:.1f}" x2="{cx+prod_hw+12}" y2="{ty:.1f}" stroke="#c03030" stroke-width="1.2"/>')

    # ── P&A PLUGS ────────────────────────────────────────────────
    if w.get("pa_data") and w["pa_data"].get("cement_plugs"):
        pcols=["#C03030","#9a1010","#7a0000","#5a0000","#3a0000"]
        s.append('<defs>')
        n_plugs = min(len(w["pa_data"]["cement_plugs"]), 5)
        for i in range(n_plugs):
            col=pcols[i]
            s.append(f'<pattern id="pp{i}" x="0" y="0" width="5" height="5" patternUnits="userSpaceOnUse"><rect width="5" height="5" fill="{col}" opacity="0.9"/><line x1="0" y1="5" x2="5" y2="0" stroke="rgba(255,255,255,0.3)" stroke-width="0.8"/></pattern>')
        s.append('</defs>')
        for i, plug in enumerate(w["pa_data"]["cement_plugs"]):
            if i >= 5: break
            col = pcols[i]; ihw=13; tp=3
            # Support both key naming conventions
            pt  = plug.get("top_md") or plug.get("top") or 0
            pb_ = plug.get("base_md") or plug.get("btm") or 0
            # Skip invalid plugs
            if not pt or pt <= 0: continue
            if pb_ <= pt: pb_ = pt + 10
            yt=sy(pt); yb=sy(pb_); ph=max(yb-yt, 8)
            s.append(f'<rect x="{cx-ihw}" y="{yt}" width="{ihw*2}" height="{ph}" fill="url(#pp{i})" rx="1"/>')
            s.append(f'<polygon points="{cx-ihw+tp},{yt} {cx+ihw-tp},{yt} {cx+ihw},{yt+3} {cx-ihw},{yt+3}" fill="{col}"/>')
            s.append(f'<polygon points="{cx-ihw},{yb-3} {cx+ihw},{yb-3} {cx+ihw-tp},{yb} {cx-ihw+tp},{yb}" fill="{col}"/>')
            mid_y = (yt+yb)/2
            s.append(f'<text x="{cx}" y="{mid_y+2}" font-size="5.5" font-family="Arial" font-weight="bold" fill="white" text-anchor="middle">P-{i+1}</text>')
            # Left side depth annotations
            s.append(f'<line x1="{cx-ihw}" y1="{yt}" x2="{cx-ihw-8}" y2="{yt}" stroke="{col}" stroke-width="0.6"/>')
            s.append(f'<text x="{cx-ihw-10}" y="{yt+4}" font-size="5" font-family="Courier New" fill="{col}" text-anchor="end">{int(pt):,}ft</text>')
            s.append(f'<line x1="{cx-ihw}" y1="{yb}" x2="{cx-ihw-8}" y2="{yb}" stroke="{col}" stroke-width="0.6"/>')
            s.append(f'<text x="{cx-ihw-10}" y="{yb+4}" font-size="5" font-family="Courier New" fill="{col}" text-anchor="end">{int(pb_):,}ft</text>')
        # Fluid between plugs labels
        for fb in w["pa_data"].get("fluid_between_plugs", []):
            yt=sy(fb.get("from_md",0)); yb=sy(fb.get("to_md",0)); mid=(yt+yb)/2
            if yb-yt > 18:
                s.append(f'<text x="{cx}" y="{mid+2}" font-size="5" font-family="Courier New" fill="#0055aa" text-anchor="middle">{fb.get("fluid_type","KCL WATER")}</text>')

    # Wellhead
    s+=[f'<rect x="{cx-17}" y="2" width="34" height="8" fill="#1B2F5B" rx="1"/>',
        f'<rect x="{cx-12}" y="10" width="24" height="6" fill="#2a3f6a" rx="1"/>',
        f'<rect x="{cx-8}" y="16" width="16" height="4" fill="#1B2F5B" rx="1"/>',
        f'<text x="{cx}" y="1" font-size="5" font-family="Arial" fill="#1B2F5B" text-anchor="middle">WELLHEAD</text>']
    tdy=sy(max_md)
    s+=[f'<line x1="{cx-28}" y1="{tdy}" x2="{cx+28}" y2="{tdy}" stroke="#222" stroke-width="2.5"/>',
        f'<text x="{cx}" y="{tdy+9}" font-size="6" font-family="Arial" font-weight="bold" fill="#222" text-anchor="middle">TD {fmt(max_md)} ft MD</text>',
        '</svg>']
    return "\n".join(s)


# ───────────────────────────────────────────────────────────────
# HTML BUILDER — CalGEM 8.5x11 three-column layout
# ───────────────────────────────────────────────────────────────
def build_html(w):
    SVG = build_svg(w)
    pa  = w.get("pa_data")
    su  = w.get("status","").upper()
    is_pa = pa is not None or ("P" in su and "A" in su)

    # API — prefer 12 digit
    api_raw = str(w.get("api","") or "").replace("-","")
    if len(api_raw) == 10:
        api12 = f"{api_raw[:2]}-{api_raw[2:5]}-{api_raw[5:10]}-00"
    elif len(api_raw) >= 12:
        api12 = f"{api_raw[:2]}-{api_raw[2:5]}-{api_raw[5:10]}-{api_raw[10:12]}"
    else:
        api12 = w.get("api","N/A")

    abandoned_date = ""
    if is_pa and pa:
        abandoned_date = pa.get("date_end","")
    elif is_pa:
        abandoned_date = w.get("generated","")[:10]

    coord_x = fmt(w.get("northing")) + " usft" if w.get("northing") else "N/A"
    coord_y = fmt(w.get("easting"))  + " usft" if w.get("easting")  else "N/A"

    # ── Left column ──────────────────────────────────────────
    def left_col():
        h=""
        # Hole sections
        h+='<div class="sec">HOLE SECTIONS</div>'
        h+='<table class="ct"><tr><th>Hole Size</th><th>Top MD(ft)</th><th>Base MD(ft)</th></tr>'
        for hole in w.get("holes",[]):
            h+=f'<tr><td class="in">{hole.get("name","")}</td><td>{fmt(hole.get("top_md",0))}</td><td>{fmt(hole.get("base_md",0))}</td></tr>'
        h+='</table>'
        # Casings
        for asm in w.get("casings",[]):
            h+=f'<div class="csec">{asm["name"]}<span class="rd">Report Date: {asm.get("installed","")}</span></div>'
            h+='<table class="ct"><tr><th>Item</th><th>OD(in)</th><th>Grade</th><th>Wt(ppf)</th><th>Conn</th><th>Top MD(ft)</th><th>Btm MD(ft)</th></tr>'
            for c in asm.get("comps",[]):
                h+=(f'<tr><td class="in">{c["name"]}</td>'
                    f'<td>{fmt(c.get("od"),3)}</td><td>{c.get("grade","")}</td>'
                    f'<td>{c.get("wt","")}</td><td>{c.get("conn","")}</td>'
                    f'<td>{fmt(c.get("top"),0)}</td><td>{fmt(c.get("base"),0)}</td></tr>')
            h+='</table>'
        return h

    # ── Right column ─────────────────────────────────────────
    def right_col():
        h=""
        # 1. Casing cement detail — narrative
        h+='<div class="sec">CASING CEMENT DETAIL</div>'
        for cem in w.get("cement",[]):
            lbl = "SURF. CSG." if "SURFACE" in cem.get("assembly","").upper() else "PROD. CSG."
            fluids = cem.get("fluids",[])
            total_cf = sum(float(f.get("sacks",0) or 0)*float(f.get("yield_",0) or f.get("yield",0) or 0) for f in fluids)
            # Enrich cem dict for narrative + TTOC
            cem_enrich = dict(cem)
            asm = next((a for a in w.get("casings",[]) if a["name"]==cem.get("assembly","")), {})
            if asm:
                comps = asm.get("comps",[])
                cem_enrich["csg_od"]    = cem_enrich.get("csg_od") or asm.get("size") or (comps[0].get("od") if comps else None)
                cem_enrich["csg_grade"] = cem_enrich.get("csg_grade") or next((c["grade"] for c in comps if c.get("grade") not in ("N/A",None,"")), "")
                cem_enrich["csg_wt"]    = cem_enrich.get("csg_wt") or next((c["wt"] for c in comps if c.get("wt") not in ("N/A",None,"")), "")
                cem_enrich["csg_conn"]  = cem_enrich.get("csg_conn") or next((c["conn"] for c in comps if c.get("conn") not in ("N/A",None,"")), "")
            # Calculate TTOC
            shoe_md   = cem.get("stage_base") or asm.get("base_md")
            # hole_size: try cement stage, then assembly, then fallback by casing size
            hole_diam = (cem_enrich.get("hole_size") or asm.get("hole_size") or
                         ({9.625: 13.5, 7.0: 9.875, 14.0: 18.0, 4.5: 6.5}.get(
                             float(asm.get("size",0) or 0), None)))
            csg_od    = cem_enrich.get("csg_od") or asm.get("size")
            rtns_bbl  = float(cem.get("returns_bbls",0) or 0)
            spud_str = w.get("spud","")
            try: spud_yr = int(spud_str[:4])
            except: spud_yr = None
            ttoc_val, toc_lbl, ttoc_note = calc_ttoc(
                fluids, shoe_md, hole_diam, csg_od,
                cem.get("returns",""), rtns_bbl, spud_yr
            )
            cem_enrich["toc_label"]   = toc_lbl
            cem_enrich["ttoc_notes"]  = ttoc_note
            narrative = cement_narrative(cem_enrich)
            h+=f'<div class="divline">- - - - - - - - - - - - - - - - - - - - - - -</div>'
            csg_od_display = cem_enrich.get("csg_od") or asm.get("size","") or ""
            csg_od_str = f'{float(csg_od_display):.3f}"' if csg_od_display else ""
            h+=f'<div class="rm"><strong>{lbl} {csg_od_str} &nbsp;|&nbsp; {cem.get("start","")[:10]} &nbsp;|&nbsp; {total_cf:.0f} CF &nbsp;|&nbsp; {toc_lbl}</strong><br>'
            h+=f'<span class="narrative">{narrative}</span></div>'

        # 2. Cement plug details — title per plug + narrative
        if pa and pa.get("cement_plugs"):
            h+='<div class="divline">- - - - - - - - - - - - - - - - - - - - - - -</div>'
            h+='<div class="sec">CEMENT PLUG DETAILS</div>'
            for i,plug in enumerate(pa["cement_plugs"]):
                h+=f'<div class="divline">- - - - - - - - - - - - - - - - - - - - - - -</div>'
                pt  = plug.get("top_md") or plug.get("top",0)
                pb_ = plug.get("base_md") or plug.get("btm",0)
                vol = plug.get("volume_cf") or plug.get("vol",0)
                p_date = plug.get("date","") or ""
                # Title: Plug N: top' – btm' | date | vol cf
                h+=(f'<div class="rm"><strong>PLUG {i+1}: {pt:,}\' – {pb_:,}\' '
                    f'| {p_date} | {vol} CF</strong><br>')
                narrative = plug_narrative(plug, i+1)
                h+=f'<span class="narrative">{narrative}</span></div>'
            h+='<div class="divline">- - - - - - - - - - - - - - - - - - - - - - -</div>'

        # 3. Wellbore Openings — table with most current status
        openings = w.get("openings",[])
        h+='<div class="sec">WELLBORE OPENINGS</div>'
        h+='<table class="ct"><tr><th>Status</th><th>Eff. Date</th><th>MD Top(ft)</th><th>MD Base(ft)</th><th>Type</th></tr>'
        for p in openings:
            st = p.get("status","")
            # SOP status codes: CLSD_CBK=plug, CLSD_CMT=squeeze, CLSD_RMV=removed, OPEN_INF=ineffective
            closed_codes = {"ABANDONED","CLSD_CBK","CLSD_CMT","CLSD_RMV","CLSD_BLK","CLSD_PAT"}
            open_codes   = {"OPEN","OPEN_INF"}
            st_col = "#8B0000" if st.upper() in closed_codes else "#1a6a3a" if st.upper() in open_codes else "#555"
            h+=(f'<tr><td style="font-weight:700;color:{st_col};text-align:left">{st}</td>'
                f'<td>{p.get("eff_date","")}</td>'
                f'<td>{p.get("top",0):,.1f}</td>'
                f'<td>{p.get("btm",0):,.1f}</td>'
                f'<td class="in">{p.get("type","")}</td></tr>')
        if not openings:
            h+='<tr><td colspan="5" style="color:#888;text-align:center">No opening data</td></tr>'
        h+='</table>'

        # 4. Formation tops
        h+='<div class="sec">FORMATION TOPS</div>'
        h+='<table class="ct"><tr><th>Formation Name</th><th>MD(ft)</th><th>TVD(ft)</th></tr>'
        for fm in w.get("formations",[]):
            if not fm.get("top_md"): continue  # skip null depth formations
            h+=f'<tr><td class="in">{fm["name"]}</td><td>{fmt(fm.get("top_md"))}</td><td>{fmt(fm.get("top_tvd"))}</td></tr>'
        base_usdw = w.get("base_usdw")
        if base_usdw:
            h+=f'<tr style="color:#0066cc;font-weight:700"><td>BOFW / BASE USDW</td><td>{fmt(base_usdw,1)}</td><td>—</td></tr>'
        h+='</table>'
        return h

    # ── Header left block ─────────────────────────────────────
    # Format API-12
    api_raw = str(w.get("api","") or "").replace("-","")
    if len(api_raw) == 10:
        api12 = f"{api_raw[:2]}-{api_raw[2:5]}-{api_raw[5:10]}-00"
    elif len(api_raw) >= 12:
        api12 = f"{api_raw[:2]}-{api_raw[2:5]}-{api_raw[5:10]}-{api_raw[10:12]}"
    else:
        api12 = w.get("api","N/A")

    coord_x = fmt(w.get("northing")) + " usft" if w.get("northing") else "N/A"
    coord_y = fmt(w.get("easting"))  + " usft" if w.get("easting")  else "N/A"
    kb_elev = round(float(w.get("datum_elev") or (w.get("kb",0) or 0) + 14), 2)

    left_kv = f'''
    <div class="kv"><span class="kk">Field Name</span><span class="vv">{na(w.get("field"))}</span></div>
    <div class="kv"><span class="kk">BH Coord X</span><span class="vv">{coord_x}</span></div>
    <div class="kv"><span class="kk">BH Coord Y</span><span class="vv">{coord_y}</span></div>
    <div class="kv"><span class="kk">Spud Date</span><span class="vv">{w.get("spud","")}</span></div>
    <div class="kv"><span class="kk">Well Status</span><span class="vv" style="color:{'#ff9999' if 'P' in w.get('status','').upper() and 'A' in w.get('status','').upper() else '#90ee90'}">{w.get("status","")}</span></div>
'''
    if is_pa and abandoned_date:
        left_kv += f'    <div class="kv"><span class="kk">Abandoned Date</span><span class="vv" style="color:#ff9999">{abandoned_date}</span></div>\n'
    left_kv += f'''    <div class="kv"><span class="kk">Wellbore</span><span class="vv">{na(w.get("wellbore"))}</span></div>
    <div class="kv"><span class="kk">API-12</span><span class="vv">{api12}</span></div>
'''

    sch_title = f'SCHEMATIC (P&A {abandoned_date})' if is_pa and abandoned_date else 'SCHEMATIC'

    CSS = """
@page{size:8.5in 11in;margin:0.2in;-webkit-print-color-adjust:exact;print-color-adjust:exact;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:Arial,sans-serif;font-size:6.5pt;color:#111;background:white;-webkit-print-color-adjust:exact;print-color-adjust:exact;
     width:8.0in;height:10.6in;overflow:hidden;}
.hdr{background:#1B2F5B;display:grid;
     grid-template-columns:1.35in 1fr 1.5in;
     align-items:stretch;border-bottom:2pt solid #C8860A;}
.hdr-logo{background:#1B2F5B;padding:4pt 6pt;display:flex;align-items:center;
           justify-content:center;border-right:1px solid rgba(255,255,255,0.15);}
.hdr-logo img{height:42pt;width:auto;filter:brightness(0) invert(1);}
.hdr-left{padding:3pt 5pt;border-right:1px solid rgba(255,255,255,0.15);
           display:flex;flex-direction:column;gap:2pt;}
.hdr-center{text-align:center;padding:3pt 6pt;
            display:flex;flex-direction:column;justify-content:center;}
.hdr-title{font-size:7.5pt;font-weight:700;color:white;letter-spacing:0.3pt;}
.hdr-well{font-size:20pt;font-weight:900;color:white;letter-spacing:2pt;line-height:1;}
.hdr-right{padding:3pt 5pt;display:flex;flex-direction:column;
            gap:1.5pt;border-left:1px solid rgba(255,255,255,0.15);}
.kv{display:flex;justify-content:space-between;gap:3pt;}
.kk{font-size:6pt;color:rgba(200,220,255,0.8);white-space:nowrap;}
.vv{font-family:'Courier New';font-size:6.5pt;color:white;text-align:right;}
.accent{background:#C8860A;height:2pt;}
.body{display:grid;grid-template-columns:3.0in 2.05in 1fr;
      height:calc(10.6in - 0.75in);overflow:hidden;}
.cl{border-right:1pt solid #1B2F5B;padding:3pt 4pt;overflow:hidden;font-size:5.5pt;}
.cm{border-right:1pt solid #1B2F5B;background:#fafaf8;display:flex;flex-direction:column;}
.cr{padding:3pt 4pt;overflow:hidden;}
.sch-hdr{background:#1B2F5B;color:white;font-size:5.5pt;font-weight:700;
         letter-spacing:0.8pt;text-transform:uppercase;padding:2pt 4pt;
         border-left:2pt solid #C8860A;flex-shrink:0;}
.sec{background:#1B2F5B;color:white;font-size:5.5pt;font-weight:700;
     letter-spacing:0.4pt;text-transform:uppercase;padding:1pt 3pt;
     margin-top:2pt;border-left:2pt solid #C8860A;}
.csec{background:#2a3f6a;color:white;font-size:5.5pt;font-weight:700;
      padding:1pt 3pt;margin-top:2pt;display:flex;justify-content:space-between;}
.rd{font-size:5.5pt;font-weight:400;color:rgba(200,220,255,0.8);}
.ct{width:100%;border-collapse:collapse;font-family:'Courier New';font-size:5pt;}
.ct th{background:#e4e1d8;color:#1B2F5B;font-family:Arial;font-size:5pt;font-weight:700;
       text-transform:uppercase;padding:0.5pt 2pt;border-bottom:1pt solid #1B2F5B;text-align:right;}
.ct th:first-child{text-align:left;}
.ct td{padding:0.4pt 2pt;border-bottom:0.4pt solid #eee;text-align:right;white-space:nowrap;}
.ct td:first-child{text-align:left;}
.ct tr:nth-child(even){background:#f8f5ee;}
.in{font-weight:600;color:#1B2F5B;}
.rm{font-size:5.5pt;padding:2pt 2pt;line-height:1.5;border-bottom:0.5pt solid #eee;}
.narrative{font-family:'Courier New',monospace;font-size:5pt;line-height:1.65;
           color:#111;display:block;margin-top:1.5pt;}
.divline{font-size:5pt;color:#ccc;padding:0.8pt 2pt;}
.op{font-size:5.5pt;border-bottom:0.5pt solid #fde8e8;padding:1pt 2pt;
    line-height:1.4;color:#c03030;}
.od{font-weight:700;}
.foot{background:#1B2F5B;padding:2pt 6pt;display:flex;justify-content:space-between;
      font-family:'Courier New';font-size:5.5pt;color:rgba(200,220,255,0.8);
      border-top:2pt solid #C8860A;}
"""

    # Logo — embedded if available
    logo_tag = ""
    logo_path = r"C:\Users\castroca\OneDrive - California Resources Corporation\Desktop\CRC_logo.png"
    import os, base64
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as lf_:
            logo_b64 = base64.b64encode(lf_.read()).decode()
        logo_tag = f'<img src="data:image/png;base64,{logo_b64}" alt="CRC">' 
    else:
        # Text fallback
        logo_tag = '<div style="color:white;font-size:7pt;font-weight:900;text-align:center;line-height:1.2">CALIFORNIA<br>RESOURCES</div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>@media print{{*{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important;color-adjust:exact!important;}}html,body{{width:8.0in;height:10.4in;overflow:hidden;}}}}</style>
<title>{w.get("well_name","Well")} — CRC Wellbore Schematic</title>
<style>{CSS}</style>
</head><body>
<div class="hdr">

  <div class="hdr-left">{left_kv}</div>
  <div class="hdr-center">
    <div class="hdr-title">CALIFORNIA RESOURCES ELK HILLS, LLC</div>
    <div class="hdr-well">{w.get("well_name","")}</div>
  </div>
  <div class="hdr-right">
    <div class="kv"><span class="kk">PBMD:</span><span class="vv">{fmt(w.get("pb_md"))} ft</span></div>
    <div class="kv"><span class="kk">Btm TMD:</span><span class="vv">{fmt(w.get("td_md"))} ft</span></div>
    <div class="kv"><span class="kk">BTM TVD:</span><span class="vv">{fmt(w.get("td_tvd"))} ft</span></div>
    <div class="kv"><span class="kk">BH Coord X:</span><span class="vv">{coord_x}</span></div>
    <div class="kv"><span class="kk">BH Coord Y:</span><span class="vv">{coord_y}</span></div>
    <div class="kv"><span class="kk">Ground Level:</span><span class="vv">{fmt(w.get("ground_elev") or (w.get("kb",0) or 0),2)} ft</span></div>
    <div class="kv"><span class="kk">Kelly Bushing:</span><span class="vv">{fmt(kb_elev,2)} ft</span></div>

  </div>
</div>
<div class="accent"></div>
<div class="body">
  <div class="cl">{left_col()}</div>
  <div class="cm">
    <div class="sch-hdr">MD SCALE: 0–{fmt(w.get("td_md"))}FT</div>
    {SVG}
  </div>
  <div class="cr">{right_col()}</div>
</div>
<div class="foot">
  <span>WELL: {w.get("well_name","")} &nbsp;&middot;&nbsp; API-12: {api12}</span>
  <span>SOURCE: {SCHEMA}/EDMADMIN &nbsp;&middot;&nbsp; {w.get("generated","")}</span>
  <span>CALIFORNIA RESOURCES ELK HILLS, LLC</span>
</div>
</body></html>"""


# ───────────────────────────────────────────────────────────────
# GUI APPLICATION
# ───────────────────────────────────────────────────────────────
class WBDApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CRC Wellbore Diagram Generator")
        self.root.geometry("900x700")
        self.root.resizable(True, True)
        self.root.configure(bg=NAVY)
        self.conn = None
        self.is_connected = False
        self.is_running = False
        self.generated = []
        self._build_ui()
        self.root.after(600, self._auto_connect)

    def _build_ui(self):
        root = self.root
        # Header
        hdr = tk.Frame(root, bg=NAVY, height=70)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)
        title_f = tk.Frame(hdr, bg=NAVY)
        title_f.pack(side=tk.LEFT, padx=16, pady=10)
        tk.Label(title_f, text="CRC", font=("Arial Black",20,"bold"), fg=GOLD, bg=NAVY).pack(side=tk.LEFT, padx=(0,4))
        tk.Label(title_f, text="Wellbore Diagram Generator", font=("Arial",14), fg=WHITE, bg=NAVY).pack(side=tk.LEFT)
        right_hdr = tk.Frame(hdr, bg=NAVY)
        right_hdr.pack(side=tk.RIGHT, padx=16, pady=10)
        self.conn_badge = tk.Label(right_hdr, text="⟳  CONNECTING...", font=("Arial",8,"bold"), fg="#f0c040", bg=NAVY)
        self.conn_badge.pack(side=tk.TOP, anchor="e")
        self.conn_detail = tk.Label(right_hdr, text="Reading Windows Credential Manager...", font=("Courier New",7), fg="#6080a0", bg=NAVY)
        self.conn_detail.pack(side=tk.TOP, anchor="e")
        tk.Frame(root, bg=GOLD, height=3).pack(fill=tk.X)
        body = tk.Frame(root, bg=LGRAY)
        body.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(body, bg=LGRAY, width=310)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        right = tk.Frame(body, bg=WHITE)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_left(left)
        self._build_right(right)
        statusbar = tk.Frame(root, bg="#0a1428", height=26)
        statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        statusbar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Starting up — reading Windows credentials...")
        tk.Label(statusbar, textvariable=self.status_var, font=("Courier New",8), fg=GOLD, bg="#0a1428", anchor="w").pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        self.progress = ttk.Progressbar(statusbar, length=140, mode="indeterminate")
        self.progress.pack(side=tk.RIGHT, padx=10, pady=5)

    def _build_left(self, parent):
        # Connection info
        db_frame = tk.LabelFrame(parent, text=" EDM Connection ", font=("Arial",9,"bold"), fg=NAVY, bg=LGRAY, bd=1, relief=tk.GROOVE)
        db_frame.pack(fill=tk.X, padx=10, pady=(10,4))
        info = [("Server", "bkx9dbadm01 (10.20.240.102)"),("Port","1521"),("Database","ODW / ODW1"),("Schema",SCHEMA),("Auth","Windows Credential Manager")]
        for label,val in info:
            row = tk.Frame(db_frame, bg=LGRAY)
            row.pack(fill=tk.X, padx=6, pady=1)
            tk.Label(row, text=f"{label}:", font=("Arial",8), fg="#666", bg=LGRAY, width=9, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=val, font=("Courier New",8), fg=NAVY, bg=LGRAY, anchor="w").pack(side=tk.LEFT)
        self.btn_reconnect = tk.Button(db_frame, text="⟳  Reconnect", font=("Arial",8,"bold"), bg="#c8c4ba", fg=DGRAY, relief=tk.FLAT, cursor="hand2", pady=3, command=self._do_connect_thread)
        self.btn_reconnect.pack(fill=tk.X, padx=6, pady=(4,6))

        # Output folder
        out_frame = tk.LabelFrame(parent, text=" Output Folder ", font=("Arial",9,"bold"), fg=NAVY, bg=LGRAY, bd=1, relief=tk.GROOVE)
        out_frame.pack(fill=tk.X, padx=10, pady=4)
        self.out_var = tk.StringVar(value=str(OUTPUT_DIR))
        row = tk.Frame(out_frame, bg=LGRAY)
        row.pack(fill=tk.X, padx=6, pady=4)
        tk.Entry(row, textvariable=self.out_var, font=("Courier New",7), relief=tk.SOLID, bd=1).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(row, text="📁", font=("Arial",10), bg=LGRAY, relief=tk.FLAT, cursor="hand2", command=self._browse_folder).pack(side=tk.LEFT, padx=(3,0))

        # Well selection
        well_frame = tk.LabelFrame(parent, text=" Well Selection ", font=("Arial",9,"bold"), fg=NAVY, bg=LGRAY, bd=1, relief=tk.GROOVE)
        well_frame.pack(fill=tk.X, padx=10, pady=4)
        tab_row = tk.Frame(well_frame, bg=LGRAY)
        tab_row.pack(fill=tk.X, padx=6, pady=(4,0))
        self.mode_var = tk.StringVar(value="single")
        self.tab_single = tk.Button(tab_row, text="Single Well", font=("Arial",8,"bold"), bg=NAVY, fg=WHITE, relief=tk.FLAT, padx=10, pady=3, cursor="hand2", command=lambda: self._set_mode("single"))
        self.tab_single.pack(side=tk.LEFT)
        self.tab_batch = tk.Button(tab_row, text="Batch / CSV", font=("Arial",8,"bold"), bg="#c8c4ba", fg=NAVY, relief=tk.FLAT, padx=10, pady=3, cursor="hand2", command=lambda: self._set_mode("batch"))
        self.tab_batch.pack(side=tk.LEFT, padx=(2,0))
        self.single_panel = tk.Frame(well_frame, bg=LGRAY)
        self.single_panel.pack(fill=tk.X, padx=6, pady=6)
        tk.Label(self.single_panel, text="API Number or Well Name:", font=("Arial",8), fg=DGRAY, bg=LGRAY, anchor="w").pack(fill=tk.X)
        self.well_var = tk.StringVar()
        well_entry = tk.Entry(self.single_panel, textvariable=self.well_var, font=("Courier New",12,"bold"), relief=tk.SOLID, bd=1, fg=NAVY)
        well_entry.pack(fill=tk.X, pady=(2,0))
        well_entry.bind("<Return>", lambda e: self._generate())
        self.batch_panel = tk.Frame(well_frame, bg=LGRAY)
        tk.Label(self.batch_panel, text="Well Names / APIs (one per line):", font=("Arial",8), fg=DGRAY, bg=LGRAY, anchor="w").pack(fill=tk.X)
        self.batch_text = tk.Text(self.batch_panel, height=5, font=("Courier New",8), relief=tk.SOLID, bd=1, wrap=tk.NONE)
        self.batch_text.pack(fill=tk.X, pady=(2,2))
        csv_row = tk.Frame(self.batch_panel, bg=LGRAY)
        csv_row.pack(fill=tk.X)
        tk.Button(csv_row, text="Load CSV", font=("Arial",8), bg=LGRAY, relief=tk.GROOVE, cursor="hand2", command=self._load_csv).pack(side=tk.LEFT)
        self.csv_lbl = tk.Label(csv_row, text="", font=("Arial",8), fg="#666", bg=LGRAY)
        self.csv_lbl.pack(side=tk.LEFT, padx=6)
        self.btn_generate = tk.Button(well_frame, text="⚡  Generate Diagram", font=("Arial",12,"bold"), bg=GOLD, fg=WHITE, relief=tk.FLAT, pady=10, cursor="hand2", command=self._generate)
        self.btn_generate.pack(fill=tk.X, padx=6, pady=(6,8))

        # Generated files
        files_frame = tk.LabelFrame(parent, text=" Generated Files ", font=("Arial",9,"bold"), fg=NAVY, bg=LGRAY, bd=1, relief=tk.GROOVE)
        files_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self.files_list = tk.Listbox(files_frame, font=("Courier New",7), bg=WHITE, fg=DGRAY, selectbackground=NAVY, selectforeground=WHITE, relief=tk.FLAT, bd=0)
        self.files_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.files_list.bind("<Double-Button-1>", self._open_selected)
        btn_row = tk.Frame(files_frame, bg=LGRAY)
        btn_row.pack(fill=tk.X, padx=4, pady=(0,4))
        tk.Button(btn_row, text="Open in Browser", font=("Arial",8), bg=NAVY, fg=WHITE, relief=tk.FLAT, cursor="hand2", command=self._open_selected).pack(side=tk.LEFT)
        tk.Button(btn_row, text="Open Folder", font=("Arial",8), bg=LGRAY, relief=tk.GROOVE, cursor="hand2", command=self._open_folder).pack(side=tk.LEFT, padx=4)

    def _build_right(self, parent):
        hdr = tk.Frame(parent, bg=NAVY, height=30)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Activity Log", font=("Arial",9,"bold"), fg=GOLD, bg=NAVY).pack(side=tk.LEFT, padx=10, pady=6)
        tk.Button(hdr, text="Clear", font=("Arial",8), fg=WHITE, bg=NAVY, relief=tk.FLAT, cursor="hand2", command=self._clear_log).pack(side=tk.RIGHT, padx=10)
        self.log_text = scrolledtext.ScrolledText(parent, font=("Courier New",8), bg=DBLUE, fg="#a8c8e8", relief=tk.FLAT, bd=0, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_config("info",  foreground="#a8c8e8")
        self.log_text.tag_config("ok",    foreground="#50d080")
        self.log_text.tag_config("warn",  foreground="#f0c040")
        self.log_text.tag_config("error", foreground="#ff6060")
        self.log_text.tag_config("head",  foreground=GOLD, font=("Courier New",8,"bold"))
        self.log("━"*52, "head")
        self.log("  CRC Wellbore Diagram Generator", "head")
        self.log("  Elk Hills Asset Development Team", "head")
        self.log("━"*52, "head")
        self.log(f"  Server  : bkx9dbadm01 (10.20.240.102)", "info")
        self.log(f"  Database: ODW / ODW1", "info")
        self.log(f"  Schema  : {SCHEMA}", "info")
        self.log(f"  Auth    : Windows Credential Manager (ODW + MCP_USER@ODW)", "info")
        self.log(f"  Client  : {ORACLE_CLIENT}", "info")
        self.log(f"  Output  : {OUTPUT_DIR}", "info")
        self.log("━"*52, "head")
        self.log("  Auto-connecting using your Windows credentials...", "warn")
        self.log("", "info")

    def log(self, msg, tag="info"):
        def _do():
            self.log_text.configure(state=tk.NORMAL)
            ts = datetime.now().strftime("%H:%M:%S")
            prefix = f"[{ts}] " if tag != "head" else "  "
            self.log_text.insert(tk.END, f"{prefix}{msg}\n", tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.root.after(0, _do)

    def set_status(self, msg):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_mode(self, mode):
        self.mode_var.set(mode)
        if mode == "single":
            self.tab_single.configure(bg=NAVY, fg=WHITE)
            self.tab_batch.configure(bg="#c8c4ba", fg=NAVY)
            self.batch_panel.pack_forget()
            self.single_panel.pack(fill=tk.X, padx=6, pady=6)
        else:
            self.tab_batch.configure(bg=NAVY, fg=WHITE)
            self.tab_single.configure(bg="#c8c4ba", fg=NAVY)
            self.single_panel.pack_forget()
            self.batch_panel.pack(fill=tk.X, padx=6, pady=6)

    def _browse_folder(self):
        f = filedialog.askdirectory(title="Select Output Folder")
        if f: self.out_var.set(f)

    def _load_csv(self):
        path = filedialog.askopenfilename(title="Select CSV", filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path: return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            wells = [r[0].strip() for r in rows if r and r[0].strip() and not r[0].strip().lower().startswith(("api","well","name","#"))]
            self.batch_text.delete("1.0", tk.END)
            self.batch_text.insert(tk.END, "\n".join(wells))
            self.csv_lbl.configure(text=f"({len(wells)} wells)")
            self.log(f"Loaded {len(wells)} wells from {Path(path).name}", "ok")
        except Exception as e:
            self.log(f"CSV error: {e}", "error")

    def _auto_connect(self):
        creds = get_credentials()
        found = [c for c in creds if c[2] != "fallback"]
        if found:
            self.log(f"  Found {len(found)} credential(s) in Windows Credential Manager:", "ok")
            for u,_,src in found:
                self.log(f"    → {src} (user: {u})", "info")
        else:
            self.log("  No keyring credentials found — using fallback credentials", "warn")
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect_thread(self):
        if self.is_running: return
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect(self):
        self.root.after(0, lambda: self.progress.start(10))
        self.root.after(0, lambda: self.conn_badge.configure(text="⟳  CONNECTING...", fg="#f0c040"))
        self.root.after(0, lambda: self.btn_reconnect.configure(state=tk.DISABLED, text="Connecting..."))
        if self.conn:
            try: self.conn.close()
            except: pass
            self.conn = None
        try:
            conn, desc = connect()
            self.conn = conn
            self.is_connected = True
            self.root.after(0, lambda: self.conn_badge.configure(text="● CONNECTED", fg="#50d080"))
            self.root.after(0, lambda: self.conn_detail.configure(text=desc[:50]))
            self.root.after(0, lambda: self.btn_reconnect.configure(state=tk.NORMAL, text="⟳  Reconnect", bg="#c8c4ba", fg=DGRAY))
            self.log(f"✓ Connected: {desc}", "ok")
            self.log("  Ready — type a well name or API number and click Generate!", "ok")
            self.set_status(f"Connected — {desc}")
        except Exception as e:
            self.is_connected = False
            self.root.after(0, lambda: self.conn_badge.configure(text="✗  FAILED", fg="#ff6060"))
            self.root.after(0, lambda: self.conn_detail.configure(text="Check network / credentials"))
            self.root.after(0, lambda: self.btn_reconnect.configure(state=tk.NORMAL, text="⟳  Retry", bg="#c83030", fg=WHITE))
            self.log(f"✗ Connection failed: {e}", "error")
            self.set_status("Connection failed — check CRC network")
        finally:
            self.root.after(0, lambda: self.progress.stop())

    def _generate(self):
        if self.is_running: return
        if not self.is_connected or not self.conn:
            messagebox.showwarning("Not Connected", "Please connect to EDM first.\nMake sure you are on the CRC network.")
            return
        if self.mode_var.get() == "single":
            ident = self.well_var.get().strip()
            if not ident:
                messagebox.showwarning("No Well", "Enter a well name or API number.")
                return
            identifiers = [ident]
        else:
            raw = self.batch_text.get("1.0", tk.END).strip()
            if not raw:
                messagebox.showwarning("No Wells", "Enter well names or load a CSV.")
                return
            identifiers = list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")))
        out_dir = Path(self.out_var.get())
        threading.Thread(target=self._do_generate, args=(identifiers, out_dir), daemon=True).start()

    def _do_generate(self, identifiers, out_dir):
        self.is_running = True
        self.root.after(0, lambda: self.btn_generate.configure(state=tk.DISABLED, text="Generating...", bg="#888"))
        self.root.after(0, lambda: self.progress.start(8))
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"Cannot create output folder: {e}", "error")
            self._done_generating(); return
        self.log("", "info")
        self.log(f"━━━ Generating {len(identifiers)} well(s) ━━━", "head")
        self.log(f"Output: {out_dir}", "info")
        ok = 0; fail = 0; t_start = time.time()
        for i, ident in enumerate(identifiers, 1):
            self.log(f"[{i}/{len(identifiers)}] {ident}", "head")
            self.set_status(f"Processing [{i}/{len(identifiers)}]: {ident}")
            t0 = time.time()
            try:
                w = collect(self.conn, ident)
                self.log(f"  → {w['well_name']} | {w['well_id']} | KB={w['kb']:.1f}ft | Status: {w['status']}", "info")
                self.log(f"  → {len(w['casings'])} casings · {w['total_comps']} comps · {len(w['cement'])} cement jobs · {len(w['formations'])} formations", "info")
                if w.get("pa_data"):
                    pa = w["pa_data"]
                    self.log(f"  → P&A: {len(pa['cement_plugs'])} plugs · {len(pa['daily_reports'])} daily reports", "ok")
                html = build_html(w)
                safe = w["well_name"].replace("/","_").replace(" ","_").replace("\\","_")
                html_path = out_dir / f"{safe}_CRC_Wellbore_Schematic.html"
                html_path.write_text(html, encoding="utf-8")
                elapsed = round(time.time()-t0, 1)
                self.log(f"  ✓ Saved: {html_path.name}  ({elapsed}s)", "ok")
                def _add(p=html_path):
                    self.files_list.insert(0, p.name)
                    self.generated.insert(0, str(p))
                self.root.after(0, _add)
                ok += 1
            except Exception as e:
                self.log(f"  ✗ Failed: {e}", "error")
                fail += 1
        total = round(time.time()-t_start, 0)
        self.log("", "info")
        self.log("━━━ Complete! ━━━", "head")
        self.log(f"  Generated: {ok}  |  Failed: {fail}  |  Time: {total:.0f}s", "ok" if fail==0 else "warn")
        self.log("  Open HTML in Chrome → Ctrl+P → Save as PDF", "info")
        self.set_status(f"Done! {ok} diagram(s) saved to {out_dir}")
        self._done_generating()

    def _done_generating(self):
        self.is_running = False
        self.root.after(0, lambda: self.btn_generate.configure(state=tk.NORMAL, text="⚡  Generate Diagram", bg=GOLD))
        self.root.after(0, lambda: self.progress.stop())

    def _open_selected(self, event=None):
        sel = self.files_list.curselection()
        path = self.generated[sel[0]] if sel else (self.generated[0] if self.generated else None)
        if not path: return
        try:
            webbrowser.open(f"file:///{Path(path).resolve()}")
            self.log(f"Opened: {Path(path).name}", "ok")
        except Exception as e:
            self.log(f"Could not open: {e}", "error")

    def _open_folder(self):
        folder = self.out_var.get()
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["open" if sys.platform=="darwin" else "xdg-open", folder])
        except Exception as e:
            self.log(f"Could not open folder: {e}", "error")


# ───────────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TProgressbar", troughcolor=NAVY, background=GOLD, thickness=5)
    WBDApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()