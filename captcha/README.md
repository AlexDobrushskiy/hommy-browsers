# captcha-solve

Local captcha / bot-wall solver for agent browsers (opt-in: `install.sh --with-captcha` creates `captcha/.venv`
and pulls the models). Deterministic first (DOM + OpenCV), a local Ollama vision model only as a fallback. No paid solving services, no notifications, max 2 tries per site per run.

## Use

```bash
# agent, on a plain-chrome tab (`plain-chrome open URL` prints cdp/port/target)
$HB_HOME/captcha-solve --cdp http://127.0.0.1:PORT --target TARGET
$HB_HOME/captcha-solve --cdp ... --target ... --detect        # only classify
$HB_HOME/captcha-solve --open URL [--warm HOME] [--fresh]    # plain-chrome open + solve; tab stays open
$HB_HOME/captcha-solve --lane --url SUBSTR                    # a tab opened with the pw-lane(-a..d) MCP tools
$HB_HOME/captcha-solve --identity --url SUBSTR                # ... or with the identity MCP (while you hold it)
$HB_HOME/captcha-solve stats                                  # success counts from the log
```
Options: `--input cdp|macos`, `--max-attempts N` (default 2), `--no-vlm`, `--force` (ignore the budget),
`--rc grid|audio|auto` (reCAPTCHA challenge: local vision [default], audio + whisper.cpp, or grid then audio).
Output: one JSON line `{"status", "kind", "attempts", "duration", "screenshot", "detail", ...}`; exit 0 only
for `solved` / `no-captcha`. Close the tab with `plain-chrome close PORT TARGET` afterwards: that is what
copies the new `datadome` / `_abck` cookie into the trust-cookie jar for every other profile.

Library (Playwright page connected over CDP; run with this venv or add the deps below):
```python
import sys; sys.path.insert(0, "<HB_HOME>/captcha")
from captcha_solver import detect, solve
info = detect(page)            # CaptchaInfo(kind, vendor, solvable, frame, url, detail) | None
res = solve(page, input="cdp", port=PORT)   # Result(status=..., kind, attempts, duration, screenshot, detail)
```
`status`: `solved`, `failed`, `hard-block` (ban page, nothing to solve: retry later / other IP),
`unsupported` (hCaptcha image task), `unknown-type`, `no-captcha`, `budget`, `error`.

MCP lanes / identity: `serve` starts their Chrome with a localhost debug port (`HB_DEBUG_BASE` layout in
`config.env`: lanes, identity, identity-a…), so `captcha-solve --lane|--identity --url <part of the tab URL>` attaches to
the tab an agent opened with the MCP tools, solves, and detaches; the MCP session keeps working
(`browser_snapshot` fine after). Nothing changes for pages (no extra flags visible;
`navigator.webdriver` false). Any local process can drive those Chromes while they run, identity included:
same trust level as the profile files. Fallback without the port: open the URL in plain-chrome and run
`captcha-solve --open URL`. Without Python: pass the contents of `mcp/slide.js` as `code` to
`browser_run_code_unsafe` (its `filename` only accepts files under `$HB_STATE_DIR/mcp/<server>`). Handles the
DataDome "slide right" slider and plain slide-to-end sliders. **On DataDome sites it does not help**: the
lane is Playwright-launched, and DataDome answered the correct slide with "Access is temporarily
restricted". Use plain-chrome + `captcha-solve` there.

## What it handles (measured on the source machine, autumn 2026)

| kind | how | result |
|---|---|---|
| `datadome-slider`, "Slide right to secure your access" (handle → drop box) | DOM parts, human drag, RETRY button handled | a DataDome site in plain-chrome: **2/2 solved** (cdp 1, macos 1), listing loaded, jar cookie then let a fresh profile through. Same drag in a Playwright lane: hard block |
| `datadome-slider`, puzzle variant (jigsaw canvases `#captcha__puzzle canvas` / `canvas.block`) | gap finder below | not seen live yet |
| `datadome-blocked` (`t=bv`, "temporarily restricted") / `hard-block` | detected, not attempted | — |
| `geetest-slide` v3 (canvas) | gap from canvas pixels | 11/12 solved (cdp 7/8, macos 4/4) |
| `geetest-slide` v4 (CSS background images) | gap from image files | 3/3 via screenshots at first; later runs placed the piece to ±1 px but GeeTest's risk engine rejected most (repetition from one IP) |
| puzzle sliders without readable pixels | piece shape from the probe-pull motion, then the gap | 7/16 on GeeTest v3 (weak, see open issues) |
| `turnstile`, `recaptcha`, `hcaptcha` checkbox | human click on the box, success = response token | Turnstile (test key) ✓, reCAPTCHA v2 demo ✓ |
| `recaptcha` image challenge (3x3 static/dynamic, 4x4) | 3x3: local VLM yes/no per tile (`qwen3.5:4b`); 4x4: OWLv2 boxes → tiles; refills re-checked; verify; loops over Google's extra rounds | **19/19 runs got a token** (lane 14/14, plain-chrome 4/4, CLI on an MCP lane tab 1/1). 33 grids shown: 19 accepted, 10 "please try again", 4 extra rounds without an error. ~25 s for one round, ~2 min worst case |
| `recaptcha` audio (`--rc audio`) | mp3 → whisper.cpp large-v3-turbo → typed | 0/5: lane gets "automated queries" (no audio at all); plain-chrome: Google closed the popup after each answer (rejected, not graded) |
| `hcaptcha` image task | canvas tasks ("click the letter of group STU" under dot noise, "match the example and count"), not tile grids; VLM points | 0/3 → off by default (`unsupported`), opt-in `CAPTCHA_HCAPTCHA_VLM=1` |
| `px-hold` (PerimeterX press & hold) | hold with tremor until it clears | not tested live |
| `datadome-interstitial`, `akamai-interstitial`, CF "Just a moment" | wait ≤25 s | — |

Gap finder (`vision.find_gap`): piece outline vs. background edge map + a shading step across the outline
+ content correlation. Offline on 32 GeeTest v3 puzzles with ground truth: 32/32 within 3 px.

## Input paths
- `cdp` (default): `Input.dispatchMouseEvent` via Playwright. Events are `isTrusted`, correct
  `screenX/Y` (window offset included), `movementX/Y`, pressure 0.5, coalesced events: on a field-by-field
  comparison the page cannot tell them from the native path. Works headless/off-screen/inactive.
- `macos`: NSEvent → `CGEventPostToPid` to the Chrome process. The real cursor never moves (checked).
  Chrome takes press/drag/release this way even when not frontmost; plain hover moves to a non-key window
  are dropped, so hover goes via CDP. Needs the window on a display. Bare `CGEventCreateMouseEvent` +
  `CGEventPostToPid` is ignored by Chrome; it has to be built through `NSEvent` (window number set).
- Side finding: macOS clamps plain-chrome's `--window-position=-3000,0`; its windows open at (0,0) on the
  main display (behind other windows, not hidden).

## Local models
Default VLM `qwen3.5:4b` via Ollama (`CAPTCHA_VLM_MODEL` / `CAPTCHA_TILE_VLM` to change).
Roles: reCAPTCHA 3x3 tiles, name an unknown wall, locate an element, last-resort gap guess.
Benchmarks (`tests/bench_grid.py`, offline; 25 hand-labelled grids from Google's public reCAPTCHA demo in
`data/grids/recaptcha/labels.json`; a grid counts only if every chosen tile is right; the locate / gap columns
came from a screenshot set that is not shipped):

| method | grids right (3x3 / 4x4) | locate err (px) | gap ≤4 px (of 12) | note |
|---|---|---|---|---|
| **qwen3.5:4b** per tile (+OWLv2 for 4x4) | **23/25** (16/17, 7/8) | **3** | 3 | ~0.5 s/tile, 3.3 GB |
| qwen3.5:9b per tile | 21/25 (14/17, 7/8) | 17 | 1 | removed |
| qwen2.5vl:7b per tile | 17/25 (10/17, 7/8) | 6 | 4 | 4.5x slower (Ollama upsizes small images); removed |
| gemma4:12b per tile | 11/25 (4/17, 7/8) | 71 | 0 | not used |
| OWLv2 base, whole grid @0.2 | 18/25 (11/17, 7/8) | — | — | used for 4x4 |
| SigLIP base per tile | 11/25 | — | — | not used |
| whole grid to a VLM ("which tiles?") | ≤12/25 for every model | — | — | not used |

Qwen3.5 answers positions in 0..1000 coordinates, qwen2.5vl in its own pixel grid (`vlm.COORDS`).
Bigger models (17–23 GB) need a machine with room for them; `OLLAMA_HOST` points the solver at another
Ollama. Detector weights (OWLv2, ~0.6 GB) come from Hugging Face on first use; torch runs on MPS.

## Files
- `captcha_solver/`: `detect.py` (classification), `core.py` (`solve`, handlers, outcome check),
  `recaptcha.py` (grid + audio), `grid.py` (tile classifiers), `hcaptcha.py` (opt-in VLM points),
  `slider.py` (closed-loop slider engine), `discover.py` (vendor selectors + markup-agnostic part finder),
  `vision.py` (OpenCV), `human.py` (trajectories), `inputs.py` (cdp / macos), `vlm.py`, `attempts.py`, `cli.py`
- `mcp/slide.js`: snippet for the MCP lanes
- `tests/e2e_demo.py`, `tests/e2e_checkbox.py`, `tests/e2e_recaptcha.py`: end-to-end runs against public demo
  pages (test targets only); `tests/collect_recaptcha.py` + `bench_grid.py`: the labelled-set benchmark
- Log: `$HB_STATE_DIR/captcha/attempts.db` (site, kind, input, method, outcome, duration, detail).
  Screenshots per run: `$HB_STATE_DIR/captcha/shots/<time>/<site>/`.
- Budget guard: refuses a site with ≥6 attempts in the last 6 h (`--force` overrides).
- venv: `.venv` (playwright 1.58, opencv-python-headless, numpy, pyobjc Quartz/AppKit, torch, transformers,
  scipy, sentencepiece). whisper.cpp (`whisper-cli`, model `CAPTCHA_WHISPER_MODEL`, default
  `~/.local/share/whisper-cpp/ggml-large-v3-turbo.bin`) for audio (optional).

## Open issues
- Screenshot-only puzzle path is weak on low-contrast pieces (motion mask misses the piece interior).
  Most widgets expose their pixels (canvas / image URL), which is the strong path.
- DataDome puzzle variant untested live (layout known from the 2026 markup).
- hCaptcha image tasks: unsolved (adversarial canvas tasks beat a 4B VLM).
- reCAPTCHA audio: transcription works, Google rejects the answer from these browsers.
- Google's grid rounds vary: budget ~2 min per reCAPTCHA in the worst case.
- A Linux box with Xvfb + xdotool would give true OS-level input for headed Chrome; not built.
