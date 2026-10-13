"""Exercise the dependency-free client family model when Node is available."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_console_family_client() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    result = subprocess.run(
        [node, "--test", "tests/console_family.test.cjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
