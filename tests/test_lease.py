"""The identity lease through `pw-identity hook pre|post` (no Chrome): a second session is denied while the first
session's call is in flight (named holder, free copies listed), and allowed once the holder closed."""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path

from helpers import ROOT, tempdir, write_config


class Lease(unittest.TestCase):
    def setUp(self):
        d = tempdir(self)
        self.state = Path(d) / "state"
        self.conf = write_config(d, HB_STATE_DIR=self.state, HB_POOL=1, HB_PORT_BASE=4790, HB_DEBUG_BASE=4793)
        self.env = {"HOME": d, "PATH": "/usr/bin:/bin", "HB_CONFIG": str(self.conf)}
        # a seeded copy: what free_pool() names
        lease = self.state / "identity-pool" / "a.lease"
        lease.mkdir(parents=True)
        (lease / "seeded-at").write_text("1")
        self.addCleanup(self.release)

    def release(self):
        """Close every hold, then wait for the detached lease reapers to exit before the temp dir goes."""
        for who in ("s-A", "s-B"):
            self.hook("post", who, "mcp__identity__browser_close", f"t-end-{who}")
        for _ in range(100):
            alive = []
            for pidfile in self.state.rglob("reaper"):
                try:
                    os.kill(int(pidfile.read_text()), 0)
                    alive.append(pidfile)
                except (OSError, ValueError):
                    pass
            if not alive:
                return
            time.sleep(0.1)
        self.fail("lease reapers still running")

    def hook(self, sub, session, tool, tid, url=""):
        ev = {"session_id": session, "cwd": "/tmp/x", "hook_event_name": "PreToolUse" if sub == "pre" else "PostToolUse",
              "tool_name": tool, "tool_input": {"url": url} if url else {}, "tool_use_id": tid}
        return subprocess.run(["/usr/bin/python3", str(ROOT / "pw-identity"), "hook", sub], input=json.dumps(ev),
                              env=self.env, capture_output=True, text=True, timeout=30)

    def pool(self):
        return subprocess.run(["/usr/bin/python3", str(ROOT / "pw-identity"), "pool"], env=self.env,
                              capture_output=True, text=True, timeout=30).stdout

    def test_in_flight_holder_blocks_others(self):
        nav = "mcp__identity__browser_navigate"
        self.assertEqual(self.hook("pre", "s-A", nav, "t1", "http://127.0.0.1:1/").returncode, 0)
        r = self.hook("pre", "s-B", nav, "t2", "http://127.0.0.1:1/")
        self.assertEqual(r.returncode, 2)
        self.assertIn("held by session s-A", r.stderr)
        self.assertIn("Free now: identity-a", r.stderr)
        self.assertRegex(self.pool(), r"identity\s+:4791 .*held: .*session s-A")
        self.assertEqual(self.hook("post", "s-A", "mcp__identity__browser_close", "t3").returncode, 0)
        self.assertEqual(self.hook("pre", "s-B", nav, "t4", "http://127.0.0.1:1/").returncode, 0)
        self.assertRegex(self.pool(), r"identity\s+:4791 .*session s-B")

    def test_plugin_tool_names(self):
        nav = "mcp__plugin_hommy-browsers_identity__browser_navigate"
        self.assertEqual(self.hook("pre", "s-A", nav, "t1").returncode, 0)
        r = self.hook("pre", "s-B", nav, "t2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("mcp__plugin_hommy-browsers_identity-a__*", r.stderr)
        self.assertEqual(self.hook("post", "s-A", "mcp__plugin_hommy-browsers_identity__browser_close", "t3").returncode, 0)

    def test_google_is_master_only(self):
        r = self.hook("pre", "s-A", "mcp__identity-a__browser_navigate", "t1", "https://accounts.google.com/")
        self.assertEqual(r.returncode, 2)
        self.assertIn("master-only", r.stderr)

    def test_other_servers_ignored(self):
        self.assertEqual(self.hook("pre", "s-A", "mcp__pw-lane__browser_navigate", "t1").returncode, 0)
        self.assertEqual(self.hook("pre", "s-A", "mcp__identity-b__browser_navigate", "t1").returncode, 0)  # not in pool


if __name__ == "__main__":
    unittest.main()
