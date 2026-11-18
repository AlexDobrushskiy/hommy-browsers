# hommy-browsers

Shared browsers for AI agents (Claude Code, Codex, opencode) on a Mac: Playwright MCP servers that launchd keeps
running, a plain-launched Chrome that gets through Akamai and DataDome walls, an identity pool that holds your own
sign-ins with one holder per browser, Keychain-only site credentials for the logins agents create, and an optional
local captcha solver (no paid services). Part of [hommy](https://github.com/AlexDobrushskiy/hommy); works on its own.

macOS on Apple Silicon only. Every browser is **Agent Chrome**: your installed Google Chrome cloned under its own
bundle id and ad-hoc re-signed, so sites see plain Chrome while macOS never confuses it with your own Chrome.

## Install

[hommy](https://github.com/AlexDobrushskiy/hommy)'s one-line installer sets this up for you (stage 7). On its own:

Prerequisites: Google Chrome, Node.js ≥ 20 (with npm), `uv`, `ffmpeg` (`brew install uv ffmpeg`); Ollama only for the
captcha solver.

```sh
git clone https://github.com/AlexDobrushskiy/hommy-browsers ~/.local/share/hommy-browsers
~/.local/share/hommy-browsers/install.sh            # add --with-captcha for the solver and its models
claude plugin marketplace add ~/.local/share/hommy-browsers
claude plugin install hommy-browsers@hommy-browsers
```

`install.sh [--with-captcha] [--no-launchd] [--uninstall]` is idempotent: `npm ci` only when the lockfile changed,
Agent Chrome rebuilt only when Chrome's version changed, a launchd job reloaded only when its plist changed (an engine
upgrade changes the servers' plists, so they restart onto it), pool copies seeded once a master profile exists. It
fails when a server does not answer an MCP `initialize`, and ends with `changed: <n>` (0 on a re-run with nothing
new). `--uninstall` stops and removes the services; your profiles stay in `HB_STATE_DIR` until you delete them.

Sign in once to the sites your agents should use as you: `~/.local/share/hommy-browsers/pw-identity login <url>`
opens a plain window on the identity profile; sign in, tick "keep me signed in", quit it.

## Configuration: `config.env`

`config.env` in the checkout (plain `KEY=value` lines; `lib/hbconfig.py` reads it); every key is optional.

| Key | Default | Meaning |
|---|---|---|
| `HB_PORT_BASE` | `8931` | lanes MCP port; identity `+1`; pool copies `+2…` |
| `HB_DEBUG_BASE` | `9331` | the servers' Chrome debug ports, same layout (for `captcha-solve --lane/--identity`) |
| `HB_POOL` | `4` | identity pool copies, 0–4 (`identity-a…d`) |
| `HB_LABEL_PREFIX` | `dev.hommy.browsers` | launchd labels `<prefix>.{lanes,identity,identity-<x>,chrome-refresh,identity-seed,reaper}`; Keychain services `<prefix>.creds.<domain>` |
| `HB_BUNDLE_ID` | `dev.hommy.agent-chrome` | Agent Chrome's bundle id |
| `HB_SITES_DIR` | empty | your own site notes, `<dir>/<site>/README.md` (the skill reads them) |
| `HB_LOGIN_EMAIL` | empty | the login `creds new` registers with (no default: `new` refuses without it) |
| `HB_NODE` | first `node` on `PATH` at install | node ≥ 20 by absolute path |
| `HB_STATE_DIR` | `~/.local/state/hommy-browsers` | all runtime state (below) |
| `HB_IDENTITY_PROFILE` | `$HB_STATE_DIR/identity-profile` | the identity master profile |
| `HB_CHROME_APP` | `/Applications/Google Chrome.app` | the Chrome that Agent Chrome clones |
| `HB_REAPER_SCOPE` | `all` | `all`: the reaper also closes orphaned throwaway browsers no install owns; `own`: this install's only |
| `HB_KEYCHAIN` | empty | `creds`' keychain file; empty = your default keychain |

Several installs can run side by side (a test install next to yours): each needs its own ports, label prefix, bundle
id and state dir. Nothing of one install ever restarts, kills or reaps another's.

## Components

| Path | What |
|---|---|
| `serve` | One Playwright MCP server (launchd): **lanes** (a throwaway context per MCP connection, one shared headless Chrome), **identity** (the persistent master profile, one holder at a time) and **identity-a…** (the pool copies). Bound to 127.0.0.1. |
| `agent-chrome` | Builds Agent Chrome into `$HB_STATE_DIR/agent-chrome/`; `--print-app` prints the app bundle; `refresh` (6-hourly) rebuilds after a Chrome update and restarts this install's servers once no Chrome of this install (by bundle id) runs. |
| `real-chrome-ua.cjs` | Init page: headless tabs present headed Chrome's User-Agent and client hints. |
| `pw-identity` | Identity leases (Claude Code hooks), the idle reaper, `login`, `acquire` for scripts, `seed` (APFS-clone the master into the copies, minus Google), two-way cookie sync, `pool` status. |
| `plain-chrome` | Agent Chrome started like a person starts it; automation attaches after the first load. Shared persistent profile + a jar of bot-check cookies. |
| `creds` | `creds new|get|check <domain>`: site logins agents create, Keychain only. |
| `browser-reaper` | Lists and (with `--kill`) closes leaked throwaway browsers of this install; launchd runs it every 30 min. |
| `captcha-solve`, `captcha/` | The solver (opt-in): DataDome/GeeTest sliders, checkboxes, reCAPTCHA image grids with a local VLM. [`captcha/README.md`](captcha/README.md). |
| `launchd/` | plist templates, rendered from `config.env` by `install.sh`. |
| `config/` | Snippets to register the servers in Codex and opencode, and the lease hooks for a Claude Code without the plugin. |
| `plugin/`, `.claude-plugin/` | The Claude Code plugin (below). |

## The Claude Code plugin

Marketplace `hommy-browsers` at the repo root, plugin `hommy-browsers` in `plugin/`: the `agent-browsers` skill (lane
vs identity vs plain-chrome, the escalation ladder, close what you open, identity hold/release, subagents, confirm
before committing), the identity-lease hooks (`pw-identity hook pre|post` on every identity tool) and the MCP
registrations `pw-lane`, `pw-lane-a…d`, `identity`, `identity-a…`. Install it from this checkout so plugin and engine
versions match; `install.sh` writes the registrations (`plugin/.mcp.json`, from `config.env`) and the engine path the
hooks use (`plugin/.hb-home`). `userConfig` (`lanes_port`, `identity_port`, `pool`) mirrors `config.env` with the engine
defaults; the ports in `config.env` are the ones served.

## Runtime state (`HB_STATE_DIR`, never in the repo)

`identity-profile/` (your sign-ins), `identity-pool/` (copies, leases, pending cookie store, `sync.log`),
`plain-chrome/` (shared profile, `trust-cookies.db`), `agent-chrome/` (the clone), `mcp/<server>/` (screenshots,
downloads), `run/` (server configs), `logs/`, `captcha/` (attempt log, screenshots).

## Models (only with `--with-captcha`)

`qwen3.5:4b` through Ollama (reCAPTCHA tiles, locating elements; 3.3 GB) and `google/owlv2-base-patch16-ensemble`
(4x4 grids; ~0.6 GB from Hugging Face). Without them the escalation ladder skips the solver rung.

## Risks

- **Site terms:** getting past bot walls and solving captchas can breach a site's terms of use; you decide which sites
  your agents visit, and the solver is off unless you install it (`--with-captcha`).
- **Your sign-ins:** the identity profile holds your real sessions; any agent that holds the identity browser acts as
  you on those sites. Agents ask before anything that commits (pay, order, submit, send), as the skill tells them.
- **Signing:** Agent Chrome is re-signed ad hoc on your Mac (no Developer ID); it is your own Chrome under another
  bundle id, not a modified browser.

## Uninstall

`~/.local/share/hommy-browsers/install.sh --uninstall` stops and removes the services, then
`claude plugin uninstall hommy-browsers@hommy-browsers` and `claude plugin marketplace remove hommy-browsers`, and
`rm -rf ~/.local/share/hommy-browsers` (the checkout) and `~/.local/state/hommy-browsers` (your profiles and sign-ins)
when you no longer want them.

## Development

`scripts/ci-preflight.sh` runs CI's checks locally (offline tests in `tests/`, shellcheck, the leak gate's scanner,
gitleaks). End-to-end runs against a scratch install: [`docs/testing.md`](docs/testing.md), [`e2e/README.md`](e2e/README.md).

## License

MIT, see [LICENSE](LICENSE).
