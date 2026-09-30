"""Placed objects must stay selectable: click, drag, resize, then lock."""
import os, sys, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from backend.api import Api
from backend import signatures
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []

path = os.path.join(OUT, "obj_sig.png")
img = Image.new("RGB", (400, 140), "white")
ImageDraw.Draw(img).line([(20, 110), (370, 40)], fill=(12, 20, 90), width=10)
img.save(path)
entry = signatures.add(path, "Drag Tester")

DRAG = """(function(sel, handle, dx, dy){
  const f = document.querySelector('.selframe');
  const t = handle ? f.querySelector('[data-h="'+handle+'"]') : f;
  const r = t.getBoundingClientRect();
  const x = r.left + r.width/2, y = r.top + r.height/2;
  const o = (a,b)=>({pointerId:7,bubbles:true,cancelable:true,clientX:a,clientY:b});
  t.dispatchEvent(new PointerEvent('pointerdown', o(x,y)));
  f.dispatchEvent(new PointerEvent('pointermove', o(x+dx,y+dy)));
  f.dispatchEvent(new PointerEvent('pointerup',   o(x+dx,y+dy)));
})"""


def drive(window):
    threading.Thread(target=lambda: (time.sleep(120), print("WATCHDOG", flush=True),
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
    js("window.openOnStart(%r)" % os.path.join(OUT, "fixture.pdf").replace("\\", "/"))
    time.sleep(3)

    js("window.pywebview.api.sig_list().then(r=>{S.sig=r.data[r.data.length-1].id})")
    time.sleep(1.2)
    js("setTool('sign')")
    time.sleep(1.0)
    js("applyTool([150,600,360,660])")
    time.sleep(3.0)
    check("placing leaves it selected", js("S.selectedAnnot"), lambda v: bool(v))
    check("it is a signature", js("(S.annots[0]||{}).is_signature"), True)
    check("with a frame and handles",
          js("document.querySelectorAll('.selframe .handle').length"), 8)

    # The trap: a user keeps the signature tool in hand and clicks what they placed.
    js("setTool('sign')")
    time.sleep(1.4)
    check("still clickable with the sign tool active",
          js("document.querySelectorAll('.annothit').length"), lambda n: n >= 1)
    js("document.querySelector('.annothit').click()")
    time.sleep(1.8)
    check("clicking it switches to the select tool", js("S.tool"), "select")
    check("and shows the frame", js("!!document.querySelector('.selframe')"), True)

    before = js("S.annots[0].rect.map(Math.round)")
    js(DRAG + "(null, null, 60, 40)")
    time.sleep(2.5)
    moved = js("S.annots[0].rect.map(Math.round)")
    check("dragging the body moves it", moved, lambda r: r and r != before)

    js(DRAG + "(null, 'se', 90, 50)")
    time.sleep(2.5)
    resized = js("S.annots[0].rect.map(Math.round)")
    check("dragging a corner resizes it", resized,
          lambda r: r and r[2] > moved[2] and r[3] > moved[3])

    js("window.pywebview.api.annot_flatten(false).then(()=>refresh(false))")
    time.sleep(2.5)
    check("flattening locks it", js("document.querySelectorAll('.annothit').length"), 0)
    check("and the frame is gone", js("document.querySelectorAll('.selframe').length"), 0)

    # --- arrows: endpoints, not a bounding box ---
    js("window.pywebview.api.annot_shape(0,'arrow',[[120,420],[320,480]])")
    time.sleep(1.8)
    js("setTool('select')")
    for _ in range(40):
        if js("document.querySelectorAll('.annothit').length"): break
        time.sleep(0.25)
    js("document.querySelector('.annothit').click()")
    time.sleep(1.8)
    check("arrow reports its two endpoints",
          js("(S.annots[0]||{}).points && S.annots[0].points.length"), 2)
    check("arrow head is filled solid",
          js("((S.annots[0]||{}).fill||[]).length"), 3)
    check("arrow gets endpoint grips not a box frame",
          js("document.querySelectorAll('.endpoint').length"), 2)
    check("and a grip to slide the whole arrow",
          js("document.querySelectorAll('.linemove').length"), 1)
    check("no rectangle handles on a line",
          js("document.querySelectorAll('.selframe').length"), 0)

    tip_before = js("S.annots[0].points[1].map(Math.round)")
    js("""(function(){
      const g = document.querySelectorAll('.endpoint')[1];
      const r = g.getBoundingClientRect();
      const x = r.left + r.width/2, y = r.top + r.height/2;
      const o = (a,b)=>({pointerId:9,bubbles:true,cancelable:true,clientX:a,clientY:b});
      g.dispatchEvent(new PointerEvent('pointerdown', o(x,y)));
      g.dispatchEvent(new PointerEvent('pointermove', o(x+70,y-90)));
      g.dispatchEvent(new PointerEvent('pointerup',   o(x+70,y-90)));
    })()""")
    time.sleep(2.5)
    tip_after = js("S.annots[0].points[1].map(Math.round)")
    check("dragging the tip aims the arrow", tip_after,
          lambda r: r and r != tip_before)
    check("the tail stayed put",
          js("S.annots[0].points[0].map(Math.round)"), lambda r: r == [120, 420])
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF objects", url=UI, js_api=api,
                          width=1200, height=880, hidden=True)
api.attach_window(w)
webview.start(drive, w)
signatures.remove(entry["id"])
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
