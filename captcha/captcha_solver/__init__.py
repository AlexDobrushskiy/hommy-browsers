"""Captcha / bot-wall solver for agent browsers (plain-chrome over CDP, Playwright pages).

    from captcha_solver import detect, solve
    info = detect(page)              # CaptchaInfo | None
    res = solve(page)                # Result(status=solved|failed|hard-block|unknown-type|unsupported|no-captcha|budget|error)

Deterministic first (DOM + OpenCV); a local Ollama VLM only as a fallback. No paid solving services.
"""
from .core import Result, solve, site_of
from .detect import CaptchaInfo, detect

__all__ = ["detect", "solve", "CaptchaInfo", "Result", "site_of"]
