import numpy as np

from cmp_common import KINDS, MODES, fig, finish, ping_dl, run1

for mode in MODES:
  f, ax = fig()
  for k in KINDS:
    run = run1("ping", mode, k)
    if run is None:
      continue
    t, rtt, lost = ping_dl(run)
    # median per 1 s (the 10 ms pings jitter with the scheduling), no line through a second without a reply
    sec = np.floor(t).astype(int)
    x = np.arange(0, 180)
    med = np.array([np.median(rtt[sec == i]) if np.any(sec == i) else np.nan for i in x])
    ax.plot(x + 0.5, med, color=k[1], ls=k[2], lw=1.2, label=k[0])
    # session lost: requests still unanswered after the last reply, a red cross next to the end of the line
    if len(lost) and lost[-1] > t[-1]:
      i = np.flatnonzero(~np.isnan(med))[-1]
      ax.plot(x[i] + 2.5, med[i], "x", color="#FF0000", ms=7, mew=1.8, zorder=5, clip_on=False)
  ax.set_yscale("log")
  ax.set_ylim(5, 1000)
  ax.set_ylabel("RTT (ms)")
  finish(f, ax, f"cmp_rtt_{mode}")
