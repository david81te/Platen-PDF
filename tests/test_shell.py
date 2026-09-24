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
check("no delivery when nothing is listening",
      inst.deliver(os.path.join(OUT, "fixture.pdf")), False)

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

print("\n" + "=" * 60)
print("No issues found." if not findings else "%d issue(s): %s" % (len(findings), findings))
