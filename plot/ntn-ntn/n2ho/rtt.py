import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import plot_rtt

plot_rtt("pcap-ntn-n2ho", "n2", "connected", "n2ho", Path(__file__).resolve().parent / "figs")
