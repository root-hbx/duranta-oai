import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import plot_bw

plot_bw("pcap-ntn-cho", "cho", "cho", "cho", Path(__file__).resolve().parent / "figs")
