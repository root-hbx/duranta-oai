"""Cross-RAT changes with a new IP and with the same IP (interworking without N26): TCP dl throughput, run 1."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_common import MODES, NEW_IP, SAME_IP, fig, finish, run1, thpt_1s  # noqa: E402

for mode in MODES:
  f, ax = fig()
  for c in NEW_IP + SAME_IP:
    run = run1("tcp-dl", mode, c)
    if run is None:
      continue
    t, mbps = thpt_1s(run)
    ax.plot(t, mbps, color=c[1], ls=c[2], lw=1.2, label=c[0])
  ax.set_ylim(bottom=-0.5)
  ax.set_ylabel("Throughput (Mbps)")
  finish(f, ax, "analysis", f"anchor_{mode}", loc="lower right", bbox_to_anchor=(1, 0.06))
