"""Use the production timeout policy in isolated extracted-JS harnesses."""
from pathlib import Path


_SOURCE = (Path(__file__).resolve().parents[1] / "static/js/api.js").read_text(encoding="utf-8")
FRONTEND_TIMEOUTS = _SOURCE[_SOURCE.index("const MathBankTimeouts ="):
                           _SOURCE.index("window.MathBankTimeouts =")]
