"""The four handover kinds on one time axis (cmp_batch.sh): LTE/LTE X2 (build-lte/pcap-cmp-lte), NTN/NTN CHO
(build/pcap-cmp-cho), LTE/NTN and NTN/LTE RAT changes at the session stage (build/pcap-cmp-xrat). Run 1 of each, 180 s
from the traffic start, the same traffic; each kind hands over when its own trigger fires. Same-RAT kinds dashed,
cross-RAT solid. The packet helpers are those of plot/ntn-ntn/common.py.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ntn-ntn"))
from common import PORT, plt, runs, save, tcp_fields, tcp_rtt  # noqa: E402

import numpy as np  # noqa: E402

T_RUN = 180
MODES = {"trans": "TRANS", "regen": "REGEN"}
# label, color, line style, (result dir, run name prefix with {kind} and {mode})
KINDS = (
  ("LTE/LTE", "black", "--", ("../build-lte/pcap-cmp-lte", "{kind}-connected")),
  ("NTN/NTN", "gray", "--", ("pcap-cmp-cho", "{kind}-{mode}-cho")),
  ("LTE/NTN", "#000080", "-", ("pcap-cmp-xrat", "{kind}-lte2ntn-{mode}-session")),
  ("NTN/LTE", "#B22222", "-", ("pcap-cmp-xrat", "{kind}-ntn2lte-{mode}-session")),
)
FIGS = Path(__file__).resolve().parent / "figs"


def run1(kind, mode, k):
  """Run 1 of a handover kind for kind tcp-dl or ping, None if there is none."""
  root, name = k[3]
  r = runs(root, name.format(kind=kind, mode=mode))
  return r[0] if r else None


def t_start(run):
  """Traffic start in epoch s: the iperf3 or ping start line of events.txt, else its first line (LTE ping)."""
  ev = [line.split(" ", 1) for line in (run / "events.txt").read_text().splitlines()]
  return float(next((t for t, e in ev if re.match(r"(iperf3|ping) ", e)), ev[0][0]))


def thpt_1s(run):
  """TCP dl goodput at the UE (ue.pcap) in Mbps per 1 s from the traffic start, zero where nothing came."""
  t0 = t_start(run)
  pkts = [(t - t0, n) for t, dport, n, _, _ in tcp_fields(run, "ue.pcap") if n > 0 and dport == PORT]
  edges = np.arange(0, T_RUN + 1, 1.0)
  b, _ = np.histogram([p[0] for p in pkts], edges, weights=[p[1] for p in pkts])
  return edges[:-1] + 0.5, b * 8 / 1e6


def ping_dl(run):
  """ping_dl.txt from the traffic start: (reply time, RTT ms) and the send times of the unanswered requests (but
  the last second, cut by the stop of the ping)."""
  t0 = t_start(run)
  reply = re.compile(r"\[([0-9.]+)\].*icmp_seq=(\d+) .*time=([0-9.]+) ms")
  pending = re.compile(r"\[([0-9.]+)\] no answer yet for icmp_seq=(\d+)")
  t, rtt, answered, waited = [], [], set(), {}
  for line in (run / "ping_dl.txt").read_text().splitlines():
    if m := reply.match(line):
      t.append(float(m[1]) - t0)
      rtt.append(float(m[3]))
      answered.add(m[2])
    elif m := pending.match(line):
      waited.setdefault(m[2], float(m[1]) - t0)
  lost = sorted(x for seq, x in waited.items() if seq not in answered)
  if lost:
    lost = [x for x in lost if x < lost[-1] - 1]
  return np.array(t), np.array(rtt), np.array(lost)


def fig():
  f, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
  ax.set_xlabel("Time (s)")
  ax.set_xlim(0, T_RUN)
  ax.grid(False)
  return f, ax


def finish(f, ax, name, **legend):
  ax.legend(**legend, ncol=1, framealpha=0.6, fontsize=7, handlelength=2.2)
  save(f, FIGS, name)
