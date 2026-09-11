# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

pymupdf_datas, pymupdf_binaries, pymupdf_hiddenimports = collect_all("pymupdf")

analysis = Analysis(
    ["wbd_marker_windows_gui.pyw"],
    pathex=[],
    binaries=pymupdf_binaries,
    datas=pymupdf_datas + [("app_icon.ico", ".")],
    hiddenimports=pymupdf_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["streamlit"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="WBD_Marker_QC_Repair",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app_icon.ico",
)
