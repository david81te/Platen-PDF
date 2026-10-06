/* Platen PDF front end. Talks to the Python backend through pywebview. */

const S = {
  info: null,
  page: 0,
  zoom: 1.25,
  zoomMode: 'width',   // width | page | fixed
  sizes: null,        // page dimensions, cached per document
  tool: 'select',
  layout: null,
  scale: 1,          // rendered pixels per PDF point
  sig: null,
  color: [1, 0.92, 0.23],
  stroke: [0.85, 0.1, 0.1],
  lineWidth: 2,
  stamp: 'approved',
  stampColor: [0.1, 0.5, 0.15],
  fontSize: 11,
  redactions: [],
  editing: null,
  hits: [],
  hitIndex: -1,
  annots: [],
  selectedAnnot: null,
  thumbLoaded: 0,
  thumbBusy: false,
  fields: [],
  selectedField: null,
  compare: null,     // last comparison result
  comparePage: -1,
  findQuery: '',
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};

/* ---------- backend bridge ---------- */

function ready() {
  return new Promise((res) => {
    if (window.pywebview && window.pywebview.api) return res();
    window.addEventListener('pywebviewready', () => res(), { once: true });
  });
}

async function call(name, ...args) {
  await ready();
  const fn = window.pywebview.api[name];
  if (!fn) throw new Error('Unknown action: ' + name);
  const res = await fn(...args);
  if (!res) throw new Error('No response from ' + name);
  if (!res.ok) throw new Error(res.error || 'Something went wrong.');
  return res.data;
}

// Work already in flight when a document closes will land against nothing.
// That is a race, not something the reader can act on, so it fails quietly.
const SILENT_ERRORS = ['No document is open.'];

async function run(name, ...args) {
  try {
    return await call(name, ...args);
  } catch (err) {
    if (!SILENT_ERRORS.includes(err.message)) toast(err.message, 'err');
    return null;
  }
}

async function busyRun(label, name, ...args) {
  showBusy(label);
  try {
    return await call(name, ...args);
  } catch (err) {
    toast(err.message, 'err');
    return null;
  } finally {
    hideBusy();
  }
}

/* ---------- chrome ---------- */

function toast(text, kind = '') {
  const node = el('div', 'msg ' + kind, text);
  $('toast').appendChild(node);
  setTimeout(() => node.remove(), kind === 'err' ? 6000 : 3200);
}
function showBusy(text) { $('busytext').textContent = text || 'Working…'; $('busy').classList.add('on'); }
function hideBusy() { $('busy').classList.remove('on'); }

function modal(title, bodyHtml, onOk, okLabel = 'OK', wide = false) {
  const box = $('modal');
  box.className = wide ? 'wide' : '';
  box.innerHTML = '<h2>' + title + '</h2>' + bodyHtml +
    '<div class="actions"><button data-x>Cancel</button>' +
    (onOk ? '<button class="primary" data-ok>' + okLabel + '</button>' : '') + '</div>';
  $('modal-back').classList.add('on');
  const close = () => { $('modal-back').classList.remove('on'); box.innerHTML = ''; };
  box.querySelector('[data-x]').onclick = close;
  const ok = box.querySelector('[data-ok]');
  if (ok) {
    ok.onclick = async () => {
      const values = {};
      box.querySelectorAll('[name]').forEach((f) => {
        values[f.name] = f.type === 'checkbox' ? f.checked : f.value;
      });
      close();
      await onOk(values);
    };
  }
  const first = box.querySelector('input,select,textarea');
  if (first) setTimeout(() => first.focus(), 30);
  return close;
}

/* ---------- document ---------- */

function askPassword(info) {
  modal('Password required',
    '<p class="hint">' + escapeHtml(info.name || 'This document') +
    ' is protected.' + (info.wrong_password ? ' That password was not accepted.' : '') +
    '</p><div class="field"><label>Password</label>' +
    '<input name="pw" type="password" autocomplete="off"></div>',
    async (v) => {
      const next = await busyRun('Opening…', 'open_path', info.path, v.pw);
      if (!next) return;
      if (next.needs_password) { askPassword(next); return; }
      setInfo(next);
      await refresh();
    }, 'Open');
}

function setInfo(info) {
  if (!info || info.cancelled) return;
  if (info.needs_password) { askPassword(info); return; }
  if (info.open === false) {
    S.info = null;
    $('stage').classList.remove('on');
    $('empty').style.display = 'grid';
    $('docname').textContent = 'No document open';
    $('pane-thumbs').innerHTML = '';
    $('pagetotal').textContent = '0';
    refreshTabs();
    return;
  }
  S.info = info;
  $('empty').style.display = 'none';
  $('stage').classList.add('on');
  $('docname').innerHTML = '<b>' + escapeHtml(info.name) + '</b>' +
    (info.dirty ? ' • unsaved changes' : '') +
    (info.encrypted ? ' • encrypted' : '');
  $('pagetotal').textContent = info.page_count;
  if (S.page >= info.page_count) S.page = info.page_count - 1;
  if (S.page < 0) S.page = 0;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function refresh(full = true) {
  if (S.hits.length) { S.hits = []; S.hitIndex = -1; renderHits(false); }
  S.sizes = null;                      // pages may have been added or rotated
  setInfo(await run('doc_info'));
  refreshTabs();
  if (!S.info) return;
  await drawPage();
  if (full) { loadThumbs(); loadOutline(); loadComments(); }
}

// The stage pads the page and the viewer may show a scrollbar; leave room for
// both or a fit-to-width page triggers the very scrollbar it has to fit inside.
const STAGE_PAD = 44;
const SCROLLBAR = 18;

function applyZoomMode() {
  if (S.zoomMode === 'fixed' || !S.info || !S.sizes) return;
  const size = S.sizes[S.page];
  if (!size) return;
  const view = $('viewer');
  const availH = Math.max(120, view.clientHeight - STAGE_PAD);
  let availW = Math.max(120, view.clientWidth - STAGE_PAD);

  // clientWidth already excludes a scrollbar that is on screen. Only reserve
  // room for one when there is none yet but scaling up would summon it --
  // otherwise the allowance is counted twice and the page sits short.
  const hasScrollbar = view.scrollHeight > view.clientHeight + 1;
  if (!hasScrollbar && size.height * (availW / size.width) > availH) {
    availW = Math.max(120, availW - SCROLLBAR);
  }

  const byWidth = availW / size.width;
  const zoom = S.zoomMode === 'page'
    ? Math.min(byWidth, availH / size.height)
    : byWidth;
  S.zoom = Math.max(0.1, Math.min(6, zoom));
}

async function drawPage() {
  if (!S.info) return;
  if (!S.sizes) S.sizes = await run('page_sizes');
  applyZoomMode();
  const data = await run('render', S.page, S.zoom);
  if (!data) return;
  const img = $('pageimg');
  img.src = data.image;
  img.width = data.width;
  img.height = data.height;
  const wrap = $('pagewrap');
  wrap.style.width = data.width + 'px';
  wrap.style.height = data.height + 'px';
  const cv = $('scratch');
  cv.width = data.width; cv.height = data.height;
  cv.style.width = data.width + 'px'; cv.style.height = data.height + 'px';
  S.scale = data.width / data.pdf_width;
  syncZoomLabel();
  $('pagenum').value = S.page + 1;
  S.layout = null;
  await buildOverlay();
  markThumb();
}

/* ---------- coordinates ---------- */

const toPt = (px) => px / S.scale;
const toPx = (pt) => pt * S.scale;
function localPoint(ev) {
  const r = $('overlay').getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}

/* ---------- overlay & tools ---------- */

// Two overlapping builds each cleared the overlay, then each appended once the
// awaits resolved, leaving duplicated hit targets. A generation counter lets a
// superseded build bail out instead.
let overlayRun = 0;

async function buildOverlay() {
  const mine = ++overlayRun;
  const stale = () => mine !== overlayRun;
  const ov = $('overlay');
  ov.innerHTML = '';
  ov.className = '';
  cancelEdit();
  if (!S.info) return;

  if (S.tool === 'text') {
    ov.classList.add('textmode');
    S.layout = await run('text_layout', S.page);
    if (stale()) return;
    if (S.layout) {
      S.layout.blocks.forEach((b) => {
        const badge = el('div', 'hit block', '¶');
        Object.assign(badge.style, {
          left: toPx(b.bbox[0]) - 17 + 'px', top: toPx(b.bbox[1]) + 'px',
          width: '15px', height: '15px', textAlign: 'center',
          fontSize: '11px', color: '#4c8dff', lineHeight: '15px',
        });
        badge.title = 'Edit whole paragraph';
        badge.onclick = (e) => { e.stopPropagation(); editBlock(b); };
        ov.appendChild(badge);

        b.lines.forEach((ln) => ln.spans.forEach((sp) => {
          const hit = el('div', 'hit');
          Object.assign(hit.style, {
            left: toPx(sp.bbox[0]) + 'px', top: toPx(sp.bbox[1]) + 'px',
            width: Math.max(4, toPx(sp.bbox[2] - sp.bbox[0])) + 'px',
            height: Math.max(6, toPx(sp.bbox[3] - sp.bbox[1])) + 'px',
          });
          hit.title = sp.font + ' ' + sp.size + 'pt — click to edit';
          hit.onclick = (e) => { e.stopPropagation(); editSpan(sp); };
          ov.appendChild(hit);
        }));
      });
    }
  } else if (S.tool !== 'select') {
    ov.classList.add('crosshair');
  }


  if (SELECTING_TOOLS.includes(S.tool)) {
    S.annots = (await run('annot_list', S.page)) || [];
    if (stale()) return;
    S.annots.forEach((a) => {
      const hit = el('div', 'annothit' + (a.id === S.selectedAnnot ? ' on' : ''));
      // A sticky note draws a small icon, so give tiny rects a usable target.
      const w = Math.max(18, toPx(a.rect[2] - a.rect[0]));
      const h = Math.max(18, toPx(a.rect[3] - a.rect[1]));
      Object.assign(hit.style, {
        left: toPx(a.rect[0]) + 'px', top: toPx(a.rect[1]) + 'px',
        width: w + 'px', height: h + 'px',
      });
      hit.title = (a.author ? a.author + ': ' : '') + (a.content || a.type);
        hit.onclick = (e) => {
          e.stopPropagation();
          // Leaving the signature tool in hand would mean the next drag
          // places another one instead of moving this.
          if (S.tool !== 'select') setTool('select');
          selectAnnot(a.id, true);
        };
      ov.appendChild(hit);
    });
    if (S.selectedAnnot) {
      const chosen = S.annots.find((a) => a.id === S.selectedAnnot);
      if (chosen) {
        drawSelection(chosen);
        // Only things that carry words are worth popping open.
        if (chosen.content && !chosen.is_signature) showBubble(chosen);
      }
    }
  } else {
    S.annots = [];
  }

  if (SELECTING_TOOLS.includes(S.tool) || S.tool === 'field') {
    S.fields = (await run('form_list', S.page)) || [];
    if (stale()) return;
    S.fields.forEach((f) => {
      const hit = el('div', 'fieldhit' + (f.name === S.selectedField ? ' on' : ''));
      Object.assign(hit.style, {
        left: toPx(f.rect[0]) + 'px', top: toPx(f.rect[1]) + 'px',
        width: Math.max(12, toPx(f.rect[2] - f.rect[0])) + 'px',
        height: Math.max(12, toPx(f.rect[3] - f.rect[1])) + 'px',
      });
      hit.title = f.name + ' (' + f.kind + ')' + (f.readonly ? ' - read only' : '');
      if (f.kind === 'checkbox' && fieldIsTicked(f)) {
        hit.innerHTML = '<span class="tick">&#10003;</span>';
      }
      hit.onclick = (e) => { e.stopPropagation(); editField(f); };
      ov.appendChild(hit);
    });
  } else {
    S.fields = [];
  }

  if (S.compare) {
    const forPage = S.compare.pages.find((p) => p.right === S.page);
    if (forPage) {
      const paint = (rects, cls) => (rects || []).forEach((r) => {
        const mark = el('div', cls);
        Object.assign(mark.style, {
          left: toPx(r[0]) - 1 + 'px', top: toPx(r[1]) - 1 + 'px',
          width: toPx(r[2] - r[0]) + 2 + 'px',
          height: toPx(r[3] - r[1]) + 2 + 'px',
        });
        ov.appendChild(mark);
      });
      paint(forPage.visual_rects, 'diff-visual');
      paint(forPage.added_rects, 'diff-add');
    }
  }

  S.hits.forEach((h, i) => {
    if (h.page !== S.page) return;
    const mark = el('div', 'searchhit' + (i === S.hitIndex ? ' on' : ''));
    Object.assign(mark.style, {
      left: toPx(h.rect[0]) - 1 + 'px', top: toPx(h.rect[1]) - 1 + 'px',
      width: toPx(h.rect[2] - h.rect[0]) + 2 + 'px',
      height: toPx(h.rect[3] - h.rect[1]) + 2 + 'px',
    });
    ov.appendChild(mark);
  });

  S.redactions.filter((r) => r.page === S.page).forEach((r) => {
    const mark = el('div', 'redactmark');
    Object.assign(mark.style, {
      left: toPx(r.rect[0]) + 'px', top: toPx(r.rect[1]) + 'px',
      width: toPx(r.rect[2] - r.rect[0]) + 'px',
      height: toPx(r.rect[3] - r.rect[1]) + 'px',
    });
    ov.appendChild(mark);
  });
}

let nudging = false;

async function nudgeSelected(key, step) {
  // Written straight through rather than moved locally first: buildOverlay
  // refetches the annotations, so an optimistic local move is wiped out before
  // it can be saved.
  if (nudging) return;
  const a = (S.annots || []).find((x) => x.id === S.selectedAnnot);
  if (!a) return;
  const dx = key === 'ArrowLeft' ? -step : key === 'ArrowRight' ? step : 0;
  const dy = key === 'ArrowUp' ? -step : key === 'ArrowDown' ? step : 0;
  if (!dx && !dy) return;

  nudging = true;
  try {
    const moved = [a.rect[0] + dx, a.rect[1] + dy, a.rect[2] + dx, a.rect[3] + dy];
    const res = await run('annot_update', S.page, a.id, moved);
    if (res && res.id) S.selectedAnnot = res.id;   // a line is rebuilt, so its id moves
    await refresh(false);
  } finally {
    nudging = false;
  }
}


/* ---- filling in a form ---- */

function fieldIsTicked(field) {
  const v = field.value;
  return v === true || (typeof v === 'string' && v !== '' && v.toLowerCase() !== 'off');
}

async function setField(field, value) {
  const r = await run('form_set', S.page, field.name, value);
  if (!r) return;
  await refresh(false);
  if (S.tool === 'field') drawInspector();
}

function editField(field) {
  S.selectedField = field.name;
  if (field.readonly) {
    toast('That field is read only.', 'err');
    return;
  }
  if (field.kind === 'checkbox') {
    setField(field, !fieldIsTicked(field));      // a tick needs no dialog
    return;
  }
  if (field.kind === 'dropdown' || field.kind === 'listbox') {
    modal('Choose a value',
      '<div class="field"><label>' + escapeHtml(field.name) + '</label>' +
      '<select name="v">' + (field.options || []).map((o) =>
        '<option' + (o === field.value ? ' selected' : '') + '>' +
        escapeHtml(o) + '</option>').join('') + '</select></div>',
      async (v) => setField(field, v.v), 'Set');
    return;
  }
  modal('Fill in the field',
    '<div class="field"><label>' + escapeHtml(field.name) + '</label>' +
    '<input name="v" value="' + escapeHtml(field.value || '') + '"></div>' +
    (field.required ? '<p class="hint">This field is marked as required.</p>' : ''),
    async (v) => setField(field, v.v), 'Set');
}

function formInspector() {
  const fields = S.fields || [];
  let html = '<h3>form fields</h3>';
  if (!fields.length) {
    return html + '<p class="hint">No fields on this page. Drag a box to add one.</p>';
  }
  fields.forEach((f, i) => {
    const id = 'ff' + i;
    let control;
    if (f.kind === 'checkbox') {
      control = '<label class="chk"><input type="checkbox" id="' + id + '"' +
        (fieldIsTicked(f) ? ' checked' : '') + (f.readonly ? ' disabled' : '') +
        '> ticked</label>';
    } else if (f.kind === 'dropdown' || f.kind === 'listbox') {
      control = '<select id="' + id + '"' + (f.readonly ? ' disabled' : '') + '>' +
        (f.options || []).map((o) => '<option' + (o === f.value ? ' selected' : '') +
          '>' + escapeHtml(o) + '</option>').join('') + '</select>';
    } else {
      control = '<input id="' + id + '" value="' + escapeHtml(f.value || '') + '"' +
        (f.readonly ? ' disabled' : '') + '>';
    }
    html += '<div class="frow' + (f.name === S.selectedField ? ' on' : '') + '">' +
      '<div class="n"><span>' + escapeHtml(f.name) + '</span><em>' + f.kind +
      (f.required ? ' &middot; required' : '') + '</em></div>' + control + '</div>';
  });
  html += '<div class="field" style="margin-top:12px">' +
    '<button id="form-export" style="width:100%">Export values to CSV...</button></div>' +
    '<div class="field"><button id="form-import" style="width:100%">Import values from CSV...</button></div>' +
    '<div class="field"><button id="form-flatten" style="width:100%">Lock the filled values in</button></div>';
  return html;
}

function wireFormInspector() {
  (S.fields || []).forEach((f, i) => {
    const input = $('ff' + i);
    if (!input || f.readonly) return;
    input.onchange = () => setField(f, f.kind === 'checkbox' ? input.checked : input.value);
  });
  const ex = $('form-export');
  if (ex) ex.onclick = async () => {
    const r = await busyRun('Exporting...', 'form_export');
    if (r && r.path) toast('Saved ' + baseName(r.path) + ' with ' + r.fields + ' field(s).', 'ok');
  };
  const im = $('form-import');
  if (im) im.onclick = async () => {
    const r = await busyRun('Importing...', 'form_import');
    if (!r || r.cancelled) return;
    toast('Filled ' + r.updated + ' field(s)' +
      (r.missing && r.missing.length ? ', ' + r.missing.length + ' not found' : '') + '.', 'ok');
    await refresh(false);
    drawInspector();
  };
  const fl = $('form-flatten');
  if (fl) fl.onclick = async () => {
    if (!confirm('Lock the values in? The fields stop being editable.')) return;
    const r = await busyRun('Flattening...', 'form_flatten');
    if (r) { toast('Form locked.', 'ok'); await refresh(); }
  };
}

/* ---- moving and resizing an annotation ---- */

const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

function drawLineSelection(a) {
  // A line has no rectangle to resize: it has two ends. Round grips move each
  // end (so the point of an arrow can be aimed anywhere), and the square grip
  // in the middle slides the whole thing.
  const ov = $('overlay');
  let pts = a.points.map((p) => p.slice());

  const guide = el('div', 'lineguide');
  guide.innerHTML = '<svg width="100%" height="100%" style="overflow:visible">' +
    '<line stroke="#4c8dff" stroke-width="1.5" stroke-dasharray="4 3"/></svg>';
  Object.assign(guide.style, { left: '0', top: '0', width: '100%', height: '100%' });
  ov.appendChild(guide);
  const svgLine = guide.querySelector('line');

  const kill = el('button', 'linekill', '✕');
  kill.title = 'Delete (or press Delete)';
  kill.onclick = (e) => { e.stopPropagation(); deleteSelected(); };
  kill.onpointerdown = (e) => e.stopPropagation();
  ov.appendChild(kill);

  const grips = [
    el('div', 'endpoint'),
    el('div', 'endpoint tip'),
    el('div', 'linemove'),
  ];
  grips[0].title = 'Drag to move the tail';
  grips[1].title = 'Drag to aim the point';
  grips[2].title = 'Drag to move the whole arrow';
  grips.forEach((g) => ov.appendChild(g));

  const paint = () => {
    grips[0].style.left = toPx(pts[0][0]) + 'px';
    grips[0].style.top = toPx(pts[0][1]) + 'px';
    grips[1].style.left = toPx(pts[1][0]) + 'px';
    grips[1].style.top = toPx(pts[1][1]) + 'px';
    grips[2].style.left = toPx((pts[0][0] + pts[1][0]) / 2) + 'px';
    grips[2].style.top = toPx((pts[0][1] + pts[1][1]) / 2) + 'px';
    kill.style.left = toPx((pts[0][0] + pts[1][0]) / 2) + 22 + 'px';
    kill.style.top = toPx((pts[0][1] + pts[1][1]) / 2) + 'px';
    svgLine.setAttribute('x1', toPx(pts[0][0]));
    svgLine.setAttribute('y1', toPx(pts[0][1]));
    svgLine.setAttribute('x2', toPx(pts[1][0]));
    svgLine.setAttribute('y2', toPx(pts[1][1]));
  };
  paint();

  let drag = null;
  grips.forEach((grip, index) => {
    grip.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      e.preventDefault();
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* non-fatal */ }
      drag = { which: index, x: e.clientX, y: e.clientY, from: pts.map((p) => p.slice()) };
    });
    grip.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = toPt(e.clientX - drag.x);
      const dy = toPt(e.clientY - drag.y);
      if (drag.which === 2) {
        pts = drag.from.map((p) => [p[0] + dx, p[1] + dy]);
      } else {
        pts = drag.from.map((p) => p.slice());
        pts[drag.which] = [drag.from[drag.which][0] + dx, drag.from[drag.which][1] + dy];
      }
      paint();
    });
    const done = async () => {
      if (!drag) return;
      drag = null;
      const res = await run('annot_line_points', S.page, a.id, pts);
      if (res && res.id) S.selectedAnnot = res.id;   // rebuilt, so the id moved
      await refresh(false);
      await loadComments();
    };
    grip.addEventListener('pointerup', done);
    grip.addEventListener('pointercancel', done);
  });
}

function drawSelection(a) {
  if (a.points && a.points.length === 2) return drawLineSelection(a);
  const frame = el('div', 'selframe');
  const place = (r) => Object.assign(frame.style, {
    left: toPx(r[0]) + 'px', top: toPx(r[1]) + 'px',
    width: Math.max(6, toPx(r[2] - r[0])) + 'px',
    height: Math.max(6, toPx(r[3] - r[1])) + 'px',
  });
  place(a.rect);
  HANDLES.forEach((h) => {
    const grip = el('div', 'handle');
    grip.dataset.h = h;
    frame.appendChild(grip);
  });
  frame.title = a.is_signature ? 'Drag to move, corners to resize'
                               : 'Drag to move, corners to resize, double-click to edit';
  const kill = el('button', 'kill', '✕');
  kill.title = 'Delete (or press Delete)';
  kill.onclick = (e) => { e.stopPropagation(); deleteSelected(); };
  kill.onpointerdown = (e) => e.stopPropagation();
  frame.appendChild(kill);
  $('overlay').appendChild(frame);

  let drag = null;
  frame.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    e.preventDefault();
    // Capture keeps the drag alive if the cursor leaves the frame, but it
    // throws for a pointer the browser does not consider active -- never let
    // that stop the drag being set up.
    try { frame.setPointerCapture(e.pointerId); } catch (err) { /* non-fatal */ }
    drag = {
      handle: e.target.dataset.h || null,
      x: e.clientX, y: e.clientY,
      rect: a.rect.slice(),
    };
    if (!drag.handle) frame.classList.add('moving');
  });

  frame.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = toPt(e.clientX - drag.x);
    const dy = toPt(e.clientY - drag.y);
    const r = drag.rect.slice();
    if (!drag.handle) {
      r[0] += dx; r[2] += dx; r[1] += dy; r[3] += dy;
    } else {
      if (drag.handle.includes('w')) r[0] += dx;
      if (drag.handle.includes('e')) r[2] += dx;
      if (drag.handle.includes('n')) r[1] += dy;
      if (drag.handle.includes('s')) r[3] += dy;
    }
    if (r[2] - r[0] < 8 || r[3] - r[1] < 8) return;   // keep it grabbable
    drag.live = r;
    place(r);
  });

  const finish = async (e) => {
    if (!drag) return;
    const moved = drag.live;
    drag = null;
    frame.classList.remove('moving');
    if (!moved) return;
    await run('annot_update', S.page, a.id, moved);
    await refresh(false);
    await loadComments();
  };
  frame.addEventListener('pointerup', finish);
  frame.addEventListener('pointercancel', finish);

  frame.addEventListener('dblclick', (e) => {
    e.stopPropagation();
    if (!a.is_signature) editAnnot(a);
  });
}

/* ---- annotation comments ---- */

function closeBubble() {
  const open = document.querySelector('.bubble');
  if (open) open.remove();
}

function showBubble(a) {
  closeBubble();
  const bubble = el('div', 'bubble');
  const body = a.content
    ? escapeHtml(a.content)
    : '<i style="color:var(--muted)">No text on this ' + escapeHtml(a.type) + '.</i>';
  bubble.innerHTML =
    '<span class="x" title="Close">✕</span>' +
    '<div class="who"><b>' + escapeHtml(a.author || 'Unsigned') + '</b>' +
    '<span>' + escapeHtml(a.type) + '</span></div>' +
    '<div class="txt">' + body + '</div>' +
    '<div class="acts"><button data-edit>Edit</button>' +
    '<button data-del class="danger">Delete</button></div>';

  const left = Math.min(toPx(a.rect[0]) + 22, Math.max(0, $('overlay').clientWidth - 290));
  bubble.style.left = Math.max(0, left) + 'px';
  bubble.style.top = (toPx(a.rect[3]) + 6) + 'px';
  $('overlay').appendChild(bubble);

  bubble.querySelector('.x').onclick = () => { S.selectedAnnot = null; closeBubble(); buildOverlay(); };
  bubble.querySelector('[data-edit]').onclick = () => editAnnot(a);
  bubble.querySelector('[data-del]').onclick = () => deleteAnnot(a);
}

async function selectAnnot(id, fromPage) {
  S.selectedAnnot = id;
  if (!fromPage) showPane('comments');
  await loadComments();
  await buildOverlay();
  const row = document.querySelector('.cmt.on');
  if (row) row.scrollIntoView({ block: 'nearest' });
}

function editAnnot(a) {
  modal('Edit comment',
    '<div class="field"><label>Comment</label><textarea name="text" rows="5">' +
    escapeHtml(a.content || '') + '</textarea></div>' +
    '<div class="field"><label>Author</label><input name="author" value="' +
    escapeHtml(a.author || '') + '"></div>',
    async (v) => {
      await run('annot_update', S.page, a.id, null, v.text, null, null, null, v.author);
      await refresh(false);
      await loadComments();
      await buildOverlay();
    }, 'Save');
}

async function deleteSelected(silent) {
  // Undo covers this, so a key press removes the object outright rather than
  // stopping to ask.
  if (S.selectedAnnot) {
    const id = S.selectedAnnot;
    const found = (S.annots || []).find((a) => a.id === id);
    S.selectedAnnot = null;
    closeBubble();
    const r = await run('annot_delete', S.page, id);
    if (!r) return false;
    await refresh(false);
    await loadComments();
    if (!silent) toast('Deleted ' + ((found && found.type) || 'it') +
      ' — Ctrl+Z to put it back.', 'ok');
    return true;
  }
  if (S.selectedField) {
    const name = S.selectedField;
    if (!confirm('Remove the form field "' + name + '"?')) return false;
    S.selectedField = null;
    const r = await run('form_delete', S.page, name);
    if (!r) return false;
    await refresh(false);
    drawInspector();
    if (!silent) toast('Removed the field — Ctrl+Z to put it back.', 'ok');
    return true;
  }
  return false;
}


async function deleteAnnot(a) {
  if (!confirm('Delete this ' + a.type + '?')) return;
  await run('annot_delete', S.page, a.id);
  if (S.selectedAnnot === a.id) S.selectedAnnot = null;
  closeBubble();
  await refresh(false);
  await loadComments();
}

/* ---- in-place text editing ---- */

function cancelEdit() {
  if (!S.editing) return;
  S.editing.box.remove();
  if (S.editing.bar) S.editing.bar.remove();
  S.editing = null;
}

function openEditor(rect, value, multiline, onSave, sizePt) {
  cancelEdit();
  const box = el('textarea', 'editor');
  box.value = value;
  const pad = 4;
  // Match the text being replaced rather than a fixed size, and centre it in
  // the box: a span's bbox spans ascender to descender, so text laid out from
  // the top edge sits visibly high.
  const fontPx = Math.max(9, toPx(sizePt || S.fontSize));
  const boxH = Math.max(fontPx + 10, toPx(rect[3] - rect[1]) + pad * 2);
  const inner = boxH - 8;                       // minus 2px border + 2px padding each side
  Object.assign(box.style, {
    left: toPx(rect[0]) - pad + 'px',
    top: toPx(rect[1]) - pad + 'px',
    width: Math.max(90, toPx(rect[2] - rect[0]) + pad * 3) + 'px',
    height: boxH + 'px',
    fontSize: fontPx + 'px',
    lineHeight: multiline ? 1.3 : inner + 'px',
    paddingTop: multiline ? '3px' : '0',
  });
  $('overlay').appendChild(box);

  const bar = el('div', 'editbar',
    '<button data-save class="primary">Save</button><button data-cancel>Cancel</button>');
  bar.style.left = toPx(rect[0]) - pad + 'px';
  bar.style.top = toPx(rect[3]) + 8 + 'px';
  $('overlay').appendChild(bar);

  S.editing = { box, bar };
  box.focus();
  box.setSelectionRange(value.length, value.length);

  const commit = async () => {
    const text = box.value;
    cancelEdit();
    if (text === value) return;
    await onSave(text);
  };
  bar.querySelector('[data-save]').onclick = commit;
  bar.querySelector('[data-cancel]').onclick = cancelEdit;
  box.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === 'Escape') { cancelEdit(); }
    else if (e.key === 'Enter' && (!multiline || e.ctrlKey)) { e.preventDefault(); commit(); }
  };
}

function editSpan(sp) {
  openEditor(sp.bbox, sp.text, false, async (text) => {
    const res = await busyRun('Rewriting text…', 'edit_span', sp.id, text);
    if (res && res.shrunk) toast('Text was narrowed slightly to fit the line.');
    await refresh(false);
  }, sp.size);
}

function editBlock(b) {
  const first = b.lines[0] && b.lines[0].spans[0];
  openEditor(b.bbox, b.text, true, async (text) => {
    const res = await busyRun('Re-typesetting paragraph…', 'edit_block', b.id, text);
    if (res && res.overflow) toast('New text is longer than the original space.', 'err');
    else if (res && res.shrunk) toast('Text was shrunk slightly to fit.');
    await refresh(false);
  }, first ? first.size : null);
}

/* ---- pointer driven tools ---- */

let drag = null;

$('overlay').addEventListener('pointerdown', (ev) => {
  if (!S.info || S.tool === 'select' || S.tool === 'text') return;
  if (ev.target.closest('.annothit, .selframe, .handle, .bubble')) return;
  if (ev.target.classList.contains('hit')) return;
  const p = localPoint(ev);
  $('overlay').setPointerCapture(ev.pointerId);

  if (S.tool === 'note') { addNote(p); return; }

  if (S.tool === 'ink') {
    drag = { kind: 'ink', points: [[toPt(p.x), toPt(p.y)]] };
    const ctx = $('scratch').getContext('2d');
    ctx.beginPath(); ctx.moveTo(p.x, p.y);
    ctx.lineWidth = S.lineWidth * S.scale;
    ctx.strokeStyle = rgbCss(S.stroke);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    return;
  }

  const marquee = el('div', 'marquee');
  $('overlay').appendChild(marquee);
  drag = { kind: 'rect', x0: p.x, y0: p.y, node: marquee };
});

$('overlay').addEventListener('pointermove', (ev) => {
  if (!drag) return;
  const p = localPoint(ev);
  if (drag.kind === 'ink') {
    drag.points.push([toPt(p.x), toPt(p.y)]);
    const ctx = $('scratch').getContext('2d');
    ctx.lineTo(p.x, p.y); ctx.stroke();
    return;
  }
  const x = Math.min(drag.x0, p.x), y = Math.min(drag.y0, p.y);
  const w = Math.abs(p.x - drag.x0), h = Math.abs(p.y - drag.y0);
  Object.assign(drag.node.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px' });
});

$('overlay').addEventListener('pointerup', async (ev) => {
  if (!drag) return;
  const d = drag; drag = null;
  const p = localPoint(ev);

  if (d.kind === 'ink') {
    $('scratch').getContext('2d').clearRect(0, 0, $('scratch').width, $('scratch').height);
    if (d.points.length > 2) {
      await run('annot_ink', S.page, [d.points], S.stroke, S.lineWidth);
      await refresh(false);
    }
    return;
  }

  d.node.remove();
  const rect = [toPt(Math.min(d.x0, p.x)), toPt(Math.min(d.y0, p.y)),
                toPt(Math.max(d.x0, p.x)), toPt(Math.max(d.y0, p.y))];
  if (rect[2] - rect[0] < 3 || rect[3] - rect[1] < 3) return;
  await applyTool(rect);
});

async function applyTool(rect) {
  switch (S.tool) {
    case 'highlight': case 'underline': case 'strikeout':
      await run('annot_markup', S.page, S.tool, [rect], S.color); break;
    case 'rect': case 'circle': case 'arrow': {
      const pts = [[rect[0], rect[1]], [rect[2], rect[3]]];
      await run('annot_shape', S.page, S.tool, pts, S.stroke, null, S.lineWidth); break;
    }
    case 'stamp':
      await run('annot_stamp', S.page, rect, S.stamp, S.stampColor); break;
    case 'sign': {
      if (!S.sig) { toast('Pick a signature in the Signatures panel first.', 'err'); return; }
      const placed = await run('sig_place', S.page, S.sig, rect);
      await refresh(false);
      if (placed && placed.id) {
        setTool('select');
        await selectAnnot(placed.id);
        toast('Drag to reposition, handles to resize. Lock it with Protect ▸ Flatten.');
      }
      return;
    }
    case 'redact':
      await run('redact_mark', S.page, [rect]);
      S.redactions.push({ page: S.page, rect });
      break;
    case 'addtext':
      return promptAddText(rect);
    case 'link':
      return promptLink(rect);
    case 'field':
      return promptField(rect);
    case 'imagebox': {
      const r = await run('annot_image', S.page, rect);
      if (r && r.cancelled) return;
      setTool('select');
      break;
    }
    case 'crop':
      await run('page_crop', S.page, rect);
      setTool('select');
      break;
    default: return;
  }
  await refresh(false);
}

function rgbCss(c) {
  return 'rgb(' + c.map((v) => Math.round(v * 255)).join(',') + ')';
}

async function addNote(p) {
  modal('Add a note', '<div class="field"><label>Note</label><textarea name="text" rows="4"></textarea></div>' +
    '<div class="field"><label>Author</label><input name="author" value=""></div>',
    async (v) => {
      if (!v.text.trim()) return;
      const made = await run('annot_note', S.page, [toPt(p.x), toPt(p.y)], v.text, v.author);
      await refresh(false);
      if (made && made.id) { setTool('select'); await selectAnnot(made.id); }
    }, 'Add note');
}

function promptAddText(rect) {
  modal('Add a text box',
    '<div class="field"><label>Text</label><textarea name="text" rows="4"></textarea></div>' +
    '<div class="row"><div class="field"><label>Size</label><input name="size" type="number" value="11"></div>' +
    '<div class="field"><label>Align</label><select name="align">' +
    '<option value="0">left</option><option value="1">centre</option>' +
    '<option value="2">right</option></select></div></div>' +
    '<div class="field"><label><input type="checkbox" name="permanent" style="width:auto"> ' +
    'Draw straight into the page (cannot be moved afterwards)</label></div>' +
    '<p class="hint">Otherwise the box stays selectable: drag to move, use the ' +
    'handles to resize, double-click to change the words.</p>',
    async (v) => {
      if (!v.text.trim()) return;
      if (v.permanent) {
        await busyRun('Adding text…', 'add_text', S.page, rect, v.text, 'Calibri',
          parseFloat(v.size) || 11, [0, 0, 0], 'left', false, false);
        await refresh(false);
        return;
      }
      const made = await run('annot_textbox', S.page, rect, v.text,
        parseFloat(v.size) || 11, [0, 0, 0], null, null,
        parseInt(v.align, 10) || 0);
      await refresh(false);
      if (made && made.id) { setTool('select'); await selectAnnot(made.id); }
    }, 'Add');
}

function promptLink(rect) {
  modal('Add a link', '<div class="field"><label>Web address</label><input name="uri" placeholder="https://…"></div>' +
    '<div class="field"><label>…or jump to page</label><input name="page" type="number" min="1"></div>',
    async (v) => {
      const target = v.page ? parseInt(v.page, 10) - 1 : null;
      await run('link_add', S.page, rect, v.uri || '', target);
      await refresh(false);
    }, 'Add link');
}

function promptField(rect) {
  modal('Add a form field',
    '<div class="field"><label>Field name</label><input name="name"></div>' +
    '<div class="field"><label>Type</label><select name="kind"><option value="text">Text box</option>' +
    '<option value="checkbox">Check box</option><option value="dropdown">Dropdown</option></select></div>' +
    '<div class="field"><label>Options (dropdown, comma separated)</label><input name="options"></div>',
    async (v) => {
      if (!v.name.trim()) { toast('The field needs a name.', 'err'); return; }
      const opts = v.options ? v.options.split(',').map((s) => s.trim()).filter(Boolean) : [];
      await run('form_add', S.page, v.kind, rect, v.name, '', opts);
      await refresh(false);
    }, 'Add field');
}

/* ---------- document tabs ---------- */

async function refreshTabs() {
  const state = await run('tab_list');
  if (!state) return;
  const host = $('tabs');
  host.innerHTML = '';
  state.tabs.forEach((t) => {
    const tab = el('div', 'dtab' + (t.active ? ' on' : ''));
    tab.title = t.path || t.name;
    tab.innerHTML = (t.dirty ? '<span class="dot"></span>' : '') +
      '<span class="nm">' + escapeHtml(t.name) +
      (t.open ? ' <small>(' + t.page_count + ')</small>' : '') + '</span>' +
      '<span class="cl" title="Close">✕</span>';
    tab.onclick = (e) => {
      if (e.target.classList.contains('cl')) return closeTab(t.index);
      if (!t.active) switchTab(t.index);
    };
    host.appendChild(tab);
  });
}

function resetPerDocumentState() {
  S.page = 0;
  S.hits = [];
  S.hitIndex = -1;
  S.findQuery = '';
  S.layout = null;
  S.redactions = [];
  S.annots = [];
  S.selectedAnnot = null;
  S.compare = null;
  S.comparePage = -1;
  S.fields = [];
  S.selectedField = null;
  S.sizes = null;                      // zoom mode is a preference, so it stays
  cancelEdit();
  closeBubble();
  const box = $('findq');
  if (box) box.value = '';
}

async function switchTab(index) {
  const res = await run('tab_switch', index);
  if (!res) return;
  resetPerDocumentState();
  setInfo(res.info);
  await refresh();
}

async function closeTab(index) {
  const state = await run('tab_list');
  const tab = state && state.tabs[index];
  if (tab && tab.dirty &&
      !confirm('"' + tab.name + '" has unsaved changes. Close it anyway?')) return;
  const res = await run('tab_close', index);
  if (!res) return;
  resetPerDocumentState();
  setInfo(res.info && res.info.open ? res.info : { open: false });
  await refresh();
}

/* ---------- sidebar panes ---------- */

const THUMB_BATCH = 16;

async function loadThumbs() {
  if (!S.info) return;
  const pane = $('pane-thumbs');
  pane.innerHTML = '';
  S.thumbLoaded = 0;

  // Placeholders first, so the strip has its full height immediately and the
  // scrollbar does not jump as pictures arrive. Rendering every page up front
  // is what made opening a long document feel slow.
  const sizes = S.sizes || (S.sizes = await run('page_sizes')) || [];
  for (let i = 0; i < S.info.page_count; i++) {
    const size = sizes[i] || { width: 612, height: 792 };
    const slot = el('div', 'thumb');
    slot.dataset.page = i;
    slot.style.height = Math.round(170 * (size.height / size.width)) + 'px';
    slot.innerHTML = '<span>' + (i + 1) + '</span>';
    slot.onclick = () => gotoPage(i);
    wireThumbDrag(slot);
    pane.appendChild(slot);
  }
  markThumb();
  await fillThumbs();
  watchThumbs();
}

async function fillThumbs() {
  if (!S.info || S.thumbBusy) return;
  S.thumbBusy = true;
  try {
    const pane = $('pane-thumbs');
    const slots = [...pane.querySelectorAll('.thumb:not([data-done])')];
    const needed = slots.filter((slot) => {
      const box = slot.getBoundingClientRect();
      const view = pane.getBoundingClientRect();
      return box.bottom > view.top - 400 && box.top < view.bottom + 400;
    }).slice(0, THUMB_BATCH);
    if (!needed.length) return;

    // Ask for one contiguous run: the bridge round trip costs more than the
    // rendering does.
    const first = parseInt(needed[0].dataset.page, 10);
    const last = parseInt(needed[needed.length - 1].dataset.page, 10);
    const batch = await run('thumbs', first, last - first + 1, 170);
    (batch || []).forEach((t) => {
      const slot = pane.querySelector('.thumb[data-page="' + t.index + '"]');
      if (!slot || slot.dataset.done) return;
      slot.dataset.done = '1';
      slot.style.height = '';
      slot.innerHTML = '<img src="' + t.image + '" alt="Page ' + (t.index + 1) +
        '"><span>' + (t.index + 1) + '</span>';
    });
  } finally {
    S.thumbBusy = false;
  }
}

let thumbTimer = null;
function watchThumbs() {
  const pane = $('pane-thumbs');
  if (pane.dataset.watching) return;
  pane.dataset.watching = '1';
  pane.addEventListener('scroll', () => {
    clearTimeout(thumbTimer);
    thumbTimer = setTimeout(fillThumbs, 90);
  });
}

// ---- reordering pages by dragging a thumbnail ----
let dragPage = null;

function wireThumbDrag(slot) {
  slot.draggable = true;
  slot.addEventListener('dragstart', (e) => {
    dragPage = parseInt(slot.dataset.page, 10);
    slot.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', String(dragPage)); } catch (err) { /* ignore */ }
  });
  slot.addEventListener('dragend', () => {
    slot.classList.remove('dragging');
    document.querySelectorAll('.thumb').forEach((t) =>
      t.classList.remove('drop-before', 'drop-after'));
    dragPage = null;
  });
  slot.addEventListener('dragover', (e) => {
    if (dragPage === null) return;
    e.preventDefault();
    const box = slot.getBoundingClientRect();
    const after = (e.clientY - box.top) > box.height / 2;
    slot.classList.toggle('drop-after', after);
    slot.classList.toggle('drop-before', !after);
  });
  slot.addEventListener('dragleave', () =>
    slot.classList.remove('drop-before', 'drop-after'));
  slot.addEventListener('drop', async (e) => {
    e.preventDefault();
    if (dragPage === null) return;
    const box = slot.getBoundingClientRect();
    const after = (e.clientY - box.top) > box.height / 2;
    let target = parseInt(slot.dataset.page, 10) + (after ? 1 : 0);
    const from = dragPage;
    dragPage = null;
    document.querySelectorAll('.thumb').forEach((t) =>
      t.classList.remove('drop-before', 'drop-after'));
    if (target === from || target === from + 1) return;

    // Build the whole new order and send it once, so the result is exactly
    // what was dropped rather than depending on insert-before semantics.
    const order = [];
    for (let i = 0; i < S.info.page_count; i++) if (i !== from) order.push(i);
    if (target > from) target -= 1;
    order.splice(target, 0, from);
    const r = await run('page_reorder', order);
    if (!r) return;
    S.page = order.indexOf(from);
    await refresh();
    toast('Moved page ' + (from + 1) + ' to position ' + (S.page + 1) + '.', 'ok');
  });
}


function markThumb() {
  document.querySelectorAll('.thumb').forEach((n) => {
    n.classList.toggle('on', parseInt(n.dataset.page, 10) === S.page);
  });
}

async function loadOutline() {
  const pane = $('pane-outline');
  if (!S.info) { pane.innerHTML = ''; return; }
  const items = await run('outline');
  pane.innerHTML = '';
  if (!items || !items.length) {
    pane.innerHTML = '<p class="hint">This document has no bookmarks.</p>';
    return;
  }
  items.forEach((it) => {
    const node = el('div', 'item',
      '<div class="t" style="padding-left:' + (it.level - 1) * 10 + 'px">' +
      escapeHtml(it.title) + '</div><div class="s">page ' + (it.page + 1) + '</div>');
    node.onclick = () => gotoPage(it.page);
    pane.appendChild(node);
  });
}

async function loadComments() {
  const pane = $('pane-comments');
  if (!S.info) { pane.innerHTML = ''; return; }
  const items = await run('annot_list', S.page) || [];
  pane.innerHTML = '';
  if (!items.length) {
    pane.innerHTML = '<p class="hint">Nothing marked up on this page yet. ' +
      'Use the note, highlight, shape or stamp tools, then click anything on ' +
      'the page to read it.</p>';
    await loadLinks();
    return;
  }
  items.forEach((a) => {
    const row = el('div', 'cmt' + (a.id === S.selectedAnnot ? ' on' : ''));
    row.innerHTML =
      '<div class="hdr"><span class="kind">' + escapeHtml(a.type) + '</span>' +
      '<span>' + escapeHtml(a.author || 'unsigned') + '</span></div>' +
      '<div class="body' + (a.content ? '' : ' empty') + '">' +
      (a.content ? escapeHtml(a.content) : 'no text') + '</div>' +
      '<div class="acts"><button data-edit>Edit</button>' +
      '<button data-del>Delete</button></div>';
    row.onclick = (e) => {
      if (e.target.dataset.edit !== undefined) return editAnnot(a);
      if (e.target.dataset.del !== undefined) return deleteAnnot(a);
      selectAnnot(a.id);          // selecting is not deleting
    };
    pane.appendChild(row);
  });
  await loadLinks();
}

async function loadLinks() {
  const pane = $('pane-comments');
  if (!S.info) return;
  const links = (await run('link_list', S.page)) || [];
  if (!links.length) return;
  const head = el('div', 'pane-sub', 'Links on this page');
  pane.appendChild(head);
  links.forEach((l) => {
    const row = el('div', 'lrow');
    const target = l.uri ? l.uri : 'Go to page ' + (l.page + 1);
    row.innerHTML = '<span class="t" title="' + escapeHtml(target) + '">' +
      escapeHtml(target) + '</span><span class="x" title="Remove">&#10005;</span>';
    row.querySelector('.t').onclick = () => {
      if (!l.uri && l.page >= 0) gotoPage(l.page);
    };
    row.querySelector('.x').onclick = async () => {
      await run('link_delete', S.page, l.index);
      await refresh(false);
      loadComments();
    };
    pane.appendChild(row);
  });
}


async function loadSigs() {
  const list = $('sig-list');
  const items = await run('sig_list');
  list.innerHTML = '';
  if (!items || !items.length) {
    list.innerHTML = '<p class="hint">No signatures saved yet.</p>';
    return;
  }
  items.forEach((s) => {
    const card = el('div', 'sig-card' + (S.sig === s.id ? ' on' : ''));
    card.innerHTML = '<img src="' + s.image + '"><div><div class="n">' +
      escapeHtml(s.name) + '</div><div class="r">' + escapeHtml(s.role || '') +
      '</div></div><button class="rn" title="Rename">&#9998;</button>' +
      '<button class="x" title="Delete">&#10005;</button>';
    card.onclick = (e) => {
      if (e.target.classList.contains('x') || e.target.classList.contains('rn')) return;
      S.sig = (S.sig === s.id) ? null : s.id;
      loadSigs();
      if (S.sig) { setTool('sign'); toast('Now drag a box where the signature should go.'); }
    };
    card.querySelector('.rn').onclick = (e) => {
      e.stopPropagation();
      modal('Rename signature',
        '<div class="field"><label>Name</label><input name="name" value="' +
        escapeHtml(s.name) + '"></div>' +
        '<div class="field"><label>Role</label><input name="role" value="' +
        escapeHtml(s.role || '') + '"></div>',
        async (v) => {
          if (!v.name.trim()) { toast('Give it a name.', 'err'); return; }
          await run('sig_rename', s.id, v.name, v.role);
          loadSigs();
        }, 'Save');
    };
    card.querySelector('.x').onclick = async (e) => {
      e.stopPropagation();
      if (!confirm('Delete the signature "' + s.name + '"?')) return;
      await run('sig_delete', s.id);
      if (S.sig === s.id) S.sig = null;
      loadSigs();
    };
    list.appendChild(card);
  });
}

/* ---------- inspector ---------- */

const TONES = {
  'Warm ivory': [1, 0.99, 0.94],
  'Soft grey': [0.95, 0.95, 0.96],
  'Pale blue': [0.94, 0.97, 1],
  'Pale green': [0.94, 0.99, 0.94],
  'White': [1, 1, 1],
};

// Tools that leave existing objects clickable rather than drawing over them.
const SELECTING_TOOLS = ['select', 'sign'];

// Every stamp the document format defines, with a readable label.
const STAMPS = [
  ['approved', 'Approved'], ['notapproved', 'Not approved'],
  ['draft', 'Draft'], ['final', 'Final'],
  ['confidential', 'Confidential'], ['topsecret', 'Top secret'],
  ['forcomment', 'For comment'], ['forpublicrelease', 'For public release'],
  ['notforpublicrelease', 'Not for public release'],
  ['experimental', 'Experimental'], ['expired', 'Expired'],
  ['sold', 'Sold'], ['asis', 'As is'], ['departmental', 'Departmental'],
];

const SWATCHES = [
  [0.85, 0.12, 0.12], [0.90, 0.45, 0.05], [1, 0.82, 0.16], [0.10, 0.55, 0.25],
  [0.10, 0.45, 0.85], [0.35, 0.28, 0.72], [0.72, 0.25, 0.55], [0.18, 0.20, 0.26],
  [1, 0.92, 0.23], [0.45, 0.85, 0.40], [0.40, 0.75, 1], [1, 0.55, 0.75],
];

function swatchHtml(current) {
  return '<div class="swatches">' + SWATCHES.map((c, i) =>
    '<div class="swatch' + (JSON.stringify(c) === JSON.stringify(current) ? ' on' : '') +
    '" data-i="' + i + '" style="background:' + rgbCss(c) + '"></div>').join('') + '</div>';
}

function compareInspector() {
  const r = S.compare;
  const s = r.summary || {};
  const pages = r.pages.filter((p) => p.state !== 'same');
  let html = '<h3>comparison</h3>' +
    '<div class="cmp-head"><b>' + (s.identical ? 'No differences' :
      (pages.length + ' page' + (pages.length === 1 ? '' : 's') + ' differ')) + '</b>' +
    '<span class="against">against ' + escapeHtml(r.against || 'the other document') + '</span></div>' +
    '<div class="cmp-stats">' +
      '<span class="plus">+' + (s.added_words || 0) + ' words</span>' +
      '<span class="minus">−' + (s.removed_words || 0) + ' words</span>' +
      (s.pages_added ? '<span class="plus">+' + s.pages_added + ' pages</span>' : '') +
      (s.pages_removed ? '<span class="minus">−' + s.pages_removed + ' pages</span>' : '') +
    '</div>';

  pages.forEach((p, i) => {
    const where = p.right !== null ? 'Page ' + (p.right + 1)
                                   : 'Page ' + (p.left + 1) + ' (removed)';
    const bits = (p.changes || []).slice(0, 3).map((c) =>
      (c.before ? '<del>' + escapeHtml(c.before.slice(0, 60)) + '</del> ' : '') +
      (c.after ? '<ins>' + escapeHtml(c.after.slice(0, 60)) + '</ins>' : '')).join('<br>');
    html += '<div class="cmp-row' + (p.right === S.comparePage ? ' on' : '') +
      '" data-page="' + (p.right === null ? -1 : p.right) + '">' +
      '<div class="p">' + where + ' <span class="cmp-state ' + p.state + '">' +
      p.state + '</span></div>' +
      (bits ? '<div class="s">' + bits + '</div>' : '') + '</div>';
  });

  html += '<div class="field" style="margin-top:12px"><button id="cmp-mark" style="width:100%">' +
    'Highlight these in the document</button></div>' +
    '<div class="field"><button id="cmp-clear" style="width:100%">Clear comparison</button></div>';
  return html;
}

function drawInspector() {
  const body = $('insp-body');
  const t = S.tool;

  if (S.compare) {
    body.innerHTML = compareInspector();
    body.querySelectorAll('.cmp-row').forEach((row) => {
      row.onclick = async () => {
        const page = parseInt(row.dataset.page, 10);
        if (page < 0) return;
        S.comparePage = page;
        await gotoPage(page);
        drawInspector();
      };
    });
    $('cmp-mark').onclick = async () => {
      const r = await busyRun('Marking up…', 'compare_markup');
      if (r) { toast('Added ' + r.marked + ' marks.', 'ok'); await refresh(false); }
    };
    $('cmp-clear').onclick = async () => {
      await run('compare_clear');
      S.compare = null;
      S.comparePage = -1;
      drawInspector();
      await drawPage();
    };
    return;
  }

  let html = '<h3>' + t + '</h3>';

  if (['highlight', 'underline', 'strikeout'].includes(t)) {
    html += '<div class="field"><label>Colour</label>' + swatchHtml(S.color) + '</div>';
  } else if (['ink', 'rect', 'circle', 'arrow'].includes(t)) {
    html += '<div class="field"><label>Colour</label>' + swatchHtml(S.stroke) + '</div>' +
      '<div class="field"><label>Line width</label><input id="lw" type="range" min="0.5" max="8" step="0.5" value="' + S.lineWidth + '"></div>';
  } else if (t === 'stamp') {
    html += '<div class="field"><label>Stamp</label><select id="stampsel">' +
      STAMPS.map((s) => '<option value="' + s[0] + '"' +
        (s[0] === S.stamp ? ' selected' : '') + '>' + s[1] + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label>Colour</label>' + swatchHtml(S.stampColor) + '</div>' +
      '<p class="hint">Drag a box on the page to place it.</p>';
  } else if (t === 'sign') {
    html += '<p class="hint">Choose a signature in the <b>Signatures</b> panel, ' +
      'then drag a box on the page.</p>' +
      '<p class="hint">Already placed one? Click it to get a frame — drag to ' +
      'move, corners to resize. Lock it for good with ' +
      '<b>Protect ▸ Flatten annotations</b>.</p>';
  } else if (t === 'text') {
    html += '<p class="hint">Click any text to rewrite that run. Click the <b>¶</b> marker to the left of a paragraph to rewrite the whole paragraph with re-wrapping.</p>';
  } else if (t === 'redact') {
    html += '<p class="hint">Drag over anything to mark it, or mark every ' +
      'occurrence of a word below. Marks are only a preview until they are applied.</p>' +
      '<div class="field"><label>Mark every occurrence of</label>' +
      '<input id="redact-find" placeholder="e.g. an account number"></div>' +
      '<div class="field"><button id="redact-all" style="width:100%">Mark all matches</button></div>' +
      '<div class="field"><button id="redact-undo" style="width:100%">Clear all marks</button></div>' +
      '<button id="redact-now" class="danger" style="width:100%">Apply redactions — permanent</button>';
  } else if (t === 'field') {
    body.innerHTML = formInspector();
    wireFormInspector();
    return;
  } else if (t === 'select') {
    html += '<p class="hint">Pick a tool from the toolbar. Use <b>T</b> to edit ' +
      'existing text in place.</p>' +
      (S.fields && S.fields.length
        ? '<p class="hint">This page has ' + S.fields.length + ' form field(s). ' +
          'Click one to fill it in.</p>'
        : '');
  } else {
    html += '<p class="hint">Drag on the page to place.</p>';
  }
  body.innerHTML = html;

  body.querySelectorAll('.swatch').forEach((sw) => {
    sw.onclick = () => {
      const c = SWATCHES[parseInt(sw.dataset.i, 10)];
      if (['highlight', 'underline', 'strikeout'].includes(S.tool)) S.color = c;
      else if (S.tool === 'stamp') S.stampColor = c;
      else S.stroke = c;
      drawInspector();
    };
  });
  const lw = $('lw'); if (lw) lw.oninput = () => { S.lineWidth = parseFloat(lw.value); };
  const ss = $('stampsel'); if (ss) ss.onchange = () => { S.stamp = ss.value; };
  const rn = $('redact-now'); if (rn) rn.onclick = applyRedactions;
  const ra = $('redact-all');
  if (ra) ra.onclick = async () => {
    const needle = $('redact-find').value.trim();
    if (!needle) { toast('Type the text to mark first.', 'err'); return; }
    const r = await busyRun('Marking…', 'redact_search', needle);
    if (!r) return;
    if (!r.marked) { toast('No matches found.', 'err'); return; }
    toast('Marked ' + r.marked + ' occurrence(s). Apply when ready.', 'ok');
    await refresh(false);
  };
  const ru = $('redact-undo');
  if (ru) ru.onclick = async () => {
    const r = await run('redact_clear');
    if (r) { S.redactions = []; toast('Marks cleared.'); await refresh(false); }
  };
}

/* ---------- navigation ---------- */

async function gotoPage(index) {
  if (!S.info) return;
  if (S.page !== index) { S.selectedAnnot = null; closeBubble(); }
  S.page = Math.max(0, Math.min(index, S.info.page_count - 1));
  await drawPage();
  loadComments();
}

function setTool(tool) {
  S.tool = tool;
  if (tool === 'sign') {          // take the user to the signatures, not a dead end
    showPane('sigs');
    loadSigs();
  }
  document.querySelectorAll('.tool').forEach((b) =>
    b.classList.toggle('active', b.dataset.tool === tool));
  drawInspector();
  // The form panel lists fields that buildOverlay fetches, so draw again once
  // they arrive -- otherwise switching to the field tool shows an empty panel
  // on a document that plainly has fields.
  Promise.resolve(buildOverlay()).then(() => {
    if (S.tool === 'field') drawInspector();
  });
}

/* ---------- menu actions ---------- */

const ACTIONS = {
  open: async () => {
    resetPerDocumentState();
    setInfo(await busyRun('Opening…', 'open_dialog'));
    await refresh();
  },
  create: async () => { setInfo(await busyRun('Building PDF…', 'create_from_files')); await refresh(); },
  save: async () => { const r = await busyRun('Saving…', 'save'); if (r && !r.cancelled) { setInfo(r); toast('Saved.', 'ok'); } },
  saveas: async () => { const r = await busyRun('Saving…', 'save_as'); if (r && !r.cancelled) { setInfo(r); toast('Saved.', 'ok'); } },
  close: async () => {
    await run('close_doc');
    resetPerDocumentState();
    setInfo({ open: false });
    ['outline', 'comments', 'sigs'].forEach((p) => {
      if (p !== 'sigs') $('pane-' + p).innerHTML = '';
    });
    $('insp-body').innerHTML = '';
    await buildOverlay();
  },
  undo: async () => { setInfo(await run('undo')); await refresh(); },
  redo: async () => { setInfo(await run('redo')); await refresh(); },
  find: () => openFind(),
  replace: () => promptReplace(),
  compare: async () => {
    const state = await run('tab_list');
    const others = (state ? state.tabs : []).filter((t) => t.open && !t.active);
    const options = others.map((t) =>
      '<option value="' + t.index + '">' + escapeHtml(t.name) + '</option>').join('');
    modal('Compare with',
      '<div class="field"><label>Compare this document against</label>' +
      '<select name="source">' + options +
      '<option value="file">A file on disk…</option></select></div>' +
      '<div class="field"><label>Which is the earlier version?</label>' +
      '<select name="older"><option value="other">The other one</option>' +
      '<option value="current">This one</option></select></div>' +
      '<div class="field"><label><input type="checkbox" name="visual" style="width:auto" checked> ' +
      'Also look for changes to graphics and layout</label></div>' +
      '<p class="hint">Differences are marked on whichever document is the newer ' +
      'of the two &mdash; green for added text, a dashed outline where the page ' +
      'itself looks different.</p>',
      async (v) => {
        const r = v.source === 'file'
          ? await busyRun('Comparing…', 'compare_file', v.visual, v.older)
          : await busyRun('Comparing…', 'compare_tab', parseInt(v.source, 10), v.visual, v.older);
        if (!r || r.cancelled) return;
        S.compare = r;
        const s = r.summary;
        if (s.identical) toast('No differences found.', 'ok');
        else toast('+' + s.added_words + ' / −' + s.removed_words + ' words across ' +
          (s.pages_changed + s.pages_added + s.pages_removed) + ' page(s).', 'ok');
        setTool('select');
        drawInspector();
        await drawPage();
      }, 'Compare');
  },
  props: () => promptProps(),
  about: () => showAbout(),
  defaultapp: async () => {
    const st = await run('default_app_status');
    if (!st) return;
    if (!st.packaged) {
      modal('Not available in the development build',
        '<p class="hint">Registering would point Windows at python.exe. ' +
        'Run the built <b>PlatenPDF.exe</b> and try again.</p>', null);
      return;
    }
    if (st.is_default) {
      modal('Already the default',
        '<p class="hint">Platen PDF already opens .pdf files on this account.</p>' +
        '<div class="field"><label>Remove the association?</label></div>',
        async () => {
          await run('unregister_file_types');
          toast('Removed. Windows will fall back to another PDF app.', 'ok');
        }, 'Remove');
      return;
    }
    modal('Set as default PDF app',
      '<p class="hint">Windows does not let an application make itself the ' +
      'default — that choice is yours to confirm. Platen PDF will be added to ' +
      'the list of PDF apps, then the Windows <b>Default apps</b> screen opens ' +
      'so you can pick it.</p>' +
      '<p class="hint">Currently opening PDFs: <b>' +
      escapeHtml(st.current_handler || 'not set') + '</b></p>' +
      '<p class="hint">This affects your Windows account only and needs no ' +
      'administrator rights.</p>',
      async () => {
        const r = await busyRun('Registering…', 'register_file_types');
        if (!r) return;
        toast('Added. Choose Platen PDF under Default apps to finish.', 'ok');
      }, 'Register and open Settings');
  },

  rotateL: async () => { await run('page_rotate', [S.page], -90); await refresh(); },
  rotateR: async () => { await run('page_rotate', [S.page], 90); await refresh(); },
  insert: () => modal('Insert a blank page',
    '<div class="field"><label>Size</label><select name="paper"><option>letter</option><option>legal</option>' +
    '<option>a4</option><option>a3</option><option>tabloid</option></select></div>' +
    '<div class="field"><label><input type="checkbox" name="landscape" style="width:auto"> Landscape</label></div>',
    async (v) => { await run('page_insert', S.page + 1, v.paper, v.landscape); await refresh(); }, 'Insert'),
  duplicate: async () => { await run('page_duplicate', [S.page]); await refresh(); },
  delete: async () => {
    if (!confirm('Delete page ' + (S.page + 1) + '?')) return;
    await run('page_delete', [S.page]); await refresh();
  },
  merge: async () => { await busyRun('Merging…', 'page_merge', S.page + 1); await refresh(); },
  mergetab: async () => {
    const state = await run('tab_list');
    if (!state) return;
    const others = state.tabs.filter((t) => t.open && !t.active);
    if (!others.length) {
      toast('Open another document in a second tab first.', 'err');
      return;
    }
    modal('Merge an open tab',
      '<div class="field"><label>Take all pages from</label><select name="src">' +
      others.map((t) => '<option value="' + t.index + '">' + escapeHtml(t.name) +
        ' (' + t.page_count + ' pages)</option>').join('') + '</select></div>' +
      '<div class="field"><label>Insert at page</label><input name="at" type="number" min="1" value="' +
      (S.info ? S.info.page_count + 1 : 1) + '"></div>',
      async (v) => {
        const at = Math.max(0, (parseInt(v.at, 10) || 1) - 1);
        const r = await busyRun('Merging…', 'merge_tab', parseInt(v.src, 10), at);
        if (r) { toast('Added ' + r.added + ' pages.', 'ok'); await refresh(); }
      }, 'Merge');
  },
  split: () => modal('Split document',
    '<div class="field"><label>Mode</label><select name="mode"><option value="every">Every N pages</option>' +
    '<option value="ranges">Page ranges</option></select></div>' +
    '<div class="field"><label>Pages per file</label><input name="size" type="number" value="1"></div>' +
    '<div class="field"><label>Ranges (e.g. 1-3,5,8-10)</label><input name="ranges"></div>',
    async (v) => {
      const r = await busyRun('Splitting…', 'page_split', v.mode, parseInt(v.size, 10) || 1, v.ranges);
      if (r && r.count) toast('Wrote ' + r.count + ' files.', 'ok');
    }, 'Split'),
  extract: () => modal('Extract pages',
    '<div class="field"><label>Pages</label><input name="pages" value="' +
    (S.page + 1) + '" placeholder="e.g. 1-3,7"></div>' +
    '<div class="field"><label><input type="checkbox" name="remove" style="width:auto"> ' +
    'Delete these pages from this document</label></div>' +
    '<div class="field"><label><input type="checkbox" name="save" style="width:auto" checked> ' +
    'Also save them to a file</label></div>' +
    '<p class="hint">The extracted pages open in a new tab either way.</p>',
    async (v) => {
      const idx = parseRanges(v.pages, S.info.page_count);
      if (!idx.length) { toast('No valid pages.', 'err'); return; }
      if (v.remove && idx.length >= S.info.page_count) {
        toast('That is every page — the document would be empty.', 'err');
        return;
      }
      if (v.remove && !confirm('Remove ' + idx.length +
          ' page(s) from this document after extracting them?')) return;
      const r = await busyRun('Extracting…', 'page_extract', idx, v.remove, v.save, true);
      if (!r || r.cancelled) return;
      resetPerDocumentState();
      if (r.info) setInfo(r.info);
      await refresh();
      toast('Extracted ' + r.pages + ' page(s) into a new tab' +
        (r.removed ? ', and removed them here' : '') +
        (r.path ? ', saved as ' + baseName(r.path) : '') + '.', 'ok');
    }, 'Extract'),
  cropstart: () => { setTool('crop'); toast('Drag the area to keep.'); },
  cropreset: async () => { await run('page_reset_crop', S.page); await refresh(); },

  watermark: () => modal('Watermark',
    '<div class="field"><label>Text</label><input name="text" value="CONFIDENTIAL"></div>' +
    '<div class="row"><div class="field"><label>Size</label><input name="size" type="number" value="48"></div>' +
    '<div class="field"><label>Angle</label><input name="angle" type="number" value="45"></div></div>' +
    '<div class="field"><label>Opacity</label><input name="opacity" type="range" min="0.05" max="1" step="0.05" value="0.25"></div>' +
    '<div class="field"><label><input type="checkbox" name="behind" style="width:auto"> Place behind the text</label></div>',
    async (v) => {
      await busyRun('Applying…', 'watermark', v.text, null, parseFloat(v.size),
        [0.6, 0.6, 0.6], parseFloat(v.opacity), parseFloat(v.angle), v.behind);
      await refresh();
    }, 'Apply'),
  wmimage: async () => { await busyRun('Applying…', 'watermark_image', null, 0.5, true); await refresh(); },
  numbers: () => modal('Page numbers',
    '<div class="field"><label>Format</label><input name="template" value="Page {page} of {pages}"></div>' +
    '<div class="field"><label>Position</label><select name="position">' +
    ['bottom-center', 'bottom-right', 'bottom-left', 'top-center', 'top-right', 'top-left']
      .map((p) => '<option>' + p + '</option>').join('') + '</select></div>' +
    '<div class="field"><label>Start numbering at</label><input name="start" type="number" value="1"></div>' +
    '<div class="field"><label><input type="checkbox" name="skip" style="width:auto"> Skip the first page</label></div>',
    async (v) => {
      await busyRun('Numbering…', 'stamp_text', v.template, v.position, null, 9,
        [0.2, 0.2, 0.2], 36, parseInt(v.start, 10) || 1, v.skip);
      await refresh();
    }, 'Apply'),
  headfoot: () => modal('Header / footer',
    '<div class="field"><label>Text</label><input name="template" placeholder="Willmore Capital Partners"></div>' +
    '<div class="field"><label>Position</label><select name="position">' +
    ['top-left', 'top-center', 'top-right', 'bottom-left', 'bottom-center', 'bottom-right']
      .map((p) => '<option>' + p + '</option>').join('') + '</select></div>',
    async (v) => {
      if (!v.template.trim()) { toast('Type the text to place first.', 'err'); return; }
      await busyRun('Applying…', 'stamp_text', v.template, v.position, null, 9);
      await refresh();
    }, 'Apply'),
  bg: () => modal('Background colour',
    '<div class="field"><label>Colour</label><select name="tone">' +
    Object.keys(TONES).map((k) => '<option>' + k + '</option>').join('') +
    '</select></div><p class="hint">Applied behind the existing page content.</p>',
    async (v) => { await run('background', null, TONES[v.tone] || [1, 1, 1]); await refresh(); }, 'Apply'),
  signature: async () => {
    showPane('sigs');
    await loadSigs();
    const saved = await run('sig_list');
    if (!saved || !saved.length) {
      toast('Add a signature image first, using the button in the panel.');
      return;
    }
    toast('Pick a signature, then drag a box on the page.');
  },
  image: () => { setTool('imagebox'); toast('Drag a box where the image should go.'); },
  bookmarks: () => promptBookmarks(),

  docx: async () => { const r = await busyRun('Converting to Word…', 'export_docx'); if (r && r.path) toast('Saved ' + baseName(r.path), 'ok'); },
  xlsx: async () => {
    const r = await busyRun('Converting to Excel…', 'export_xlsx');
    if (!r || !r.path) return;
    toast(r.tables
      ? 'Saved ' + baseName(r.path) + ' — ' + r.tables + ' table(s), numbers kept as numbers'
      : 'No tables found, so ' + baseName(r.path) + ' holds the page text instead', 'ok');
  },
  pptx: () => modal('Convert to PowerPoint',
    '<div class="field"><label>How</label><select name="mode">' +
    '<option value="editable">Editable text over the page graphics</option>' +
    '<option value="image">Exact picture of each page</option>' +
    '<option value="text">Text boxes only, blank slides</option></select></div>' +
    '<p class="hint"><b>Editable</b> keeps tables, rules and logos as the slide ' +
    'background and puts real text on top, so the wording can be changed. ' +
    '<b>Picture</b> looks exactly like the PDF but nothing can be edited.</p>',
    async (v) => {
      const r = await busyRun('Converting…', 'export_pptx', v.mode);
      if (!r || !r.path) return;
      toast('Saved ' + baseName(r.path) + ' — ' + r.slides + ' slides' +
        (r.resized_pages ? ', ' + r.resized_pages + ' page(s) fitted to the slide size' : ''), 'ok');
    }, 'Convert'),
  images: () => modal('Export as images',
    '<div class="row"><div class="field"><label>Resolution (DPI)</label><input name="dpi" type="number" value="200"></div>' +
    '<div class="field"><label>Format</label><select name="fmt"><option>png</option><option>jpg</option></select></div></div>',
    async (v) => { const r = await busyRun('Rendering…', 'export_images', parseInt(v.dpi, 10) || 200, v.fmt); if (r && r.count) toast('Wrote ' + r.count + ' images.', 'ok'); }, 'Export'),
  text: async () => { const r = await busyRun('Extracting…', 'export_text'); if (r && r.path) toast('Saved ' + baseName(r.path), 'ok'); },
  ocr: () => modal('Recognise text (OCR)',
    '<p class="hint">Reads text from scanned pages and adds an invisible text ' +
    'layer, so the page looks identical but becomes searchable and selectable. ' +
    'The engine is built in — nothing to install.</p>' +
    '<div class="field"><label>Quality</label><select name="dpi">' +
    '<option value="160">Fast (160 DPI)</option>' +
    '<option value="220" selected>Balanced (220 DPI)</option>' +
    '<option value="300">Best (300 DPI)</option></select></div>' +
    '<div class="field"><label><input type="checkbox" name="force" style="width:auto"> ' +
    'Re-recognise pages that already have text</label></div>',
    async (v) => {
      const r = await busyRun('Recognising text…', 'ocr_run', 'eng',
        parseInt(v.dpi, 10) || 220, v.force);
      if (!r) return;
      toast('Read ' + r.blocks + ' text blocks across ' + r.pages + ' page(s)' +
        (r.skipped ? ', skipped ' + r.skipped + ' that already had text' : '') + '.', 'ok');
      await refresh();
    }, 'Recognise'),
  compress: () => modal('Compress',
    '<div class="field"><label>Level</label><select name="level"><option value="low">Light</option>' +
    '<option value="medium" selected>Balanced</option><option value="high">Strong</option></select></div>',
    async (v) => {
      const r = await busyRun('Compressing…', 'compress', v.level);
      if (r && r.path) {
        const mb = (n) => (n / 1048576).toFixed(2) + ' MB';
        toast('Saved ' + baseName(r.path) + (r.before ? ' — ' + mb(r.before) + ' → ' + mb(r.size) : ''), 'ok');
      }
    }, 'Compress'),

  protect: () => modal('Encrypt with a password',
    '<div class="field"><label>Password to open (optional)</label><input name="user" type="password"></div>' +
    '<div class="field"><label>Password to change permissions</label><input name="owner" type="password"></div>' +
    '<div class="field"><label>Still allow</label></div>' +
    ['print', 'copy', 'annotate', 'forms'].map((p) =>
      '<div class="field"><label><input type="checkbox" name="' + p + '" style="width:auto" checked> ' + p + '</label></div>').join(''),
    async (v) => {
      const allowed = ['print', 'copy', 'annotate', 'forms'].filter((p) => v[p]);
      const r = await busyRun('Encrypting…', 'protect', v.user, v.owner, allowed);
      if (r && r.path) toast('Encrypted copy saved.', 'ok');
    }, 'Encrypt'),
  unprotect: async () => { const r = await busyRun('Saving…', 'unprotect'); if (r && r.path) toast('Unprotected copy saved.', 'ok'); },
  flatten: async () => {
    if (!confirm('Flatten all annotations into the page? They can no longer be edited.')) return;
    await busyRun('Flattening…', 'annot_flatten', false); await refresh();
  },
  flattenforms: async () => {
    if (!confirm('Flatten all form fields? Values become permanent page content.')) return;
    await busyRun('Flattening…', 'form_flatten'); await refresh();
  },
  redactapply: () => applyRedactions(),
  redactclear: async () => { await run('redact_clear'); S.redactions = []; await refresh(); },
};

function baseName(p) { return String(p).split(/[\\/]/).pop(); }

function parseRanges(text, max) {
  const out = new Set();
  String(text).split(',').forEach((chunk) => {
    chunk = chunk.trim();
    if (!chunk) return;
    if (chunk.includes('-')) {
      const [a, b] = chunk.split('-').map((n) => parseInt(n, 10));
      for (let i = a; i <= b; i++) if (i >= 1 && i <= max) out.add(i - 1);
    } else {
      const n = parseInt(chunk, 10);
      if (n >= 1 && n <= max) out.add(n - 1);
    }
  });
  return [...out].sort((a, b) => a - b);
}

async function applyRedactions() {
  if (!confirm('Apply redactions? The marked content is deleted permanently.')) return;
  const r = await busyRun('Redacting…', 'redact_apply', true);
  if (r) { S.redactions = []; toast('Redacted ' + r.pages + ' pages.', 'ok'); await refresh(); }
}

/* ---------- find ---------- */

function showPane(name) {
  document.querySelectorAll('.tab').forEach((x) =>
    x.classList.toggle('active', x.dataset.pane === name));
  document.querySelectorAll('.pane').forEach((x) => x.classList.remove('active'));
  $('pane-' + name).classList.add('active');
}

function openFind() {
  showPane('find');
  const box = $('findq');
  box.focus();
  box.select();
}

async function runSearch() {
  const query = $('findq').value;
  const matchCase = $('findcase').checked;
  S.findQuery = query;
  if (!query.trim()) {
    S.hits = []; S.hitIndex = -1;
    $('findlist').innerHTML = '';
    $('findcount').textContent = 'Type to search';
    buildOverlay();
    return;
  }
  const res = await run('search', query, matchCase);
  if (!res) return;
  S.hits = res.hits || [];
  S.hitIndex = S.hits.length ? 0 : -1;
  renderHits(res.truncated);
  if (S.hits.length) await goToHit(0);
  else buildOverlay();
}

function highlightSnippet(snippet, query) {
  // Index based rather than regex: the query is user text and may contain
  // characters that would otherwise need escaping.
  if (!query) return escapeHtml(snippet);
  const hay = snippet.toLowerCase();
  const needle = query.toLowerCase();
  let out = '';
  let at = 0;
  for (;;) {
    const i = hay.indexOf(needle, at);
    if (i < 0) break;
    out += escapeHtml(snippet.slice(at, i)) +
      '<mark>' + escapeHtml(snippet.slice(i, i + needle.length)) + '</mark>';
    at = i + needle.length;
  }
  return out + escapeHtml(snippet.slice(at));
}


function renderHits(truncated) {
  const list = $('findlist');
  list.innerHTML = '';
  $('findcount').textContent = S.hits.length
    ? (S.hitIndex + 1) + ' of ' + S.hits.length + (truncated ? '+' : '')
    : 'No matches';
  if (!S.hits.length) return;

  if (S.compare) {
    const forPage = S.compare.pages.find((p) => p.right === S.page);
    if (forPage) {
      const paint = (rects, cls) => (rects || []).forEach((r) => {
        const mark = el('div', cls);
        Object.assign(mark.style, {
          left: toPx(r[0]) - 1 + 'px', top: toPx(r[1]) - 1 + 'px',
          width: toPx(r[2] - r[0]) + 2 + 'px',
          height: toPx(r[3] - r[1]) + 2 + 'px',
        });
        ov.appendChild(mark);
      });
      paint(forPage.visual_rects, 'diff-visual');
      paint(forPage.added_rects, 'diff-add');
    }
  }

  S.hits.forEach((h, i) => {
    const row = el('div', 'findrow' + (i === S.hitIndex ? ' on' : ''),
      '<div class="p">Page ' + (h.page + 1) + '</div>' +
      '<div class="s">' + highlightSnippet(h.snippet || '', S.findQuery) + '</div>');
    row.onclick = () => goToHit(i);
    list.appendChild(row);
  });
}

async function goToHit(index) {
  if (!S.hits.length) return;
  S.hitIndex = (index + S.hits.length) % S.hits.length;
  const hit = S.hits[S.hitIndex];
  renderHits(false);
  const row = $('findlist').children[S.hitIndex];
  if (row) row.scrollIntoView({ block: 'nearest' });
  if (hit.page !== S.page) await gotoPage(hit.page);
  else await buildOverlay();
  const node = document.querySelector('.searchhit.on');
  if (node) node.scrollIntoView({ block: 'center', inline: 'center' });
}

function promptReplace() {
  modal('Find and replace',
    '<div class="field"><label>Find</label><input name="find"></div>' +
    '<div class="field"><label>Replace with</label><input name="rep"></div>' +
    '<div class="field"><label><input type="checkbox" name="case" style="width:auto" checked> Match case</label></div>' +
    '<p class="hint">Replacements happen inside a single styled run at a time; anything split across styling is reported as skipped.</p>',
    async (v) => {
      if (!v.find) return;
      const r = await busyRun('Replacing…', 'replace_all', v.find, v.rep, v.case);
      if (r) {
        toast('Replaced ' + r.replaced + (r.skipped ? ', skipped ' + r.skipped : ''), r.replaced ? 'ok' : 'err');
        await refresh();
      }
    }, 'Replace all');
}

async function showAbout() {
  const a = await run('about');
  if (!a) return;
  const parts = a.components.map(
    (c) => '<tr><td>' + escapeHtml(c.name) + '</td><td>' +
           escapeHtml(c.licence) + '</td></tr>').join('');
  modal('About ' + escapeHtml(a.name),
    '<p class="about-v">Version ' + escapeHtml(a.version) + '</p>' +
    '<p>' + escapeHtml(a.copyright) + '</p>' +
    '<p class="about-lic">' + escapeHtml(a.licence) + '<br>' +
    'Source code: <b>' + escapeHtml(a.source) + '</b></p>' +
    '<p class="hint">Built on open-source components, which keep their own ' +
    'licences:</p>' +
    '<table class="about-l"><tbody>' + parts + '</tbody></table>', null, 'OK');
}

async function promptProps() {
  const i = S.info || {};
  modal('Document properties',
    '<div class="field"><label>Title</label><input name="title" value="' + escapeHtml(i.title || '') + '"></div>' +
    '<div class="field"><label>Author</label><input name="author" value="' + escapeHtml(i.author || '') + '"></div>' +
    '<div class="field"><label>Subject</label><input name="subject" value="' + escapeHtml(i.subject || '') + '"></div>' +
    '<div class="field"><label>Keywords</label><input name="keywords" value="' + escapeHtml(i.keywords || '') + '"></div>' +
    '<p class="hint">' + (i.page_count || 0) + ' pages' + (i.path ? ' • ' + escapeHtml(i.path) : '') + '</p>',
    async (v) => { setInfo(await run('set_metadata', v)); }, 'Save');
}

async function promptBookmarks() {
  const items = await run('outline') || [];
  const rows = items.map((it, n) =>
    '<div class="field"><input name="b' + n + '" value="' + escapeHtml(it.title) +
    '" data-page="' + it.page + '" data-level="' + it.level + '"></div>').join('');
  modal('Bookmarks',
    (rows || '<p class="hint">No bookmarks yet.</p>') +
    '<div class="field"><label>Add a bookmark for the current page</label><input name="add" placeholder="Title"></div>',
    async (v) => {
      const next = items.map((it, n) => ({ level: it.level, title: v['b' + n] || it.title, page: it.page }));
      if (v.add && v.add.trim()) next.push({ level: 1, title: v.add.trim(), page: S.page });
      await run('set_outline', next);
      loadOutline();
    }, 'Save');
}

/* ---------- wiring ---------- */

document.querySelectorAll('.menu').forEach((m) => {
  m.querySelector('span').onclick = (e) => {
    e.stopPropagation();
    const open = m.classList.contains('open');
    document.querySelectorAll('.menu').forEach((x) => x.classList.remove('open'));
    if (!open) m.classList.add('open');
  };
  m.querySelectorAll('li[data-act]').forEach((li) => {
    li.onclick = async () => {
      m.classList.remove('open');
      const fn = ACTIONS[li.dataset.act];
      if (!fn) return;
      if (!S.info && !['open', 'create', 'defaultapp', 'about'].includes(li.dataset.act)) {
        toast('Open a document first.', 'err');
        return;
      }
      await fn();
    };
  });
});
document.body.addEventListener('click', () =>
  document.querySelectorAll('.menu').forEach((m) => m.classList.remove('open')));

document.querySelectorAll('.tool').forEach((b) => {
  b.onclick = () => setTool(b.dataset.tool);
});
document.querySelectorAll('.tab').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.pane').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    $('pane-' + b.dataset.pane).classList.add('active');
    if (b.dataset.pane === 'sigs') loadSigs();
    if (b.dataset.pane === 'comments') loadComments();
    if (b.dataset.pane === 'find') $('findq').focus();
  };
});

$('empty-open').onclick = ACTIONS.open;
$('tabadd').onclick = ACTIONS.open;
$('sig-add').onclick = () => modal('Add a signature',
  '<div class="field"><label>Name</label><input name="name" placeholder="Ernie Willmore"></div>' +
  '<div class="field"><label>Role (optional)</label><input name="role" placeholder="Managing Partner"></div>' +
  '<div class="field"><label><input type="checkbox" name="clean" style="width:auto" checked> Remove the white paper background</label></div>' +
  '<p class="hint">You will be asked for the image file next. A photo or scan of a signature on white paper works well.</p>',
  async (v) => {
    if (!v.name.trim()) { toast('Give the signature a name.', 'err'); return; }
    const r = await run('sig_add_dialog', v.name, v.role, v.clean);
    if (r && !r.cancelled) { toast('Signature saved.', 'ok'); loadSigs(); }
  }, 'Choose image…');

let findTimer = null;
$('findq').oninput = () => {
  clearTimeout(findTimer);
  findTimer = setTimeout(runSearch, 280);
};
$('findq').onkeydown = (e) => {
  if (e.key === 'Enter') { e.preventDefault(); clearTimeout(findTimer);
    if (S.hits.length && $('findq').value === S.findQuery) goToHit(S.hitIndex + (e.shiftKey ? -1 : 1));
    else runSearch(); }
  if (e.key === 'Escape') { $('findq').value = ''; runSearch(); }
};
$('findcase').onchange = runSearch;
$('findnext').onclick = () => goToHit(S.hitIndex + 1);
$('findprev').onclick = () => goToHit(S.hitIndex - 1);

$('prev').onclick = () => gotoPage(S.page - 1);
$('next').onclick = () => gotoPage(S.page + 1);
$('pagenum').onchange = () => gotoPage((parseInt($('pagenum').value, 10) || 1) - 1);
function syncZoomLabel() {
  const pick = $('zoompick');
  if (pick) pick.value = S.zoomMode === 'fixed' ? 'fixed' : S.zoomMode;
  const label = $('zoomlabel');
  if (label) label.textContent = Math.round(S.zoom * 100) + '%';
}

function setZoom(mode, value) {
  S.zoomMode = mode;
  if (mode === 'fixed' && value) S.zoom = Math.max(0.1, Math.min(6, value));
  drawPage();
}

$('zoomin').onclick = () => setZoom('fixed', S.zoom * 1.2);
$('zoomout').onclick = () => setZoom('fixed', S.zoom / 1.2);
$('zoomfit').onclick = () => setZoom(S.zoomMode === 'width' ? 'page' : 'width');
const zoompick = $('zoompick');
if (zoompick) {
  zoompick.onchange = () => {
    const v = zoompick.value;
    if (v === 'width' || v === 'page') setZoom(v);
    else setZoom('fixed', parseFloat(v));
  };
}

/* ---------- the right-hand panel ---------- */
// It is empty or near-empty for most of the time people spend in the program,
// so it folds away and gives the width to the document. The choice is
// remembered; being asked to re-hide it on every launch would be worse than
// not having the button.

function setInspector(collapsed, redraw) {
  const panel = $('inspector');
  const button = $('insp-toggle');
  if (!panel || !button) return;
  panel.classList.toggle('collapsed', collapsed);
  button.setAttribute('aria-expanded', String(!collapsed));
  button.title = collapsed ? 'Show panel' : 'Hide panel';
  try {
    localStorage.setItem('inspectorCollapsed', collapsed ? '1' : '0');
  } catch (e) {
    // Private browsing or blocked storage: the panel still works, it just
    // forgets. Not worth failing the toggle over.
  }
  // The stage is a different width now. A page drawn to fit has to be redrawn
  // against the new width, and no resize event fires for a layout change.
  if (redraw && S.info && S.zoomMode !== 'fixed') drawPage();
}

function restoreInspector() {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem('inspectorCollapsed') === '1';
  } catch (e) { /* see above */ }
  setInspector(collapsed, false);
}

if ($('insp-toggle')) {
  $('insp-toggle').onclick = () => {
    setInspector(!$('inspector').classList.contains('collapsed'), true);
  };
}
restoreInspector();

// Re-fit when the window changes size, which is what maximising does.
let resizeTimer = null;
window.addEventListener('resize', () => {
  if (!S.info || S.zoomMode === 'fixed') return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => drawPage(), 110);
});

/* ---------- mouse wheel ---------- */
// Ctrl+wheel zooms. Plain wheel scrolls the page, and rolls on to the next or
// previous page once there is nothing left to scroll -- which is immediately
// when the whole page already fits.

let zoomTimer = null;
let pageFlipAt = 0;

function queueZoom() {
  clearTimeout(zoomTimer);
  zoomTimer = setTimeout(() => drawPage(), 90);
  syncZoomLabel();
}

$('viewer').addEventListener('wheel', (e) => {
  if (!S.info) return;

  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    S.zoomMode = 'fixed';
    const step = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    S.zoom = Math.max(0.15, Math.min(6, S.zoom * step));
    queueZoom();
    return;
  }

  const view = $('viewer');
  const slack = view.scrollHeight - view.clientHeight;
  const atTop = view.scrollTop <= 1;
  const atBottom = view.scrollTop >= slack - 1;
  const forward = e.deltaY > 0;

  if (slack > 2 && !(forward ? atBottom : atTop)) return;   // normal scrolling

  const now = Date.now();
  if (now - pageFlipAt < 320) { e.preventDefault(); return; }  // one page per gesture
  const next = S.page + (forward ? 1 : -1);
  if (next < 0 || next >= S.info.page_count) return;          // let the edges rest
  e.preventDefault();
  pageFlipAt = now;
  gotoPage(next).then(() => {
    // Enter the new page from the edge the reader is travelling towards.
    view.scrollTop = forward ? 0 : Math.max(0, view.scrollHeight - view.clientHeight);
  });
}, { passive: false });

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault(); openFind(); return;
  }
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key.toLowerCase() === 'o') { e.preventDefault(); ACTIONS.open(); }
  else if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); ACTIONS.save(); }
  else if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); ACTIONS.undo(); }
  else if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); ACTIONS.redo(); }
  else if (ctrl && e.key.toLowerCase() === 'f') { e.preventDefault(); openFind(); }
  // An arrow nudges the selection when there is one, and turns the page when
  // there is not. This has to be tested before the paging branches below,
  // which would otherwise swallow Left and Right.
  else if (e.key.startsWith('Arrow') && S.selectedAnnot && !S.editing) {
    e.preventDefault();
    nudgeSelected(e.key, e.shiftKey ? 10 : 1);
  }
  else if (e.key === 'PageDown' || e.key === 'ArrowRight') gotoPage(S.page + 1);
  else if (e.key === 'PageUp' || e.key === 'ArrowLeft') gotoPage(S.page - 1);
  else if (e.key === 'Delete' || e.key === 'Backspace') {
    if (S.editing) return;
    if (S.selectedAnnot || S.selectedField) { e.preventDefault(); deleteSelected(); }
  }
  else if (e.key === 'Escape') { cancelEdit(); setTool('select'); }
});

window.openOnStart = async (path) => {
  setInfo(await busyRun('Opening…', 'open_path', path));
  await refresh();
};

ready().then(async () => {
  drawInspector();
  loadSigs();
  const s = await run('ping');
  if (s && s.ocr && !s.ocr.available) {
    console.warn('OCR engine missing from this build.');
  }
  refreshTabs();
  const pending = await run('pending_open');
  if (pending && pending.path) await window.openOnStart(pending.path);
});
