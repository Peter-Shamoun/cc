"""Run the repo's script-style checks under pytest."""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = [
    "claude/schema/tests/test_semantic_layer.py",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_passes(script):
    r = subprocess.run([sys.executable, str(ROOT / script)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-4000:]
