"""Config rendering: port layout for every pool size, launchd plists for two prefixes, the plugin's MCP file."""
import json
import plistlib
import unittest
from pathlib import Path

from helpers import ROOT, tempdir, write_config

import hbconfig


class PortLayout(unittest.TestCase):
    def test_pool_0_to_4(self):
        for pool in range(5):
            d = tempdir(self)
            cfg = hbconfig.load(write_config(d, HB_PORT_BASE=4720, HB_DEBUG_BASE=4723, HB_POOL=pool,
                                             HB_STATE_DIR=d))
            got = hbconfig.servers(cfg)
            names = ["lanes", "identity"] + [f"identity-{x}" for x in "abcd"[:pool]]
            self.assertEqual([n for n, _, _ in got], names)
            self.assertEqual([p for _, p, _ in got], list(range(4720, 4722 + pool)))
            self.assertEqual([p for _, _, p in got], list(range(4723, 4725 + pool)))
            self.assertEqual(hbconfig.pool(cfg), names[2:])

    def test_defaults(self):
        cfg = hbconfig.load(Path(tempdir(self)) / "absent.env")
        self.assertEqual(hbconfig.servers(cfg)[0], ("lanes", 8931, 9331))
        self.assertEqual(len(hbconfig.servers(cfg)), 6)
        self.assertEqual(cfg["HB_LABEL_PREFIX"], "dev.hommy.browsers")
        self.assertEqual(cfg["HB_BUNDLE_ID"], "dev.hommy.agent-chrome")
        self.assertEqual(cfg["HB_LOGIN_EMAIL"], "")
        self.assertTrue(cfg["HB_STATE_DIR"].endswith("/.local/state/hommy-browsers"))
        self.assertEqual(cfg["HB_IDENTITY_PROFILE"], cfg["HB_STATE_DIR"] + "/identity-profile")

    def test_bad_values(self):
        for keys in ({"HB_POOL": 5}, {"HB_POOL": "x"}, {"HB_PORT_BASE": 80}, {"HB_LABEL_PREFIX": "a b"},
                     {"HB_STATE_DIR": "relative/dir"}, {"HB_UNKNOWN": 1}, {"HB_REAPER_SCOPE": "some"}):
            with self.subTest(keys=keys), self.assertRaises(hbconfig.ConfigError):
                hbconfig.load(write_config(tempdir(self), **keys))


class Plists(unittest.TestCase):
    def render(self, prefix, base, pool):
        d = tempdir(self)
        state = f"{d}/state"
        cfg = hbconfig.load(write_config(d, HB_LABEL_PREFIX=prefix, HB_PORT_BASE=base, HB_DEBUG_BASE=base + 3,
                                         HB_POOL=pool, HB_STATE_DIR=state))
        out = Path(d) / "out"
        labels = hbconfig.render_plists(cfg, out, home="/tmp/hb-home", node="/opt/node/bin/node")
        return cfg, out, labels, state

    def test_two_prefixes(self):
        a = self.render("dev.hommy.test-s1.browsers", 4720, 1)
        b = self.render("dev.hommy.browsers", 8931, 4)
        self.assertEqual(sorted(a[2]), sorted(f"dev.hommy.test-s1.browsers.{n}" for n in
                                              ["lanes", "identity", "identity-a", "chrome-refresh", "identity-seed",
                                               "reaper"]))
        self.assertEqual(len(b[2]), 9)
        for cfg, out, labels, state in (a, b):
            for label in labels:
                p = out / f"{label}.plist"
                d = plistlib.loads(p.read_bytes())
                self.assertEqual(d["Label"], label)
                self.assertEqual(d["EnvironmentVariables"]["HOME"], "/tmp/hb-home")
                self.assertEqual(d["EnvironmentVariables"]["HB_HOME"], str(ROOT))
                self.assertTrue(d["StandardOutPath"].startswith(state + "/logs/"))
                self.assertIn("/opt/node/bin", d["EnvironmentVariables"]["PATH"])
                self.assertNotRegex(p.read_text(), r"__[A-Z_]+__")     # every placeholder filled
                other = "dev.hommy.browsers." if cfg["HB_LABEL_PREFIX"] != "dev.hommy.browsers" else "test-s1"
                self.assertNotIn(other, p.read_text())
            lanes = plistlib.loads((out / f"{cfg['HB_LABEL_PREFIX']}.lanes.plist").read_bytes())
            self.assertEqual(lanes["ProgramArguments"], [f"{ROOT}/serve", "lanes"])
            self.assertTrue(lanes["KeepAlive"])
            self.assertTrue(lanes["EnvironmentVariables"]["HB_ENGINE"])
            reaper = plistlib.loads((out / f"{cfg['HB_LABEL_PREFIX']}.reaper.plist").read_bytes())
            self.assertEqual(reaper["ProgramArguments"][1:], [f"{ROOT}/browser-reaper", "--kill"])

    def test_no_seed_job_without_pool(self):
        _, _, labels, _ = self.render("dev.hommy.x", 5000, 0)
        self.assertFalse(any(l.endswith("identity-seed") for l in labels))

    def test_render_is_stable(self):
        cfg, out, labels, _ = self.render("dev.hommy.y", 5100, 2)
        first = {l: (out / f"{l}.plist").read_bytes() for l in labels}
        hbconfig.render_plists(cfg, out, home="/tmp/hb-home", node="/opt/node/bin/node")
        self.assertEqual(first, {l: (out / f"{l}.plist").read_bytes() for l in labels})


    def test_config_change_restarts_servers(self):
        """A port change re-renders the server plists (install.sh reloads only changed plists)."""
        _, out_a, _, _ = self.render("dev.hommy.z", 5200, 1)
        _, out_b, _, _ = self.render("dev.hommy.z", 5210, 1)
        eng = [plistlib.loads((o / "dev.hommy.z.lanes.plist").read_bytes())["EnvironmentVariables"]["HB_ENGINE"]
               for o in (out_a, out_b)]
        self.assertNotEqual(eng[0], eng[1])


class McpFile(unittest.TestCase):
    def test_pool_one(self):
        d = tempdir(self)
        cfg = hbconfig.load(write_config(d, HB_PORT_BASE=4720, HB_DEBUG_BASE=4723, HB_POOL=1, HB_STATE_DIR=d))
        p = Path(d) / ".mcp.json"
        self.assertTrue(hbconfig.render_mcp(cfg, p))
        self.assertFalse(hbconfig.render_mcp(cfg, p))
        srv = json.loads(p.read_text())["mcpServers"]
        self.assertEqual(sorted(srv), sorted(["pw-lane", "pw-lane-a", "pw-lane-b", "pw-lane-c", "pw-lane-d",
                                              "identity", "identity-a"]))
        ports = {s["url"].split(":")[2].split("/")[0] for s in srv.values()}
        self.assertEqual(ports, {"4720", "4721", "4722"})
        urls = [s["url"] for s in srv.values()]
        self.assertEqual(len(set(urls)), len(urls))     # Claude Code drops a plugin server with a duplicate URL


if __name__ == "__main__":
    unittest.main()
