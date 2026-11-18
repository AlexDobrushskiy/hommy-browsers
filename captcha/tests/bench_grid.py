"""Offline accuracy of the grid backends on the hand-labelled set (captcha/data/grids/<kind>/labels.json).
  .venv/bin/python tests/bench_grid.py recaptcha siglip|owl|vlm:<model>|... [--save]
A challenge counts as right when the chosen tiles include every core tile and nothing outside core+maybe.
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
import cv2, numpy as np
from captcha_solver import grid

kind, methods = sys.argv[1], [m for m in sys.argv[2:] if not m.startswith("--")]
D = Path(__file__).resolve().parents[1] / "data/grids" / kind   # labelled set, in the repo
labels = {k: v for k, v in json.loads((D / "labels.json").read_text()).items() if not k.startswith("_")}


def judge(pred, lab):
    core, maybe = set(lab["core"]), set(lab.get("maybe", []))
    return core <= pred and pred <= core | maybe, len(core & pred), len(core), len(pred - core - maybe)


def run(method, cid, meta, g, tiles):
    t = grid.target_of(meta["instruction"]); R, C = meta["rows"], meta["cols"]
    if method.startswith("siglip"):
        thr = float(method.split("@")[1]) if "@" in method else 0.5
        s = grid.siglip_scores(tiles, t)[:, 1]
        return {i for i, v in enumerate(s) if v >= thr}
    if method.startswith("owl@") or method == "owl":
        thr = float(method.split("@")[1]) if "@" in method else 0.15
        boxes = grid.owl_boxes(g, t, thresh=thr)
        return grid.tiles_from_boxes(boxes, g.shape[0], g.shape[1], R, C)
    if method.startswith("owltile"):
        thr = float(method.split("@")[1]) if "@" in method else 0.2
        if R * C == 9:
            return {i for i, v in enumerate(grid.owl_tile_scores(tiles, t)) if v >= thr}
        return grid.tiles_from_boxes(grid.owl_boxes(g, t, thresh=thr), g.shape[0], g.shape[1], R, C)
    if method.startswith("vlmtile:"):
        if R * C != 9: return grid.tiles_from_boxes(grid.owl_boxes(g, t, thresh=0.2), g.shape[0], g.shape[1], R, C)
        return {i for i, tl in enumerate(tiles) if grid.vlm_tile_yes(tl, t, model=method[8:])}
    if method.startswith("vlm:"):
        return grid.vlm_tiles(g, R, C, t, model=method[4:])
    raise SystemExit(f"unknown method {method}")


for method in methods:
    res = {"3x3": [0, 0], "4x4": [0, 0]}; tp = fn = fp = 0; t0 = time.time()
    for cid, lab in sorted(labels.items()):
        d = D / cid; meta = json.loads((d / "meta.json").read_text())
        g = cv2.imread(str(d / "grid.png")); tiles = [cv2.imread(str(p)) for p in sorted((d / "tiles").glob("*.png"))]
        pred = run(method, cid, meta, g, tiles)
        ok, hit, ncore, wrong = judge(pred, lab)
        key = f"{meta['rows']}x{meta['cols']}"; res[key][0] += ok; res[key][1] += 1
        tp += hit; fn += ncore - hit; fp += wrong
        if "--v" in sys.argv: print(f"  {cid} {key} {grid.target_of(meta['instruction']):18} pred={sorted(pred)} core={lab['core']} {'OK' if ok else 'x'}")
    n = sum(v[1] for v in res.values())
    print(f"{method:22} challenges {sum(v[0] for v in res.values())}/{n}  (3x3 {res['3x3'][0]}/{res['3x3'][1]}, 4x4 {res['4x4'][0]}/{res['4x4'][1]})"
          f"  tiles: recall {tp}/{tp + fn}, false+ {fp}   {time.time() - t0:.0f}s", flush=True)
