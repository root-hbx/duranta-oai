"""Network-layer plots of the LTE X2 handover captures (build-lte/pcap-lte-ho).

Per run directory: ue.pcap (throughput), sgi.pcap (TCP RTT), ping_{dl,ul}.txt (ping RTT), core.pcap (handover
times). Packet fields come from tshark and are cached as <run>/.plot-<pcap>.csv.
The time axis is seconds from the first handover of each run.
"""

import csv
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BUILD = Path(__file__).resolve().parent.parent / "build-lte"
FIGS = Path(__file__).resolve().parent / "figs"
PORT = 5201  # iperf3 server on the UE; the PGW-U is the client

TCP_COLOR = "#2a78d6"
PING_COLOR = "#eb6834"
INK = "#3d3d3a"
plt.rcParams.update({
  "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8, "legend.fontsize": 7,
  "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.edgecolor": INK, "axes.labelcolor": INK,
  "xtick.color": INK, "ytick.color": INK, "text.color": INK, "axes.spines.top": False,
  "axes.spines.right": False, "axes.grid": True, "grid.color": "#e4e4e0", "grid.linewidth": 0.5,
  "pdf.fonttype": 42,
})


def runs(root, kind):
  """The handover runs <root>/<kind>-<n>-<stamp>, by run number."""
  dirs = [d for d in (BUILD / root).glob(f"{kind}-[0-9]-*") if d.is_dir()]
  return sorted(dirs, key=lambda d: int(d.name[len(kind) + 1:].split("-")[0]))


def tshark(run, pcap, fields, dfilter=""):
  """Rows of the tshark fields of <run>/<pcap>, cached."""
  cache = run / f".plot-{Path(pcap).stem}.csv"
  if not cache.exists():
    cmd = ["tshark", "-r", str(run / pcap), "-T", "fields", "-E", "separator=;"]
    if dfilter:
      cmd += ["-Y", dfilter]
    for f in fields:
      cmd += ["-e", f]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    cache.write_text(out)
  with open(cache) as f:
    return list(csv.reader(f, delimiter=";"))


def tcp_fields(run, pcap):
  """time, dst port, payload length, TSval, TSecr of the iperf3 packets."""
  rows = tshark(run, pcap, ["frame.time_epoch", "tcp.dstport", "tcp.len", "tcp.options.timestamp.tsval",
                            "tcp.options.timestamp.tsecr"], f"tcp.port=={PORT}")
  return [(float(r[0]), int(r[1]), int(r[2]), r[3], r[4]) for r in rows]


def thpt(run, direction, bin_s=0.1):
  """iperf3 data at the UE (dl received, ul sent) in Mbps per bin: (bin start, Mbps)."""
  pkts = [(t, n) for t, dport, n, _, _ in tcp_fields(run, "ue.pcap")
          if n > 0 and (dport == PORT) == (direction == "dl")]
  t = np.array([p[0] for p in pkts])
  n = np.array([p[1] for p in pkts])
  edges = np.arange(t.min(), t.max() + bin_s, bin_s)
  b, _ = np.histogram(t, edges, weights=n)
  return edges[:-1], b * 8 / bin_s / 1e6


def tcp_rtt(run):
  """RTT at the PGW-U from the TCP timestamps: the first PGW-U packet with a TSval to the first UE packet echoing it.
  The PGW-U sends the data (dl) or the ACKs (ul), so the dl RTT is the sender's and the ul RTT includes the UE queue."""
  sent, t_out, rtt = {}, [], []
  for t, dport, _, tsval, tsecr in tcp_fields(run, "sgi.pcap"):
    if dport == PORT:
      if tsval and tsval not in sent:
        sent[tsval] = t
    elif tsecr in sent:
      t0 = sent.pop(tsecr)
      if t - t0 < 10:
        t_out.append(t)
        rtt.append((t - t0) * 1000)
  return np.array(t_out), np.array(rtt)


def ping(run, direction):
  """ping_<dl|ul>.txt: (reply time, RTT ms) and the send times of the unanswered requests."""
  reply = re.compile(r"\[([0-9.]+)\].*icmp_seq=(\d+) .*time=([0-9.]+) ms")
  pending = re.compile(r"\[([0-9.]+)\] no answer yet for icmp_seq=(\d+)")
  t, rtt, answered, waited = [], [], set(), {}
  for line in (run / f"ping_{direction}.txt").read_text().splitlines():
    if m := reply.match(line):
      t.append(float(m[1]))
      rtt.append(float(m[3]))
      answered.add(m[2])
    elif m := pending.match(line):
      waited.setdefault(m[2], float(m[1]))
  # requests in the last second were cut by the stop of the ping, not lost
  lost = sorted(ts for seq, ts in waited.items() if seq not in answered and ts < t[-1] - 1)
  return np.array(t), np.array(rtt), np.array(lost)


def ho_spans(run):
  """X2 handover interruptions (start, end) in epoch s: X2AP HandoverRequestAcknowledge (the source then sends the
  handover command) to S1AP PathSwitchRequest (the UE is on the target)."""
  info = [(float(t), i) for t, i in tshark(run, "core.pcap", ["frame.time_epoch", "_ws.col.Info"], "x2ap || s1ap")]
  ack = [t for t, i in info if "HandoverRequestAcknowledge" in i]
  switch = [t for t, i in info if "PathSwitchRequest" in i and "Acknowledge" not in i]
  return list(zip(ack, switch))


def fig3(title):
  """Three rows (one per run) sharing the time axis, single-column paper width."""
  fig, axes = plt.subplots(3, 1, sharex=True, sharey=True, figsize=(3.5, 4.2), constrained_layout=True)
  fig.suptitle(title, fontsize=9)
  axes[-1].set_xlabel("Time from handover (s)")
  return fig, axes


def gaps(t, y, max_s=1):
  """NaN between samples more than max_s apart, so a line is not drawn across a silence."""
  cut = np.flatnonzero(np.diff(t) > max_s) + 1
  return np.insert(t.astype(float), cut, np.nan), np.insert(y.astype(float), cut, np.nan)


def shade(ax, spans, t0):
  for a, b in spans:
    ax.axvspan(a - t0, b - t0, color="#8a8a85", alpha=0.35, lw=0)
    ax.axvline(a - t0, color="#8a8a85", lw=0.6, ls="--")


def label_run(ax, n):
  ax.text(0.01, 0.97, f"run {n}", transform=ax.transAxes, va="top", fontsize=7)


def save(fig, name):
  FIGS.mkdir(exist_ok=True)
  fig.savefig(FIGS / f"{name}.pdf")
  plt.close(fig)
  print(FIGS / f"{name}.pdf")


DIRS = ("dl", "ul")
NAMES = {"dl": "DL", "ul": "UL"}


def plot_bw(root):
  """Throughput at the UE, one figure per direction: figs/lte_bw_<d>.pdf."""
  for d in DIRS:
    fig, axes = fig3(f"LTE X2 HO {NAMES[d]}: TCP throughput (ue.pcap)")
    for n, (ax, run) in enumerate(zip(axes, runs(root, f"tcp-{d}-connected")), 1):
      spans = ho_spans(run)
      t0 = spans[0][0]
      t, mbps = thpt(run, d)
      shade(ax, spans, t0)
      ax.plot(t - t0, mbps, color=TCP_COLOR, lw=1)
      ax.set_ylim(bottom=0)
      ax.set_ylabel("Mbps")
      label_run(ax, n)
    save(fig, f"lte_bw_{d}")


def plot_rtt(root):
  """TCP RTT at the PGW-U (sgi.pcap) with the ping RTT of the ping run with the same number, initiated on the TCP
  sender's side (dl: PGW-U, ul: UE): figs/lte_rtt_<d>.pdf."""
  for d in DIRS:
    fig, axes = fig3(f"LTE X2 HO {NAMES[d]}: RTT")
    tcp_runs, ping_runs = runs(root, f"tcp-{d}-connected"), runs(root, "ping-connected")
    for n, (ax, tr, pr) in enumerate(zip(axes, tcp_runs, ping_runs), 1):
      spans = ho_spans(tr)
      t0 = spans[0][0]
      shade(ax, spans, t0)
      t, rtt = gaps(*tcp_rtt(tr))
      ax.plot(t - t0, rtt, color=TCP_COLOR, lw=0.6, rasterized=True, label="TCP (sgi.pcap, with queueing)")
      p0 = ho_spans(pr)[0][0]
      t, rtt, lost = ping(pr, d)
      t, rtt = gaps(t, rtt)
      ax.plot(t - p0, rtt, color=PING_COLOR, lw=0.6, rasterized=True, label="ping (end to end)")
      ax.set_yscale("log")
      ax.set_ylim(3, 10000)
      ax.plot(lost - p0, np.full(len(lost), 3.6), "x", color=PING_COLOR, ms=3, mew=0.8, label="ping lost")
      ax.set_ylabel("RTT (ms)")
      label_run(ax, n)
    fig.legend(*axes[0].get_legend_handles_labels(), loc="outside lower center", ncol=2, frameon=False)
    save(fig, f"lte_rtt_{d}")
