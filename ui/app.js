/* PDF Studio front end. Talks to the Python backend through pywebview. */

const S = {
  info: null,
  page: 0,
  zoom: 1.25,
  fit: false,
  tool: 'select',
  layout: null,
  scale: 1,          // rendered pixels per PDF point
  sig: null,
  color: [1, 0.92, 0.23],
  stroke: [0.85, 0.1, 0.1],
  lineWidth: 2,
  stamp: 'approved',
  fontSize: 11,
  redactions: [],
  editing: null,
  hits: [],
  hitIndex: -1,
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

async function run(name, ...args) {
  try {
    return await call(name, ...args);
  } catch (err) {
    toast(err.message, 'err');
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
  setInfo(await run('doc_info'));
  refreshTabs();
  if (!S.info) return;
  await drawPage();
  if (full) { loadThumbs(); loadOutline(); loadComments(); }
}

async function drawPage() {
  if (!S.info) return;
  if (S.fit) {
    const sizes = await run('page_sizes');
    if (sizes && sizes[S.page]) {
      const avail = $('viewer').clientWidth - 60;
      S.zoom = Math.max(0.2, Math.min(4, avail / sizes[S.page].width));
    }
  }
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
  $('zoomlabel').textContent = Math.round(S.zoom * 100) + '%';
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

async function buildOverlay() {
  const ov = $('overlay');
  ov.innerHTML = '';
  ov.className = '';
  cancelEdit();
  if (!S.info) return;

  if (S.tool === 'text') {
    ov.classList.add('textmode');
    S.layout = await run('text_layout', S.page);
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
      await run('annot_stamp', S.page, rect, S.stamp); break;
    case 'sign':
      if (!S.sig) { toast('Pick a signature in the Signatures panel first.', 'err'); return; }
      await run('sig_place', S.page, S.sig, rect); break;
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
      await run('annot_note', S.page, [toPt(p.x), toPt(p.y)], v.text, v.author);
      await refresh(false);
    }, 'Add note');
}

function promptAddText(rect) {
  modal('Add text', '<div class="field"><label>Text</label><textarea name="text" rows="4"></textarea></div>' +
    '<div class="row"><div class="field"><label>Size</label><input name="size" type="number" value="11"></div>' +
    '<div class="field"><label>Align</label><select name="align"><option>left</option><option>center</option>' +
    '<option>right</option><option>justify</option></select></div></div>' +
    '<div class="row"><div class="field"><label><input type="checkbox" name="bold" style="width:auto"> Bold</label></div>' +
    '<div class="field"><label><input type="checkbox" name="italic" style="width:auto"> Italic</label></div></div>',
    async (v) => {
      if (!v.text.trim()) return;
      await busyRun('Adding text…', 'add_text', S.page, rect, v.text, 'Calibri',
        parseFloat(v.size) || 11, [0, 0, 0], v.align, v.bold, v.italic);
      await refresh(false);
    }, 'Add text');
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
  S.fit = false;
  cancelEdit();
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

async function loadThumbs() {
  if (!S.info) return;
  const pane = $('pane-thumbs');
  const data = await run('thumbs', 0, S.info.page_count, 170);
  if (!data) return;
  pane.innerHTML = '';
  data.forEach((t) => {
    const node = el('div', 'thumb');
    node.dataset.page = t.index;
    node.innerHTML = '<img src="' + t.image + '"><span>' + (t.index + 1) + '</span>';
    node.onclick = () => gotoPage(t.index);
    pane.appendChild(node);
  });
  markThumb();
}

function markThumb() {
  document.querySelectorAll('.thumb').forEach((n) => {
    n.classList.toggle('on', parseInt(n.dataset.page, 10) === S.page);
  });
}

async function loadOutline() {
  const pane = $('pane-outline');
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
  const items = await run('annot_list', S.page);
  pane.innerHTML = '';
  if (!items || !items.length) {
    pane.innerHTML = '<p class="hint">No annotations on this page.</p>';
    return;
  }
  items.forEach((a) => {
    const node = el('div', 'item',
      '<div class="t">' + a.type + (a.content ? ': ' + escapeHtml(a.content.slice(0, 40)) : '') + '</div>' +
      '<div class="s">' + (a.author || 'unsigned') + '</div>');
    node.onclick = async () => {
      if (confirm('Delete this ' + a.type + '?')) {
        await run('annot_delete', S.page, a.id);
        await refresh(false); loadComments();
      }
    };
    pane.appendChild(node);
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
      '</div></div><button class="x" title="Delete">✕</button>';
    card.onclick = (e) => {
      if (e.target.classList.contains('x')) return;
      S.sig = (S.sig === s.id) ? null : s.id;
      loadSigs();
      if (S.sig) { setTool('sign'); toast('Now drag a box where the signature should go.'); }
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

const SWATCHES = [
  [1, 0.92, 0.23], [0.45, 0.85, 0.4], [0.4, 0.75, 1], [1, 0.55, 0.75],
  [0.85, 0.1, 0.1], [0.1, 0.1, 0.1], [0.1, 0.45, 0.9], [0.55, 0.3, 0.8],
];

function swatchHtml(current) {
  return '<div class="swatches">' + SWATCHES.map((c, i) =>
    '<div class="swatch' + (JSON.stringify(c) === JSON.stringify(current) ? ' on' : '') +
    '" data-i="' + i + '" style="background:' + rgbCss(c) + '"></div>').join('') + '</div>';
}

function drawInspector() {
  const body = $('insp-body');
  const t = S.tool;
  let html = '<h3>' + t + '</h3>';

  if (['highlight', 'underline', 'strikeout'].includes(t)) {
    html += '<div class="field"><label>Colour</label>' + swatchHtml(S.color) + '</div>';
  } else if (['ink', 'rect', 'circle', 'arrow'].includes(t)) {
    html += '<div class="field"><label>Colour</label>' + swatchHtml(S.stroke) + '</div>' +
      '<div class="field"><label>Line width</label><input id="lw" type="range" min="0.5" max="8" step="0.5" value="' + S.lineWidth + '"></div>';
  } else if (t === 'stamp') {
    html += '<div class="field"><label>Stamp</label><select id="stampsel">' +
      ['approved', 'draft', 'final', 'confidential', 'expired', 'sold', 'notapproved']
        .map((s) => '<option' + (s === S.stamp ? ' selected' : '') + '>' + s + '</option>').join('') +
      '</select></div>';
  } else if (t === 'sign') {
    html += '<p class="hint">Choose a signature in the <b>Signatures</b> panel, then drag a box on the page.</p>';
  } else if (t === 'text') {
    html += '<p class="hint">Click any text to rewrite that run. Click the <b>¶</b> marker to the left of a paragraph to rewrite the whole paragraph with re-wrapping.</p>';
  } else if (t === 'redact') {
    html += '<p class="hint">Drag over anything to mark it. Marks are previewed in black; choose <b>Protect ▸ Apply redactions</b> to delete the underlying content permanently.</p>' +
      '<button id="redact-now" class="danger" style="width:100%">Apply redactions</button>';
  } else if (t === 'select') {
    html += '<p class="hint">Pick a tool from the toolbar. Use <b>T</b> to edit existing text in place.</p>';
  } else {
    html += '<p class="hint">Drag on the page to place.</p>';
  }
  body.innerHTML = html;

  body.querySelectorAll('.swatch').forEach((sw) => {
    sw.onclick = () => {
      const c = SWATCHES[parseInt(sw.dataset.i, 10)];
      if (['highlight', 'underline', 'strikeout'].includes(S.tool)) S.color = c;
      else S.stroke = c;
      drawInspector();
    };
  });
  const lw = $('lw'); if (lw) lw.oninput = () => { S.lineWidth = parseFloat(lw.value); };
  const ss = $('stampsel'); if (ss) ss.onchange = () => { S.stamp = ss.value; };
  const rn = $('redact-now'); if (rn) rn.onclick = applyRedactions;
}

/* ---------- navigation ---------- */

async function gotoPage(index) {
  if (!S.info) return;
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
  buildOverlay();
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
  close: async () => { await run('close_doc'); setInfo({ open: false }); },
  undo: async () => { setInfo(await run('undo')); await refresh(); },
  redo: async () => { setInfo(await run('redo')); await refresh(); },
  find: () => openFind(),
  replace: () => promptReplace(),
  props: () => promptProps(),
  defaultapp: async () => {
    const st = await run('default_app_status');
    if (!st) return;
    if (!st.packaged) {
      modal('Not available in the development build',
        '<p class="hint">Registering would point Windows at python.exe. ' +
        'Run the built <b>PDFStudio.exe</b> and try again.</p>', null);
      return;
    }
    if (st.is_default) {
      modal('Already the default',
        '<p class="hint">PDF Studio already opens .pdf files on this account.</p>' +
        '<div class="field"><label>Remove the association?</label></div>',
        async () => {
          await run('unregister_file_types');
          toast('Removed. Windows will fall back to another PDF app.', 'ok');
        }, 'Remove');
      return;
    }
    modal('Set as default PDF app',
      '<p class="hint">Windows does not let an application make itself the ' +
      'default — that choice is yours to confirm. PDF Studio will be added to ' +
      'the list of PDF apps, then the Windows <b>Default apps</b> screen opens ' +
      'so you can pick it.</p>' +
      '<p class="hint">Currently opening PDFs: <b>' +
      escapeHtml(st.current_handler || 'not set') + '</b></p>' +
      '<p class="hint">This affects your Windows account only and needs no ' +
      'administrator rights.</p>',
      async () => {
        const r = await busyRun('Registering…', 'register_file_types');
        if (!r) return;
        toast('Added. Choose PDF Studio under Default apps to finish.', 'ok');
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
    '<div class="field"><label>Pages (e.g. 1-3,7)</label><input name="pages" value="' + (S.page + 1) + '"></div>',
    async (v) => {
      const idx = parseRanges(v.pages, S.info.page_count);
      if (!idx.length) { toast('No valid pages.', 'err'); return; }
      const r = await busyRun('Extracting…', 'page_extract', idx);
      if (r && r.path) toast('Extracted ' + r.pages + ' pages.', 'ok');
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
      if (!v.template.trim()) return;
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
  xlsx: async () => { const r = await busyRun('Converting to Excel…', 'export_xlsx'); if (r && r.path) toast('Saved ' + baseName(r.path) + ' (' + r.tables + ' tables)', 'ok'); },
  pptx: () => modal('Convert to PowerPoint',
    '<div class="field"><label>Mode</label><select name="mode">' +
    '<option value="editable">Editable text boxes</option>' +
    '<option value="image">Exact page pictures</option></select></div>',
    async (v) => { const r = await busyRun('Converting…', 'export_pptx', v.mode); if (r && r.path) toast('Saved ' + baseName(r.path), 'ok'); }, 'Convert'),
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
      if (!S.info && !['open', 'create', 'defaultapp'].includes(li.dataset.act)) {
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
$('zoomin').onclick = () => { S.fit = false; S.zoom = Math.min(5, S.zoom * 1.2); drawPage(); };
$('zoomout').onclick = () => { S.fit = false; S.zoom = Math.max(0.15, S.zoom / 1.2); drawPage(); };
$('zoomfit').onclick = () => { S.fit = !S.fit; drawPage(); };

/* ---------- mouse wheel ---------- */
// Ctrl+wheel zooms. Plain wheel scrolls the page, and rolls on to the next or
// previous page once there is nothing left to scroll -- which is immediately
// when the whole page already fits.

let zoomTimer = null;
let pageFlipAt = 0;

function queueZoom() {
  clearTimeout(zoomTimer);
  zoomTimer = setTimeout(() => drawPage(), 90);
  $('zoomlabel').textContent = Math.round(S.zoom * 100) + '%';
}

$('viewer').addEventListener('wheel', (e) => {
  if (!S.info) return;

  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    S.fit = false;
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
  else if (e.key === 'PageDown' || e.key === 'ArrowRight') gotoPage(S.page + 1);
  else if (e.key === 'PageUp' || e.key === 'ArrowLeft') gotoPage(S.page - 1);
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
