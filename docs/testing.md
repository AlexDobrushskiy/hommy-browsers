# hommy-browsers: testing

- **Offline (CI `test` job, `scripts/ci-preflight.sh test`):** `tests/` with `python3 -m unittest`: config and port
  layout for pool 0–4, plist rendering for two prefixes, the plugin's MCP file, the reaper's own-install filter,
  `creds` against a scratch keychain (its own `HOME`, never the user's keychains), the identity lease through
  `pw-identity hook` (no Chrome), the shipped captcha benchmark set. No Chrome, no launchd, no network.
- **End to end:** a scratch install of the tree under test, driven by the `hommy` repo's harness (a clean worktree of
  `hommy` at `origin/main` next to this checkout, `../hommy-harness`): `scripts/test-env.sh up --slot 1 --layout
  browsers --no-start` (scratch `HOME` `/tmp/hm-s1/home` with its own keychain), `scripts/sx.sh` (clean environment,
  scratch `HOME`), `scripts/mcp-call.sh`, `scripts/grep-tree.sh`, `bin/hommy-e2e static-site`. This repo adds
  `scripts/test-install.sh` (the copy of the tree at `/tmp/hm-s1/home/.local/share/hommy-browsers` + its test
  `config.env`), `scripts/test-site.sh` (the static test page), `scripts/mcp-session.sh` and `scripts/mcp-init.sh`
  (MCP sessions kept across commands; the harness client opens one per call).
- **Slot 1, layout B:** lanes `4720`, identity `4721`, identity-a `4722`, Chrome debug `4723`–`4725`, static page
  `4726`; labels `dev.hommy.test-s1.browsers.*`, bundle id `dev.hommy.test-s1.agent-chrome`, state
  `/tmp/hm-s1/home/.local/state/hommy-browsers`, pool 1, `HB_REAPER_SCOPE=own`, no captcha models.
- **Never the live setup:** every command runs under `sx`; the install's labels, ports, bundle id and state dir are
  its own, and the reaper and `agent-chrome refresh` act only on this install's processes. The harness's
  `scripts/live-guard.sh check` brackets a run.
- Rows and commands: [`../e2e/README.md`](../e2e/README.md).
