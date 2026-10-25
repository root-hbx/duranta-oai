import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import plot_rtt

plot_rtt("pcap-lte-ho", Path(__file__).resolve().parent / "figs")
