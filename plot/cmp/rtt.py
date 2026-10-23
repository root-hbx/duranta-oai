import numpy as np

from cmp_common import KINDS, MODES, T_RUN, fig, finish, ping_dl, run1, t_start, tcp_rtt


def med_1s(t, rtt):
  """Median per 1 s from the traffic start, NaN in a second without a sample (no line through it)."""
  sec = np.floor(t).astype(int)
  x = np.arange(0, T_RUN)
  return x + 0.5, np.array([np.median(rtt[sec == i]) if np.any(sec == i) else np.nan for i in x])


def cross(ax, x, med):
  """A red cross next to the last point of a line whose session is lost."""
  i = np.flatnonzero(~np.isnan(med))[-1]
  ax.plot(x[i] + 2, med[i], "x", color="#FF0000", ms=7, mew=1.8, zorder=5, clip_on=False)


def icmp(run):
  """Idle RTT of the ping run; the session is lost when requests are still unanswered after the last reply."""
  t, rtt, lost = ping_dl(run)
  return t, rtt, len(lost) > 0 and lost[-1] > t[-1]


def tcp(run):
  """Loaded RTT of the TCP run (TCP timestamps at the UPF, sgi.pcap); the session is lost when the RTTs stop
  before the end."""
  t, rtt = tcp_rtt(run)
  t = t - t_start(run)
  return t[t < T_RUN], rtt[t < T_RUN], t.max() < T_RUN - 5


# figure name, run kind, RTT source, y axis top and tick step (ms)
for name, kind, rtts, top, step in (("icmp", "ping", icmp, 150, 30), ("tcp", "tcp-dl", tcp, 1000, 200)):
  for mode in MODES:
    f, ax = fig()
    for k in KINDS:
      run = run1(kind, mode, k)
      if run is None:
        continue
      t, rtt, lost = rtts(run)
      x, med = med_1s(t, rtt)
      ax.plot(x, med, color=k[1], ls=k[2], lw=1.2, label=k[0])
      if lost:
        cross(ax, x, med)
    ax.set_ylim(0, top)
    ax.set_yticks(np.arange(0, top + 1, step))
    ax.set_ylabel("RTT (ms)")
    finish(f, ax, f"cmp_{name}_rtt_{mode}", loc="upper right")
