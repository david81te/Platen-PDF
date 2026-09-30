"""The bridge the UI calls into.

Every method returns {"ok": True, "data": ...} or {"ok": False, "error": "..."}
so the front end has one uniform shape to handle.
"""
from __future__ import annotations

import functools
import os
import traceback

import pymupdf as fitz

from . import (annots, compare as comparison, convert, decorate, forms,
               pages, security, shell_integration, signatures, textedit,
               version)
from .session import NoDocument, PdfError, Session

PDF_TYPES = ("PDF files (*.pdf)",)
IMAGE_TYPES = ("Image files (*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.tif;*.tiff)",)
ANY_INPUT = ("Documents (*.pdf;*.doc;*.docx;*.rtf;*.txt;*.xls;*.xlsx;*.csv;"
             "*.ppt;*.pptx;*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp)",)


def endpoint(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return {"ok": True, "data": fn(self, *args, **kwargs)}
        except (PdfError, NoDocument, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # unexpected: log for diagnosis, report cleanly
            traceback.print_exc()
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    return wrapper


def _color(value, default=(0, 0, 0)):
    if value is None:
        return default
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return tuple(max(0.0, min(1.0, float(c))) for c in value[:3])
    return default


def _maybe_color(value):
    return None if value is None else _color(value)


class Api:
    # pywebview walks the public attributes of this object to build its JS
    # bridge. Anything public that points at the native window recurses through
    # the WebView2 COM tree and kills the bridge entirely, so all state is
    # private and only the @endpoint methods are exposed.

    def __init__(self):
        self._docs = [Session()]
        self._active = 0
        self._window = None
        self._startup_path = None
        self._comparison = None      # last comparison result, kept for the UI

    def attach_window(self, window):
        self._window = window

    # Every endpoint works against whichever tab is in front. Exposing this as
    # a property means the rest of the class needed no changes when multiple
    # documents arrived.
    @property
    def _session(self) -> Session:
        return self._docs[self._active]

    def _tab_state(self) -> dict:
        tabs = []
        for index, session in enumerate(self._docs):
            info = session.info()
            tabs.append({
                "index": index,
                "name": info.get("name") if info.get("open") else "Empty",
                "open": bool(info.get("open")),
                "dirty": bool(info.get("dirty")),
                "page_count": info.get("page_count", 0),
                "path": info.get("path"),
                "active": index == self._active,
            })
        return {"tabs": tabs, "active": self._active}

    def _slot_for_new_document(self) -> int:
        """Reuse the front tab when it is empty, otherwise start a new one."""
        if self._session.doc is None:
            return self._active
        self._docs.append(Session())
        self._active = len(self._docs) - 1
        return self._active

    # ---- dialogs --------------------------------------------------------

    def _dialog(self, kind, **kwargs):
        if self._window is None:
            raise PdfError("The window is not ready yet.")
        import webview
        result = self._window.create_file_dialog(kind, **kwargs)
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return list(result)
        return [result]

    def _ask_open(self, multiple=False, types=PDF_TYPES):
        import webview
        return self._dialog(webview.OPEN_DIALOG, allow_multiple=multiple,
                            file_types=types)

    def _ask_save(self, filename, types=PDF_TYPES):
        import webview
        found = self._dialog(webview.SAVE_DIALOG, save_filename=filename,
                             file_types=types)
        return found[0] if found else None

    def _ask_folder(self):
        import webview
        found = self._dialog(webview.FOLDER_DIALOG)
        return found[0] if found else None

    def _default_name(self, extension: str) -> str:
        base = self._session.path or "Document.pdf"
        return os.path.splitext(os.path.basename(base))[0] + extension

    # ---- document -------------------------------------------------------

    @endpoint
    def ping(self):
        return {"ready": True, "ocr": convert.ocr_status()}

    @endpoint
    def about(self):
        return version.about()

    @endpoint
    def default_app_status(self):
        return shell_integration.status()

    @endpoint
    def register_file_types(self):
        state = shell_integration.register()
        state["settings_opened"] = shell_integration.open_default_apps_settings()
        return state

    @endpoint
    def unregister_file_types(self):
        return shell_integration.unregister()

    @endpoint
    def pending_open(self):
        """A file passed on the command line, claimed once by the UI."""
        path, self._startup_path = self._startup_path, None
        return {"path": path}

    @endpoint
    def open_dialog(self):
        chosen = self._ask_open(multiple=True, types=ANY_INPUT)
        if not chosen:
            return {"cancelled": True}
        opened = None
        for path in chosen:
            self._slot_for_new_document()
            opened = self._open_any(path)
            if opened.get("needs_password"):
                break
        return opened

    @endpoint
    def open_path(self, path, password=None):
        if password is None:
            self._slot_for_new_document()
        return self._open_any(path, password)

    # ---- tabs -----------------------------------------------------------

    def unsaved(self) -> list[dict]:
        """Tabs with changes that would be lost. Plain method, not an endpoint."""
        out = []
        for index, session in enumerate(self._docs):
            info = session.info()
            if info.get("open") and info.get("dirty"):
                out.append({"index": index, "name": info.get("name"),
                            "path": info.get("path")})
        return out

    def save_all(self) -> dict:
        """Save every changed tab, asking for a location where there is none."""
        saved, skipped = [], []
        keep = self._active
        try:
            for item in self.unsaved():
                self._active = item["index"]
                if self._session.path:
                    self._session.save()
                    saved.append(item["name"])
                    continue
                target = self._ask_save(self._default_name(".pdf"))
                if not target:
                    skipped.append(item["name"])
                    continue
                self._session.save(target)
                saved.append(os.path.basename(target))
        finally:
            self._active = min(keep, len(self._docs) - 1)
        return {"saved": saved, "skipped": skipped}

    @endpoint
    def unsaved_list(self):
        return {"tabs": self.unsaved()}

    @endpoint
    def save_all_tabs(self):
        return self.save_all()

    # ---- comparing two documents ---------------------------------------

    def _run_comparison(self, older, newer, visual, label):
        result = comparison.compare(older, newer, visual=bool(visual))
        result["against"] = label
        self._comparison = result
        return result

    @endpoint
    def compare_tab(self, other_index, visual=True, older="other"):
        """Compare the front document with another open tab."""
        other_index = int(other_index)
        if other_index < 0 or other_index >= len(self._docs):
            raise PdfError("That tab is no longer open.")
        if other_index == self._active:
            raise PdfError("Choose a different tab to compare against.")
        other = self._docs[other_index]
        current = self._session.require()
        name = other.info().get("name") or "the other tab"
        if older == "other":
            return self._run_comparison(other.require(), current, visual, name)
        return self._run_comparison(current, other.require(), visual, name)

    @endpoint
    def compare_file(self, visual=True, older="other"):
        """Compare the front document with a file on disk."""
        chosen = self._ask_open(types=PDF_TYPES)
        if not chosen:
            return {"cancelled": True}
        other = fitz.open(chosen[0])
        if other.needs_pass:
            other.close()
            raise PdfError("That file is password protected. Open it in a tab first.")
        try:
            current = self._session.require()
            name = os.path.basename(chosen[0])
            if older == "other":
                return self._run_comparison(other, current, visual, name)
            return self._run_comparison(current, other, visual, name)
        finally:
            other.close()

    @endpoint
    def compare_result(self):
        return self._comparison or {"pages": [], "summary": None}

    @endpoint
    def compare_clear(self):
        self._comparison = None
        return {"ok": True}

    @endpoint
    def compare_markup(self):
        """Highlight the differences on the front document."""
        if not self._comparison:
            raise PdfError("Run a comparison first.")
        doc = self._session.require()
        self._session.checkpoint()
        result = comparison.mark_up(doc, self._comparison)
        self._session.touch()
        return result

    @endpoint
    def tab_list(self):
        return self._tab_state()

    @endpoint
    def tab_switch(self, index):
        index = int(index)
        if index < 0 or index >= len(self._docs):
            raise PdfError("That tab is no longer open.")
        self._active = index
        return {**self._tab_state(), "info": self._session.info()}

    @endpoint
    def tab_close(self, index):
        index = int(index)
        if index < 0 or index >= len(self._docs):
            raise PdfError("That tab is no longer open.")
        self._docs[index].close()
        self._docs.pop(index)
        if not self._docs:                      # always keep one empty slot
            self._docs.append(Session())
        self._active = min(self._active if index > self._active
                           else max(0, self._active - 1), len(self._docs) - 1)
        return {**self._tab_state(), "info": self._session.info()}

    @endpoint
    def tab_new(self):
        self._docs.append(Session())
        self._active = len(self._docs) - 1
        return self._tab_state()

    @endpoint
    def merge_tab(self, source_index, at=None):
        """Merge another open tab into the front one."""
        source_index = int(source_index)
        if source_index < 0 or source_index >= len(self._docs):
            raise PdfError("That tab is no longer open.")
        if source_index == self._active:
            raise PdfError("Choose a different tab to merge from.")
        other = self._docs[source_index].require()
        doc = self._session.require()
        self._session.checkpoint()
        result = pages.merge_document(doc, other, None if at is None else int(at))
        self._session.touch()
        return result

    def _open_any(self, path, password=None):
        extension = os.path.splitext(path)[1].lower()
        if extension and extension != ".pdf":
            built = convert.from_files([path])
            info = self._session.adopt(built, path=None)
            info["converted_from"] = os.path.basename(path)
            return info
        try:
            return self._session.open(path, password)
        except PdfError as exc:
            if str(exc) != "PASSWORD_REQUIRED":
                raise
            # Reported rather than raised so the UI can ask for the password
            # instead of showing an error the user cannot act on.
            return {"open": False, "needs_password": True, "path": path,
                    "name": os.path.basename(path),
                    "wrong_password": bool(password)}

    @endpoint
    def close_doc(self):
        self._session.close()
        return {"open": False, **self._tab_state()}

    @endpoint
    def doc_info(self):
        return self._session.info()

    @endpoint
    def render(self, index, zoom=1.0):
        return self._session.render(int(index), float(zoom))

    @endpoint
    def thumbs(self, start=0, count=20, width=150):
        return self._session.thumbnails(int(start), int(count), int(width))

    @endpoint
    def page_sizes(self):
        return self._session.page_sizes()

    def save(self):
        """Save, falling back to Save As when there is no path yet.

        Not wrapped in @endpoint: it returns whatever save_as already built, so
        a failure there surfaces as its own message rather than a KeyError.
        """
        if not self._session.path:
            return self.save_as()
        try:
            return {"ok": True, "data": self._session.save()}
        except (PdfError, NoDocument, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}

    @endpoint
    def save_as(self):
        target = self._ask_save(self._default_name(".pdf"))
        if not target:
            return {"cancelled": True}
        return self._session.save(target)

    @endpoint
    def undo(self):
        return self._session.undo()

    @endpoint
    def redo(self):
        return self._session.redo()

    @endpoint
    def search(self, query, match_case=False):
        return self._session.search(query, bool(match_case))

    @endpoint
    def outline(self):
        return self._session.outline()

    @endpoint
    def set_outline(self, items):
        return self._session.set_outline(items or [])

    @endpoint
    def set_metadata(self, fields):
        return self._session.set_metadata(fields or {})

    # ---- text editing ---------------------------------------------------

    @endpoint
    def text_layout(self, index):
        return textedit.layout(self._session.page(int(index)))

    @endpoint
    def edit_span(self, span_id, text):
        doc = self._session.require()
        self._session.checkpoint()
        result = textedit.edit_span(doc, span_id, text)
        self._session.touch()
        self._session.reload()
        return result

    @endpoint
    def edit_block(self, block_id, text):
        doc = self._session.require()
        self._session.checkpoint()
        result = textedit.edit_block(doc, block_id, text)
        self._session.touch()
        self._session.reload()
        return result

    @endpoint
    def delete_text(self, index, rect):
        doc = self._session.require()
        self._session.checkpoint()
        result = textedit.delete_region(doc, int(index), rect)
        self._session.touch()
        return result

    @endpoint
    def add_text(self, index, rect, text, font="Calibri", size=11, color=None,
                 align="left", bold=False, italic=False):
        doc = self._session.require()
        self._session.checkpoint()
        result = textedit.add_text(doc, int(index), rect, text, font_name=font,
                                   size=float(size), color=_color(color),
                                   align=align, bold=bool(bold), italic=bool(italic))
        self._session.touch()
        self._session.reload()
        return result

    @endpoint
    def replace_all(self, find, replace, match_case=True):
        doc = self._session.require()
        self._session.checkpoint()
        result = textedit.replace_all(doc, find, replace, bool(match_case))
        self._session.touch()
        self._session.reload()
        return result

    # ---- pages ----------------------------------------------------------

    def _mutate(self, fn, *args, **kwargs):
        doc = self._session.require()
        self._session.checkpoint()
        result = fn(doc, *args, **kwargs)
        self._session.touch()
        return result

    @endpoint
    def page_rotate(self, indices, degrees=90):
        return self._mutate(pages.rotate, indices, int(degrees))

    @endpoint
    def page_delete(self, indices):
        return self._mutate(pages.delete, indices)

    @endpoint
    def page_insert(self, at, paper="letter", landscape=False):
        return self._mutate(pages.insert_blank, int(at), paper, bool(landscape))

    @endpoint
    def page_move(self, source, target):
        return self._mutate(pages.move, int(source), int(target))

    @endpoint
    def page_reorder(self, order):
        return self._mutate(pages.reorder, [int(i) for i in order])

    @endpoint
    def page_duplicate(self, indices):
        return self._mutate(pages.duplicate, indices)

    @endpoint
    def page_crop(self, index, rect):
        return self._mutate(pages.crop, int(index), rect)

    @endpoint
    def page_reset_crop(self, index):
        return self._mutate(pages.reset_crop, int(index))

    @endpoint
    def page_extract(self, indices, remove=False, save_copy=True, open_tab=True):
        """Pull pages out, optionally deleting them and showing them in a tab."""
        source = self._session.require()
        wanted = sorted({int(i) for i in indices})
        target = None
        if save_copy:
            target = self._ask_save(self._default_name("_extract.pdf"))
            if not target:
                return {"cancelled": True}

        built, summary = pages.extract(source, wanted, target)

        if remove:
            if len(wanted) >= source.page_count:
                built.close()
                raise PdfError("Those are all the pages; the document would be empty.")
            self._session.checkpoint()
            pages.delete(source, wanted)
            self._session.touch()
        summary["removed"] = bool(remove)

        if open_tab:
            self._slot_for_new_document()
            info = self._session.adopt(built, path=target,
                                       dirty=target is None)
            summary["tab"] = self._active
            summary["info"] = info
        else:
            built.close()
        return summary

    @endpoint
    def page_merge(self, at=None):
        chosen = self._ask_open(multiple=True, types=ANY_INPUT)
        if not chosen:
            return {"cancelled": True}
        return self._mutate(pages.merge, chosen, None if at is None else int(at))

    @endpoint
    def page_split(self, mode="every", size=1, ranges=""):
        folder = self._ask_folder()
        if not folder:
            return {"cancelled": True}
        stem = os.path.splitext(os.path.basename(self._session.path or "document"))[0]
        return pages.split(self._session.require(), folder, mode, int(size), ranges, stem)

    # ---- annotations ----------------------------------------------------

    @endpoint
    def annot_markup(self, index, kind, rects, color=None, author="", note="",
                     snap=True):
        default = (1.0, 0.92, 0.23) if kind == "highlight" else (0.85, 0.1, 0.1)
        return self._mutate(annots.add_markup, int(index), kind, rects,
                            _color(color, default), author, note, bool(snap))

    @endpoint
    def annot_note(self, index, point, text, author=""):
        return self._mutate(annots.add_note, int(index), point, text, author)

    @endpoint
    def annot_textbox(self, index, rect, text, size=11, color=None, fill=None,
                      border=None, align=0):
        return self._mutate(annots.add_textbox, int(index), rect, text,
                            float(size), _color(color), _maybe_color(fill),
                            _maybe_color(border), int(align))

    @endpoint
    def annot_shape(self, index, kind, points, color=None, fill=None,
                    width=1.5, opacity=1.0):
        return self._mutate(annots.add_shape, int(index), kind, points,
                            _color(color, (0.85, 0.1, 0.1)), _maybe_color(fill),
                            float(width), float(opacity))

    @endpoint
    def annot_ink(self, index, strokes, color=None, width=2.0, opacity=1.0):
        return self._mutate(annots.add_ink, int(index), strokes,
                            _color(color, (0.85, 0.1, 0.1)), float(width),
                            float(opacity))

    @endpoint
    def annot_stamp(self, index, rect, label="approved", color=None):
        return self._mutate(annots.add_stamp, int(index), rect, label,
                            _color(color, (0.1, 0.5, 0.15)))

    @endpoint
    def annot_image(self, index, rect):
        chosen = self._ask_open(types=IMAGE_TYPES)
        if not chosen:
            return {"cancelled": True}
        return self._mutate(annots.add_image, int(index), rect, chosen[0])

    @endpoint
    def annot_list(self, index):
        return annots.listing(self._session.require(), int(index))

    @endpoint
    def annot_update(self, index, annot_id, rect=None, content=None, color=None,
                     fill=None, opacity=None, author=None):
        return self._mutate(annots.update, int(index), int(annot_id), rect, content,
                            _maybe_color(color), _maybe_color(fill),
                            None if opacity is None else float(opacity), author)

    @endpoint
    def annot_line_points(self, index, annot_id, points):
        """Reshape a line or arrow. The annotation is rebuilt, so the id changes."""
        return self._mutate(annots.update_line, int(index), int(annot_id), points)

    @endpoint
    def annot_delete(self, index, annot_id):
        return self._mutate(annots.delete, int(index), int(annot_id))

    @endpoint
    def annot_flatten(self, include_forms=False):
        result = self._mutate(annots.flatten, True, bool(include_forms))
        self._session.reload()
        return result

    @endpoint
    def link_list(self, index):
        return annots.links(self._session.require(), int(index))

    @endpoint
    def link_add(self, index, rect, uri="", target_page=None):
        return self._mutate(annots.add_link, int(index), rect, uri,
                            None if target_page is None else int(target_page))

    @endpoint
    def link_delete(self, index, link_index):
        return self._mutate(annots.delete_link, int(index), int(link_index))

    # ---- signatures -----------------------------------------------------

    @endpoint
    def sig_list(self):
        return signatures.listing()

    @endpoint
    def sig_add_dialog(self, name, role="", drop_background=True):
        chosen = self._ask_open(types=IMAGE_TYPES)
        if not chosen:
            return {"cancelled": True}
        return signatures.add(chosen[0], name, role, bool(drop_background))

    @endpoint
    def sig_add_data(self, data_url, name, role="", drop_background=True):
        return signatures.add(data_url, name, role, bool(drop_background))

    @endpoint
    def sig_rename(self, identifier, name, role=""):
        return signatures.rename(identifier, name, role)

    @endpoint
    def sig_delete(self, identifier):
        return signatures.remove(identifier)

    @endpoint
    def sig_place(self, index, identifier, rect, date_text="", flatten=False):
        doc = self._session.require()
        self._session.checkpoint()
        result = signatures.place_dated(doc, int(index), identifier, rect,
                                        date_text, flatten=bool(flatten))
        self._session.touch()
        if date_text:
            self._session.reload()
        return result

    # ---- forms ----------------------------------------------------------

    @endpoint
    def form_list(self, index=None):
        return forms.listing(self._session.require(),
                             None if index is None else int(index))

    @endpoint
    def form_set(self, index, name, value):
        return self._mutate(forms.set_value, int(index), name, value)

    @endpoint
    def form_set_many(self, values):
        return self._mutate(forms.set_many, values or [])

    @endpoint
    def form_add(self, index, kind, rect, name, value="", options=None,
                 size=11, required=False):
        return self._mutate(forms.add_field, int(index), kind, rect, name, value,
                            options or [], float(size), bool(required))

    @endpoint
    def form_delete(self, index, name):
        return self._mutate(forms.delete_field, int(index), name)

    @endpoint
    def form_export(self):
        target = self._ask_save(self._default_name("_fields.csv"),
                                ("CSV files (*.csv)",))
        if not target:
            return {"cancelled": True}
        return forms.export_csv(self._session.require(), target)

    @endpoint
    def form_import(self):
        chosen = self._ask_open(types=("CSV files (*.csv)",))
        if not chosen:
            return {"cancelled": True}
        return self._mutate(forms.import_csv, chosen[0])

    @endpoint
    def form_flatten(self):
        result = self._mutate(forms.flatten)
        self._session.reload()
        return result

    # ---- security & redaction -------------------------------------------

    @endpoint
    def security_info(self):
        return security.describe(self._session.require(),
                                 self._session.encrypted)

    @endpoint
    def protect(self, user_password="", owner_password="", allowed=None):
        target = self._ask_save(self._default_name("_protected.pdf"))
        if not target:
            return {"cancelled": True}
        return security.save_protected(self._session.require(), target,
                                       user_password, owner_password, allowed)

    @endpoint
    def unprotect(self):
        target = self._ask_save(self._default_name("_unlocked.pdf"))
        if not target:
            return {"cancelled": True}
        return security.save_unprotected(self._session.require(), target)

    @endpoint
    def redact_mark(self, index, rects, label=""):
        return self._mutate(security.mark, int(index), rects, label)

    @endpoint
    def redact_search(self, needle):
        return self._mutate(security.mark_matches, needle)

    @endpoint
    def redact_pending(self, index):
        return {"count": security.pending(self._session.require(), int(index))}

    @endpoint
    def redact_apply(self, remove_images=True):
        result = self._mutate(security.apply, bool(remove_images))
        self._session.reload()
        return result

    @endpoint
    def redact_clear(self):
        return self._mutate(security.clear_marks)

    # ---- conversion -----------------------------------------------------

    @endpoint
    def export_docx(self):
        target = self._ask_save(self._default_name(".docx"),
                                ("Word documents (*.docx)",))
        if not target:
            return {"cancelled": True}
        return convert.to_docx(self._session.require(), target)

    @endpoint
    def export_xlsx(self):
        target = self._ask_save(self._default_name(".xlsx"),
                                ("Excel workbooks (*.xlsx)",))
        if not target:
            return {"cancelled": True}
        return convert.to_xlsx(self._session.require(), target)

    @endpoint
    def export_pptx(self, mode="editable"):
        target = self._ask_save(self._default_name(".pptx"),
                                ("PowerPoint decks (*.pptx)",))
        if not target:
            return {"cancelled": True}
        return convert.to_pptx(self._session.require(), target, mode)

    @endpoint
    def export_images(self, dpi=200, fmt="png"):
        folder = self._ask_folder()
        if not folder:
            return {"cancelled": True}
        return convert.to_images(self._session.require(), folder, int(dpi), fmt)

    @endpoint
    def export_text(self):
        target = self._ask_save(self._default_name(".txt"),
                                ("Text files (*.txt)",))
        if not target:
            return {"cancelled": True}
        return convert.to_text(self._session.require(), target)

    @endpoint
    def create_from_files(self):
        chosen = self._ask_open(multiple=True, types=ANY_INPUT)
        if not chosen:
            return {"cancelled": True}
        built = convert.from_files(chosen)
        return self._session.adopt(built, path=None)

    @endpoint
    def ocr_status(self):
        return convert.ocr_status()

    @endpoint
    def ocr_run(self, language="eng", dpi=220, force=False):
        result = self._mutate(convert.ocr, language, int(dpi), None, bool(force))
        self._session.reload()
        return result

    @endpoint
    def compress(self, level="medium"):
        target = self._ask_save(self._default_name("_compressed.pdf"))
        if not target:
            return {"cancelled": True}
        before = os.path.getsize(self._session.path) if self._session.path and \
            os.path.isfile(self._session.path) else None
        result = convert.compress(self._session.require(), target, level)
        result["before"] = before
        return result

    # ---- decoration -----------------------------------------------------

    @endpoint
    def watermark(self, text, pages_list=None, size=48, color=None, opacity=0.25,
                  angle=45, behind=False):
        return self._mutate(decorate.watermark_text, text, pages_list, float(size),
                            _color(color, (0.6, 0.6, 0.6)), float(opacity),
                            float(angle), "Helvetica", bool(behind))

    @endpoint
    def watermark_image(self, pages_list=None, scale=0.5, behind=True):
        chosen = self._ask_open(types=IMAGE_TYPES)
        if not chosen:
            return {"cancelled": True}
        return self._mutate(decorate.watermark_image, chosen[0], pages_list,
                            float(scale), bool(behind))

    @endpoint
    def stamp_text(self, template, position="bottom-center", pages_list=None,
                   size=9, color=None, margin=36, start_at=1, skip_first=False):
        return self._mutate(decorate.stamp_text, template, position, pages_list,
                            float(size), _color(color, (0.2, 0.2, 0.2)),
                            "Helvetica", float(margin), int(start_at),
                            bool(skip_first))

    @endpoint
    def background(self, pages_list=None, color=None):
        return self._mutate(decorate.background, pages_list, _maybe_color(color), None)
