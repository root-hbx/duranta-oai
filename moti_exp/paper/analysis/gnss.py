"""LTE/NTN with the same IP, the NR UE GNSS position off by GNSS_KM km (a moving UE with a stale fix):
gnss_rate: the share of runs (TCP and ping) whose target UE got a RAR, the top axis the minutes without a fix at
120 km/h; gnss_bw: TCP dl throughput of run 1 at 0 km and the smallest error that fails."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_common import GNSS_KM, MODES, all_runs, fig, finish, gnss_curve, out, plt, run1, steps, thpt_1s  # noqa: E402

import numpy as np  # noqa: E402

KMH = 120

for mode in MODES:
  ok, n = [], []
  for km in GNSS_KM:
    rs = [r for kind in ("tcp-dl", "ping") for r in all_runs(kind, mode, gnss_curve(km))]
    ok.append(sum("rar" in steps(r) for r in rs))
    n.append(len(rs))
  f, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
  x = np.arange(len(GNSS_KM))
  rate = [a / b if b else np.nan for a, b in zip(ok, n)]
  ax.bar(x, rate, width=0.6, color="#000080")
  for i, (a, b) in enumerate(zip(ok, n)):
    ax.text(i, (rate[i] if b else 0) + 0.02, f"{a}/{b}", ha="center", va="bottom", fontsize=7)
  ax.set_xticks(x, [str(km) for km in GNSS_KM])
  ax.set_xlabel("GNSS position error (km)")
  ax.set_ylim(0, 1.15)
  ax.set_yticks(np.arange(0, 1.01, 0.25))
  ax.set_ylabel("Runs with a RAR")
  ax.grid(False)
  top = ax.secondary_xaxis("top")
  top.set_xticks(x, [f"{km * 60 / KMH:g}" for km in GNSS_KM])
  top.set_xlabel(f"Minutes without a fix at {KMH} km/h")
  out(f, "analysis", f"gnss_rate_{mode}")

  f, ax = fig()
  for km, ls in ((GNSS_KM[0], "-"), (GNSS_KM[1], ":")):
    c = gnss_curve(km)
    run = run1("tcp-dl", mode, c)
    if run is None:
      continue
    t, mbps = thpt_1s(run)
    ax.plot(t, mbps, color=c[1], ls=ls, lw=1.2, label=f"LTE/NTN, same IP, GNSS {km} km off")
  ax.set_ylim(bottom=-0.5)
  ax.set_ylabel("Throughput (Mbps)")
  finish(f, ax, "analysis", f"gnss_bw_{mode}", loc="lower right", bbox_to_anchor=(1, 0.06))
