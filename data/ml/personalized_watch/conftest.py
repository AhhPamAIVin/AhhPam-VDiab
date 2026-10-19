"""Makes the pipeline scripts in data/NHANES/final/ importable from tests
in this directory (e.g. `from train_threshold_regression import ...`)."""

import sys
from pathlib import Path

_FINAL_DIR = Path(__file__).resolve().parent.parent.parent / "NHANES" / "final"
if str(_FINAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FINAL_DIR))
