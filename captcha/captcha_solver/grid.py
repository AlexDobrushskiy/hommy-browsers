"""Image-grid captchas ("select all images / squares with X"): which tiles contain the target?

Backends (all local):
  siglip   per-tile zero-shot classification (google/siglip-base-patch16-224)
  owl      open-vocabulary detection on the whole grid, tiles = boxes x tile overlap (google/owlv2-base-patch16-ensemble)
  vlm      Ollama vision model sees the numbered grid and lists tiles
Each returns a set of tile indices (row-major, 0-based).
"""
from __future__ import annotations

import functools
import json
import re

import cv2
import numpy as np

# reCAPTCHA's usual targets -> extra phrasings that work better as text queries
SYNONYMS = {
    "crosswalks": ["a pedestrian crosswalk with white zebra stripes on the road"],
    "traffic lights": ["a traffic light"], "a fire hydrant": ["a fire hydrant"], "fire hydrants": ["a fire hydrant"],
    "buses": ["a bus"], "bus": ["a bus"], "cars": ["a car"], "motorcycles": ["a motorcycle"],
    "bicycles": ["a bicycle"], "stairs": ["outdoor stairs, steps"], "chimneys": ["a chimney"],
    "bridges": ["a bridge"], "boats": ["a boat"], "tractors": ["a tractor"], "palm trees": ["a palm tree"],
    "mountains or hills": ["mountains or hills"], "taxis": ["a yellow taxi cab"], "parking meters": ["a parking meter"],
}
NEGATIVES = ["a street", "trees", "a building", "sky", "grass", "a road", "a sidewalk", "a wall", "a fence", "a house"]


def target_of(instruction: str) -> str:
    """'Select all images with\\na fire hydrant\\nClick verify ...' -> 'a fire hydrant'."""
    lines = [l.strip() for l in instruction.splitlines() if l.strip()]
    if len(lines) >= 2 and re.match(r"select all (images|squares) with( a| an)?$", lines[0], re.I):
        t = lines[1]
        if re.search(r"with a$", lines[0], re.I): t = "a " + t
        return t
    m = re.search(r"(?:images|squares) (?:with|containing|of)\s+(.+?)(?:\.|click|if there|$)", " ".join(lines), re.I)
    return m.group(1).strip() if m else " ".join(lines)


def queries(target: str) -> list[str]:
    t = target.lower().strip()
    return SYNONYMS.get(t) or [t if t.startswith(("a ", "an ")) else f"a {t}"]


def split(grid: np.ndarray, rows: int, cols: int, gap: int = 0) -> list[np.ndarray]:
    H, W = grid.shape[:2]
    return [grid[r * H // rows: (r + 1) * H // rows, c * W // cols: (c + 1) * W // cols] for r in range(rows) for c in range(cols)]


def _device():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


@functools.lru_cache(maxsize=2)
def _siglip(name="google/siglip-base-patch16-224"):
    from transformers import AutoModel, AutoProcessor
    return AutoModel.from_pretrained(name).to(_device()).eval(), AutoProcessor.from_pretrained(name)


def siglip_scores(tiles: list[np.ndarray], target: str) -> np.ndarray:
    """Per tile: probability-like score that the tile shows the target (softmax vs. scene negatives)."""
    import torch
    from PIL import Image
    model, proc = _siglip()
    texts = [f"a photo of {q}" for q in queries(target)] + [f"a photo of {n}" for n in NEGATIVES]
    ims = [Image.fromarray(cv2.cvtColor(t, cv2.COLOR_BGR2RGB)) for t in tiles]
    inp = proc(text=texts, images=ims, padding="max_length", return_tensors="pt").to(_device())
    with torch.no_grad():
        out = model(**inp)
    logits = out.logits_per_image.float().cpu().numpy()       # tiles x texts
    k = len(queries(target))
    pos = logits[:, :k].max(1)
    sig = 1 / (1 + np.exp(-pos))
    soft = np.exp(logits - logits.max(1, keepdims=True)); soft /= soft.sum(1, keepdims=True)
    return np.stack([sig, soft[:, :k].sum(1)], 1)              # [sigmoid of best positive, softmax mass on target]


@functools.lru_cache(maxsize=1)
def _owl(name="google/owlv2-base-patch16-ensemble"):
    from transformers import Owlv2ForObjectDetection, Owlv2Processor
    return Owlv2ForObjectDetection.from_pretrained(name).to(_device()).eval(), Owlv2Processor.from_pretrained(name)


def owl_boxes(img: np.ndarray, target: str, thresh: float = 0.1):
    """[(x1, y1, x2, y2, score)] in img pixels."""
    import torch
    from PIL import Image
    model, proc = _owl()
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    inp = proc(text=[queries(target)], images=im, return_tensors="pt").to(_device())
    with torch.no_grad():
        out = model(**inp)
    side = max(im.size)  # OWLv2 pads to a square
    res = proc.post_process_grounded_object_detection(out, threshold=thresh, target_sizes=torch.tensor([[side, side]]))[0]
    return [(*b.tolist(), float(s)) for b, s in zip(res["boxes"].cpu(), res["scores"].cpu())]


def tiles_from_boxes(boxes, H, W, rows, cols, min_frac=0.06):
    """Tiles that a detected box covers by at least min_frac of the tile's area (or 25% of the box)."""
    sel = set()
    th, tw = H / rows, W / cols
    for x1, y1, x2, y2, s in boxes:
        ba = max(1.0, (x2 - x1) * (y2 - y1))
        for r in range(rows):
            for c in range(cols):
                ix = max(0, min(x2, (c + 1) * tw) - max(x1, c * tw)); iy = max(0, min(y2, (r + 1) * th) - max(y1, r * th))
                inter = ix * iy
                if inter >= min_frac * tw * th or inter >= 0.25 * ba: sel.add(r * cols + c)
    return sel


def numbered(grid: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Grid with each tile's index drawn in its corner (for the VLM)."""
    g = grid.copy(); H, W = g.shape[:2]
    for r in range(rows):
        for c in range(cols):
            x, y = c * W // cols, r * H // rows
            cv2.rectangle(g, (x, y), (x + 30, y + 24), (0, 0, 0), -1)
            cv2.putText(g, str(r * cols + c + 1), (x + 4, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.rectangle(g, (x, y), (x + W // cols - 1, y + H // rows - 1), (255, 255, 255), 2)
    return g


def vlm_tiles(grid: np.ndarray, rows: int, cols: int, target: str, model: str | None = None) -> set[int]:
    from . import vlm
    kind = "images" if rows * cols == 9 else "squares"
    prompt = (f"This is a {rows}x{cols} grid of {kind} numbered 1 to {rows * cols} (left to right, top to bottom). "
              f"Which {kind} contain {target}? Include squares that show only part of it. "
              'Answer with JSON only: {"tiles": [numbers]}')
    text = vlm.ask_text(numbered(grid, rows, cols), prompt, model=model)
    m = re.search(r"\{.*?\}", text, re.S)
    try: nums = json.loads(m.group(0))["tiles"] if m else []
    except Exception: nums = [int(x) for x in re.findall(r"\d+", text)]
    return {int(n) - 1 for n in nums if 1 <= int(n) <= rows * cols}


def owl_tile_scores(tiles: list[np.ndarray], target: str) -> list[float]:
    """3x3 grids are 9 separate photos: best detection score of the target in each tile."""
    out = []
    for t in tiles:
        big = cv2.resize(t, (300, 300), interpolation=cv2.INTER_CUBIC)
        b = owl_boxes(big, target, thresh=0.01)
        out.append(max([s for *_, s in b], default=0.0))
    return out


def vlm_tile_yes(tile: np.ndarray, target: str, model: str | None = None) -> bool:
    from . import vlm
    big = cv2.resize(tile, (336, 336), interpolation=cv2.INTER_CUBIC)
    a = vlm.ask_text(big, f"Does this photo show {target} (even partly)? Answer yes or no.", model=model)
    return a.strip().lower().startswith("yes")
