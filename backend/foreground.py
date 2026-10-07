"""Bringing the window to the front when a document arrives.

Windows deliberately makes this hard. A process cannot simply raise itself
over whatever the person is working in, or every background program would
fight for attention. The rule is that only a process which owns the current
foreground window, or one that has been given permission by such a process,
may call SetForegroundWindow and have it work.

That is exactly the situation here, and it is legitimate: someone has just
double-clicked a PDF, so a window appearing is what they asked for. The second
launch - the one Explorer started, which does own the foreground at that
moment - calls AllowSetForegroundWindow before handing the path over, which
grants the already-running copy the right to raise itself.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

ASFW_ANY = -1
SW_RESTORE = 9
SW_SHOW = 5


def allow_any_process() -> bool:
    """Let whoever we are about to talk to raise itself.

    Called by the launch that is about to exit, while it still owns the
    foreground and so still has the right to give it away.
    """
    try:
        return bool(ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY))
    except Exception:
        return False


def _our_windows() -> list[int]:
    user32 = ctypes.windll.user32
    found: list[int] = []
    mine = ctypes.windll.kernel32.GetCurrentProcessId()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == mine and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def bring_to_front(hwnd: int | None = None) -> bool:
    """Raise our window and give it the keyboard. False if Windows refused."""
    user32 = ctypes.windll.user32
    targets = [hwnd] if hwnd else _our_windows()
    if not targets:
        return False
    target = targets[0]

    # A minimised window cannot take focus; restore it first, and restore
    # rather than show so it returns to the size the person left it.
    if user32.IsIconic(target):
        user32.ShowWindow(target, SW_RESTORE)
    else:
        user32.ShowWindow(target, SW_SHOW)

    if user32.SetForegroundWindow(target):
        return True

    # Refused. The documented way through is to attach our input queue to the
    # thread that currently owns the foreground, which makes us a peer for the
    # purposes of the rule above, then detach again immediately.
    try:
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        their_thread = user32.GetWindowThreadProcessId(foreground, None)
        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        if their_thread == our_thread:
            return False
        if not user32.AttachThreadInput(their_thread, our_thread, True):
            return False
        try:
            user32.BringWindowToTop(target)
            raised = bool(user32.SetForegroundWindow(target))
        finally:
            user32.AttachThreadInput(their_thread, our_thread, False)
        return raised
    except Exception:
        return False
