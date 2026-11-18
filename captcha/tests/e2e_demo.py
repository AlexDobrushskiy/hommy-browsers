"""End-to-end runs against public captcha demo pages (test targets only; no paid API is ever called).

  .venv/bin/python tests/e2e_demo.py CDP_URL geetest3|geetest4 N [--input cdp|macos]
Set CAPTCHA_SCREENSHOT_ONLY=1 to force the screenshot path (no canvas/image pixels).
"""
import json, sys, time
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from playwright.sync_api import sync_playwright
from captcha_solver import solve, detect

URLS = {"geetest3": "https://2captcha.com/demo/geetest", "geetest4": "https://2captcha.com/demo/geetest-v4"}
cdp, which, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
inp = sys.argv[sys.argv.index("--input") + 1] if "--input" in sys.argv else "cdp"
port = int(cdp.rsplit(":", 1)[1])
ok = 0
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(cdp)
    page = b.contexts[0].new_page()
    for i in range(n):
        page.goto(URLS[which], wait_until="domcontentloaded"); time.sleep(5)
        r = solve(page, input=inp, port=port, max_attempts=1, use_vlm=False, budget=(10**6, 1), site=f"demo-{which}")
        a = r.detail.get("attempt1", {})
        ok += r.status == "solved"
        print(json.dumps(dict(i=i, status=r.status, dur=r.duration, method=a.get("method"), target=a.get("target_px"),
                              ratio=a.get("ratio"), err=a.get("final_err_px"), margin=a.get("gap_margin"), notes=a.get("notes"), msg=r.message)), flush=True)
    page.close()
print(f"SUMMARY {which} input={inp} solved {ok}/{n}")
