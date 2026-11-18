"""Detect + click on checkbox-style demo widgets (Turnstile, reCAPTCHA v2, hCaptcha). Test targets only.
  .venv/bin/python tests/e2e_checkbox.py CDP_URL
"""
import json, sys, time
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from playwright.sync_api import sync_playwright
from captcha_solver import solve, detect
cdp = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(cdp); page = b.contexts[0].new_page()
    for name, url in [("turnstile", "https://2captcha.com/demo/cloudflare-turnstile"), ("recaptcha", "https://2captcha.com/demo/recaptcha-v2"),
                      ("hcaptcha", "https://accounts.hcaptcha.com/demo")]:
        page.goto(url, wait_until="domcontentloaded"); time.sleep(7)
        info = detect(page)
        r = solve(page, max_attempts=1, use_vlm=False, budget=(10**6, 1), site=f"demo-{name}", outcome_timeout=10)
        extra = {}
        try:
            extra["token"] = page.evaluate("""() => { const e = document.querySelector('[name="cf-turnstile-response"], #g-recaptcha-response, [name="h-captcha-response"]'); return e ? (e.value || '').length : null; }""")
        except Exception: pass
        print(json.dumps(dict(name=name, detected=info and info.kind, status=r.status, msg=r.message, **extra, detail=r.detail)), flush=True)
    page.close()
