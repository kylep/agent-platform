import subprocess
import sys
from pathlib import Path


def test_persona_ingress_real_runtime():
    # One small subprocess isolates FastMCP from the unit tests' module stubs.
    result = subprocess.run([sys.executable, str(Path(__file__).parent/'tests/persona_runtime.py')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
