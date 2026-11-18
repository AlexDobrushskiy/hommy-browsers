"""Find and classify a bot wall on a Playwright page (all frames, incl. cross-origin iframes).

kinds (CaptchaInfo.kind):
  datadome-slider     DataDome captcha iframe (t=fe): puzzle slider / "slide right"     -> solvable
  datadome-blocked    DataDome iframe with t=bv: IP/device banned, nothing to solve      -> hard block
  datadome-interstitial  DataDome "device check" page: wait, it resolves itself          -> wait
  geetest-slide       GeeTest v3/v4 puzzle slider (also the "click to verify" button)   -> solvable
  generic-slider      some other slider/puzzle slider (handle + track found)              -> solvable (best effort)
  turnstile           Cloudflare Turnstile checkbox / "Just a moment" managed challenge   -> click / wait
  recaptcha           reCAPTCHA v2 checkbox                                               -> click (image grid: unsolved)
  hcaptcha            hCaptcha checkbox                                                   -> click (image task: unsolved)
  px-hold             PerimeterX/HUMAN "Press & Hold"                                      -> solvable
  akamai-interstitial Akamai sec-cpt challenge page                                       -> wait
  hard-block          403-style page with nothing to solve ("Access denied", "Zugriff verweigert") -> hard block
  unknown             looks like a wall (captcha words) but no known widget               -> VLM / give up
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

BLOCK_TEXT = re.compile(r"access denied|access is temporarily restricted|zugriff verweigert|acc[eè]s refus[ée]|you have been blocked|vous avez [ée]t[ée] bloqu|"
                        r"request unsuccessful|pardon our interruption|forbidden|sorry, you have been blocked|"
                        r"acesso negado|dost[eę]p zablokowany|error 1020", re.I)
WALL_TEXT = re.compile(r"captcha|verify you are (a )?human|are you a robot|prove your humanity|slide (right|to)|"
                       r"glissez|faites glisser|deslize|schieben|przesu[nń]|press (and|&) hold|i'?m not a robot|"
                       r"pas un robot|kein roboter|just a moment|un instant|checking your browser", re.I)


@dataclass
class CaptchaInfo:
    kind: str
    vendor: str
    solvable: bool
    frame: object = None            # Playwright Frame holding the widget (None = main frame)
    url: str = ""
    detail: dict = field(default_factory=dict)

    def __repr__(self):
        return f"CaptchaInfo({self.kind}, vendor={self.vendor}, solvable={self.solvable}, detail={self.detail})"


def _visible_box(frame, sel):
    try:
        loc = frame.locator(sel).first
        if loc.count() == 0: return None
        b = loc.bounding_box(timeout=1500)
        return b if b and b["width"] > 2 and b["height"] > 2 else None
    except Exception:
        return None


def _text(frame, n=4000):
    try: return frame.evaluate("document.body ? document.body.innerText.slice(0, %d) : ''" % n)
    except Exception: return ""


def _frame_box(frame, visible=False):
    try:
        el = frame.frame_element()
        if visible and not el.is_visible(): return None
        b = el.bounding_box()
        return b if b and b["width"] > 2 and b["height"] > 2 else None
    except Exception:
        return None


def furl(f, page=None) -> str:
    """Frame URL; over CDP Playwright sometimes loses an out-of-process iframe's URL (''), so ask the frame."""
    u = f.url
    if not u and (page is None or f != page.main_frame):
        try: u = f.evaluate("location.href")
        except Exception: u = ""
    return u


def token_len(page) -> int:
    """Length of a Turnstile / reCAPTCHA / hCaptcha response token on the page (0 = none yet)."""
    try:
        return page.evaluate("""() => Math.max(0, ...[...document.querySelectorAll(
            '[name="cf-turnstile-response"], [name="g-recaptcha-response"], [name="h-captcha-response"]')].map(e => (e.value || '').length))""")
    except Exception:
        return 0


def detect(page, status: int | None = None) -> CaptchaInfo | None:
    """Return what wall the page shows, or None if it looks like a normal page."""
    frames = page.frames
    urls = {f: furl(f, page) for f in frames}
    # --- DataDome ---
    for f in frames:
        fu = urls[f]
        u = urlparse(fu)
        if "captcha-delivery.com" in u.netloc:
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            box = _frame_box(f)
            if "/interstitial" in u.path:
                return CaptchaInfo("datadome-interstitial", "datadome", False, f, fu, dict(box=box))
            if q.get("t") == "bv" or re.search(r"blocked|bloqu|temporarily restricted|temporairement restreint|acc[eè]s.{0,20}restreint", _text(f, 800), re.I) and not _visible_box(f, ".slider, [class*=slider]"):
                return CaptchaInfo("datadome-blocked", "datadome", False, f, fu, dict(t=q.get("t"), box=box))
            if "/captcha" in u.path:
                return CaptchaInfo("datadome-slider", "datadome", True, f, fu, dict(t=q.get("t"), box=box))
    try: html_head = page.content()[:6000]
    except Exception: html_head = ""
    m = re.search(r"var dd=\{[^}]*'rt':'(\w)'[^}]*\}", html_head)
    if m:
        t = re.search(r"'t':'(\w+)'", m.group(0))
        if t and t.group(1) == "bv":
            return CaptchaInfo("datadome-blocked", "datadome", False, None, page.url, dict(t="bv", note="block page, iframe not loaded"))
        kind = "datadome-interstitial" if m.group(1) == "i" else "datadome-slider"
        return CaptchaInfo(kind, "datadome", kind == "datadome-slider", None, page.url, dict(note="iframe not loaded yet"))

    # --- GeeTest (same-origin widget) ---
    for f in frames:
        if _visible_box(f, ".geetest_slider_button, .geetest_btn, .geetest_btn_click, .geetest_canvas_bg, .geetest_radar_tip, .geetest_bg"):
            try:
                done = f.evaluate("!!document.querySelector('.geetest_radar_success, .geetest_lock_success, .geetest_success')")
            except Exception:
                done = False
            if not done:
                return CaptchaInfo("geetest-slide", "geetest", True, f, urls[f])

    # --- PerimeterX / HUMAN press & hold ---
    for f in frames:
        if _visible_box(f, "#px-captcha"):
            return CaptchaInfo("px-hold", "perimeterx", True, f, urls[f])

    # --- Cloudflare ---
    title = ""
    try: title = page.title()
    except Exception: pass
    for f in frames:
        if "challenges.cloudflare.com" in urls[f]:
            box = _frame_box(f)
            if box and token_len(page) < 15:
                return CaptchaInfo("turnstile", "cloudflare", True, f, urls[f], dict(box=box))
    if re.search(r"just a moment|un instant|einen moment", title, re.I) or "cf-chl" in html_head or "challenge-platform" in html_head:
        return CaptchaInfo("turnstile", "cloudflare", True, None, page.url, dict(note="managed challenge page, widget not visible yet"))

    # --- reCAPTCHA / hCaptcha ---
    has_token = token_len(page) >= 15
    for f in frames:
        if not has_token and re.search(r"google\.com/recaptcha/(api2|enterprise)/bframe|recaptcha\.net/recaptcha/(api2|enterprise)/bframe", urls[f]):
            box = _frame_box(f, visible=True)
            if box and box["height"] > 150:
                return CaptchaInfo("recaptcha", "google", True, f, urls[f], dict(stage="image-challenge", box=box))
    for f in frames:
        if not has_token and re.search(r"(google\.com|recaptcha\.net)/recaptcha/(api2|enterprise)/anchor", urls[f]) and _frame_box(f):
            return CaptchaInfo("recaptcha", "google", True, f, urls[f], dict(stage="checkbox"))
    for f in frames:
        if not has_token and "hcaptcha.com" in urls[f] and "frame=challenge" in urls[f]:
            box = _frame_box(f, visible=True)
            if box and box["height"] > 150:
                return CaptchaInfo("hcaptcha", "hcaptcha", True, f, urls[f], dict(stage="image-challenge", box=box))
    for f in frames:
        if not has_token and "hcaptcha.com" in urls[f] and "frame=checkbox" in urls[f] and _frame_box(f):
            return CaptchaInfo("hcaptcha", "hcaptcha", True, f, urls[f], dict(stage="checkbox"))

    # --- Akamai ---
    if "sec-if-cpt" in html_head or "_sec/cp_challenge" in html_head:
        return CaptchaInfo("akamai-interstitial", "akamai", False, None, page.url)

    # --- generic slider anywhere ---
    for f in frames:
        h = _visible_box(f, "[class*=slider] [class*=handle], [class*=slider] [class*=button], [class*=slider] [class*=btn], "
                            "[class*=slider][class*=icon], .sliderIcon, [class*=drag][class*=btn], [class*=captcha] [class*=slider]")
        if h and WALL_TEXT.search(_text(f, 2000)):
            return CaptchaInfo("generic-slider", "unknown", True, f, urls[f], dict(handle=h))

    # --- hard block / unknown wall ---
    body = _text(page.main_frame, 3000)
    nav_status = status
    if nav_status is None:
        try: nav_status = page.evaluate("(performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0")
        except Exception: nav_status = 0
    if BLOCK_TEXT.search(title + " " + body[:1500]) and len(body) < 3000:
        return CaptchaInfo("hard-block", _vendor_guess(html_head, body), False, None, page.url, dict(status=nav_status, text=body[:200]))
    if nav_status in (403, 429) and len(body) < 1500:
        kind = "unknown" if WALL_TEXT.search(body) else "hard-block"
        return CaptchaInfo(kind, _vendor_guess(html_head, body), False, None, page.url, dict(status=nav_status, text=body[:200]))
    if WALL_TEXT.search(title + " " + body[:800]) and len(body) < 1500 and not has_token:
        return CaptchaInfo("unknown", _vendor_guess(html_head, body), False, None, page.url, dict(status=nav_status, text=body[:200]))
    return None


def _vendor_guess(html, body):
    s = (html + body).lower()
    for v, k in [("datadome", "datadome"), ("akamai", "reference #"), ("akamai", "edgesuite"), ("cloudflare", "cloudflare"),
                 ("imperva", "incapsula"), ("perimeterx", "px-captcha")]:
        if k in s: return v
    return "unknown"
