"""The shipped captcha benchmark set is complete: every labelled grid has its grid image, meta and rows×cols tiles,
and labels point at real tiles (tests/bench_grid.py reads exactly these; the models it needs are opt-in)."""
import json
import unittest

from helpers import ROOT

DATA = ROOT / "captcha" / "data" / "grids" / "recaptcha"


class CaptchaData(unittest.TestCase):
    def test_labelled_set(self):
        labels = {k: v for k, v in json.loads((DATA / "labels.json").read_text()).items() if not k.startswith("_")}
        self.assertEqual(len(labels), 25)
        for gid, lab in labels.items():
            with self.subTest(grid=gid):
                meta = json.loads((DATA / gid / "meta.json").read_text())
                n = meta["rows"] * meta["cols"]
                self.assertTrue((DATA / gid / "grid.png").is_file())
                self.assertEqual(sorted(p.name for p in (DATA / gid / "tiles").glob("*.png")),
                                 [f"{i:02d}.png" for i in range(n)])
                for t in lab["core"] + lab.get("maybe", []):
                    self.assertLess(t, n)


if __name__ == "__main__":
    unittest.main()
