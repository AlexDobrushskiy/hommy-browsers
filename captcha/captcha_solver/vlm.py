"""Optional local vision model (Ollama) for what the deterministic code can't see: locate an element in a
screenshot ("the slider handle", "the empty puzzle slot") and name an unknown captcha.

Model: qwen3.5:4b (natively multimodal, grounding in 0..1000 coords). Override with CAPTCHA_VLM_MODEL.
Never sends anything off this machine.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.request

import cv2
import numpy as np

def _ollama_base(host: str) -> str:
    """OLLAMA_HOST in Ollama's own forms (`0.0.0.0`, `127.0.0.1:11434`, `http://host:port`) as a base URL."""
    host = host.strip().rstrip("/") or "127.0.0.1"
    scheme, sep, rest = host.partition("://")
    if not sep:
        scheme, rest = "http", host
    name, colon, port = rest.rpartition(":") if ":" in rest else (rest, "", "")
    if name in ("0.0.0.0", ""):  # a bind-all address: connect to this machine
        name = "127.0.0.1"
    if not colon and scheme == "http":
        port = "11434"
    return f"{scheme}://{name}" + (f":{port}" if port else "")


URL = _ollama_base(os.environ.get("OLLAMA_HOST", "")) + "/api/generate"
MODEL = os.environ.get("CAPTCHA_VLM_MODEL", "qwen3.5:4b")
MAX_SIDE = 1600
PIXELS = 829_000


def available() -> bool:
    try:
        with urllib.request.urlopen(URL.replace("/api/generate", "/api/tags"), timeout=2) as r:
            return any(m["name"].startswith(MODEL.split(":")[0]) for m in json.load(r)["models"])
    except Exception:
        return False


def _grid(w, h):
    """The pixel grid the model answers in. Measured 2026-10-26 (qwen2.5vl:7b via Ollama): images under
    ~0.83 MP are upscaled to that size (28-px patch grid) and boxes come back in the upscaled grid;
    bigger images are answered in their own pixels."""
    sc = max(1.0, (PIXELS / (w * h)) ** 0.5)
    if sc == 1.0: return w, h
    return max(28, round(w * sc / 28) * 28), max(28, round(h * sc / 28) * 28)


def ask(img: np.ndarray, prompt: str, timeout=180, model: str | None = None) -> tuple[str, float, float]:
    """Returns (answer text, kx, ky): multiply model x / y by kx / ky to get img pixels."""
    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE:  # only to keep the upload small; the model rescales anyway
        s = MAX_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", img)
    body = json.dumps({"model": model or MODEL, "prompt": prompt, "images": [base64.b64encode(png.tobytes()).decode()],
                       "stream": False, "think": False, "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = json.load(r)["response"]
    sh, sw = img.shape[:2]          # what the model got
    gw, gh = _grid(sw, sh)
    return text, w / gw, h / gh


# How each model family reports positions: "grid" = pixels of the grid it saw (see _grid),
# "norm" = 0..1000 relative to the image (Qwen3-VL family). Measured with tests/bench_vlm.py.
COORDS = {"qwen2.5vl": "grid"}


def _coords(model):
    m = (model or MODEL).split(":")[0]
    return COORDS.get(m, "norm" if m.startswith(("qwen3",)) else "grid")


def map_point(x, y, img, kx, ky, model=None):
    if _coords(model) == "norm":
        h, w = img.shape[:2]
        return x * w / 1000, y * h / 1000
    return x * kx, y * ky


def ask_text(img: np.ndarray, prompt: str, model: str | None = None) -> str:
    return ask(img, prompt, model=model)[0]


def _boxes(text: str):
    """Pull [x1, y1, x2, y2] boxes out of the model's answer (JSON or loose)."""
    out = []
    for m in re.finditer(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]", text):
        x1, y1, x2, y2 = map(float, m.groups())
        if x2 > x1 and y2 > y1: out.append((x1, y1, x2, y2))
    return out


def locate(img: np.ndarray, what: str, model: str | None = None) -> tuple[float, float, float, float] | None:
    """Bounding box (x1, y1, x2, y2) of `what` in img pixels, or None."""
    text, kx, ky = ask(img, f"Locate {what} in this image. Answer with JSON only: "
                            '[{"bbox_2d": [x1, y1, x2, y2], "label": "..."}]', model=model)
    b = _boxes(text)
    if not b: return None
    x1, y1 = map_point(b[0][0], b[0][1], img, kx, ky, model)
    x2, y2 = map_point(b[0][2], b[0][3], img, kx, ky, model)
    return (x1, y1, x2, y2)


def classify(img: np.ndarray) -> dict:
    """What kind of challenge is on screen. Returns {"type": ..., "raw": ...}."""
    text, _, _ = ask(img, "This is a screenshot of a web page. Is a CAPTCHA or bot check shown? Answer with JSON only: "
                       '{"captcha": true|false, "type": "slider-puzzle|slider|checkbox|press-and-hold|image-grid|text|blocked|other", '
                       '"instruction": "the challenge text, verbatim"}')
    m = re.search(r"\{.*\}", text, re.S)
    try: d = json.loads(m.group(0)) if m else {}
    except Exception: d = {}
    d["raw"] = text[:300]
    return d


def gap_target(shot: np.ndarray, pb: dict, bgb: dict, f: float):
    """Fallback for the slider engine: CSS px the piece must travel, from where the model sees the slot."""
    b = locate(shot, "the empty puzzle-piece shaped slot (the darker or outlined hole where the loose piece must go), "
                     "not the loose piece itself on the left")
    if not b: return None
    x1 = b[0] / f + bgb["x"]
    if x1 <= pb["x"] + pb["width"] * 0.5: return None
    return x1 - pb["x"]
