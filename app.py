"""Platen PDF - a standalone PDF editor."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time

import webview

from backend import (foreground, shell_integration, single_instance, titlebar,
                     version)
from backend.api import Api

APP_NAME = version.APP_NAME


def _base_dir() -> str:
    # PyInstaller unpacks bundled data into _MEIPASS at runtime.
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def selftest() -> int:
    """Headless check that a packaged build has everything it needs.

    Mainly proves the OCR models resolved inside the frozen bundle, which is
    the part most likely to break when PyInstaller relocates package data.
    """
    import pymupdf as fitz
    from backend import convert

    print("base dir:", _base_dir())
    print("environment:", shell_integration.environment())
    print("ui present:", os.path.isfile(os.path.join(_base_dir(), "ui", "index.html")))
    status = convert.ocr_status()
    print("ocr status:", status)
    if not status.get("available"):
        print("SELFTEST: FAIL (no OCR engine)")
        return 1

    doc = fitz.open()
    page = doc.new_page(width=420, height=160)
    page.insert_text(fitz.Point(30, 90), "Willmore Capital 2026", fontsize=28)
    flat = fitz.open()
    image = flat.new_page(width=420, height=160)
    image.insert_image(image.rect, pixmap=page.get_pixmap(matrix=fitz.Matrix(3, 3)))
    print("text before ocr:", repr(flat[0].get_text().strip()))

    convert.ocr(flat)
    found = flat[0].get_text()
    print("text after ocr :", repr(found.strip()))
    ok = "Willmore" in found
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def smoketest() -> int:
    """Every feature that leans on a bundled library, run inside the build.

    The test suites run from source, so a build that leaves out something a
    feature needs - which is what trimming the download risks - passes all of
    them and fails only for the person using it. This runs each of those
    features in the packaged program and reads back what it made.
    """
    import tempfile
    import zipfile

    import pymupdf as fitz
    from backend import convert, decorate, textedit

    out = tempfile.mkdtemp(prefix="platen-smoke-")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Quarterly summary for Willmore Capital", fontsize=14)
    for row, cells in enumerate((("Item", "Amount", "Due"), ("Deposit", "$100,000", "Jan 15"),
                                 ("Balance", "$4,150,000", "Mar 31"))):
        for col, text in enumerate(cells):
            page.insert_text((72 + 160 * col, 140 + 20 * row), text, fontsize=11)
    for x in (66, 226, 386, 546):
        page.draw_line((x, 124), (x, 186))
    for y in (124, 146, 166, 186):
        page.draw_line((66, y), (546, y))
    arial = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
    if os.path.isfile(arial):                 # an embedded TrueType font, for fontTools
        page.insert_font(fontname="F0", fontfile=arial)
        page.insert_text((72, 240), "Embedded font line to edit", fontname="F0", fontsize=12)
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")

    def zipped(path, member, needle):
        with zipfile.ZipFile(path) as bundle:
            names = [n for n in bundle.namelist() if n.startswith(member)]
            return any(needle.encode() in bundle.read(n) for n in names)

    checks = []

    def run(label, action):
        try:
            ok = bool(action())
            checks.append(ok)
            print("  %-34s %s" % (label, "ok" if ok else "FAILED"))
        except Exception as exc:                             # noqa: BLE001
            checks.append(False)
            print("  %-34s FAILED %s: %s" % (label, type(exc).__name__, exc))

    target = lambda name: os.path.join(out, name)                     # noqa: E731
    run("Word (pdf2docx, opencv, lxml)", lambda: convert.to_docx(doc, target("a.docx")) is not None
        and zipped(target("a.docx"), "word/document.xml", "Willmore"))
    run("Excel (openpyxl)", lambda: convert.to_xlsx(doc, target("a.xlsx")) is not None
        and zipped(target("a.xlsx"), "xl/", "Deposit"))
    run("PowerPoint (python-pptx)", lambda: convert.to_pptx(doc, target("a.pptx")) is not None
        and zipped(target("a.pptx"), "ppt/slides/", "Willmore"))
    run("images (Pillow)", lambda: convert.to_images(doc, out, 72, "png")
        and any(n.endswith(".png") for n in os.listdir(out)))
    run("plain text", lambda: convert.to_text(doc, target("a.txt")) is not None
        and "Willmore" in open(target("a.txt"), encoding="utf-8").read())
    run("compress (pikepdf)", lambda: convert.compress(doc, target("small.pdf"), "high")
        and "Willmore" in fitz.open(target("small.pdf"))[0].get_text())

    def edit():
        copy = fitz.open(stream=doc.tobytes(), filetype="pdf")
        # One word: Arial's spaces read back as no-break spaces.
        span = next(s for b in textedit.layout(copy[0])["blocks"] for l in b["lines"]
                    for s in l["spans"] if "Embedded" in s["text"])
        textedit.edit_span(copy, span["id"], "Embedded font line, edited")
        return "edited" in fitz.open(stream=copy.tobytes(), filetype="pdf")[0].get_text()
    run("edit text (fontTools)", edit)

    def stamp():
        copy = fitz.open(stream=doc.tobytes(), filetype="pdf")
        decorate.stamp(copy, "numbers", {"bottom-center": "Page {page} of {pages}"})
        return "Page 1 of 1" in copy[0].get_text()
    run("page numbers", stamp)

    def recognise():
        flat = fitz.open()
        image = flat.new_page(width=420, height=160)
        image.insert_image(image.rect, pixmap=doc[0].get_pixmap(
            matrix=fitz.Matrix(3, 3), clip=fitz.Rect(60, 60, 480, 100)))
        convert.ocr(flat)
        return "Willmore" in flat[0].get_text()
    run("OCR (onnxruntime, opencv)", recognise)

    def secure():
        import ssl
        import truststore
        return ssl.SSLContext is truststore.SSLContext
    run("HTTPS through Windows", secure)

    ok = all(checks)
    print("SMOKETEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> None:
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--smoketest" in sys.argv:
        raise SystemExit(smoketest())

    if "--register" in sys.argv:
        try:
            state = shell_integration.register()
        except RuntimeError as exc:
            print(exc)
            raise SystemExit(1)
        # --quiet is how an update re-registers: someone who already chose
        # their PDF app does not want Settings thrown at them every version.
        if "--quiet" not in sys.argv:
            print("Registered. Windows still needs you to confirm the choice:")
            print("  Settings > Apps > Default apps > Platen PDF")
            print("  current .pdf handler:", state["current_handler"])
            shell_integration.open_default_apps_settings()
        raise SystemExit(0)

    if "--unregister" in sys.argv:
        shell_integration.unregister()
        print("Removed Platen PDF from the Windows file associations.")
        raise SystemExit(0)

    missing = shell_integration.webview2_version() is None
    if missing:
        message = (
            "Platen PDF needs the Microsoft Edge WebView2 runtime, which is "
            "not installed on this PC.\n\n"
            "It is free from Microsoft and installs in under a minute:\n"
            + shell_integration.WEBVIEW2_DOWNLOAD +
            "\n\nDownload the Evergreen Standalone Installer, run it, "
            "then start Platen PDF again.")
        try:
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
        except Exception:
            print(message)
        raise SystemExit(1)

    startup = [a for a in sys.argv[1:] if os.path.isfile(a)]

    # Hand the file to a copy that is already running rather than starting a
    # second one; this is what keeps Explorer from spawning a process per file.
    if startup and single_instance.deliver(startup[0]):
        # The running copy listens for one file at a time and opens the pipe
        # again only once it has dealt with the last, so the rest wait a turn.
        for extra in startup[1:]:
            deadline = time.time() + 5
            while not single_instance.deliver(extra) and time.time() < deadline:
                time.sleep(0.1)
        raise SystemExit(0)

    api = Api()
    index = os.path.join(_base_dir(), "ui", "index.html")
    window = webview.create_window(
        APP_NAME,
        url=index,
        js_api=api,
        width=1480,
        height=940,
        min_size=(1060, 680),
        text_select=False,
    )
    api.attach_window(window)

    # Windows draws the caption bar itself, and pywebview ties it to the system
    # theme - so on a PC set to Light mode it sits white above the dark app.
    titlebar.attach(window)

    if startup:
        api._startup_paths = [os.path.abspath(path) for path in startup]

    MB_YESNOCANCEL = 0x03
    MB_ICONWARNING = 0x30
    ID_YES, ID_NO, ID_CANCEL = 6, 7, 2

    def confirm_close() -> bool:
        """Ask before discarding unsaved work. False keeps the window open."""
        try:
            pending = api.unsaved()
        except Exception:
            return True
        if not pending:
            return True
        names = chr(10).join("  - " + (item["name"] or "Untitled")
                             for item in pending)
        prompt = ("These documents have unsaved changes:" + chr(10) * 2 +
                  names + chr(10) * 2 + "Save before closing?")
        try:
            answer = ctypes.windll.user32.MessageBoxW(
                None, prompt, APP_NAME, MB_YESNOCANCEL | MB_ICONWARNING)
        except Exception:
            return True
        if answer == ID_CANCEL:
            return False
        if answer == ID_YES:
            result = api.save_all()
            if result["skipped"]:
                # They cancelled a Save As, so do not throw the work away.
                return False
        return True

    window.events.closing += confirm_close

    def handle_incoming(path: str) -> None:
        """A later launch asked us to open a file: show it, and come forward.

        Raised before the document loads, so the window is already in front
        while it opens rather than appearing after a pause by which time the
        person has looked somewhere else.
        """
        try:
            foreground.bring_to_front()
        except Exception:
            pass
        try:
            window.evaluate_js("window.openOnStart(%s)" % json.dumps(path))
        except Exception:
            pass

    single_instance.serve(handle_incoming)
    webview.start(debug=bool(os.environ.get("PDFSTUDIO_DEBUG")))


if __name__ == "__main__":
    main()
