"""Network-layer plots of the LTE <-> NR NTN RAT change captures (build/pcap-xrat, xrat_tcp.sh / xrat_ping.sh).

One figure per direction and NTN mode, one row per stage (base, trigger, access, session), run 1 of each: the
throughput at the UE of the TCP runs and the RTT of the ping from the UPF to the source UE IP. The time axis is seconds
from the start of the traffic; the dashed line is the RAT change, the dotted one the target UE IP (events.txt).
The packet helpers are those of plot/ntn-ntn/common.py.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ntn-ntn"))
from common import PING_COLOR, TCP_COLOR, gaps, ping, plt, runs, save, t_start, thpt  # noqa: E402

import numpy as np  # noqa: E402

ROOT = "pcap-xrat"
STAGES = ("base", "trigger", "access", "session")
MODES = ("trans", "regen")
TITLES = {"lte2ntn": "LTE to NTN", "ntn2lte": "NTN to LTE", "trans": "TRANS", "regen": "REGEN"}
MARK = "#8a8a85"


def event(run, text):
  """Epoch s of the first events.txt line containing text, None if there is none."""
  for line in (run / "events.txt").read_text().splitlines():
    t, e = line.split(" ", 1)
    if text in e:
      return float(t)
  return None


def lost(run):
  """Send times of the unanswered requests of ping_dl.txt, up to 1 s before the last one (cut by the stop). Unlike
  common.ping, it keeps those after the last reply: here the replies stop for good."""
  pending = re.compile(r"\[([0-9.]+)\] no answer yet for icmp_seq=(\d+)")
  reply = re.compile(r"\[[0-9.]+\].*icmp_seq=(\d+) ")
  waited, answered = {}, set()
  for line in (run / "ping_dl.txt").read_text().splitlines():
    if m := pending.match(line):
      waited.setdefault(m[2], float(m[1]))
    elif m := reply.match(line):
      answered.add(m[1])
  t = sorted(ts for seq, ts in waited.items() if seq not in answered)
  return np.array([x for x in t if x < t[-1] - 1]) if t else np.array([])


def fig4(title):
  """Four rows (one per stage) sharing the time axis, single-column paper width."""
  fig, axes = plt.subplots(4, 1, sharex=True, sharey=True, figsize=(3.5, 5.4), constrained_layout=True)
  fig.suptitle(title, fontsize=9)
  axes[-1].set_xlabel("Time (s)")
  return fig, axes


def marks(ax, run, t0, stage):
  ax.text(0.01, 0.97, stage, transform=ax.transAxes, va="top", fontsize=7)
  change = event(run, "change:")
  if change:
    ax.axvline(change - t0, color=MARK, lw=0.8, ls="--", label="RAT change")
  ip = event(run, "target UE IP")
  if ip:
    ax.axvline(ip - t0, color=MARK, lw=0.8, ls=":", label="target UE IP")


def stage_runs(kind, direction, mode):
  """Run 1 of each stage, None for a stage without runs."""
  out = []
  for s in STAGES:
    r = runs(ROOT, f"{kind}-{direction}-{mode}-{s}")
    out.append(r[0] if r else None)
  return out


def plot_bw(direction, figs):
  """TCP throughput at the UE (ue.pcap): <figs>/<direction>_bw_<mode>.pdf."""
  for m in MODES:
    if not any(stage_runs("tcp-dl", direction, m)):
      continue
    fig, axes = fig4(f"{TITLES[direction]} {TITLES[m]}: TCP DL throughput (ue.pcap)")
    for ax, s, run in zip(axes, STAGES, stage_runs("tcp-dl", direction, m)):
      if run is None:
        continue
      t0 = t_start(run)
      t, mbps = thpt(run, "dl")
      ax.plot(t - t0, mbps, color=TCP_COLOR, lw=0.5, alpha=0.3, label="100 ms")
      mean = np.convolve(mbps, np.ones(10), "same") / np.convolve(np.ones(len(mbps)), np.ones(10), "same")
      ax.plot(t - t0, mean, color=TCP_COLOR, lw=1.2, label="1 s mean")
      marks(ax, run, t0, s)
      ax.set_ylim(bottom=0)
      ax.set_ylabel("Mbps")
    handles = {h.get_label(): h for ax in axes for h in ax.get_legend_handles_labels()[0]}
    fig.legend(handles.values(), handles.keys(), loc="outside lower center", ncol=2, frameon=False)
    save(fig, figs, f"{direction}_bw_{m}")


def plot_rtt(direction, figs):
  """RTT of the ping from the UPF to the source UE IP, with the unanswered requests: <figs>/<direction>_rtt_<mode>.pdf."""
  for m in MODES:
    if not any(stage_runs("ping", direction, m)):
      continue
    fig, axes = fig4(f"{TITLES[direction]} {TITLES[m]}: ping RTT, UPF to the source UE IP")
    for ax, s, run in zip(axes, STAGES, stage_runs("ping", direction, m)):
      if run is None:
        continue
      t0 = t_start(run)
      t, rtt, _ = ping(run, "dl")
      t, rtt = gaps(t, rtt)
      lst = lost(run)
      ax.plot(t - t0, rtt, color=PING_COLOR, lw=0.6, rasterized=True, label="ping")
      ax.plot(lst - t0, np.full(len(lst), 6), "x", color=PING_COLOR, ms=2, mew=0.5, label="ping lost")
      marks(ax, run, t0, s)
      ax.set_yscale("log")
      ax.set_ylim(5, 3000)
      ax.set_ylabel("RTT (ms)")
    handles = {h.get_label(): h for ax in axes for h in ax.get_legend_handles_labels()[0]}
    fig.legend(handles.values(), handles.keys(), loc="outside lower center", ncol=2, frameon=False)
    save(fig, figs, f"{direction}_rtt_{m}")
