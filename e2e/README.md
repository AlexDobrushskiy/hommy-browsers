# E2E: how testers run hommy-browsers (phase 03 rows E1–E10)

Design: [`docs/testing.md`](../docs/testing.md). Run every command from this checkout's root in `zsh -l` or `bash`.
The harness is the `hommy` repo's, from a **clean worktree at `origin/main`** next to this checkout
(`../hommy-harness`), never from the `hommy` phase checkout. Slot 1, layout B: lanes `4720`, identity `4721`,
identity-a `4722`, debug `4723`–`4725`, static page `4726`.

## Start here

```sh
H=$(cd .. && pwd)/hommy-harness
[ -d "$H" ] || git -C ../app worktree add --detach "$H" origin/main       # once; ../app is the hommy clone
git -C ../app fetch -q origin && git -C "$H" checkout -q --detach origin/main && "$H/scripts/dev-build.sh"
SX() { "$H/scripts/sx.sh" --slot 1 "$@"; }
M() { "$H/scripts/mcp-call.sh" "$@"; }
MS() { SX scripts/mcp-session.sh "$@"; }
HB=/tmp/hm-s1/home/.local/share/hommy-browsers
"$H/scripts/live-guard.sh" check || "$H/scripts/live-guard.sh" snapshot  # ok: the existing snapshot stands
"$H/scripts/test-env.sh" down --slot 1 --purge                            # cold start (no install)
"$H/scripts/test-env.sh" up --slot 1 --layout browsers --no-start         # scratch HOME + keychain; ends `ready`
scripts/test-install.sh --slot 1                                          # prints $HB: the copy of this tree + config.env
scripts/test-site.sh start                                                # `static site on :4726`
```

- `test-install.sh` copies the working tree (tracked + untracked, not ignored) to `$HB` and writes its `config.env`
  (ports above, `HB_POOL=1`, labels `dev.hommy.test-s1.browsers.*`, bundle id `dev.hommy.test-s1.agent-chrome`,
  state `/tmp/hm-s1/home/.local/state/hommy-browsers`, `HB_REAPER_SCOPE=own`, `HB_LOGIN_EMAIL=owner@example.test`).
- `scripts/mcp-session.sh PORT start | call TOOL JSON | end`: one MCP session across commands. Lanes give every
  session its own throwaway context and `mcp-call.sh` opens a new session per call, so a row that navigates and then
  snapshots uses one session; an identity call through `mcp-call.sh` leaves its session holding the profile.
- The static page is this repo's `e2e/fixtures/static` (heading `hommy-browsers test page`), served by the harness's
  `hommy-e2e static-site --addr 127.0.0.1:4726` (the binary has `--addr`, not `--port`).
- Never print a password, the Claude token or a matched leak line. Screenshots: synthetic pages only.

## Rows

**E2 · leak gate on the branch**
```sh
scripts/leak-scan.sh --history; scripts/leak-scan.sh .                # each: `… 0 hits`
git log --format='%ae %ce' | tr ' ' '\n' | sort -u                     # only the noreply address and noreply@github.com
git ls-files | grep -c '^sites/'; git ls-files 'captcha/data/bench/*' | grep -c png   # 0 and 0
```

**E3 · install twice (scratch HOME, no install)**
```sh
SX $HB/install.sh; echo "exit $?"          # 0; the summary lists the created services, `changed: <n>`
pids() { for s in lanes identity identity-a; do launchctl print gui/$(id -u)/dev.hommy.test-s1.browsers.$s | awk '/^\tpid =/ {print $3}'; done; }
pids > /tmp/hm-s1/pids1
SX $HB/install.sh; echo "exit $?"          # 0, `changed: 0`
pids | diff /tmp/hm-s1/pids1 - && echo "same PIDs"
```

**E4 · services, MCP, bundle id**
```sh
for s in lanes identity identity-a; do launchctl print gui/$(id -u)/dev.hommy.test-s1.browsers.$s | grep -m1 'state ='; done   # running ×3
SX scripts/mcp-init.sh 4720                # a JSON-RPC result with serverInfo
SX defaults read "$(SX $HB/agent-chrome --print-app)/Contents/Info" CFBundleIdentifier   # dev.hommy.test-s1.agent-chrome
```
Deviation: the row's `M 4720 initialize {}` asks `mcp-call.sh` for a *tool* named `initialize` (`Tool "initialize"
not found`); `mcp-init.sh` sends the MCP `initialize` request itself.

**E5 · a lane opens Chrome on the first tool call and closes the page**
```sh
curl -s -m 2 127.0.0.1:4723/json/version || echo "no lanes Chrome yet"     # nothing before the first call
MS 4720 start
MS 4720 call browser_navigate '{"url":"http://127.0.0.1:4726/"}' >/dev/null && echo navigated
curl -s 127.0.0.1:4723/json/version | grep '"Browser"'                     # a Browser field: the lane's Chrome
MS 4720 call browser_snapshot '{}' | jq -r '.content[0].text' | grep heading   # heading "hommy-browsers test page"
MS 4720 call browser_close '{}' | jq -r '.content[0].text' | head -2       # ok ("No open tabs")
MS 4720 end
sleep 30; SX $HB/browser-reaper --json
```
Then: `[]`: no Chrome of this install. The lanes server keeps one shared Chrome while any lane session is open and
closes it with the last one (`MS … end`); a lane session left open (e.g. by `mcp-call.sh`, which never ends its
session) keeps it until Playwright MCP's idle timeout (1 h), listed as `"server": "4720"`, `"action": "leave"`.

**E6 · identity lease (hooks)**
```sh
MS 4721 start; MS 4721 call browser_navigate '{"url":"http://127.0.0.1:4726/"}' >/dev/null; MS 4721 call browser_close '{}' >/dev/null; MS 4721 end
SX $HB/pw-identity seed --all              # identity-a: seeded (a copy needs a master profile: the lines above make it)
P=e2e/payloads
SX $HB/pw-identity hook pre <$P/pre-navigate-s-A.json; echo "exit $?"     # 0: s-A allowed
SX $HB/pw-identity hook pre <$P/pre-navigate-s-B.json; echo "exit $?"     # 2: "held by session s-A …", "Free now: identity-a"
SX $HB/pw-identity pool                                                    # identity held … by session s-A
SX $HB/pw-identity hook pre <$P/pre-close-s-A.json; SX $HB/pw-identity hook post <$P/post-close-s-A.json
SX $HB/pw-identity hook pre <$P/pre-navigate-s-B-again.json; echo "exit $?"   # 0: s-B allowed
SX $HB/pw-identity hook post <$P/post-close-s-B.json                       # cleanup
```

**E7 · the plugin**
```sh
SX claude plugin marketplace add $HB
SX claude plugin install hommy-browsers@hommy-browsers --config lanes_port=4720 --config identity_port=4721 --config pool=1
SX claude plugin list --json | jq '.[] | select(.id == "hommy-browsers@hommy-browsers") | {enabled, mcpServers}'
SX claude plugin details hommy-browsers@hommy-browsers
(cd /tmp/hm-s1 && SX claude mcp list | grep plugin:hommy-browsers)   # what a session really loads
```
Expect: `enabled: true`; details list the `agent-browsers` skill, hooks PreToolUse/PostToolUse/PostToolUseFailure, and
MCP servers `pw-lane`, `pw-lane-a…d`, `identity`, `identity-a` whose URLs use 4720/4721/4722 only; `mcp list` shows
all 7 `plugin:hommy-browsers:*` servers `Connected` (`pw-lane-a…d` as `…:4720/mcp?lane=a…d`: Claude Code drops a
plugin server whose URL repeats another's).
Deviation: plugin configs have no arithmetic or conditionals, so `install.sh` writes `plugin/.mcp.json` from
`config.env` (exactly `HB_POOL` copies); `userConfig` declares the three values with the engine defaults. Claude Code
reads `.mcp.json` from the checkout (a directory marketplace), so a port change plus `install.sh` reaches new
sessions without reinstalling the plugin.

**E8 · creds (config `HB_LOGIN_EMAIL=owner@example.test`)**
```sh
SX $HB/creds new shop.example.test         # owner@example.test
SX $HB/creds get shop.example.test         # owner@example.test
SX $HB/creds check shop.example.test       # ok
SX $HB/creds get shop.example.test --print | "$H/scripts/grep-tree.sh" /tmp/hm-s1 --exclude Keychains   # 0 files
```

**E9 · uninstall**
```sh
SX $HB/install.sh --uninstall
launchctl list | grep -c dev.hommy.test-s1.browsers        # 0
SX $HB/browser-reaper --json                                # []
"$H/scripts/test-env.sh" down --slot 1 --purge              # `down` (nothing listens on 4720–4729)
lsof -nP -iTCP:4720-4729 -sTCP:LISTEN | wc -l               # 0
```

## Maintainer-only rows (the build workspace's `ops/src-record.sh`)

`ops/src-record.sh` records the private source's HEAD, remote URL, `status --porcelain --ignored`, a SHA-256 over
its working tree and a marker; `check` prints `src-record: unchanged` or `src-record: changed: <field>` (counts only).

**E1** (after ship)
```sh
gh repo view AlexDobrushskiy/hommy-browsers --json isPrivate          # isPrivate: true
git log --max-parents=0 --format='%H %s' origin/main                    # one root commit (the skeleton, below)
git log --format='%h %s' origin/main                                     # the skeleton + the phase-03 commit
git show --stat origin/main | tail -3                                    # the phase commit holds the whole engine
../ops/src-record.sh check                                               # src-record: unchanged
```
Deviation: the repo was created before this phase with a skeleton root commit (README stub + pre-push hook; lane
note), and `main` only moves by fast-forward, so the whole engine is the one phase commit on top of that root, not
the root itself. Phase 07 publishes a fresh repository from the clean `main` history (S9).

**E10** (all rows done)
```sh
"$H/scripts/live-guard.sh" check           # live-guard: ok (…)
../ops/src-record.sh check                 # src-record: unchanged
```
