---
name: agent-browsers
description: Use before any browser work on the user's behalf — reading or searching real websites (shops, prices, availability, bookings, order status), anything that needs the user's own sign-in, Cloudflare "Just a moment" / bot checks, "Access denied" / 403 / captcha / blank page / "I'm blocked on this site", "Browser is already in use", or dispatching subagents that browse. Picks lane vs identity vs plain-chrome, has the escalation ladder and the close-what-you-open rules; never the user's own Chrome.
---

# Agent browsers: lanes + identity

Agents never touch the user's own Chrome: no browser extension, no CDP attach to it, no copy of its profile. They use
the shared Playwright MCP servers of hommy-browsers that launchd keeps running (`$HB_HOME/serve`, labels
`<HB_LABEL_PREFIX>.lanes` / `.identity` / `.identity-<x>`), all on **Agent Chrome** (`$HB_HOME/agent-chrome`: a
re-signed clone of the installed Chrome under its own bundle id, so sites see plain Chrome). `HB_HOME` is the engine
checkout (default `~/.local/share/hommy-browsers`); its `config.env` sets ports, pool size and the state dir.
Never launch agent or script Chrome from Google Chrome.app itself (macOS takes it for the user's Chrome) or Chrome for
Testing; scripts pass `$HB_HOME/agent-chrome --no-update` as `executable_path`.

The plugin registers the servers as `pw-lane`, `pw-lane-a`…`-d`, `identity` and `identity-a`… (as many copies as
`HB_POOL`); their tools are `mcp__plugin_hommy-browsers_<server>__*`.

| | **Lane** `pw-lane` (+ `pw-lane-a`…`-d`) | **Identity** `identity` (+ copies `identity-a`…) |
|---|---|---|
| Profile | throwaway, in memory, one per MCP connection | master `HB_IDENTITY_PROFILE`, copies `HB_STATE_DIR/identity-pool/<x>`; persistent |
| Logins | none | whatever the user signed into (`pw-identity login <url>`) |
| Window | headless (presents as headed Chrome: UA + client hints) | headless too; sign-ins happen in a separate visible window |
| Concurrency | unlimited: each session or subagent gets its own context | one holder per browser (master + `HB_POOL` copies) |
| Use for | everything that doesn't need the user signed in | only the steps that need a sign-in |

**Rule: if it doesn't need the user's sign-in, use a lane.** For public pages try a plain web fetch first; use a lane
when the page needs JS, or the fetch gets a 403 or a bot check.

**Identity pool:** the copies share the master's sign-ins by cookie sync (a sign-in made in any copy reaches all).
**Google is master-only** (`identity`). **Master busy? Use a free copy**; never wait on a busy identity browser.
Pool, sync, sign-ins, site accounts, scripts on the profile: [references/logins-and-accounts.md](references/logins-and-accounts.md).

**Third option: `plain-chrome`** (`$HB_HOME/plain-chrome`): Agent Chrome started like a person starts it; Playwright
attaches only after the first load. Beats walls that catch *Playwright-launched* Chrome (Akamai, DataDome);
`--headless` loses that. `plain-chrome fetch [--warm HOME_URL] URL…` to read pages; `plain-chrome open URL` → drive
**only your `target` tab** over CDP, then `plain-chrome close PORT TARGET`. Shared persistent profile + trust-cookie
jar (a solved captcha carries to every browser): **never open a second browser on a site whose captcha is showing or
was just solved**. Usage, flags, jar: [references/plain-chrome.md](references/plain-chrome.md).

**Your own site notes:** when `HB_SITES_DIR` is set, `$HB_SITES_DIR/<site>/README.md` holds what you learned about a
site (its wall, the route that works, pace, don'ts). Read it before working on that site; add to it when you meet a
new wall or a result changes (date it).

## Blocked? Escalation ladder: never settle for "blocked"
A blocked site is never a reason to answer from other data or to drop the site silently. Climb the ladder; move up
after **one** blocked hit per rung (every blocked hit raises the site's bot score; a burst of test hits can get the
network's IP flagged for hours):
1. Plain web fetch (static public pages).
2. Lane (`pw-lane`; subagents `pw-lane-a…d`).
3. `identity` MCP (real history and sign-ins).
4. `plain-chrome fetch/open` (shared profile, seeded from the jar; `--warm <home page>` for sites that captcha a cold
   deep link).
5. `plain-chrome --profile <identity profile>` (after `pw-identity acquire <name>`): plain launch + real history.
6. A captcha is showing (slider, puzzle, checkbox) → **solve it yourself, in that same browser** (the solve cookie is
   tied to that profile; the jar then carries it to the others), when the solver is installed (`install.sh
   --with-captcha`): `$HB_HOME/captcha-solve --cdp http://127.0.0.1:PORT --target TARGET` (or `captcha-solve --open
   URL`), then `plain-chrome close PORT TARGET` so the cookie reaches the jar. On a tab opened with the MCP tools:
   `captcha-solve --lane --url <part of its URL>` or `--identity --url …` (while you hold identity). One JSON line:
   `solved` / `failed` / `hard-block` / `unsupported` / `budget`. On a DataDome site, solve in `plain-chrome`, never in
   a lane. Two failed attempts on a site → stop (the solver enforces 2 per run, 6 per site per 6 h). Docs:
   `$HB_HOME/captcha/README.md`.
7. Your solve failed or the solver is not installed → ask the user for a one-time solve in that window (`plain-chrome
   show PORT TARGET`, or `pw-identity login <url>` for the identity profile); after that rungs 3–5 pass for that site.

Only after rung 7 does it go to the user, as one concrete ask ("solve the slider on <site> in the window I opened"),
never "I couldn't access it". In the answer, name any site you could not read.

## Close what you open (every browser, every run)
**Whoever opens a browser closes it, on success and on failure.**
- MCP tools (lane, identity): `browser_close` as the last browser step of the task.
- `plain-chrome`: `plain-chrome close PORT TARGET` when done.
- Your own script (Playwright/CDP): open inside a context manager / `try … finally: context.close()`, never a bare
  launch. One run = one tab; reuse it, close extra tabs.
- Tests: a real browser only behind an explicit e2e marker; the fixture closes it in teardown, and the suite ends with
  no Chrome of yours left (`browser-reaper` shows none).
- Never `pkill`/`killall` by pattern: kill only your own PID, close only your own PORT/TARGET or tab.
- Hand-off windows (OAuth or device-code sign-in, a page for the user to look at): close them once the hand-off is done.
- Safety net, not a licence: `$HB_HOME/browser-reaper` (dry run by default, `--kill` to act; launchd runs it every
  30 min) kills only throwaway temp-profile browsers that are orphaned or > 6 h old, and lists the rest.

## Identity browser: hold it for one step, then release
- One step, then `browser_close` on that identity server (check the order, then close; download the invoice, then
  close). Persistent cookies make the reopen a silent hop.
- Never probe it (`navigate about:blank` "to check") and never hold it across a wait on the user. A hook-driven reaper
  closes it after 180 s idle anyway.
- **Busy?** The plugin's PreToolUse hook blocks the call naming who holds it, for how long, and which copies are
  free: switch to a free `identity-x` (same sign-ins). Don't force a holder off. Check: `$HB_HOME/pw-identity pool`.
- **Not signed in on a site?** Sign in yourself with the user's stored credentials; type a password from a variable,
  never print it, never snapshot or screenshot a sign-in page with a password typed in; tick "keep me signed in".
- **Need an account on a site?** `$HB_HOME/creds new <domain>` registers `HB_LOGIN_EMAIL` with a generated password
  kept only in the Keychain; `creds get <domain>` prints the login, `creds get <domain> --print` the password, only to
  a pipe (straight into the field from a script). Codes and confirm links: the user's mailbox if you have access, else
  ask the user, batched.
- **Blocked as automation** (Google sign-in, Cloudflare "Just a moment")? `pw-identity acquire <name>` + your own
  headed Playwright script on that profile; Akamai/DataDome sites: `plain-chrome --profile <identity profile> open <url>`.
- **Scripts** that need the user's sign-ins: `pw-identity acquire <name> --any` (a free copy first; `-b identity` for
  Google).
- **Never park on something you can do yourself**: a sign-in page, a bot check, a consent screen or a settings form is
  your job. Escalate only a code or prompt on the user's phone, or a commit they haven't approved.
- Never sign into Chrome itself (Chrome Sync) on these profiles.

## Subagents
A subagent shares its parent's MCP connections, so give each browsing subagent its own lane: `pw-lane-a`, `-b`, `-c`,
`-d` in order, never the same one twice in one fan-out. The parent keeps `pw-lane`. Put the lane in the prompt: "Use
the `pw-lane-b` browser tools for all browser work; no other browser." Same for signed-in work: give each subagent its
own identity copy (`identity-a`…); the parent keeps `identity` (and is the one to touch Google). Scripts in subagents
use `pw-identity acquire <name> --any` and get whichever is free.

## Rules
- **You act as the user** in the identity browser. Reading, searching, comparing and filling forms are fine. **Confirm
  with the user before anything that commits:** paying, ordering, booking, submitting, sending a message, accepting
  terms, changing account settings.
- Never send a test message or a test form to a real person or business: test only up to the filled form, then close.
- Screenshots go to the server's output dir (`$HB_STATE_DIR/mcp/<server>`) or a scratch dir.
- `browser_close` your lane when done.

## Troubleshooting
| Symptom | Fix |
|---|---|
| The browser tools are missing | Servers are configured at session start: `launchctl list \| grep <HB_LABEL_PREFIX>`, logs in `$HB_STATE_DIR/logs/`. Restart one with `launchctl kickstart -k gui/$(id -u)/<HB_LABEL_PREFIX>.lanes` (this ends every session's lane tabs) |
| "Browser is already in use" on identity | Another holder: `pw-identity status`; wait it out or use a free copy |
| Lane stuck on a bot check / blank page / 403 | Climb the escalation ladder: identity → `plain-chrome` → plain on the identity profile → solver → the user's one-time solve |
| The user's Chrome won't open or quit | Something launched Google Chrome.app on an agent profile: `pw-identity release --force`, then point it at Agent Chrome |
| DataDome 403 + "slide right" on the first request | (a) Playwright launched the browser → `plain-chrome` passes; (b) the IP is flagged (every fresh profile blocked) → solve it (rung 6), else the user once via `pw-identity login <url>` |
| Need to watch the browser | `PW_VISIBLE=1` in the plist's `EnvironmentVariables`, then `launchctl kickstart -k` the service |
