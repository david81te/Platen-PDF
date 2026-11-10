"""What the build leaves out of the download. Read by both spec files.

The specs collect whole packages, because several load parts of themselves
by name at runtime and a build that misses one breaks only for the person
using it. Collecting whole packages also brings everything else they ship:
OpenCV's video reader, compilers' headers, onnxruntime's model-conversion
tools, the build tools themselves. None of it runs, and it was a fifth of the
download. Each entry here is something no feature reaches; `--smoketest`
runs every feature inside the build to keep it that way.
"""
import glob
import importlib.util
import os
import re

# Packages, and parts of packages, that nothing in the program imports.
MODULES = (
    "setuptools", "pkg_resources", "PyInstaller", "_pyinstaller_hooks_contrib",
    # Only the form-filling test builds PDFs with it; the program never imports it.
    "pikepdf",
    # Model conversion, quantisation and GPU diagnostics; OCR only runs models.
    "onnxruntime.transformers", "onnxruntime.tools", "onnxruntime.quantization",
    "shapely.tests",
    # Word, PowerPoint and Excel output use lxml.etree, and nothing else of it.
    "lxml.objectify", "lxml.html", "lxml.isoschematron",
)

# Files, matched against the path they are installed at.
FILES = (
    "opencv_videoio_ffmpeg",    # OpenCV reading video; OCR and Word export read images
    "PIL/_avif.",               # AVIF images, which nothing here opens or writes
    "PIL\\_avif.",
    "pymupdf/mupdf-devel/",     # headers and an import library, for compiling against MuPDF
    "pymupdf\\mupdf-devel\\",
)

# Source and build files some packages ship beside what they compiled.
SUFFIXES = (".c", ".cpp", ".h", ".hpp", ".pyx", ".pxd", ".pyi", ".lib")


def wanted_module(name: str) -> bool:
    return not any(name == m or name.startswith(m + ".") for m in MODULES)


def notices() -> list[tuple[str, str]]:
    """Licence files that collecting a package's code leaves behind.

    OpenCV is not collected whole - PyInstaller's own hook finds its code - and
    its licence and third-party notices were missing from the build.
    """
    found = []
    spec = importlib.util.find_spec("cv2")
    for folder in (spec.submodule_search_locations or []) if spec else []:
        found += [(path, "cv2") for path in glob.glob(os.path.join(folder, "LICENSE*.txt"))]
    return found


def _as_module(dest: str) -> str:
    """'lxml\\objectify.cp312-win_amd64.pyd' -> 'lxml.objectify'.

    Compiled modules and package data are files, not modules, so a package
    left out by name would otherwise keep its .pyd files and its data.
    """
    path = re.sub(r"\.(cp\d+-[\w]+\.)?(pyd|py|pyc)$", "", dest.replace("\\", "/"))
    return path.replace("/", ".")


def wanted_file(dest: str) -> bool:
    if any(part in dest for part in FILES) or dest.lower().endswith(SUFFIXES):
        return False
    return wanted_module(_as_module(dest))


def trim(analysis) -> None:
    """Drop the unwanted entries from an Analysis before it is packaged."""
    analysis.pure = [entry for entry in analysis.pure if wanted_module(entry[0])]
    analysis.binaries = [entry for entry in analysis.binaries if wanted_file(entry[0])]
    analysis.datas = [entry for entry in analysis.datas if wanted_file(entry[0])]
