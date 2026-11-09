"""Real-repo gate: checks behavior and that the agent added real tests."""

from pathlib import Path

from src.calc.ops import add, div, mul, sub

assert add(2, 3) == 5
assert sub(5, 2) == 3
assert mul(6, 7) == 42
assert div(8, 2) == 4

text = Path("tests/test_ops.py").read_text(encoding="utf-8")
assert "test_mul" in text, "missing test_mul"
assert "test_div" in text, "missing test_div"
print("REAL_REPO_CHECK_OK")
