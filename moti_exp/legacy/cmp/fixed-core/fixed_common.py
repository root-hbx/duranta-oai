"""The four handover kinds of plot/cmp with the cross-RAT ones on the fixed private core: LTE/LTE X2
(build-lte/pcap-cmp-lte) and NTN/NTN CHO (build/pcap-cmp-cho) as in plot/cmp, LTE/NTN and NTN/LTE RAT changes at the
redirect stage with interworking without N26 (build/pcap-xrat-iwk, CORE=private IWK=1: the target keeps the source IP).
Run 1 of each, from the traffic start. The cross-RAT runs last 70 s, the source fade-out starting at 10 s; the others
180 s. The helpers and the style are those of plot/cmp/cmp_common.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cmp_common import MODES, T_RUN, fig, ping_dl, runs, save, t_start, tcp_rtt, thpt_1s  # noqa: E402,F401

import numpy as np  # noqa: E402

# label, color, line style, (result dir, run name prefix with {kind} and {mode}), run length (s)
KINDS = (
  ("LTE/LTE", "black", "--", ("../build-lte/pcap-cmp-lte", "{kind}-connected"), T_RUN),
  ("NTN/NTN", "gray", "--", ("pcap-cmp-cho", "{kind}-{mode}-cho"), T_RUN),
  ("LTE/NTN", "#000080", "-", ("pcap-xrat-iwk", "{kind}-lte2ntn-{mode}-redirect"), 70),
  ("NTN/LTE", "#B22222", "-", ("pcap-xrat-iwk", "{kind}-ntn2lte-{mode}-redirect"), 70),
)
FIGS = Path(__file__).resolve().parent / "figs"


def run1(kind, mode, k):
  """Run 1 of a handover kind for kind tcp-dl or ping, None if there is none."""
  root, name = k[3]
  r = runs(root, name.format(kind=kind, mode=mode))
  return r[0] if r else None


def thpt_run(run, t_run):
  """thpt_1s up to the end of the run, NaN after it (no zero line past a shorter run)."""
  t, mbps = thpt_1s(run)
  mbps = mbps.astype(float)
  mbps[t > t_run] = np.nan
  return t, mbps


def finish(f, ax, name, **legend):
  ax.legend(**legend, ncol=1, framealpha=0.6, fontsize=7, handlelength=2.2)
  save(f, FIGS, name)
