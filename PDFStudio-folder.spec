# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("ui", "ui"), ("assets/pdfstudio.ico", "assets")]
binaries = []
hiddenimports = ["win32com.client", "pythoncom", "pywintypes"]

# pywebview needs its JS shim; the document libraries ship templates and data.
# rapidocr_onnxruntime carries the OCR models as package data, so collecting it
# is what makes OCR work with nothing for the user to install.
for package in ("webview", "pymupdf", "pdf2docx", "docx", "pptx", "openpyxl",
                "fitz", "PIL", "pikepdf", "fontTools",
                "rapidocr_onnxruntime", "onnxruntime", "shapely", "pyclipper"):
    try:
        extra_datas, extra_binaries, extra_hidden = collect_all(package)
    except Exception:
        continue
    datas += extra_datas
    binaries += extra_binaries
    hiddenimports += extra_hidden

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "PyQt5", "PySide2",
              "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PDFStudio",
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
    icon="assets/pdfstudio.ico",
    version="assets/version_info.txt",
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PDFStudio",
)
