import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xrat import plot_rtt

plot_rtt("lte2ntn", Path(__file__).resolve().parent / "figs")
