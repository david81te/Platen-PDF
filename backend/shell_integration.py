"""Windows file-association registration.

Everything here writes under HKEY_CURRENT_USER, so it needs no administrator
rights and only affects the person running the app.

An important limit: since Windows 8 an application cannot make itself the
default handler. The choice lives in a UserChoice key that Windows protects
with a hash it verifies, and forging it is both fragile and user-hostile. What
an app *can* do is register itself as a candidate, so it appears in "Open with"
and in Settings > Default apps -- and then send the user to that screen to make
the choice themselves. That is what this module does.
"""
from __future__ import annotations

import ctypes
import os
import sys
import winreg

APP_NAME = "PDF Studio"
PROG_ID = "PDFStudio.Document"
CAPABILITY_KEY = r"Software\PDFStudio\Capabilities"
EXTENSIONS = (".pdf",)

SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000


def executable_path() -> str | None:
    """The path Windows should launch. Only meaningful for a packaged build."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return None


def _set(root, path: str, name: str | None, value: str) -> None:
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(root, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as key:
            count = winreg.QueryInfoKey(key)[0]
            children = [winreg.EnumKey(key, i) for i in range(count)]
    except FileNotFoundError:
        return
    for child in children:
        _delete_tree(root, path + "\\" + child)
    try:
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def _notify_shell() -> None:
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def status() -> dict:
    """Where the app stands: registered as a candidate, and is it the default."""
    exe = executable_path()
    registered = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Classes\%s\shell\open\command" % PROG_ID) as key:
            registered = bool(winreg.QueryValueEx(key, None)[0])
    except FileNotFoundError:
        registered = False

    current = None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
            r"\FileExts\.pdf\UserChoice") as key:
            current = winreg.QueryValueEx(key, "ProgId")[0]
    except FileNotFoundError:
        current = None

    return {
        "packaged": exe is not None,
        "exe": exe,
        "registered": registered,
        "current_handler": current,
        "is_default": current == PROG_ID,
        # Windows will not let an application set this for the user.
        "needs_user_confirmation": True,
    }


def register() -> dict:
    """Advertise the app as a .pdf handler for the current user."""
    exe = executable_path()
    if not exe:
        raise RuntimeError(
            "Run the built PDFStudio.exe to register it. Registering the "
            "development copy would point Windows at python.exe.")

    command = '"%s" "%%1"' % exe
    hkcu = winreg.HKEY_CURRENT_USER

    # The document type itself.
    _set(hkcu, r"Software\Classes\%s" % PROG_ID, None, "PDF Document")
    _set(hkcu, r"Software\Classes\%s\DefaultIcon" % PROG_ID, None, "%s,0" % exe)
    _set(hkcu, r"Software\Classes\%s\shell\open\command" % PROG_ID, None, command)

    # Offer it in the Open with list, without disturbing the current default.
    for extension in EXTENSIONS:
        with winreg.CreateKeyEx(
            hkcu, r"Software\Classes\%s\OpenWithProgids" % extension,
            0, winreg.KEY_WRITE,
        ) as key:
            winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_NONE, b"")

    # The executable's own Open with entry.
    base = os.path.basename(exe)
    _set(hkcu, r"Software\Classes\Applications\%s\shell\open\command" % base, None, command)
    _set(hkcu, r"Software\Classes\Applications\%s" % base,
         "FriendlyAppName", APP_NAME)
    for extension in EXTENSIONS:
        _set(hkcu, r"Software\Classes\Applications\%s\SupportedTypes" % base,
             extension, "")

    # Capabilities, which is what puts the app in Settings > Default apps.
    _set(hkcu, CAPABILITY_KEY, "ApplicationName", APP_NAME)
    _set(hkcu, CAPABILITY_KEY, "ApplicationDescription",
         "Edit, sign, convert and organise PDF files.")
    for extension in EXTENSIONS:
        _set(hkcu, CAPABILITY_KEY + r"\FileAssociations", extension, PROG_ID)
    _set(hkcu, r"Software\RegisteredApplications", APP_NAME, CAPABILITY_KEY)

    _notify_shell()
    return status()


def unregister() -> dict:
    hkcu = winreg.HKEY_CURRENT_USER
    exe = executable_path()
    _delete_tree(hkcu, r"Software\Classes\%s" % PROG_ID)
    _delete_tree(hkcu, r"Software\PDFStudio")
    if exe:
        _delete_tree(hkcu, r"Software\Classes\Applications\%s" % os.path.basename(exe))
    for extension in EXTENSIONS:
        try:
            with winreg.OpenKey(hkcu, r"Software\Classes\%s\OpenWithProgids" % extension,
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, PROG_ID)
        except (FileNotFoundError, OSError):
            pass
    try:
        with winreg.OpenKey(hkcu, r"Software\RegisteredApplications",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except (FileNotFoundError, OSError):
        pass
    _notify_shell()
    return status()


def open_default_apps_settings() -> bool:
    """Open the Windows screen where the user can confirm the choice."""
    targets = [
        "ms-settings:defaultapps?registeredAppUser=%s" % APP_NAME.replace(" ", "%20"),
        "ms-settings:defaultapps",
    ]
    for target in targets:
        try:
            os.startfile(target)
            return True
        except Exception:
            continue
    return False
