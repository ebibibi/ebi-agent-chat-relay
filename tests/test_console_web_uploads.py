"""Exercise browser long-text uploads without a browser or added dependencies."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_browser_long_text_upload() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, str(root / "tests/console_long_text.cjs")], cwd=root, check=True)
