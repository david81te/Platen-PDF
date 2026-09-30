"""File-association registration and the single-instance handoff."""
import io, os, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend import shell_integration as shell, single_instance as inst

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
findings = []

def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        findings.append((label, got))
    print("  %s  %-44s %r" % ("ok  " if ok else "BUG ", label, got))

print("== single instance handoff ==")
check("named pipes available", inst.available(), True)
busy = inst.deliver(os.path.join(OUT, "fixture.pdf"))
if busy:
    print("  NOTE  a copy of Platen PDF is already running and owns the pipe;")
    print("        close it before running this suite.")
    sys.exit(2)
check("no delivery when nothing is listening", busy, False)

received = []
inst.serve(received.append)
time.sleep(0.5)
target = os.path.join(OUT, "fixture.pdf")
check("delivers to a running instance", inst.deliver(target), True)
time.sleep(0.6)
check("listener received the path", len(received), 1)
check("path is absolute and correct",
      received and os.path.normcase(received[0]) == os.path.normcase(os.path.abspath(target)),
      True)

second = os.path.join(OUT, "hard.pdf")
inst.deliver(second)
time.sleep(0.6)
check("a second file also arrives", len(received), 2)

inst.deliver(os.path.join(OUT, "does_not_exist.pdf"))
time.sleep(0.5)
check("a missing file is ignored", len(received), 2)

print("\n== registration state ==")
state = shell.status()
check("reports whether it is packaged", "packaged" in state, True)
check("reports the current handler", "current_handler" in state, True)
check("is honest that Windows needs confirmation",
      state["needs_user_confirmation"], True)
def try_register_from_source():
    """The dev copy must refuse: it would point Windows at python.exe."""
    if state["packaged"]:
        return "refused"          # not applicable when frozen
    try:
        shell.register()
        return "allowed"
    except RuntimeError:
        return "refused"


check("registering from source is refused", try_register_from_source(), "refused")

print("")
print("== environment detection ==")
env = shell.environment()
check("detects the WebView2 runtime", env["webview2"], lambda v: v is None or "." in v)
check("detects Office or LibreOffice", isinstance(env["office"], bool), True)
check("reports the Windows build", env["windows"], lambda b: b is None or b > 0)

print("")
print("== behaviour on a PC without Office ==")
from backend import convert
from backend.session import PdfError

real_word, real_soffice = convert._word_export, convert._soffice_export
try:
    import pywintypes
    office_error = pywintypes.com_error(-2147221005, "Invalid class string", None, None)
except Exception:
    office_error = OSError("no office")


def no_office(paths, out_dir):
    raise office_error           # what Dispatch raises when Office is absent


def no_libreoffice(paths, out_dir):
    raise PdfError("Install Microsoft Office or LibreOffice to convert these files.")


convert._word_export, convert._soffice_export = no_office, no_libreoffice
try:
    convert.from_files([os.path.join(OUT, "fixture.docx")])
    outcome = "no error raised"
except PdfError as exc:
    outcome = "clear message" if "Office or LibreOffice" in str(exc) else "wrong: %s" % exc
except Exception as exc:
    outcome = "leaked %s" % type(exc).__name__
finally:
    convert._word_export, convert._soffice_export = real_word, real_soffice
check("a missing Office gives a readable error", outcome, "clear message")

convert._word_export = no_office
try:
    doc = convert.from_files([os.path.join(OUT, "fixture.pdf")])
    pdf_still_works = doc.page_count >= 1
except Exception:
    pdf_still_works = False
finally:
    convert._word_export = real_word
check("PDFs still open with no Office present", pdf_still_works, True)

print("\n" + "=" * 60)
print("No issues found." if not findings else "%d issue(s): %s" % (len(findings), findings))
