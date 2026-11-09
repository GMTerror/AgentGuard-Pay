import shutil
import subprocess
import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_dashboard_logic_with_node():
    r = subprocess.run(
        ["node", "--test", "tests/js/logic.test.js"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
