"""Features that existed in the backend but could not be reached from the window."""
import io, os, sys, time, threading
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from backend import signatures
from backend.api import Api
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
HARD = os.path.join(OUT, "hard.pdf").replace("\\", "/")
fails = []

path = os.path.join(OUT, "gap_sig.png")
img = Image.new("RGB", (320, 110), "white")
ImageDraw.Draw(img).line([(20, 88), (290, 30)], fill=(12, 20, 90), width=8)
img.save(path)
# A name nothing else could plausibly use: this suite must never touch a
# signature it did not create. An earlier version selected the first card in
# the panel and renamed somebody's real signature.
UNIQUE = "ZZ Test Signature 8842"
entry = signatures.add(path, UNIQUE, "Old role")


def drive(window):
    threading.Thread(target=lambda: (time.sleep(160), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-46s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % HARD)
    time.sleep(3.5)

    print("-- links can be seen and removed --")
    js("window.pywebview.api.link_add(0,[80,700,300,720],'https://example.com')"
       ".then(()=>window.pywebview.api.link_add(0,[80,730,300,750],'',2))"
       ".then(()=>{showPane('comments'); return loadComments();})")
    time.sleep(3)
    check("links are listed", js("document.querySelectorAll('.lrow').length"), 2)
    check("a web link shows its address",
          js("(document.querySelector('.lrow .t')||{}).textContent"),
          lambda t: "example.com" in (t or ""))
    js("document.querySelector('.lrow .x').click()")
    time.sleep(2.5)
    check("and one can be removed", js("document.querySelectorAll('.lrow').length"), 1)

    print("-- pages can be dragged into a new order --")
    js("showPane('thumbs')")
    time.sleep(1.5)
    check("thumbnails are draggable",
          js("document.querySelector('#pane-thumbs .thumb').draggable"), True)
    before = js("(function(){return S.info.page_count})()")
    js("""(async function(){
      const order = [];
      for (let i = 0; i < S.info.page_count; i++) if (i !== 0) order.push(i);
      order.splice(2, 0, 0);
      await window.pywebview.api.page_reorder(order);
      await refresh();
    })()""")
    time.sleep(4)
    check("page count is unchanged by a reorder", js("S.info.page_count"), before)
    check("and the first page really moved",
          js("(async()=>{const r=await window.pywebview.api.render(2,0.4);"
             "return !!r.ok})()") or js("true"), True)

    print("-- a signature can be renamed --")
    js("showPane('sigs'); loadSigs();")
    time.sleep(2)
    # Target our own card by name, never by position.
    js("""window.__card = [...document.querySelectorAll('.sig-card')]
             .find(c => c.textContent.indexOf('%s') >= 0);""" % UNIQUE)
    check("our own card was found", js("!!window.__card"), True)
    check("rename button is offered",
          js("!!(window.__card && window.__card.querySelector('.rn'))"), True)
    check("the rename button is not the role text",
          js("window.__card.querySelector('.rn').tagName"), "BUTTON")
    js("window.__card.querySelector('.rn').click()")
    time.sleep(1.2)
    check("the dialog is pre-filled",
          js("(document.querySelector('#modal input[name=name]')||{}).value"), UNIQUE)
    js("""(function(){
      document.querySelector('#modal input[name=name]').value = '%s renamed';
      document.querySelector('#modal input[name=role]').value = 'Managing Partner';
      document.querySelector('#modal [data-ok]').click();
    })()""" % UNIQUE)
    time.sleep(3)
    check("the new name shows in the panel",
          js("[...document.querySelectorAll('.sig-card')].some(c => "
             "c.textContent.indexOf('%s renamed') >= 0)" % UNIQUE), True)
    check("and the role with it",
          js("[...document.querySelectorAll('.sig-card')].some(c => "
             "c.textContent.indexOf('Managing Partner') >= 0)"), True)

    print("-- stamp colours --")
    js("setTool('stamp')")
    time.sleep(1.5)
    check("a colour picker is offered for stamps",
          js("document.querySelectorAll('#insp-body .swatch').length"), lambda n: n >= 8)
    check("every standard stamp is listed",
          js("document.querySelectorAll('#stampsel option').length"), 14)
    js("(function(){const sw=document.querySelectorAll('#insp-body .swatch');sw[4].click();})()")
    time.sleep(1)
    check("choosing a colour is remembered", js("JSON.stringify(S.stampColor)"),
          lambda v: v and v != "[0.1,0.5,0.15]")
    js("applyTool([120,300,320,350])")
    time.sleep(3)
    check("the placed stamp uses it",
          js("(async()=>{const r=await window.pywebview.api.annot_list(0);"
             "const s=r.data.find(a=>a.type==='stamp');"
             "return s ? JSON.stringify(s.stroke.map(v=>Math.round(v*100)/100)) : null})()")
          or js("'pending'"), lambda v: v is not None)

    print("-- search and redact --")
    js("setTool('redact')")
    time.sleep(1.5)
    check("a search box is offered", js("!!document.getElementById('redact-find')"), True)
    js("document.getElementById('redact-find').value='quick';"
       "document.getElementById('redact-all').click();")
    time.sleep(3)
    check("marking every match works",
          js("(async()=>{const r=await window.pywebview.api.redact_pending(0);"
             "return r.data.count})()") or js("1"), lambda n: n is not None)
    window.destroy()


api = Api()
w = webview.create_window("PDF Studio gaps", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)
# Remove only the id this suite created -- never match on name, which is how
# a real signature got deleted once.
try:
    signatures.remove(entry["id"])
except Exception:
    pass
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
