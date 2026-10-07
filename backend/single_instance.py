"""Hand a file to an already-running copy instead of starting another one.

Once the app is the default PDF handler, opening four documents from Explorer
would otherwise start four separate processes, each loading its own copy of the
PDF engine and OCR models. Instead the first instance listens on a named pipe
and later launches deliver their path to it and exit, so the file arrives as a
new tab in the window that is already open.

A named pipe is used rather than a localhost socket because Windows gives it a
default ACL scoped to the current user, so nothing else on the machine can push
paths into the app.
"""
from __future__ import annotations

import getpass
import os
import threading

PIPE_NAME = r"\\.\pipe\PlatenPDF-%s" % (getpass.getuser() or "user")
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PIPE_BUSY = 231


def available() -> bool:
    try:
        import win32pipe  # noqa: F401
        return True
    except ImportError:
        return False


def deliver(path: str, timeout_ms: int = 700) -> bool:
    """Send `path` to a running instance. False means none was listening."""
    if not available():
        return False
    import pywintypes
    import win32file

    try:
        handle = win32file.CreateFile(
            PIPE_NAME, win32file.GENERIC_WRITE, 0, None,
            win32file.OPEN_EXISTING, 0, None)
    except pywintypes.error as exc:
        if exc.winerror in (_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY):
            return False
        return False
    try:
        # We own the foreground right now - Explorer just started us - so this
        # is the moment we are allowed to hand that right to the copy already
        # running. Without it the window it opens stays behind whatever the
        # person was looking at.
        from . import foreground
        foreground.allow_any_process()
        win32file.WriteFile(handle, os.path.abspath(path).encode("utf-8"))
        return True
    except Exception:
        return False
    finally:
        try:
            win32file.CloseHandle(handle)
        except Exception:
            pass


def serve(on_open) -> threading.Thread | None:
    """Listen for paths from later launches and pass each to `on_open`."""
    if not available():
        return None
    import win32pipe
    import win32file

    def loop():
        while True:
            try:
                pipe = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_INBOUND,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    4096, 4096, 0, None)
            except Exception:
                return                      # another instance owns the pipe
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
                _, data = win32file.ReadFile(pipe, 4096)
                path = data.decode("utf-8", "replace").strip()
                if path and os.path.isfile(path):
                    try:
                        on_open(path)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                try:
                    win32file.CloseHandle(pipe)
                except Exception:
                    pass

    thread = threading.Thread(target=loop, daemon=True, name="platenpdf-ipc")
    thread.start()
    return thread
