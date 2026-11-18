# Identity pool, sign-ins, site accounts

Reference for the `agent-browsers` skill. The hold/release rules for the identity browser stay in SKILL.md.

## Identity pool
- `identity-a`… (`HB_POOL` copies, MCP ports `HB_PORT_BASE+2…`, debug ports `HB_DEBUG_BASE+2…`) are copies of the
  master made by `pw-identity seed` (APFS clones, about a second; `install.sh` seeds copies never seeded, and the
  6-hourly `<HB_LABEL_PREFIX>.identity-seed` job reseeds stale idle ones).
- Sign-ins travel between them by **cookie sync**: when a copy's Chrome closes, its newer cookies go to the master (or
  to a pending store applied at the master's next launch while it runs); when a copy launches, it pulls the master's
  and the pending newer cookies. A sign-in made in any copy reaches the master and every copy's next launch.
- **Google is master-only** (Google signs out a session it sees in several profiles): the hook blocks Google URLs on
  copies, so Gmail / Drive / Google sign-in go to `identity`.
- Only cookies sync: a site keeping its sign-in in localStorage needs it made on the master (or a fresh `seed`).
- **Master busy? Use a free copy** (the hook's refusal names the free ones; `$HB_HOME/pw-identity pool` lists all).

## Not signed in on a site? Sign in yourself
- The user's credentials come from wherever they keep them (their password manager, the Keychain); `creds` holds the
  ones agents created.
- Type a password into the field from a variable, never print it (not in replies, files or echoed scripts), and tick
  "keep me signed in".
- Never take a `browser_snapshot` or screenshot of a sign-in page with a password typed in (it captures the value).
- A first sign-in the automation can't pass: `pw-identity login <url>` opens a plain, non-automated window on the
  master for the user (Google refuses sign-in from automation-controlled Chrome); they sign in and quit it (Cmd-Q).
- Only a code or prompt on the user's phone goes to them.

## Site accounts: `creds`
- `$HB_HOME/creds new <domain>`: generates a password for `HB_LOGIN_EMAIL` (config.env; `new` refuses without it) and
  stores it only in the Keychain (service `<HB_LABEL_PREFIX>.creds.<domain>`, account = the login). Prints the login.
- `creds get <domain>` → the login; `creds get <domain> --print` → the password, only to a pipe or file (a script
  types it into the field); `creds check <domain>` → `ok` / `missing`.
- Sign up / sign in from a script on an identity profile so the account persists for every later agent: the cookie
  sync carries the sign-in to the master and the other copies.

## Blocked as automation? (Google sign-in, Cloudflare "Just a moment"…)
- Don't hand the user a click path. Run `$HB_HOME/pw-identity acquire <name>` (prints the profile path), then your own
  Playwright script (a file) on that profile: `launch_persistent_context(<profile>, executable_path=<Agent Chrome>,
  headless=False, ignore_default_args=["--enable-automation"],
  args=["--disable-blink-features=AutomationControlled"])`.
- Do the steps there, take screenshots (not of password fields), and close it cleanly (that ends the hold; a killed
  Chrome loses its last ~30 s of cookies).
- Akamai/DataDome sites catch even that Playwright-launched headed Chrome: use `plain-chrome --profile <identity
  profile> open <url>` instead.

## Scripts that need the user's sign-ins
- Launch your own Chrome on a profile after `pw-identity acquire <name> --any` (a free copy first, the master only
  when no copy is free, so it stays free for Google); it prints `acquired <browser> for <name>` and the profile path.
  `-b identity` insists on the master (Google).
- That frees an idle agent hold, records the script as holder (agents get "held by the script …"), syncs cookies in,
  and exits 1 only if every identity browser is busy. The hold ends (and the copy's cookies sync out) when that Chrome
  exits.
- Scripts launching Chrome themselves pass `--use-mock-keychain --password-store=basic` (Playwright adds them itself).
