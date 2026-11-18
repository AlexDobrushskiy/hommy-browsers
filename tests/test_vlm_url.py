"""captcha vlm: OLLAMA_HOST in Ollama's own forms (no scheme, bind-all host, no port) still gives a usable URL."""
import importlib.util
import os
import sys
import types
import unittest

from helpers import ROOT

VLM = ROOT / "captcha" / "captcha_solver" / "vlm.py"


def load_vlm(host):
    for m in ("cv2", "numpy"):             # the image libraries live in the captcha venv; the URL needs neither
        sys.modules.setdefault(m, types.ModuleType(m))
    old = os.environ.get("OLLAMA_HOST")
    if host is None:
        os.environ.pop("OLLAMA_HOST", None)
    else:
        os.environ["OLLAMA_HOST"] = host
    try:
        spec = importlib.util.spec_from_file_location("hb_vlm", VLM)   # the module alone, not the package
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = old


class VlmUrl(unittest.TestCase):
    def test_forms(self):
        for host, want in [(None, "http://127.0.0.1:11434"), ("0.0.0.0", "http://127.0.0.1:11434"),
                           ("127.0.0.1:11434", "http://127.0.0.1:11434"), ("0.0.0.0:11500", "http://127.0.0.1:11500"),
                           ("localhost", "http://localhost:11434"), ("http://10.0.0.5:11434/", "http://10.0.0.5:11434"),
                           ("https://ollama.example.test", "https://ollama.example.test")]:
            with self.subTest(host=host):
                self.assertEqual(load_vlm(host).URL, want + "/api/generate")


if __name__ == "__main__":
    unittest.main()
