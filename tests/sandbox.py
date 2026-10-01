"""Keep the tests out of the real signature library.

Import this *before* anything from `backend`, because backend.signatures reads
the environment once at import time:

    import sandbox  # noqa: F401  (must precede the backend imports)
    from backend import signatures

Without it, every suite that exercises signatures adds, renames and deletes
entries in the library belonging to whoever is running the tests. The cleanup
at the end of each suite only runs on the normal exit path, so a watchdog
timeout or an exception in the driver thread leaves test litter behind in a
person's saved signatures - and a test that deletes by name rather than by id
can take a real signature with it.
"""
import os
import tempfile

DIR = os.path.join(tempfile.gettempdir(), "platenpdf-test-signatures")
os.environ["PLATENPDF_DATA_DIR"] = DIR
os.makedirs(DIR, exist_ok=True)
