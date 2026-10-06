"""Keep the tests out of the real signature library - and out of each other's.

Import this *before* anything from `backend`, because backend.signatures reads
the environment once at import time:

    import sandbox  # noqa: F401  (must precede the backend imports)
    from backend import signatures

Two separate problems are solved here.

Without it, every suite that exercises signatures adds, renames and deletes
entries in the library belonging to whoever is running the tests. The cleanup
at the end of a suite only runs on the normal exit path, so a watchdog timeout
or an exception in the driver thread leaves test litter behind in a person's
saved signatures - and a test that deletes by name rather than by id can take a
real signature with it.

The folder is also per-suite rather than shared. When every suite used one
folder, a suite that empties the library to simulate a fresh machine - which
test_sync and test_account_ui both do - pulled the ground out from under
whichever suite ran next. test_buttons failed exactly that way: it passed on
its own and failed in the full run, which is the most expensive kind of
failure to chase.
"""
import os
import sys
import tempfile

_script = os.path.basename(sys.argv[0] or "platenpdf")
_suite = os.path.splitext(_script)[0] or "platenpdf"

DIR = os.path.join(tempfile.gettempdir(), "platenpdf-tests", _suite)
os.environ["PLATENPDF_DATA_DIR"] = DIR
os.makedirs(DIR, exist_ok=True)
