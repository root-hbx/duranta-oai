"""Where the interruption goes: the mean over the TCP runs of each step of the cross-RAT changes with the same IP
and a valid GNSS position (summary.txt, ue.pcap), against the NTN/NTN CHO interruption (summary.txt). Steps from the
source fade-out: trigger (to the A2 report), release (with redirection), cell search (to the target sync), SIB19
(NR target), RACH (to the RAR), attach (RRC setup, registration and the session, to the IP), TCP (to the first dl
data at the UE)."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_common import MODES, NTN_NTN, SAME_IP, all_runs, event, first_data, out, plt, steps  # noqa: E402

import numpy as np  # noqa: E402

SEGS = (("trigger (scripted fade)", "#bab0ac"), ("release", "#8c6bb1"), ("cell search", "#4c78a8"), ("SIB19", "#72b7b2"),
        ("RACH", "#e45756"), ("attach", "#f58518"), ("TCP", "#54a24b"))


def xrat(run):
  """Durations (s) of SEGS for a cross-RAT run, None if the target got no IP or no data."""
  s = steps(run)
  t0 = event(run, "change: source fade-out")
  if "ip" not in s or t0 is None:
    return None
  data = first_data(run, t0 + s["ip"])
  if data is None:
    return None
  sync = s["sync"]
  sib = s.get("sib19", sync)
  marks = (0, s["a2"], s["release"], sync, sib, s["rar"], s["ip"], data - t0)
  return np.diff(marks)


def cho(run):
  """The CHO interruption (s) of summary.txt as the RACH step."""
  m = re.search(r"interruption (\d+) ms", (run / "summary.txt").read_text())
  if m is None:
    return None
  d = np.zeros(len(SEGS))
  d[4] = int(m[1]) / 1000
  return d


for mode in MODES:
  rows = []
  for c, fn in ((SAME_IP[0], xrat), (SAME_IP[1], xrat), (NTN_NTN, cho)):
    ds = [d for d in map(fn, all_runs("tcp-dl", mode, c)) if d is not None]
    if ds:
      rows.append((c[0].replace(", same IP", ""), np.mean(ds, axis=0), len(ds)))
  if not rows:
    continue
  f, ax = plt.subplots(figsize=(3.5, 2.0), constrained_layout=True)
  y = np.arange(len(rows))[::-1]
  for i, (name, color) in enumerate(SEGS):
    left = [r[1][:i].sum() for r in rows]
    # the trigger is the harness fade-out, not a protocol step
    hatch = None
    if i == 0:
      hatch = "////"
    ax.barh(y, [r[1][i] for r in rows], left=left, height=0.55, color=color, label=name, hatch=hatch, edgecolor="white", lw=0)
  for yi, r in zip(y, rows):
    ax.text(r[1].sum() + 0.05, yi, f"{r[1].sum():.1f} s", va="center", fontsize=7)
  ax.set_yticks(y, [f"{r[0]} ({r[2]})" for r in rows])
  ax.set_xlabel("Time from the trigger (s)")
  ax.grid(False)
  ax.legend(ncol=3, fontsize=6, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0))
  out(f, "analysis", f"breakdown_{mode}")
