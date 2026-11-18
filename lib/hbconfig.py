#!/usr/bin/env python3
"""hommy-browsers configuration: one place that reads `<HB_HOME>/config.env` and derives ports, labels and paths.

`config.env` holds plain `KEY=value` lines (`#` comments; values may use `~`, `$HOME` and earlier keys). A key left
out takes its default. `HB_CONFIG` names another file (tests); `HB_HOME` is the engine checkout (this file's parent).

  hbconfig.py shell                 the config as `KEY='value'` lines for bash (`eval "$(…)"`)
  hbconfig.py get KEY               one value
  hbconfig.py ports                 `<server> <port> <debug port>` per enabled server
  hbconfig.py labels                every launchd label this install owns, one per line
  hbconfig.py render-plists DIR     the launchd plists (<label>.plist) into DIR
  hbconfig.py render-mcp FILE       the plugin's MCP registrations (.mcp.json) into FILE
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HB_HOME = Path(os.environ.get("HB_HOME") or Path(__file__).resolve().parent.parent)
POOL_LETTERS = "abcd"
DEFAULTS = {
    "HB_PORT_BASE": "8931",            # lanes; +1 identity; +2… the pool copies identity-a…
    "HB_DEBUG_BASE": "9331",           # the servers' Chrome debug ports, same layout
    "HB_POOL": "4",                    # identity pool copies, 0–4
    "HB_LABEL_PREFIX": "dev.hommy.browsers",
    "HB_BUNDLE_ID": "dev.hommy.agent-chrome",
    "HB_SITES_DIR": "",                # the user's own per-site notes (<dir>/<site>/README.md); empty: none
    "HB_LOGIN_EMAIL": "",              # the login `creds new` registers with; no default
    "HB_NODE": "",                     # node ≥ 20 by absolute path; empty: the first `node` on PATH at install
    "HB_STATE_DIR": "~/.local/state/hommy-browsers",
    "HB_IDENTITY_PROFILE": "",         # the identity master profile; empty: $HB_STATE_DIR/identity-profile
    "HB_CHROME_APP": "/Applications/Google Chrome.app",
    "HB_REAPER_SCOPE": "all",          # all: this install's leaks + orphaned throwaway browsers; own: this install's only
    "HB_KEYCHAIN": "",                 # creds' keychain file; empty: the user's default keychain
}
PATH_KEYS = {"HB_SITES_DIR", "HB_NODE", "HB_STATE_DIR", "HB_IDENTITY_PROFILE", "HB_CHROME_APP", "HB_KEYCHAIN"}
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")


class ConfigError(Exception):
    pass


def config_file():
    return Path(os.environ.get("HB_CONFIG") or HB_HOME / "config.env")


def _expand(value, env):
    value = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: env.get(m.group(1), os.environ.get(m.group(1), "")),
                   value)
    return os.path.expanduser(value) if value.startswith("~") else value


def load(path=None):
    """The effective config: defaults, then the file's keys. Raises ConfigError on a bad value."""
    path = Path(path) if path else config_file()
    raw = {}
    if path.exists():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, eq, value = line.partition("=")
            key = key.strip()
            if not eq or not re.fullmatch(r"HB_[A-Z_]+", key):
                raise ConfigError(f"{path}:{n}: expected HB_KEY=value")
            if key not in DEFAULTS:
                raise ConfigError(f"{path}:{n}: unknown key {key}")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            raw[key] = value
    env = {}
    for key, default in DEFAULTS.items():
        env[key] = _expand(raw.get(key, default), env)
    if not env["HB_IDENTITY_PROFILE"]:
        env["HB_IDENTITY_PROFILE"] = str(Path(env["HB_STATE_DIR"]) / "identity-profile")
    for key in ("HB_PORT_BASE", "HB_DEBUG_BASE", "HB_POOL"):
        if not env[key].isdigit():
            raise ConfigError(f"{key} must be a number")
    if not 0 <= int(env["HB_POOL"]) <= len(POOL_LETTERS):
        raise ConfigError("HB_POOL must be 0–4")
    for key in ("HB_PORT_BASE", "HB_DEBUG_BASE"):
        if not 1024 <= int(env[key]) <= 65535 - 6:
            raise ConfigError(f"{key} out of range")
    for key in ("HB_LABEL_PREFIX", "HB_BUNDLE_ID"):
        if not LABEL_RE.match(env[key]):
            raise ConfigError(f"{key} must be a reverse-DNS name")
    if env["HB_REAPER_SCOPE"] not in ("all", "own"):
        raise ConfigError("HB_REAPER_SCOPE must be all or own")
    for key in PATH_KEYS:
        if env[key] and not os.path.isabs(env[key]):
            raise ConfigError(f"{key} must be an absolute path")
    return env


def servers(cfg):
    """[(name, port, debug port)] of the enabled MCP servers: lanes, identity, identity-a… (HB_POOL of them)."""
    base, dbg, pool = int(cfg["HB_PORT_BASE"]), int(cfg["HB_DEBUG_BASE"]), int(cfg["HB_POOL"])
    out = [("lanes", base, dbg), ("identity", base + 1, dbg + 1)]
    out += [(f"identity-{x}", base + 2 + i, dbg + 2 + i) for i, x in enumerate(POOL_LETTERS[:pool])]
    return out


def pool(cfg):
    return [f"identity-{x}" for x in POOL_LETTERS[:int(cfg["HB_POOL"])]]


def state(cfg, *parts):
    return Path(cfg["HB_STATE_DIR"]).joinpath(*parts)


def profile(cfg, browser):
    """The Chrome profile of an identity browser."""
    if browser == "identity":
        return Path(cfg["HB_IDENTITY_PROFILE"])
    return state(cfg, "identity-pool", browser.split("-", 1)[1])


def chrome_root(cfg):
    return state(cfg, "agent-chrome")


def agent_chrome_app(cfg):
    return chrome_root(cfg) / "current" / "Agent Chrome.app"


def agent_chrome_exe(cfg):
    return agent_chrome_app(cfg) / "Contents" / "MacOS" / "Google Chrome"


def label(cfg, name):
    return f"{cfg['HB_LABEL_PREFIX']}.{name}"


# ---------- launchd ----------

def services(cfg):
    """[(label-suffix, template, substitutions)] of every launchd job this install runs."""
    out = [(name, "server.plist.template", {"__NAME__": name}) for name, _, _ in servers(cfg)]
    out.append(("chrome-refresh", "chrome-refresh.plist.template", {}))
    if pool(cfg):
        out.append(("identity-seed", "identity-seed.plist.template", {}))
    out.append(("reaper", "reaper.plist.template", {}))
    return out


def labels(cfg):
    return [label(cfg, name) for name, _, _ in services(cfg)]


def engine_fingerprint(cfg):
    """A hash over what a running server executes (serve, its init page, config code, the npm lockfile, the Agent
    Chrome build) and the config it reads at start: in the server plists, so an engine upgrade or a config change
    (ports, pool, profile) changes them and install.sh restarts the servers."""
    h = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode() + b"\0")
    for rel in ("serve", "real-chrome-ua.cjs", "lib/config.sh", "lib/hbconfig.py", "package-lock.json"):
        f = HB_HOME / rel
        h.update(rel.encode() + b"\0" + (f.read_bytes() if f.exists() else b"") + b"\0")
    try:
        h.update(os.readlink(chrome_root(cfg) / "current").encode())
    except OSError:
        pass
    return h.hexdigest()[:16]


def _xml(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_plists(cfg, outdir, home=None, node=None):
    """Write <label>.plist for every service into outdir. Returns the labels."""
    home = home or os.environ.get("HOME", str(Path.home()))
    node = node or cfg["HB_NODE"]
    node_dir = os.path.dirname(node) if node else ""
    path = ":".join(p for p in (node_dir, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin",
                                "/sbin") if p)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    done = []
    for name, template, extra in services(cfg):
        lab = label(cfg, name)
        text = (HB_HOME / "launchd" / template).read_text()
        subs = {"__LABEL__": lab, "__ENGINE__": engine_fingerprint(cfg), "__HB_HOME__": str(HB_HOME), "__CONFIG__": str(config_file()),
                "__STATE__": cfg["HB_STATE_DIR"], "__HOME__": home, "__NODE__": node, "__PATH__": path, **extra}
        for k, v in subs.items():
            text = text.replace(k, _xml(v))
        leftover = re.findall(r"__[A-Z_]+__", text)
        if leftover:
            raise ConfigError(f"{template}: unknown placeholder {leftover[0]}")
        (outdir / f"{lab}.plist").write_text(text)
        done.append(lab)
    return done


# ---------- plugin MCP registrations ----------

def mcp_servers(cfg):
    """The plugin's .mcp.json: pw-lane + pw-lane-a…d on lanes, identity, and one entry per enabled pool copy.
    Claude Code drops a plugin MCP server whose URL equals another's, so each lane gets its own `?lane=` query (the
    server ignores it; every registration is its own MCP session, hence its own browser context)."""
    srv = {name: port for name, port, _ in servers(cfg)}
    out = {"pw-lane": {"type": "http", "url": f"http://127.0.0.1:{srv['lanes']}/mcp"}}
    for x in POOL_LETTERS:
        out[f"pw-lane-{x}"] = {"type": "http", "url": f"http://127.0.0.1:{srv['lanes']}/mcp?lane={x}"}
    out["identity"] = {"type": "http", "url": f"http://127.0.0.1:{srv['identity']}/mcp"}
    for b in pool(cfg):
        out[b] = {"type": "http", "url": f"http://127.0.0.1:{srv[b]}/mcp"}
    return {"mcpServers": out}


def render_mcp(cfg, path):
    """Write the plugin's .mcp.json when its content differs. Returns True when it changed."""
    text = json.dumps(mcp_servers(cfg), indent=2) + "\n"
    path = Path(path)
    if path.exists() and path.read_text() == text:
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return True


def _shell_quote(v):
    return "'" + v.replace("'", "'\\''") + "'"


def main(argv):
    if not argv:
        sys.exit(__doc__)
    try:
        cfg = load()
    except ConfigError as e:
        sys.exit(f"hommy-browsers config: {e}")
    cmd, args = argv[0], argv[1:]
    if cmd == "shell":
        for k, v in cfg.items():
            print(f"{k}={_shell_quote(v)}")
    elif cmd == "get" and len(args) == 1:
        if args[0] not in cfg:
            sys.exit(f"unknown key {args[0]}")
        print(cfg[args[0]])
    elif cmd == "ports":
        for name, port, dbg in servers(cfg):
            print(name, port, dbg)
    elif cmd == "labels":
        print("\n".join(labels(cfg)))
    elif cmd == "render-plists" and len(args) in (1, 2):
        render_plists(cfg, args[0], node=args[1] if len(args) == 2 else None)
    elif cmd == "render-mcp" and len(args) == 1:
        print("changed" if render_mcp(cfg, args[0]) else "unchanged")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
