"""Outages per hour of an NTN-first UE in Denver and New York, served by the Starlink 1st-gen Direct-to-Cell LTE
satellites (real TLEs, build/sim-xrat/tle) and by the Starlink 2nd-gen D2C NR-NTN constellation at three deployment
stages below its minimum coverage (Walker-delta, the 330 km / 53 deg shell of FCC DA 26-1078). A satellite serves when
it is at least 30 deg above the horizon. The UE uses NTN whenever it has a satellite, else LTE. An outage is a direct
RAT change or a period without any satellite (counted once).
Output in build/sim-xrat: summary.md, the per-window counts (windows.csv), the 1 h timeline of the mode window of each
site and stage (timeline-<site>-<stage>.csv).
"""

import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sgp4.api import SatrecArray, Satrec, jday

OUT = Path(__file__).resolve().parents[3] / "build" / "sim-xrat"
T0 = datetime(2026, 10, 28, tzinfo=timezone.utc)
T_SIM = 25 * 3600  # 24 h of window starts + 1 h
CHUNK = 300
WIN = 3600
WIN_STEP = 600
EL_MIN = 30.0
SITES = (("Denver", 39.74, -104.99, 1.6), ("NY", 40.71, -74.01, 0.0))  # name, lat, lon (deg), alt (km)
MU = 398600.4418
RE = 6378.137

# Starlink 1st-gen DTC in the TLEs: the near-circular 345-375 km shells at 43 and 53 deg
DTC_ALT = (345.0, 375.0)
DTC_INC = (43.0, 53.0)
# 2nd-gen D2C 53 deg shell (alt km, inc deg), FCC DA 26-1078; stage name, satellites
NTN_SHELL = (330, 53)
STAGES = (("S1-300", 300), ("S2-400", 400), ("S3-500", 500))
NONE, LTE, NTN = 0, 1, 2


def gmst(jd):
  """Greenwich mean sidereal angle (rad) at UT1 julian date jd."""
  t = (jd - 2451545.0) / 36525.0
  g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t
  return np.radians(g % 360.0)


def site_eci(site, jd):
  """Site position and local up unit vector in the inertial frame, shape (n, 3) each."""
  _, lat, lon, alt = site
  lat, lon = np.radians(lat), np.radians(lon)
  f = 1 / 298.257223563
  e2 = f * (2 - f)
  n = RE / np.sqrt(1 - e2 * np.sin(lat) ** 2)
  ecef = np.array([(n + alt) * np.cos(lat) * np.cos(lon), (n + alt) * np.cos(lat) * np.sin(lon), (n * (1 - e2) + alt) * np.sin(lat)])
  up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
  th = gmst(jd)
  c, s = np.cos(th), np.sin(th)

  def rot(v):
    return np.stack([c * v[0] - s * v[1], s * v[0] + c * v[1], np.full_like(th, v[2])], axis=1)

  return rot(ecef), rot(up)


def any_visible(pos, site, up):
  """pos (sats, n, 3), site/up (n, 3): True where some satellite is at least EL_MIN high."""
  d = pos - site[None]
  sin_el = np.einsum("snk,nk->sn", d, up) / np.linalg.norm(d, axis=2)
  return (sin_el >= np.sin(np.radians(EL_MIN))).any(axis=0)


def dtc_sats():
  lines = (OUT / "tle" / "starlink.tle").read_text().splitlines()
  sats = []
  for i in range(0, len(lines) - 2, 3):
    s = Satrec.twoline2rv(lines[i + 1], lines[i + 2])
    a = (MU / (s.no_kozai / 60.0) ** 2) ** (1 / 3)
    inc = np.degrees(s.inclo)
    if DTC_ALT[0] < a - RE < DTC_ALT[1] and s.ecco < 0.001 and min(abs(inc - x) for x in DTC_INC) < 1.0:
      sats.append(s)
  return SatrecArray(sats), len(sats)


def walker(total, shells):
  """Walker-delta (F = 1) per shell, the satellites split evenly: list of (r, n, inc, raan, u0) arrays."""
  per = total / len(shells)
  out = []
  for alt, inc in shells:
    planes = max(1, round(np.sqrt(per)))
    spp = round(per / planes)
    t = planes * spp
    p, s = np.meshgrid(np.arange(planes), np.arange(spp), indexing="ij")
    raan = 2 * np.pi * p.ravel() / planes
    u0 = 2 * np.pi * s.ravel() / spp + 2 * np.pi * p.ravel() / t
    r = RE + alt
    out.append((r, np.sqrt(MU / r**3), np.radians(inc), raan, u0))
  return out


def walker_pos(shells, ts):
  """Positions (sats, n, 3) at seconds ts from T0 (T0 = the inertial frame epoch, no J2)."""
  pos = []
  for r, n, inc, raan, u0 in shells:
    u = u0[:, None] + n * ts[None]
    cu, su = np.cos(u), np.sin(u)
    co, so = np.cos(raan)[:, None], np.sin(raan)[:, None]
    pos.append(r * np.stack([co * cu - so * su * np.cos(inc), so * cu + co * su * np.cos(inc), su * np.sin(inc)], axis=2))
  return np.concatenate(pos)


def coverage():
  """Per-second availability of LTE and of NTN at each stage for each site, plus the satellite counts."""
  jd0, fr0 = jday(T0.year, T0.month, T0.day, 0, 0, 0)
  dtc, n_dtc = dtc_sats()
  stages = {name: walker(total, (NTN_SHELL,)) for name, total in STAGES}
  lte = {s[0]: np.zeros(T_SIM, bool) for s in SITES}
  ntn = {(s[0], name): np.zeros(T_SIM, bool) for s in SITES for name in stages}
  for c0 in range(0, T_SIM, CHUNK):
    ts = np.arange(c0, c0 + CHUNK, dtype=float)
    fr = fr0 + ts / 86400.0
    err, r, _ = dtc.sgp4(np.full_like(fr, jd0), fr)
    r[err != 0] = 0.0  # below the horizon by construction
    # the Walker frame is aligned with the TEME frame at T0 (arbitrary, both are statistically uniform in RAAN)
    pos = {name: walker_pos(shells, ts) for name, shells in stages.items()}
    for s in SITES:
      site, up = site_eci(s, jd0 + fr)
      lte[s[0]][c0 : c0 + CHUNK] = any_visible(r, site, up)
      for name in stages:
        ntn[(s[0], name)][c0 : c0 + CHUNK] = any_visible(pos[name], site, up)
  n_ntn = {name: sum(len(s[3]) for s in shells) for name, shells in stages.items()}
  return lte, ntn, n_dtc, n_ntn


def serve(lte, ntn):
  """Serving RAT per second (NONE/LTE/NTN): NTN whenever it has a satellite."""
  return np.where(ntn, NTN, np.where(lte, LTE, NONE)).astype(np.int8)


def events(rat, t0, t1):
  """Events in [t0, t1): (t, kind) with kind lte2ntn / ntn2lte (direct changes), gap-start-<lte|ntn> (the RAT
  before), gap-end-<lte|ntn> (the RAT after)."""
  out = []
  for i in np.flatnonzero(rat[t0 + 1 : t1] != rat[t0 : t1 - 1]) + t0 + 1:
    a, b = rat[i - 1], rat[i]
    if a == LTE and b == NTN:
      out.append((i - t0, "lte2ntn"))
    elif a == NTN and b == LTE:
      out.append((i - t0, "ntn2lte"))
    elif b == NONE:
      out.append((i - t0, f"gap-start-{'lte' if a == LTE else 'ntn'}"))
    else:
      out.append((i - t0, f"gap-end-{'lte' if b == LTE else 'ntn'}"))
  return out


def main():
  OUT.mkdir(parents=True, exist_ok=True)
  lte, ntn, n_dtc, n_ntn = coverage()
  np.savez_compressed(OUT / "coverage.npz", **{f"{k}-lte": v for k, v in lte.items()},
                      **{f"{s}-{name}": v for (s, name), v in ntn.items()})
  rows, summary = [], []
  for site, *_ in SITES:
    for name, _ in STAGES:
      rat = serve(lte[site], ntn[(site, name)])
      per = []
      for w0 in range(0, T_SIM - WIN + 1, WIN_STEP):
        ev = events(rat, w0, w0 + WIN)
        l2n = sum(k == "lte2ntn" for _, k in ev)
        n2l = sum(k == "ntn2lte" for _, k in ev)
        gaps = sum(k.startswith("gap-start") for _, k in ev)
        gap_s = int((rat[w0 : w0 + WIN] == NONE).sum())
        per.append((w0, l2n, n2l, gaps, gap_s, ev))
        rows.append((site, name, w0, l2n, n2l, gaps, l2n + n2l + gaps, gap_s))
      tot = [p[1] + p[2] + p[3] for p in per]
      count = Counter(tot)
      mode = max(count, key=lambda k: (count[k], -k))
      w0, l2n, n2l, gaps, gap_s, ev = next(p for p, n in zip(per, tot) if n == mode)
      with open(OUT / f"timeline-{site}-{name}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["t_s", "event", "window_start_s"])
        wr.writerows((t, k, w0) for t, k in ev)
      summary.append((site, name, n_ntn[name], float(ntn[(site, name)][:86400].mean()), np.percentile(tot, [10, 50, 90]),
                      mode, count[mode], w0, l2n, n2l, gaps, gap_s))
  with open(OUT / "windows.csv", "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["site", "stage", "window_start_s", "lte2ntn", "ntn2lte", "gaps", "outages", "no_coverage_s"])
    wr.writerows(rows)
  lines = [
    f"# Outages per hour, NTN-first UE, elevation >= {EL_MIN:.0f} deg",
    "",
    f"T0 {T0:%Y-%m-%d %H:%M} UTC, 24 h of 1 h windows every {WIN_STEP // 60} min, 1 s step. "
    f"LTE: {n_dtc} DTC satellites from the TLEs, available "
    + ", ".join(f"{s[0]} {lte[s[0]][:86400].mean():.1%}" for s in SITES) + " of the time. "
    f"NTN: Walker-delta {NTN_SHELL[0]} km / {NTN_SHELL[1]} deg. An outage is a RAT change or a period without coverage.",
    "",
    "| site | stage | NTN sats | NTN avail | outages/h p10 / p50 / p90 | mode (windows) | mode window start | L→N + N→L + no coverage | no coverage s |",
    "|---|---|---|---|---|---|---|---|---|",
  ]
  for site, name, n, avail, q, mode, nw, w0, l2n, n2l, gaps, gap_s in summary:
    lines.append(f"| {site} | {name} | {n} | {avail:.1%} | {q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f} | {mode} ({nw}/{len(tot)}) | "
                 f"{w0} s | {l2n} + {n2l} + {gaps} | {gap_s} |")
  (OUT / "summary.md").write_text("\n".join(lines) + "\n")
  print("\n".join(lines))


if __name__ == "__main__":
  main()
