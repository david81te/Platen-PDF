"""Paint the window caption to match the app instead of the Windows theme.

The page is dark, but the bar across the top belongs to Windows, and pywebview
hardwires it to the system setting: on a PC set to Light mode it draws a white
caption above a dark application. Nothing in pywebview's public API overrides
that, so we do two things - persuade its own theme check to say "dark", which
gets the caption right from the first paint, and then set the exact colours
ourselves so the bar matches the menu bar underneath rather than merely being
a generic dark grey.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# DWM window attributes. 20 works from Windows 10 2004; the colours need
# Windows 11 (build 22000+) and fail harmlessly below that, leaving the
# standard dark caption from attribute 20.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _colorref(hex_rgb: str) -> int:
    """#rrggbb -> the 0x00bbggrr integer DWM wants."""
    value = int(hex_rgb.lstrip("#"), 16)
    red, green, blue = (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
    return (blue << 16) | (green << 8) | red


# Straight from ui/styles.css: --panel, --ink and --line.
CAPTION = _colorref("#242a35")
TEXT = _colorref("#e8ecf4")
BORDER = _colorref("#394152")


def _our_windows() -> list[int]:
    """Top-level windows owned by this process.

    Asking pywebview for its native handle would mean reaching into a private
    class whose shape changes between releases; enumerating is stable.
    """
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


def _set(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_int(value)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), ctypes.c_uint(attribute),
        ctypes.byref(data), ctypes.sizeof(data))
    return result == 0


def paint(hwnd: int | None = None) -> bool:
    """Darken one window's caption, or every window this process owns."""
    targets = [hwnd] if hwnd else _our_windows()
    painted = False
    for target in targets:
        # Attribute 20 is the one that matters; the rest are decoration.
        if _set(target, DWMWA_USE_IMMERSIVE_DARK_MODE, 1):
            painted = True
        _set(target, DWMWA_CAPTION_COLOR, CAPTION)
        _set(target, DWMWA_TEXT_COLOR, TEXT)
        _set(target, DWMWA_BORDER_COLOR, BORDER)
    return painted


def force_dark_detection() -> bool:
    """Make pywebview's own light/dark check always answer "dark".

    It runs this while building the form, before anything is on screen, so the
    caption is dark from the first frame with no white flash - and it runs it
    again whenever Windows reports a theme change, which would otherwise undo
    what paint() did. Guarded because it is someone else's private class.
    """
    try:
        from webview.platforms import winforms
        winforms.BrowserView.BrowserForm.is_dark_theme = lambda self: True
        return True
    except Exception:
        return False


def attach(window) -> None:
    """Keep the caption dark for the life of this window."""
    force_dark_detection()
    window.events.shown += lambda: paint()
    # Windows repaints the frame on a restore, and a maximise swaps in the
    # frameless caption, so re-assert the colours after both.
    window.events.restored += lambda: paint()
    window.events.maximized += lambda: paint()
