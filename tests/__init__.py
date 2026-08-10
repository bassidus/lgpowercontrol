# Import bootstrap for the whole suite. Run it from the repo root with:
#
#     python3 -m unittest discover -v
#
# src/ goes first on purpose: without it the tests would import whatever /opt/lgpowercontrol
# happens to hold, which is the exact mistake this project has already paid for twice - a green
# run that said nothing about the code just edited. bscpylgtv is the one non-stdlib import in the
# tree, so the installed dependency directory is used as a fallback when no venv is active; that
# is the same directory every generated wrapper puts on sys.path.
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(1, str(REPO_ROOT))  # install.py and conflict_check.py live in the root

try:
    import bscpylgtv  # noqa: F401
except ImportError:  # no venv active - borrow the installed one
    sys.path.append("/opt/lgpowercontrol/lib")
