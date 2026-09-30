# Third-party components

PDF Studio is original work by David Willmore, but it does its job by standing
on open-source libraries. They keep their own copyright and their own licences;
the notice in [LICENSE](LICENSE) covers only the code in this repository.

## The one with conditions attached

**PyMuPDF 1.28.2** — *dual licensed: GNU Affero General Public License v3, or a
commercial licence from Artifex Software.*

PyMuPDF does essentially all the PDF work in this program: opening, rendering,
text extraction, in-place editing, annotations, redaction and saving. It is not
a component that could be swapped out in an afternoon.

Under the AGPL branch, distributing a program built on it to anyone outside
your own organisation obliges you to offer them the complete corresponding
source under the AGPL as well. Internal use inside one company is the ordinary
case and is not what the clause is aimed at, which is why the permission in
LICENSE is written the way it is. Selling PDF Studio, shipping it to clients or
publishing the executable would be a different matter, and the route for that
is a commercial licence from Artifex rather than a change of wording here.

This is a plain description of the licence, not legal advice.

## The permissive ones

| Component | Licence | Used for |
| --- | --- | --- |
| pikepdf 10.13 | MPL-2.0 | PDF object repair |
| Pillow 12.3 | MIT-CMU | image handling |
| pdf2docx 0.5.13 | MIT | PDF to Word |
| python-docx 1.2 | MIT | Word output |
| python-pptx 1.0.2 | MIT | PowerPoint output |
| openpyxl 3.1.5 | MIT | Excel output |
| fontTools 4.66 | MIT | font subsetting, ToUnicode repair |
| pyclipper 1.4 | MIT | OCR geometry |
| onnxruntime 1.30 | MIT | runs the OCR models |
| RapidOCR 1.4.4 | Apache-2.0 | built-in text recognition |
| opencv-python 5.0 | Apache-2.0 | OCR image preparation |
| pywebview 6.2.1 | BSD | the desktop window |
| Shapely 2.1.2 | BSD-3-Clause | OCR geometry |
| NumPy 2.5.3 | BSD-3-Clause | pixel and image maths |
| pywin32 312 | PSF | Office automation, Windows APIs |

PyInstaller is GPLv2 with a linking exception written for exactly this purpose:
it lets the executables it produces carry whatever licence you choose. It is a
build tool and no part of it ends up under your copyright either way.

Microsoft Edge WebView2 renders the interface and is a Windows component the
user already has; PDF Studio does not redistribute it.
