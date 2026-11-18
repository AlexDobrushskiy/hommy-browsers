"""Collect reCAPTCHA v2 image challenges from Google's public demo page into a labelling set (test target only).
  .venv/bin/python tests/collect_recaptcha.py N [--cdp URL]
Each challenge: <dir>/<id>/grid.png (as displayed), tiles/NN.png, meta.json {instruction, rows, cols, dynamic}.
Uses its own Playwright-launched Agent Chrome (escalates to images more readily) unless --cdp is given.
"""
import json, re, sys, time, uuid
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "data/grids/recaptcha"   # commit new labelled samples
EXE = __import__("subprocess").run([str(Path(__file__).resolve().parents[2] / "agent-chrome"), "--no-update"],
                                   capture_output=True, text=True, check=True).stdout.strip()
N = int(sys.argv[1]); cdp = sys.argv[sys.argv.index("--cdp") + 1] if "--cdp" in sys.argv else None


def bframe(page):
    for f in page.frames:
        if "recaptcha/api2/bframe" in f.url or "recaptcha/enterprise/bframe" in f.url: return f


def save(page, f):
    desc = f.locator(".rc-imageselect-desc-wrapper").first.inner_text().strip()
    table = f.locator("table[class*=rc-imageselect-table]").first
    cls = table.get_attribute("class")
    m = re.search(r"table-(\d)(\d)", cls); rows, cols = int(m.group(1)), int(m.group(2))
    cid = time.strftime("%H%M%S-") + uuid.uuid4().hex[:4]
    d = OUT / cid; (d / "tiles").mkdir(parents=True)
    table.screenshot(path=str(d / "grid.png"))
    for i, td in enumerate(f.locator("td.rc-imageselect-tile").all()):
        td.screenshot(path=str(d / "tiles" / f"{i:02d}.png"))
    meta = dict(instruction=desc, rows=rows, cols=cols, dynamic="none left" in desc.lower(), table_class=cls)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(cid, rows, cols, desc.replace("\n", " | "), flush=True)


with sync_playwright() as p:
    if cdp:
        b = p.chromium.connect_over_cdp(cdp); page = b.contexts[0].new_page()
    else:
        b = p.chromium.launch(executable_path=EXE, headless=True); page = b.new_page(viewport={"width": 1100, "height": 900})
    page.goto("https://www.google.com/recaptcha/api2/demo"); time.sleep(3)
    anchor = next(f for f in page.frames if "api2/anchor" in f.url)
    anchor.locator("#recaptcha-anchor").click(); time.sleep(4)
    got = 0
    while got < N:
        f = bframe(page)
        if not f or not f.locator("table[class*=rc-imageselect-table]").count():
            txt = f.locator("body").inner_text()[:200] if f else ""
            print("no image challenge:", txt.replace("\n", " ")); break
        save(page, f); got += 1
        f.locator("#recaptcha-reload-button").click(); time.sleep(3.5)
    page.screenshot(path=str(OUT / "last.png"))
    b.close()
