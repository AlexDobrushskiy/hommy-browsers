"""browser-reaper's own-install filter: which browsers it lists, which it would kill, never another install's."""
import os
import unittest
from pathlib import Path

from helpers import ROOT, load_script, tempdir, write_config

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Reaper(unittest.TestCase):
    def setUp(self):
        d = tempdir(self)
        self.state = f"{d}/state"
        app = Path(self.state) / "agent-chrome/1.0/Agent Chrome.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(
            b'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key>'
            b'<string>dev.hommy.test-s9.agent-chrome</string></dict></plist>')
        self.own_exe = f"{app}/Contents/MacOS/Google Chrome"
        other = Path(d) / "elsewhere/Agent Chrome.app"
        (other / "Contents/MacOS").mkdir(parents=True)
        (other / "Contents/Info.plist").write_bytes(
            b'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key>'
            b'<string>dev.example.other</string></dict></plist>')
        self.other_exe = f"{other}/Contents/MacOS/Google Chrome"
        self.conf = write_config(d, HB_STATE_DIR=self.state, HB_BUNDLE_ID="dev.hommy.test-s9.agent-chrome",
                                 HB_REAPER_SCOPE="own")
        self.r = load_script("browser-reaper", {"HB_CONFIG": str(self.conf)})
        self.cfg = self.r.hbconfig.load(self.conf)
        cli = ROOT / "node_modules/@playwright/mcp/cli.js"
        self.procs = {
            1: {"pid": 1, "ppid": 0, "age_s": 99999, "args": "/sbin/launchd"},
            10: {"pid": 10, "ppid": 1, "age_s": 100, "args": f"/opt/node {cli} --port 4720 --browser chrome"},
            11: {"pid": 11, "ppid": 10, "age_s": 50, "args": f"{self.own_exe} --user-data-dir=/var/folders/x/T/playwright_chromiumdev_profile-a"},
            12: {"pid": 12, "ppid": 1, "age_s": 50, "args": f"{self.own_exe} --user-data-dir=/tmp/pytest-of-u/p1"},
            13: {"pid": 13, "ppid": 1, "age_s": 50, "args": f"{self.other_exe} --user-data-dir=/tmp/pytest-of-u/p2"},
            14: {"pid": 14, "ppid": 1, "age_s": 50, "args": f"{CHROME} --user-data-dir=/tmp/pytest-of-u/p3"},
            15: {"pid": 15, "ppid": 1, "age_s": 50, "args": f"{CHROME}"},
            16: {"pid": 16, "ppid": 1, "age_s": 50, "args": f"{CHROME} --user-data-dir={self.state}/identity-profile"},
        }

    def rows(self):
        out = {}
        for p in self.r.browser_mains(self.procs):
            out[p["pid"]] = (self.r.owner(p, self.cfg), self.r.classify(p, self.procs, 6 * 3600, self.cfg)[0])
        return out

    def test_owner(self):
        rows = self.rows()
        self.assertEqual(rows[11][0], "own")      # this install's bundle id
        self.assertEqual(rows[12][0], "own")
        self.assertEqual(rows[13][0], "other")    # another install's Agent Chrome
        self.assertEqual(rows[14][0], "none")     # Google Chrome with a temp profile
        self.assertEqual(rows[15][0], "none")     # the user's own Chrome
        self.assertEqual(rows[16][0], "own")      # any Chrome on this install's identity profile

    def test_actions(self):
        rows = self.rows()
        self.assertEqual(rows[11][1], "leave")    # the lanes server's Chrome: managed, never a leak
        self.assertEqual(rows[12][1], "kill")     # own orphaned throwaway
        self.assertEqual(rows[15][1], "leave")    # a person's browser
        self.assertEqual(rows[16][1], "leave")    # the identity profile: managed
        p11 = next(p for p in self.r.browser_mains(self.procs) if p["pid"] == 11)
        self.assertEqual(self.r.server_of(p11, self.procs), "4720")

    def test_scope_own_lists_only_own(self):
        listed = []
        for p in self.r.browser_mains(self.procs):
            if self.r.in_scope(self.r.owner(p, self.cfg), self.cfg):
                listed.append(p["pid"])
        self.assertEqual(sorted(listed), [11, 12, 16])

    def test_other_users_browsers_skipped(self):
        procs = dict(self.procs)
        procs[20] = {"pid": 20, "ppid": 1, "age_s": 50, "uid": os.getuid() + 1,
                     "args": f"{CHROME} --user-data-dir=/var/folders/x/T/someone-else"}
        self.assertNotIn(20, [p["pid"] for p in self.r.browser_mains(procs)])

    def test_kill_not_permitted(self):
        self.assertEqual(self.r.kill(1), "not-permitted")   # launchd: EPERM, never an exception

    def test_plain_chrome_fresh_profile_is_managed(self):
        pc = load_script("plain-chrome", {"HB_CONFIG": str(self.conf)})
        prof, temp = pc.profile_for({"profile": None, "fresh": True})
        self.addCleanup(os.rmdir, prof)
        self.assertTrue(temp)
        p = {"pid": 30, "ppid": 1, "age_s": 50, "args": f"{self.own_exe} --user-data-dir={prof}", "profile": prof}
        procs = {**self.procs, 30: p}
        self.assertEqual(self.r.classify(p, procs, 6 * 3600, self.cfg)[0], "leave")


if __name__ == "__main__":
    unittest.main()
