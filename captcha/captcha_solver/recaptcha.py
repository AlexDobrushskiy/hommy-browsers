"""reCAPTCHA v2 challenge (bframe): image grids (3x3 static/dynamic, 4x4 "squares") and the audio challenge.

Grid decisions (measured on 25 hand-labelled Google demo challenges, 2026-10-27; see README):
  3x3: each tile is its own photo -> ask the local VLM "does this show X?" per tile
  4x4: one photo cut in squares -> OWLv2 open-vocabulary boxes, tiles = box overlap
Audio: download the mp3, transcribe with whisper.cpp (large-v3-turbo), type the words.
One call = one challenge "attempt", which may span several rounds (Google often asks 2-4 grids).
"""
from __future__ import annotations

import os
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from . import grid, human, vision

WHISPER = os.environ.get("CAPTCHA_WHISPER", "/opt/homebrew/bin/whisper-cli")
WHISPER_MODEL = os.environ.get("CAPTCHA_WHISPER_MODEL", str(Path.home() / ".local/share/whisper-cpp/ggml-large-v3-turbo.bin"))
TILE_VLM = os.environ.get("CAPTCHA_TILE_VLM", "qwen3.5:4b")
MAX_ROUNDS = 6


def _vis(f, sel):
    try:
        loc = f.locator(sel).first
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False


def _click(mouse, box, rx=(0.3, 0.7), ry=(0.3, 0.7)):
    mouse.reach(box["x"] + box["width"] * random.uniform(*rx), box["y"] + box["height"] * random.uniform(*ry))
    time.sleep(human.pause(0.05, 0.15))
    mouse.down(); time.sleep(human.pause(0.05, 0.12)); mouse.up()


def _tile_imgs(page, f):
    tds = f.locator("td.rc-imageselect-tile").all()
    boxes = [td.bounding_box() for td in tds]
    imgs = [vision.decode_png(page.screenshot(clip=b, scale="device"))[:, :, :3] for b in boxes]
    return boxes, imgs


def _decide(tiles, table_img, rows, cols, target, notes):
    if rows * cols == 9:
        sel = set()
        for i, t in enumerate(tiles):
            try:
                if grid.vlm_tile_yes(t, target, model=TILE_VLM): sel.add(i)
            except Exception as e:
                notes.append(f"vlm tile failed ({e}); owl fallback")
                if grid.owl_tile_scores([t], target)[0] >= 0.2: sel.add(i)
        return sel
    boxes = grid.owl_boxes(table_img, target, thresh=0.2)
    return grid.tiles_from_boxes(boxes, table_img.shape[0], table_img.shape[1], rows, cols)


def _frame_open(f):
    try: return f.frame_element().is_visible()
    except Exception: return False


def _state(f):
    """What the bframe shows after a verify: wrong / more-needed / new grid / audio / blocked / closed.
    "closed" = Google shut the popup and left the box unticked: the answer was rejected (risk), not graded."""
    if not _frame_open(f): return "closed"
    if _vis(f, ".rc-doscaptcha-header"): return "blocked"
    if _vis(f, ".rc-imageselect-incorrect-response"): return "wrong"
    for s in (".rc-imageselect-error-select-more", ".rc-imageselect-error-dynamic-more", ".rc-imageselect-error-select-something"):
        if _vis(f, s): return "more"
    if _vis(f, ".rc-audiochallenge-error-message"): return "wrong"
    return "grid" if _vis(f, "table[class*=rc-imageselect-table]") else "other"


def solve_grid(page, mouse, f, shots: Path | None, token_ok) -> dict:
    """Play image rounds until a token appears. Returns {rounds, wrong, extra_rounds, outcome, log}."""
    rep = dict(rounds=0, wrong=0, extra=0, log=[], notes=[])
    for rnd in range(MAX_ROUNDS):
        if token_ok(): rep["outcome"] = "solved"; return rep
        if not _frame_open(f) or not _vis(f, "table[class*=rc-imageselect-table]"):
            rep["outcome"] = _state(f); return rep
        rep["rounds"] += 1
        desc = f.locator(".rc-imageselect-desc-wrapper").first.inner_text().strip()
        cls = f.locator("table[class*=rc-imageselect-table]").first.get_attribute("class")
        m = re.search(r"table-(\d)(\d)", cls); rows, cols = int(m.group(1)), int(m.group(2))
        target = grid.target_of(desc)
        dynamic = "none left" in desc.lower()
        tb = f.locator("table[class*=rc-imageselect-table]").first.bounding_box()
        table_img = vision.decode_png(page.screenshot(clip=tb, scale="device"))[:, :, :3]
        boxes, tiles = _tile_imgs(page, f)
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True); cv2.imwrite(str(shots / f"round{rnd + 1}.jpg"), table_img)
        time.sleep(human.pause(0.8, 1.6))  # look at it
        sel = _decide(tiles, table_img, rows, cols, target, rep["notes"])
        rep["log"].append(dict(target=target, grid=f"{rows}x{cols}", dynamic=dynamic, picked=sorted(sel)))
        order = sorted(sel); random.shuffle(order) if random.random() < 0.3 else None
        mouse.place()
        for i in order:
            _click(mouse, boxes[i]); time.sleep(human.pause(0.25, 0.7))
        if dynamic:
            pending = set(sel)
            for _ in range(6):  # refilled tiles fade in over ~4 s
                if not pending: break
                time.sleep(random.uniform(4.0, 5.0))
                boxes, tiles = _tile_imgs(page, f)
                new = set()
                for i in pending:
                    try:
                        if grid.vlm_tile_yes(tiles[i], target, model=TILE_VLM): new.add(i)
                    except Exception:
                        pass
                rep["log"][-1].setdefault("refills", []).append(sorted(new))
                for i in sorted(new):
                    _click(mouse, boxes[i]); time.sleep(human.pause(0.25, 0.7))
                pending = new
        time.sleep(human.pause(0.4, 1.0))
        _click(mouse, f.locator("#recaptcha-verify-button").first.bounding_box())
        time.sleep(random.uniform(2.0, 3.0))
        if token_ok(): rep["outcome"] = "solved"; return rep
        st = _state(f)
        if st in ("blocked", "closed"): rep["outcome"] = st; rep["log"][-1]["after"] = st; return rep
        if st in ("wrong", "more"): rep["wrong"] += 1
        elif st == "grid": rep["extra"] += 1
        rep["log"][-1]["after"] = st
    rep["outcome"] = "rounds-exhausted"
    return rep


def transcribe(mp3: bytes) -> str:
    d = Path(tempfile.mkdtemp(prefix="rc-audio-"))
    (d / "a.mp3").write_bytes(mp3)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(d / "a.mp3"), "-ar", "16000", "-ac", "1", str(d / "a.wav")], check=True)
    subprocess.run([WHISPER, "-m", WHISPER_MODEL, "-f", str(d / "a.wav"), "-l", "en", "-nt", "-np", "-otxt", "-of", str(d / "a")],
                   check=True, capture_output=True)
    text = (d / "a.txt").read_text().strip()
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def solve_audio(page, mouse, f, shots: Path | None, token_ok) -> dict:
    rep = dict(rounds=0, wrong=0, extra=0, log=[], notes=[])
    if not _vis(f, ".rc-audiochallenge-tdownload-link"):
        _click(mouse, f.locator("#recaptcha-audio-button").first.bounding_box())
        time.sleep(random.uniform(2.0, 3.0))
    for rnd in range(3):
        if token_ok(): rep["outcome"] = "solved"; return rep
        if _vis(f, ".rc-doscaptcha-header"):
            rep["outcome"] = "blocked"; rep["notes"].append(f.locator(".rc-doscaptcha-body").first.inner_text()[:160]); return rep
        if not _vis(f, ".rc-audiochallenge-tdownload-link"):
            rep["outcome"] = "no-audio"; return rep
        rep["rounds"] += 1
        href = f.locator(".rc-audiochallenge-tdownload-link").first.get_attribute("href")
        r = page.context.request.get(href)
        text = transcribe(r.body())
        rep["log"].append(dict(heard=text))
        box = f.locator("#audio-response").first.bounding_box()
        _click(mouse, box)
        for ch in text:
            page.keyboard.type(ch); time.sleep(random.uniform(0.05, 0.18))
        time.sleep(human.pause(0.4, 1.0))
        _click(mouse, f.locator("#recaptcha-verify-button").first.bounding_box())
        time.sleep(random.uniform(2.0, 3.0))
        if token_ok(): rep["outcome"] = "solved"; return rep
        st = _state(f)
        rep["log"][-1]["after"] = st
        if st in ("blocked", "closed"): rep["outcome"] = st; return rep
        rep["wrong"] += 1
    rep["outcome"] = "failed"
    return rep
