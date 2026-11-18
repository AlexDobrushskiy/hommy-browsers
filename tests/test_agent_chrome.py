"""agent-chrome run for real against a fake Google Chrome.app (a tiny compiled sleeper as its executable) and a
`codesign` stub on PATH: a failed build never replaces the installed one, and pruning never deletes a build in use."""
import os
import plistlib
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, tempdir, write_config

ID = "dev.hommy.unit-ac.agent-chrome"
CODESIGN_STUB = """#!/bin/sh
# fails when the bundle's version is listed in $CODESIGN_FAIL_FOR
for a; do last=$a; done
v=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$last/Contents/Info.plist" 2>/dev/null)
case " $CODESIGN_FAIL_FOR " in *" $v "*) echo "codesign: stub failure" >&2; exit 1 ;; esac
exit 0
"""


SLEEPER_C = "#include <stdlib.h>\n#include <unistd.h>\nint main(int c, char **v) { sleep(c > 1 ? atoi(v[1]) : 0); return 0; }\n"


@unittest.skipUnless(shutil.which("cc"), "needs the Command Line Tools' cc")
class AgentChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A copy of /bin/sleep is killed outside its system path; a fresh ad-hoc-signed binary runs anywhere.
        cls.bin = Path(tempfile.mkdtemp(prefix="hb-test-sleeper-"))
        (cls.bin / "sleeper.c").write_text(SLEEPER_C)
        subprocess.run(["cc", "-o", str(cls.bin / "sleeper"), str(cls.bin / "sleeper.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.bin, True)

    def setUp(self):
        d = tempdir(self)
        self.d = Path(d)
        self.src = self.d / "Google Chrome.app"
        self.root = self.d / "state/agent-chrome"
        stub = self.d / "bin"
        stub.mkdir()
        (stub / "codesign").write_text(CODESIGN_STUB)
        (stub / "codesign").chmod(0o755)
        self.conf = write_config(d, HB_STATE_DIR=f"{d}/state", HB_CHROME_APP=str(self.src), HB_BUNDLE_ID=ID,
                                 HB_LABEL_PREFIX="dev.hommy.unit-ac.browsers", HB_POOL="0",
                                 HB_PORT_BASE="4790", HB_DEBUG_BASE="4795")
        self.env = {"HOME": d, "PATH": f"{stub}:/usr/bin:/bin:/usr/sbin:/sbin", "HB_CONFIG": str(self.conf)}
        self.procs = []
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self.procs:
            p.send_signal(signal.SIGKILL)
            p.wait()

    def chrome_version(self, ver):
        """(Re)write the fake Google Chrome.app as version ver."""
        shutil.rmtree(self.src, ignore_errors=True)
        c = self.src / "Contents"
        (c / "MacOS").mkdir(parents=True)
        shutil.copyfile(self.bin / "sleeper", c / "MacOS/Google Chrome")
        (c / "MacOS/Google Chrome").chmod(0o755)
        (c / f"Frameworks/Google Chrome Framework.framework/Versions/{ver}/Helpers").mkdir(parents=True)
        (c / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.google.Chrome", "CFBundleName": "Google Chrome",
            "CFBundleDisplayName": "Google Chrome", "CFBundleShortVersionString": ver, "KSProductID": "x"}))

    def run_ac(self, *args, fail_for=""):
        env = dict(self.env, CODESIGN_FAIL_FOR=fail_for)
        return subprocess.run([str(ROOT / "agent-chrome"), *args], env=env, capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL)

    def current(self):
        return os.readlink(self.root / "current")

    def start_own_chrome(self):
        """A running 'Chrome' of this install: the current build's executable (sleep) from its bundle."""
        exe = self.root / self.current() / "Agent Chrome.app/Contents/MacOS/Google Chrome"
        self.procs.append(subprocess.Popen([str(exe), "600"]))

    def test_build_ok(self):
        self.chrome_version("1.0")
        r = self.run_ac()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.current(), "1.0")
        with open(self.root / "1.0/Agent Chrome.app/Contents/Info.plist", "rb") as f:
            self.assertEqual(plistlib.load(f)["CFBundleIdentifier"], ID)

    def test_failed_signing_keeps_installed_build(self):
        self.chrome_version("1.0")
        self.assertEqual(self.run_ac().returncode, 0)
        self.chrome_version("2.0")
        r = self.run_ac(fail_for="2.0")
        self.assertIn("build of 2.0 failed", r.stderr)
        self.assertEqual(r.returncode, 0)                 # the installed 1.0 is printed
        self.assertEqual(self.current(), "1.0")
        self.assertFalse((self.root / "2.0").exists())
        self.assertEqual([p.name for p in self.root.glob(".build.*")], [])

    def test_failed_signing_first_install(self):
        self.chrome_version("1.0")
        r = self.run_ac(fail_for="1.0")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.root / "1.0").exists())
        self.assertFalse((self.root / "current").exists())

    def test_prune_keeps_build_in_use(self):
        self.chrome_version("1.0")
        self.assertEqual(self.run_ac().returncode, 0)
        self.start_own_chrome()                                # servers' Chrome stays on 1.0
        for ver in ("2.0", "3.0"):
            self.chrome_version(ver)
            r = self.run_ac("refresh")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.current(), "1.0")            # stays put while an own Chrome runs
            self.assertTrue((self.root / "1.0/Agent Chrome.app/Contents/MacOS/Google Chrome").exists(), ver)
        self.assertTrue((self.root / "3.0").exists())


if __name__ == "__main__":
    unittest.main()
