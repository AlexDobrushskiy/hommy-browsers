"""End-to-end reCAPTCHA v2 / hCaptcha runs on Google's / hCaptcha's public demo pages (test targets only).
Drives a tab that is already open in an MCP lane (via the lane Chrome's debug port: HB_DEBUG_BASE) or any --cdp.
  .venv/bin/python tests/e2e_recaptcha.py N [--rc grid|audio|auto] [--hcaptcha] [--url-part demo] [--cdp URL]
Counts per run: solved?, rounds shown, wrong answers, extra rounds (Google asked again without saying wrong).
"""
import json, sys, time
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from playwright.sync_api import sync_playwright
from captcha_solver import solve
from captcha_solver.cli import _page_for

arg = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
N = int(sys.argv[1]); rc = arg("--rc", "grid"); cdp = arg("--cdp", "http://127.0.0.1:9331")
hc = "--hcaptcha" in sys.argv
URL = "https://accounts.hcaptcha.com/demo" if hc else "https://www.google.com/recaptcha/api2/demo"
tot = dict(solved=0, runs=0)
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(cdp)
    page = _page_for(b, None, arg("--url-part", "demo"))
    for i in range(N):
        page.goto(URL, wait_until="domcontentloaded"); time.sleep(4)
        r = solve(page, max_attempts=1, budget=(10**6, 1), site="demo-hcaptcha" if hc else f"demo-recaptcha-{rc}",
                  rc_mode=rc, outcome_timeout=8)
        a = r.detail.get("attempt1", {})
        tot["runs"] += 1; tot["solved"] += r.status == "solved"
        print(json.dumps(dict(i=i, status=r.status, dur=r.duration, rounds=a.get("rounds"), wrong=a.get("wrong"),
                              extra=a.get("extra"), outcome=a.get("outcome"), log=a.get("log"), notes=a.get("notes"))), flush=True)
print("SUMMARY", json.dumps(tot))
