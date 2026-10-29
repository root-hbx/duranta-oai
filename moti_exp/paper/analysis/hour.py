"""One hour of outages per site and NTN stage: the mode 1 h window of sim/xrat_count.py, each outage replaced by a
REGEN same-IP OAI run (build/pcap-paper-xrat-1h, xrat_tcp.sh with TAG iwk): a RAT change by a redirect run of that
direction, a period without coverage by a session run with that gap (GAP) between the RATs before and after it. TCP dl
throughput per 1 s. Each second comes from the run of the nearest outage, aligned on its change (source fade-out, or
the source kill), within SPAN of it; any other second is the median throughput of the serving RAT before the changes."""

import csv
import sys
from functools import cache
from pathlib import Path

import numpy as np
from matplotlib.patches import Patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_common import event, out, plt, tcp_fields  # noqa: E402

BUILD = Path(__file__).resolve().parents[3] / "build"
SITES = ("Denver", "NY")
STAGES = ("S1-300", "S2-400", "S3-500")
RUNS = BUILD / "pcap-paper-xrat-1h"
WIN = 3600
SPAN = (-30, 60)  # s around the change taken from a run (after the gap for a no-coverage one)
COLORS = {"lte2ntn": "#000080", "ntn2lte": "#B22222"}


def runs(prefix):
  """The runs whose directory starts with prefix, in the order they ran."""
  return sorted((d for d in RUNS.glob(prefix + "-[0-9]*") if d.is_dir()), key=lambda d: d.name[-11:])


@cache
def per_second(run):
  """TCP dl goodput at the UE in Mbps per 1 s, keyed by the whole seconds from the change."""
  tc = event(run, "change:")
  t0 = event(run, "iperf3 dl")
  pkts = [(t - tc, n) for t, dport, n, _, _ in tcp_fields(run, "ue.pcap") if n > 0 and dport == 5201 and t > t0]
  last = max(p[0] for p in pkts)
  edges = np.arange(np.ceil(t0 - tc), np.floor(last) + 1)
  b, _ = np.histogram([p[0] for p in pkts], edges, weights=[p[1] for p in pkts])
  return dict(zip(edges[:-1].astype(int), b * 8 / 1e6))


def outage(run, gap):
  """The longest gap (s) between TCP dl payloads at the UE in the gap + 60 s after the change."""
  tc = event(run, "change:")
  t = np.array([t for t, dport, n, _, _ in tcp_fields(run, "ue.pcap")
                if n > 0 and dport == 5201 and tc - 1 < t < tc + gap + 60])
  return float(np.diff(t).max())


def outages(site, stage):
  """The outages of the mode window: (start s, gap s, RAT before, RAT after, run)."""
  rows = list(csv.DictReader(open(BUILD / "sim-xrat" / f"timeline-{site}-{stage}.csv")))
  pool = {d: runs(f"tcp-dl-{d}-regen-redirect-iwk") for d in COLORS}
  used = {d: 0 for d in COLORS}
  res, start = [], None
  for r in rows:
    t, e = int(r["t_s"]), r["event"]
    if e in COLORS:
      res.append((t, 0, e[:3], e[-3:], pool[e][used[e]]))
      used[e] += 1
    elif e.startswith("gap-start"):
      start = (t, e[-3:])
    else:
      # a window starting without coverage: the gap from its start
      t0, a = start or (0, e[-3:])
      g = t - t0
      res.append((t0, g, a, e[-3:], runs(f"tcp-dl-{a}2{e[-3:]}-regen-session-iwk-gap{g}")[0]))
      start = None
  return sorted(res)


def figure(site, stage):
  ev = outages(site, stage)
  series = [per_second(r) for *_, r in ev]
  lost = [outage(r, g) for _, g, _, _, r in ev]
  # steady throughput of each RAT: the seconds before the fade-out of the runs leaving it
  steady = {a: np.median([v for r in runs(f"tcp-dl-{a}2{b}-regen-redirect-iwk") for k, v in per_second(r).items() if -30 <= k < -5])
            for a, b in (("lte", "ntn"), ("ntn", "lte"))}
  y = np.empty(WIN)
  for s in range(WIN):
    k = int(np.argmin([max(t - s, 0, s - t - g) for t, g, *_ in ev]))
    t, g, a, b, _ = ev[k]
    if SPAN[0] <= s - t < g + SPAN[1] and s - t in series[k]:
      y[s] = series[k][s - t]
    else:
      y[s] = steady[a if s < t else b]

  f, ax = plt.subplots(figsize=(7, 2.2), constrained_layout=True)
  for t, g, *_ in ev:
    if g:
      ax.axvspan(t / 60, (t + g) / 60, color="0.85", lw=0)
  ax.plot(np.arange(WIN) / 60, y, color="black", lw=0.6)
  for t, g, a, b, _ in ev:
    if not g:
      ax.plot(t / 60, -1.5, marker="^", ms=3.5, color=COLORS[f"{a}2{b}"], ls="none")
  ax.plot([], [], marker="^", ms=3.5, color=COLORS["lte2ntn"], ls="none", label="LTE→NTN")
  ax.plot([], [], marker="^", ms=3.5, color=COLORS["ntn2lte"], ls="none", label="NTN→LTE")
  ax.set_xlim(0, WIN / 60)
  ax.grid(False)
  ax.set_ylim(bottom=-3)
  ax.set_xlabel("Time (min)")
  ax.set_ylabel("Throughput (Mbps)")
  n_gap = sum(1 for _, g, *_ in ev if g)
  ax.set_title(f"{len(ev)} outages in 1 h: {len(ev) - n_gap} RAT changes + {n_gap} no coverage, "
               f"outage median {np.median(lost):.1f} s, {sum(lost):.0f} s in total", fontsize=8, loc="left", pad=12)
  handles, _ = ax.get_legend_handles_labels()
  ax.legend(handles=handles + [Patch(color="0.85", label="No coverage")], loc="lower right", bbox_to_anchor=(1, 1), ncol=3, frameon=False, fontsize=6)
  out(f, "analysis", f"hour_{site.lower()}_{stage.split('-')[1]}")
  print(f"{site} {stage}: outages {len(ev)} ({n_gap} no coverage), s p10/50/90 {np.percentile(lost, [10, 50, 90]).round(1)}, "
        f"total {sum(lost):.0f} s, no coverage {[round(x, 1) for (_, g, *_), x in zip(ev, lost) if g]} s, steady {steady}")


def main():
  for site in SITES:
    for stage in STAGES:
      figure(site, stage)


if __name__ == "__main__":
  main()
