"""creds against a scratch keychain (never the user's): new / get / check, refusals, the password only to a pipe."""
import os
import subprocess
import unittest
from pathlib import Path

from helpers import ROOT, tempdir, write_config


class Creds(unittest.TestCase):
    def setUp(self):
        d = tempdir(self)
        self.home = Path(d) / "home"
        (self.home / "Library/Preferences").mkdir(parents=True)
        self.kc = str(self.home / "Library/Keychains/hb-test.keychain-db")
        (self.home / "Library/Keychains").mkdir(parents=True)
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        pw = "hb-test-keychain"
        # HOME is the scratch one for every security call: the user's search list and login keychain stay untouched
        for args in (["create-keychain", "-p", pw, self.kc], ["unlock-keychain", "-p", pw, self.kc],
                     ["set-keychain-settings", self.kc]):
            subprocess.run(["/usr/bin/security", *args], env=self.env, check=True, capture_output=True, timeout=20,
                           stdin=subprocess.DEVNULL)
        self.addCleanup(subprocess.run, ["/usr/bin/security", "delete-keychain", self.kc], env=self.env,
                        capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
        self.conf = write_config(d, HB_KEYCHAIN=self.kc, HB_LABEL_PREFIX="dev.hommy.unit.browsers",
                                 HB_LOGIN_EMAIL="owner@example.test", HB_STATE_DIR=f"{d}/state")

    def creds(self, *args, conf=None):
        env = dict(self.env, HB_CONFIG=str(conf or self.conf))
        return subprocess.run(["/usr/bin/python3", str(ROOT / "creds"), *args], env=env, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_lifecycle(self):
        r = self.creds("check", "shop.example.test")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "missing"))
        self.assertEqual(self.creds("get", "shop.example.test").returncode, 1)
        r = self.creds("new", "shop.example.test")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "owner@example.test"))
        r = self.creds("get", "shop.example.test")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "owner@example.test"))
        self.assertEqual(self.creds("check", "shop.example.test").stdout.strip(), "ok")
        pw = self.creds("get", "shop.example.test", "--print").stdout.strip()
        self.assertEqual(len(pw), 24)
        self.assertNotIn(pw, r.stdout + r.stderr)
        self.assertEqual(self.creds("new", "shop.example.test").returncode, 1)      # exists: refuses
        self.assertEqual(self.creds("new", "shop.example.test", "--replace").returncode, 0)
        self.assertNotEqual(self.creds("get", "shop.example.test", "--print").stdout.strip(), pw)
        # the item lives in the scratch keychain under the prefix's service, account = the login
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s",
                            "dev.hommy.unit.browsers.creds.shop.example.test", self.kc], env=self.env,
                           capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0)
        self.assertIn('"acct"<blob>="owner@example.test"', r.stdout)

    def test_new_needs_login_email(self):
        d = tempdir(self)
        conf = write_config(d, HB_KEYCHAIN=self.kc, HB_STATE_DIR=f"{d}/state")
        r = self.creds("new", "shop.example.test", conf=conf)
        self.assertEqual(r.returncode, 2)
        self.assertIn("HB_LOGIN_EMAIL", r.stderr)

    def test_bad_domain(self):
        self.assertEqual(self.creds("check", "not a domain").returncode, 2)


if __name__ == "__main__":
    unittest.main()
