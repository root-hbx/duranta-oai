from cmp_common import KINDS, MODES, fig, finish, run1, thpt_1s

for mode in MODES:
  f, ax = fig()
  for k in KINDS:
    run = run1("tcp-dl", mode, k)
    if run is None:
      continue
    t, mbps = thpt_1s(run)
    ax.plot(t, mbps, color=k[1], ls=k[2], lw=1.2, label=k[0])
  # below 0 a little: the zero throughput after a lost session stays clear of the x axis
  ax.set_ylim(bottom=-0.5)
  ax.set_ylabel("Throughput (Mbps)")
  finish(f, ax, f"cmp_bw_{mode}")
