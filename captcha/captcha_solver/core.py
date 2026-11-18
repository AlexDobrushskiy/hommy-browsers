"""solve(page): detect the wall, run the matching handler, verify, log. Never pings anyone."""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import attempts, human, inputs, vlm as vlm_mod
from .detect import CaptchaInfo, detect
from .discover import discover, known_parts
from .slider import Slider

SHOTS = Path(os.environ.get("CAPTCHA_STATE_DIR") or Path.home() / ".local/state/hommy-browsers/captcha") / "shots"
HARD = {"datadome-blocked", "hard-block"}
WAIT = {"datadome-interstitial", "akamai-interstitial"}


@dataclass
class Result:
    status: str                 # solved | failed | hard-block | unknown-type | unsupported | no-captcha | budget | error
    kind: str | None = None
    vendor: str | None = None
    attempts: int = 0
    duration: float = 0.0
    input: str = "cdp"
    screenshot: str | None = None
    message: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self): return asdict(self)


def site_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _safe_detect(page):
    for _ in range(3):
        try: return detect(page), None
        except Exception as e:
            err = e; time.sleep(0.7)
    return "error", str(err)[:200]


def wait_outcome(page, before: CaptchaInfo, timeout=15.0):
    """Poll until the wall is gone (solved), turned into a hard block, or is still there after `timeout`."""
    t0 = time.time()
    last = before
    while time.time() - t0 < timeout:
        time.sleep(0.7)
        info, err = _safe_detect(page)
        if info == "error": continue  # navigating
        if info is None:
            time.sleep(1.0)  # let a reload settle, then confirm
            info2, _ = _safe_detect(page)
            if info2 is None: return "solved", None
            info = info2
        last = info
        if info.kind in HARD: return "hard-block", info
        if info.kind == "datadome-slider":
            try:
                if info.frame and info.frame.locator("button.retryLink").first.is_visible():
                    return "failed", info
            except Exception:
                pass
            if before.kind == "datadome-slider" and time.time() - t0 > 8:
                return "failed", info
    return "failed", last


# ---------------- handlers: each does ONE attempt and returns a dict of what it did ----------------

def _slider(page, mouse, info, shots, use_vlm):
    f = info.frame or page.main_frame
    if info.vendor == "geetest":
        # v3/v4 start with a "click to verify" button; the puzzle opens after it
        for sel in (".geetest_radar_tip", ".geetest_btn_click"):
            try:
                loc = f.locator(sel).first
                if loc.count() and loc.is_visible() and not f.locator(".geetest_slider_button, .geetest_track").first.is_visible():
                    b = loc.bounding_box()
                    mouse.click(b["x"] + b["width"] * random.uniform(0.3, 0.6), b["y"] + b["height"] * random.uniform(0.35, 0.65))
                    time.sleep(random.uniform(2.0, 3.0))
                    break
            except Exception:
                pass
    if info.vendor == "datadome":
        # an expired / failed DataDome challenge shows a RETRY button; a person clicks it and gets a fresh slider
        try:
            retry = f.locator("button.retryLink").first
            if retry.count() and retry.is_visible():
                b = retry.bounding_box()
                mouse.click(b["x"] + b["width"] * random.uniform(0.3, 0.7), b["y"] + b["height"] * random.uniform(0.3, 0.7))
                time.sleep(random.uniform(2.0, 3.0))
        except Exception:
            pass
    parts = known_parts(f, info.vendor)
    found = None
    if parts is None and info.vendor == "datadome":
        return {"error": "DataDome widget without a known slider (new layout?)", "html_classes": _classes(f)}
    if parts is None:
        parts, found = discover(f)
    if parts is None:
        return {"error": "no slider handle found", "discover": found}
    time.sleep(human.pause(0.4, 1.0))  # look at the puzzle first
    rep = Slider(page, mouse, parts, shots, vlm=(vlm_mod.gap_target if use_vlm and vlm_mod.available() else None)).run()
    return {"method": rep.method, "target_px": rep.target_px, "ratio": rep.ratio, "final_err_px": rep.final_err_px,
            "gap_margin": rep.gap_margin, "notes": rep.notes, "parts": [parts.handle, parts.bg, parts.piece, parts.track]}


def _classes(f):
    try:
        return f.evaluate("[...new Set([...document.querySelectorAll('[class]')].filter(e => e.getBoundingClientRect().width > 0)"
                          ".map(e => String(e.className).slice(0, 40)))].slice(0, 60)")
    except Exception:
        return None


def _checkbox(page, mouse, info, shots, use_vlm):
    f = info.frame
    if info.kind == "turnstile":
        if f is None:
            return {"waited": True}
        b = info.detail.get("box") or f.frame_element().bounding_box()
        x, y = b["x"] + min(30, b["width"] * 0.12) + random.uniform(-4, 4), b["y"] + b["height"] / 2 + random.uniform(-4, 4)
        mouse.click(x, y)
        return {"clicked": [round(x), round(y)]}
    if info.kind == "recaptcha" and info.detail.get("stage") == "image-challenge":
        return _recaptcha_challenge(page, mouse, f, shots)
    if info.kind == "hcaptcha" and info.detail.get("stage") == "image-challenge":
        return _hcaptcha_challenge(page, mouse, f, shots)
    sel = "#recaptcha-anchor" if info.kind == "recaptcha" else "#checkbox"
    bb = f.locator(sel).first.bounding_box()
    x, y = bb["x"] + bb["width"] * random.uniform(0.3, 0.7), bb["y"] + bb["height"] * random.uniform(0.3, 0.7)
    mouse.click(x, y)
    out = {"clicked": [round(x), round(y)]}
    time.sleep(random.uniform(2.5, 3.5))
    new, _ = _safe_detect(page)
    if new and new != "error" and new.detail.get("stage") == "image-challenge":
        out.update((_recaptcha_challenge if new.kind == "recaptcha" else _hcaptcha_challenge)(page, mouse, new.frame, shots))
    return out


def _hcaptcha_challenge(page, mouse, f, shots):
    if not os.environ.get("CAPTCHA_HCAPTCHA_VLM"):
        return {"method": "hcaptcha", "outcome": "unsupported", "notes": ["hCaptcha image task: VLM path is opt-in (CAPTCHA_HCAPTCHA_VLM=1)"]}
    from . import hcaptcha
    from .detect import token_len
    rep = hcaptcha.solve_challenge(page, mouse, f, shots, lambda: token_len(page) >= 15)
    rep["method"] = "hcaptcha-vlm"
    return rep


RC_MODE = {"mode": "grid"}   # set by solve(rc_mode=...)


def _recaptcha_challenge(page, mouse, f, shots):
    from . import recaptcha
    from .detect import token_len
    ok = lambda: token_len(page) >= 15
    rep = (recaptcha.solve_audio if RC_MODE["mode"] == "audio" else recaptcha.solve_grid)(page, mouse, f, shots, ok)
    if RC_MODE["mode"] == "auto" and rep.get("outcome") not in ("solved",):
        rep2 = recaptcha.solve_audio(page, mouse, f, shots, ok)
        rep = dict(grid=rep, audio=rep2, outcome=rep2.get("outcome"))
    rep["method"] = f"recaptcha-{RC_MODE['mode']}"
    return rep


def _hold(page, mouse, info, shots, use_vlm):
    f = info.frame or page.main_frame
    b = f.locator("#px-captcha").first.bounding_box()
    x, y = b["x"] + b["width"] * random.uniform(0.35, 0.65), b["y"] + b["height"] * random.uniform(0.35, 0.65)
    mouse.place(); mouse.reach(x, y); time.sleep(human.pause(0.15, 0.4)); mouse.down()
    t0 = time.time()
    while time.time() - t0 < 15:
        mouse.play(human.segment(mouse.x, mouse.y, x + random.gauss(0, 0.8), y + random.gauss(0, 0.8), random.uniform(0.3, 0.6), tremor=0.2))
        try:
            if not f.locator("#px-captcha").first.is_visible(): break
            if re.search(r"verified|vérifi|verificad", f.locator("#px-captcha").first.inner_text(timeout=500), re.I): break
        except Exception:
            break
    held = time.time() - t0
    time.sleep(human.pause(0.1, 0.3)); mouse.up()
    return {"held_s": round(held, 1)}


def _unknown(page, mouse, info, shots, use_vlm):
    """Let the local VLM name it; act only on the simple shapes (checkbox, slider-to-end)."""
    if not (use_vlm and vlm_mod.available()):
        return {"error": "unknown wall and no local VLM"}
    import cv2, numpy as np
    from . import vision
    shot = vision.decode_png(page.screenshot(scale="css"))[:, :, :3]
    c = vlm_mod.classify(shot)
    out = {"vlm": c}
    if c.get("type") == "checkbox":
        b = vlm_mod.locate(shot, "the captcha checkbox")
        if b:
            mouse.click((b[0] + b[2]) / 2, (b[1] + b[3]) / 2); out["clicked"] = b
    elif c.get("type") in ("slider", "slider-puzzle"):
        parts, found = discover(info.frame or page.main_frame)
        if parts:
            out.update(_slider(page, mouse, CaptchaInfo("generic-slider", "unknown", True, info.frame), shots, use_vlm))
        else:
            out["error"] = "slider seen by the VLM but no DOM handle found"
    return out


HANDLERS = {"datadome-slider": _slider, "geetest-slide": _slider, "generic-slider": _slider,
            "turnstile": _checkbox, "recaptcha": _checkbox, "hcaptcha": _checkbox, "px-hold": _hold, "unknown": _unknown}


def _wait_frame(page, info, timeout=10):
    """DataDome's iframe content loads after the block page: wait until its widget renders."""
    if info.kind == "datadome-slider":
        t0 = time.time()
        while time.time() - t0 < timeout:
            fr = info.frame
            if fr is not None:
                try:
                    if fr.evaluate("document.readyState") == "complete" and fr.evaluate(
                            "!!document.querySelector('canvas, img, [class*=slider]')"):
                        time.sleep(1.0)
                        return info
                except Exception:
                    pass
            time.sleep(0.5)
            new, _ = _safe_detect(page)
            if new and new != "error": info = new
    return info


def solve(page, *, input: str = "cdp", port: int | None = None, max_attempts: int = 2, site: str | None = None,
          budget: tuple[int, int] = (6, 6 * 3600), use_vlm: bool = True, info: CaptchaInfo | None = None,
          shots_root: Path = SHOTS, outcome_timeout: float = 15.0, dry_run: bool = False,
          rc_mode: str = "grid") -> Result:
    """Solve whatever wall `page` shows. page: a Playwright Page (connected over CDP to plain-chrome, or any).
    input: "cdp" (default) or "macos" (native events to the Chrome process; needs port). Max `max_attempts`
    tries this run; refuses when the site already had budget[0] attempts in the last budget[1] seconds."""
    t0 = time.time()
    RC_MODE["mode"] = rc_mode   # reCAPTCHA challenge: "grid" (local vision), "audio" (whisper.cpp) or "auto" (grid, then audio)
    url = page.url
    site = site or site_of(url)
    info = info or detect(page)
    t_settle = time.time()
    while info is not None and info.kind == "unknown" and time.time() - t_settle < 8:
        time.sleep(1.0)   # widgets load late (iframes): give a known one the chance to show up
        new, _ = _safe_detect(page)
        if new != "error": info = new
    run_dir = shots_root / time.strftime("%Y%m%d-%H%M%S") / site
    res = Result("no-captcha", input=input)
    if info is None:
        res.duration = time.time() - t0
        return res
    res.kind, res.vendor = info.kind, info.vendor

    def finish(status, msg="", **detail):
        res.status, res.message, res.duration = status, msg, round(time.time() - t0, 2)
        res.detail.update(detail)
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            p = run_dir / f"final-{status}.jpg"
            page.screenshot(path=str(p), type="jpeg", quality=70)
            res.screenshot = str(p)
        except Exception:
            pass
        return res

    if info.kind in WAIT or (info.kind == "turnstile" and info.frame is None):
        out, new = wait_outcome(page, info, timeout=25)
        attempts.record(site, url, info.kind, input, "wait", out, time.time() - t0, 0)
        if out == "solved": return finish("solved", "interstitial cleared by itself")
        if new is None or new.kind == info.kind:
            return finish("failed", "interstitial did not clear in 25 s")
        info = new; res.kind, res.vendor = info.kind, info.vendor
    if info.kind in HARD:
        attempts.record(site, url, info.kind, input, "-", "hard-block", time.time() - t0, 0, info.detail)
        return finish("hard-block", "blocked, nothing to solve (IP/device ban). Retry later or from another IP/profile.",
                      **{k: v for k, v in info.detail.items() if k != "box"})
    if info.kind == "hcaptcha" and info.detail.get("stage") == "image-challenge" and not os.environ.get("CAPTCHA_HCAPTCHA_VLM"):
        attempts.record(site, url, info.kind, input, "-", "unsupported", time.time() - t0, 0)
        return finish("unsupported", "hCaptcha image task: not solved locally (VLM path is opt-in: CAPTCHA_HCAPTCHA_VLM=1; 0/3 measured)")
    handler = HANDLERS.get(info.kind)
    if handler is None:
        return finish("unknown-type", f"no handler for {info.kind}")
    used = attempts.recent(site, budget[1])
    if used >= budget[0]:
        return finish("budget", f"{used} attempts on {site} in the last {budget[1] // 3600} h; not trying again now")
    if dry_run:
        return finish("dry-run", "detected only", info=repr(info))

    mouse = None
    for n in range(1, max_attempts + 1):
        ta = time.time()
        info = _wait_frame(page, info)
        if mouse is None:
            mouse = inputs.make(page, input, port)
        try:
            did = handler(page, mouse, info, run_dir / f"attempt{n}", use_vlm)
        except Exception as e:
            did = {"error": f"{type(e).__name__}: {e}"[:300]}
        res.attempts = n
        if did.get("error") and not did.get("method"):
            attempts.record(site, url, info.kind, input, "-", "error", time.time() - ta, n, did)
            return finish("unknown-type" if "no slider" in did["error"] or "unknown" in did["error"] else "error",
                          did["error"], **{f"attempt{n}": did})
        if did.get("outcome") == "unsupported":
            attempts.record(site, url, info.kind, input, did.get("method", info.kind), "unsupported", time.time() - ta, n, did)
            return finish("unsupported", "; ".join(did.get("notes", [])) or "unsupported challenge", **{f"attempt{n}": did})
        out, new = wait_outcome(page, info, timeout=outcome_timeout)
        attempts.record(site, url, info.kind, input, did.get("method", info.kind), out, time.time() - ta, n, did)
        res.detail[f"attempt{n}"] = did
        if out == "solved":
            return finish("solved")
        if out == "unsupported":
            return finish("unsupported", f"{info.kind} escalated to an image challenge: not solved by this tool")
        if out == "hard-block":
            return finish("hard-block", "the wall turned into a hard block after the attempt", after=new.kind)
        if new is None or n == max_attempts: break
        info = new
        time.sleep(random.uniform(1.5, 3.5))  # a person re-reads the new puzzle
    return finish("failed", f"still blocked after {res.attempts} attempt(s)")
