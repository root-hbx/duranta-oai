import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xrat import plot_bw

plot_bw("ntn2lte", Path(__file__).resolve().parent / "figs")
