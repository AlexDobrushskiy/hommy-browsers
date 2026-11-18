# plain-chrome, Agent Chrome, DataDome, trust-cookie jar

Reference for the `agent-browsers` skill.

## Agent Chrome
`$HB_HOME/agent-chrome`: the installed Google Chrome cloned to `$HB_STATE_DIR/agent-chrome/current/Agent Chrome.app`
under this install's bundle id (`HB_BUNDLE_ID`), ad-hoc re-signed, rebuilt when Chrome updates (at server start and by
the 6-hourly `<HB_LABEL_PREFIX>.chrome-refresh` job). Same binary, so sites see plain Chrome.
- Never launch agent or script Chrome from Google Chrome.app itself: macOS takes that process for the user's Chrome
  (a Dock click activates the windowless agent; the user's Chrome won't open or quit).
- Not Chrome for Testing either: sites treat it differently.
- Scripts on the identity profile pass `executable_path` = `$HB_HOME/agent-chrome --no-update`, not `channel="chrome"`.

## plain-chrome (`$HB_HOME/plain-chrome`)
Agent Chrome started the way a person starts it (only a debug port, headed, no focus steal; the window opens behind
other windows); Playwright attaches over CDP only after the first page loaded, then can keep navigating that tab.
Beats bot walls that catch *Playwright-launched* Chrome (Akamai, DataDome). Headed vs headless and signed-in vs fresh
do **not** matter for those walls: what matters is whether Playwright launched the browser. `--headless` loses that.

- `plain-chrome fetch [--warm HOME_URL] URL…` → one JSON line per URL (`blocked`, `title`, `len`) +
  `<out>/NN.txt/.html/.jpg`. Good for reading pages.
- `plain-chrome open URL` → prints `{"cdp", "port", "target", ...}`; drive it from your own script with
  `p.chromium.connect_over_cdp(cdp)` and use **only the tab whose CDP targetId == `target`** (other agents may have
  tabs in the same browser); `plain-chrome close PORT TARGET` when done. `plain-chrome show PORT TARGET` brings the
  window on-screen for the user to solve a captcha.

### Shared profile + trust-cookie jar
- **The default profile is persistent and shared** (`$HB_STATE_DIR/plain-chrome/shared`): if a plain-chrome already
  runs on it you get a new tab, not a second browser; it closes when the last caller releases.
- **Trust-cookie jar** (`$HB_STATE_DIR/plain-chrome/trust-cookies.db`, list with `plain-chrome cookies`): bot-check
  cookies only (datadome, _abck, cf_clearance, reese84, _px…, never sign-ins) are saved from every plain-chrome on
  close and from the identity profile at each launch, and seeded into every launch. A captcha solved once in any of
  these browsers carries to all of them.
- **Never open a second browser on a site whose captcha is showing or was just solved; reuse that one.**
- `--fresh` = throwaway profile (still seeded), only to test whether the IP is flagged.
- `--profile <identity profile>` = plain launch with the user's history and sign-ins (run `pw-identity acquire
  <name>` first).
- plain-chrome launches with `--use-mock-keychain --password-store=basic` (the cookie encryption Playwright and
  `pw-identity login` use); any other script launching Chrome on an identity profile needs the same two flags, or
  Chrome drops every sign-in it cannot decrypt.

## DataDome 403 + "slide right" captcha on the first request
- (a) Playwright launched the browser: `plain-chrome` passes.
- (b) The IP is flagged (every fresh profile blocked, `plain-chrome` included): solve the captcha yourself (ladder
  rung 6); if that fails, ask the user to solve it once in the identity profile with `pw-identity login <url>`; after
  that the identity MCP passes.
- A `datadome` cookie in the identity profile does **not** prove a solve: blocked visits set one too.
- Lanes stay blocked (no cookie), so these sites go to `identity` / `plain-chrome`; solve in `plain-chrome`, never in
  a lane (a correct slide in a Playwright lane gets "Access is temporarily restricted").
