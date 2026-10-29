"""Util Func:

Fewest NTN satellites for full coverage at a minimum elevation: the smallest Walker-delta shell of sim/xrat_count.py
(330 km / 53 deg, F = 1, sqrt(N) planes) with a satellite at least that high over each site every second of the 25 h.
Usage: python3 cal_min_coverage.py <min elevation deg>"""

import sys
from pathlib import Path

import numpy as np
from sgp4.api import jday

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sim"))
import xrat_count as X  # noqa: E402

CHUNK = 600


def shells(el):
  """Distinct Walker configurations (total, planes, sats per plane, target N) in increasing total, from the area bound."""
  r = X.RE + X.NTN_SHELL[0]
  lam = np.arccos(X.RE * np.cos(el) / r) - el
  lo = int(np.ceil(2 / (1 - np.cos(lam))))
  out = {}
  for n in range(lo, 4 * lo):
    planes = max(1, round(np.sqrt(n)))
    spp = round(n / planes)
    out.setdefault((planes, spp), n)
  return lo, sorted((p * s, p, s, n) for (p, s), n in out.items() if p * s >= lo)


def covered(n, el, step):
  """True if each site sees a satellite of the n-satellite shell at least el high at every step s."""
  sh = X.walker(n, (X.NTN_SHELL,))
  jd0, fr0 = jday(X.T0.year, X.T0.month, X.T0.day, 0, 0, 0)
  ts = np.arange(0, X.T_SIM, step, dtype=float)
  for c0 in range(0, len(ts), CHUNK):
    t = ts[c0 : c0 + CHUNK]
    pos = X.walker_pos(sh, t)
    for s in X.SITES:
      site, up = X.site_eci(s, jd0 + fr0 + t / 86400.0)
      d = pos - site[None]
      sin_el = np.einsum("snk,nk->sn", d, up) / np.linalg.norm(d, axis=2)
      if (sin_el.max(axis=0) < np.sin(el)).any():
        return False
  return True


def main():
  el = np.radians(float(sys.argv[1]))
  lo, cands = shells(el)
  print(f"area bound {lo} satellites, {len(cands)} configurations to try", file=sys.stderr)
  for total, planes, spp, n in cands:
    # a coarse pass first: most configurations have a hole long enough for 5 s steps
    if covered(n, el, 5) and covered(n, el, 1):
      print(f"{total} ({planes} planes x {spp})")
      return
  sys.exit("no full coverage below 4x the area bound")


if __name__ == "__main__":
  main()
