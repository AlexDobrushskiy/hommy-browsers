"""hCaptcha challenge frame. Current hCaptcha tasks are drawn on one canvas ("find all the animals that
match the example", "click the X", ...), not tile grids, so the only general local approach is a VLM that
reads the prompt + canvas and returns click points. Best effort; measured rates in the README.
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

import cv2

from . import human, vision, vlm


def _vis(f, sel):
    try:
        loc = f.locator(sel).first
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False


def points_for(img, prompt: str, model: str | None = None) -> list[tuple[float, float]]:
    """Click points (img pixels) the VLM proposes for an hCaptcha prompt."""
    text, kx, ky = vlm.ask(img, f"This is a captcha puzzle. Instruction: \"{prompt}\". Decide which objects in the image "
                                "must be clicked to complete the instruction. Answer with JSON only: "
                                '[{"point_2d": [x, y], "label": "..."}]', model=model)
    pts = []
    for m in re.finditer(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]", text):
        pts.append(vlm.map_point(float(m.group(1)), float(m.group(2)), img, kx, ky, model))
    return pts


def solve_challenge(page, mouse, f, shots: Path | None, token_ok, model=None) -> dict:
    rep = dict(rounds=0, log=[], notes=[])
    for rnd in range(4):
        if token_ok(): rep["outcome"] = "solved"; return rep
        if not _vis(f, ".prompt-text"):
            rep["outcome"] = "no-challenge"; return rep
        rep["rounds"] += 1
        prompt = f.locator(".prompt-text").first.inner_text().strip()
        cv_ = f.locator("canvas").first
        cb = cv_.bounding_box()
        img = vision.decode_png(page.screenshot(clip=cb, scale="css"))[:, :, :3]
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True); cv2.imwrite(str(shots / f"hc-round{rnd + 1}.jpg"), img)
        time.sleep(human.pause(0.8, 1.5))
        pts = points_for(img, prompt, model)
        rep["log"].append(dict(prompt=prompt, points=[[round(x), round(y)] for x, y in pts]))
        mouse.place()
        for x, y in pts:
            if 0 <= x < cb["width"] and 0 <= y < cb["height"]:
                mouse.click(cb["x"] + x, cb["y"] + y); time.sleep(human.pause(0.3, 0.8))
        sb = f.locator(".button-submit").first.bounding_box()
        mouse.click(sb["x"] + sb["width"] * random.uniform(0.3, 0.7), sb["y"] + sb["height"] * random.uniform(0.3, 0.7))
        time.sleep(random.uniform(2.5, 3.5))
    rep["outcome"] = "solved" if token_ok() else "failed"
    return rep
