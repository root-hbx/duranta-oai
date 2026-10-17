#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-CSSL-1.0

"""Find two satellites of a TLE set that serve a ground position one after the other.

Satellite A serves at the trace start and sets below the minimum elevation, satellite B rises before A sets.
The overlap [B rises, A sets] is where the UE can hand over from A to B. Prints the best pairs with the
trace start (UTC) and the TLE indices to pass to generate_orbital_trace.py --start-utc / --sat-index.
"""

from __future__ import annotations

import argparse

import numpy as np
from skyfield.api import EarthSatellite, load, wgs84

from generate_orbital_trace import load_tle_entries
from trace_utils import ecef_to_geodetic_wgs84, parse_position_triplet


def passes(elev: np.ndarray, t: np.ndarray, min_elev: float) -> list[tuple[float, float, float]]:
    """(rise, set, max elevation) of each pass above min_elev, in seconds of the search grid."""
    above = elev >= min_elev
    out = []
    edges = np.flatnonzero(np.diff(above.astype(int)))
    starts = list(edges[~above[edges]] + 1)
    ends = list(edges[above[edges]])
    if above[0]:
        starts.insert(0, 0)
    if above[-1]:
        ends.append(len(above) - 1)
    for s, e in zip(starts, ends):
        out.append((t[s], t[e], float(elev[s : e + 1].max())))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tle", required=True, help="TLE file")
    parser.add_argument("--ue", required=True, help="UE ECEF position (m), e.g. '-3310204,-5006785,2157610'")
    parser.add_argument("--min-elev", type=float, default=30.0, help="minimum elevation of a serving satellite (deg)")
    parser.add_argument("--hours", type=float, default=6.0, help="search span after the newest TLE epoch (h)")
    parser.add_argument("--step", type=float, default=2.0, help="search grid (s)")
    parser.add_argument("--lead", type=float, default=120.0, help="trace start before B rises (s), A must serve then")
    parser.add_argument("--overlap", type=float, nargs=2, default=(30.0, 120.0), metavar=("MIN", "MAX"),
                        help="overlap of the two passes (s)")
    parser.add_argument("-n", type=int, default=5, help="number of pairs to print")
    args = parser.parse_args()

    ts = load.timescale()
    entries = load_tle_entries(args.tle)
    sats = [EarthSatellite(l1, l2, name, ts) for name, l1, l2 in entries]
    lat, lon, alt = ecef_to_geodetic_wgs84(*parse_position_triplet(args.ue))
    station = wgs84.latlon(lat, lon, elevation_m=alt)

    t0 = max(s.epoch.tt for s in sats)
    grid = np.arange(0.0, args.hours * 3600.0, args.step)
    times = ts.tt_jd(t0 + grid / 86400.0)
    all_passes = []
    for i, sat in enumerate(sats):
        elev = (sat - station).at(times).altaz()[0].degrees
        all_passes += [(i, *p) for p in passes(elev, grid, args.min_elev)]

    pairs = []
    for a, a_rise, a_set, a_max in all_passes:
        for b, b_rise, b_set, b_max in all_passes:
            overlap = a_set - b_rise
            start = b_rise - args.lead
            if a == b or b_set <= a_set or not args.overlap[0] <= overlap <= args.overlap[1] or a_rise > start:
                continue
            pairs.append((min(a_max, b_max), a, b, start, b_rise - start, a_set - start, b_set - start))
    pairs.sort(reverse=True)

    print(f"{len(all_passes)} passes above {args.min_elev} deg, {len(pairs)} pairs")
    for max_elev, a, b, start, b_rise, a_set, b_set in pairs[: args.n]:
        utc = ts.tt_jd(t0 + start / 86400.0).utc_strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"start {utc}  A=#{a} {entries[a][0]}  B=#{b} {entries[b][0]}  "
              f"B rises {b_rise:.0f} s, A sets {a_set:.0f} s, B sets {b_set:.0f} s, min of max elev {max_elev:.1f} deg")


if __name__ == "__main__":
    main()
