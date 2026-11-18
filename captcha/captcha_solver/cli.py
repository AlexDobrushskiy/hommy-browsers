"""captcha-solve — solve the bot wall showing in a plain-chrome tab (or any Chrome with a CDP port).

  captcha-solve --cdp http://127.0.0.1:PORT --target TARGET [opts]   solve in that tab (from `plain-chrome open`)
  captcha-solve --open URL [--warm HOME_URL] [--fresh] [opts]        plain-chrome open URL, then solve; leaves the
                                                                      tab open and prints its cdp/port/target
  captcha-solve --lane --url SUBSTR [opts]                           a tab an agent opened with the pw-lane(-a..d) MCP
  captcha-solve --identity --url SUBSTR [opts]                       ... or with the identity MCP (hold it first)
  captcha-solve --identity-b --url SUBSTR [opts]                     ... or a pool copy's MCP (identity-a…; debug ports
                                                                      from config.env: HB_DEBUG_BASE layout)
  captcha-solve --cdp ... --target ... --detect                      only say what wall is showing
  captcha-solve stats                                                success counts from the attempt log

  opts: --input cdp|macos   pointer path (default cdp; macos = native events posted to the Chrome process,
                            the real cursor never moves)
        --max-attempts N    default 2 (more raises the site's bot score)
        --no-vlm            never call the local vision model
        --rc grid|audio|auto  reCAPTCHA image challenge: local vision (default), audio + whisper.cpp, or grid then audio
        --force             ignore the per-site budget (6 attempts / 6 h)
Prints one JSON line: {"status": solved|failed|hard-block|unknown-type|unsupported|no-captcha|budget|error, ...}.
Exit code 0 only for solved / no-captcha. Never notifies anyone.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HB_HOME = Path(__file__).resolve().parents[2]
PLAIN = HB_HOME / "plain-chrome"


def debug_port(server):
    """The debug port of an MCP server's Chrome (serve: HB_DEBUG_BASE layout in config.env)."""
    sys.path.insert(0, str(HB_HOME / "lib"))
    import hbconfig
    for name, _, dbg in hbconfig.servers(hbconfig.load()):
        if name == server:
            return dbg
    sys.exit(f"captcha-solve: {server} is not enabled in config.env")


def _page_for(browser, target, url_part=None):
    pages = [pg for c in browser.contexts for pg in c.pages]
    if url_part and not target:
        hits = [pg for pg in pages if url_part in pg.url]
        if len(hits) == 1: return hits[0]
        sys.exit(f"captcha-solve: {len(hits)} tabs match --url {url_part!r}: " + ", ".join(pg.url[:80] for pg in hits)
                 if hits else f"captcha-solve: no tab matches --url {url_part!r}")
    if not target:
        if len(pages) == 1: return pages[0]
        sys.exit("captcha-solve: several tabs open; pass --target (CDP targetId from `plain-chrome open`)")
    for pg in pages:
        s = pg.context.new_cdp_session(pg)
        try:
            if s.send("Target.getTargetInfo")["targetInfo"]["targetId"] == target: return pg
        finally:
            s.detach()
    sys.exit(f"captcha-solve: tab {target} not found")


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help"): print(__doc__); return 0
    if a[0] == "stats":
        from .attempts import stats
        print(f"{'site':18} {'kind':18} {'input':8} {'method':10} {'outcome':11} {'n':>3} {'avg s':>6}  last")
        for r in stats(): print(f"{r[0]:18} {r[1]:18} {r[2][:8]:8} {str(r[3])[:10]:10} {r[4]:11} {r[5]:>3} {r[6]:>6}  {r[7]}")
        return 0
    o = dict(rc="grid", url=None, cdp=None, target=None, input="cdp", max_attempts=2, vlm=True, force=False, detect=False, open=None, warm=None, fresh=False)
    it = iter(a)
    for x in it:
        if x == "--cdp": o["cdp"] = next(it)
        elif x == "--target": o["target"] = next(it)
        elif x == "--url": o["url"] = next(it)
        elif x == "--lane": o["cdp"] = f"http://127.0.0.1:{debug_port('lanes')}"   # pw-lane MCP Chrome
        elif x == "--identity": o["cdp"] = f"http://127.0.0.1:{debug_port('identity')}"
        elif x in ("--identity-a", "--identity-b", "--identity-c", "--identity-d"):
            o["cdp"] = f"http://127.0.0.1:{debug_port(x[2:])}"     # pool copy MCP Chrome
        elif x == "--input": o["input"] = next(it)
        elif x == "--max-attempts": o["max_attempts"] = int(next(it))
        elif x == "--no-vlm": o["vlm"] = False
        elif x == "--rc": o["rc"] = next(it)
        elif x == "--force": o["force"] = True
        elif x == "--detect": o["detect"] = True
        elif x == "--open": o["open"] = next(it)
        elif x == "--warm": o["warm"] = next(it)
        elif x == "--fresh": o["fresh"] = True
        else: sys.exit(f"captcha-solve: unknown argument {x}\n{__doc__}")
    opened = None
    if o["open"]:
        first = o["warm"] or o["open"]
        cmd = [str(PLAIN), "open"] + (["--fresh"] if o["fresh"] else []) + [first]
        opened = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1])
        o["cdp"], o["target"] = opened["cdp"], opened["target"]
        time.sleep(12)
    if not o["cdp"]: sys.exit(__doc__)
    port = int(o["cdp"].rsplit(":", 1)[1].split("/")[0])

    from playwright.sync_api import sync_playwright
    from . import detect, solve
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(o["cdp"])
        page = _page_for(b, o["target"], o["url"])
        if opened and o["warm"]:
            from .detect import detect as _d
            if _d(page) is None:  # home page passed: now the real URL
                page.goto(o["open"], wait_until="domcontentloaded", timeout=45000); time.sleep(8)
        if o["detect"]:
            info = detect(page)
            print(json.dumps({"captcha": None} if info is None else
                             {"kind": info.kind, "vendor": info.vendor, "solvable": info.solvable, "frame_url": info.url[:200],
                              "detail": {k: v for k, v in info.detail.items()}}, default=str))
            return 0
        res = solve(page, input=o["input"], port=port, max_attempts=o["max_attempts"], use_vlm=o["vlm"],
                    budget=(10**6, 1) if o["force"] else (6, 6 * 3600), rc_mode=o["rc"])
        out = res.to_dict()
        out["url"] = page.url
        if opened: out.update(cdp=opened["cdp"], port=opened["port"], target=opened["target"])
        print(json.dumps(out, default=str))
        return 0 if res.status in ("solved", "no-captcha") else 1


if __name__ == "__main__":
    sys.exit(main())
