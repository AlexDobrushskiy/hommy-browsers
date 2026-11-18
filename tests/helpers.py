"""Shared test helpers: a temp config.env, and loading the engine's extension-less Python scripts as modules."""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))


def write_config(d, **keys):
    """A config.env in d with the given HB_* keys; returns its path."""
    p = Path(d) / "config.env"
    p.write_text("".join(f"{k}={v}\n" for k, v in keys.items()))
    return p


def load_script(name, env):
    """Import an engine script (no .py) with HB_CONFIG etc. from env set while it loads."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        loader = importlib.machinery.SourceFileLoader(f"hb_{name.replace('-', '_')}", str(ROOT / name))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def tempdir(test):
    """A temp dir removed after the test; removal tolerates a detached lease reaper's last write (test_lease)."""
    d = tempfile.mkdtemp(prefix="hb-test-")
    test.addCleanup(shutil.rmtree, d, True)
    return os.path.realpath(d)
