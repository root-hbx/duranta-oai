#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-CSSL-1.0

"""Service windows of the satellites of several traces seen from a UE, and the CHO time trigger between them.

A satellite serves while its elevation is at least --min-elev. For each pair of consecutive satellites (trace order),
the overlap [next rises, previous sets] gives the condEventT1 parameters of 38.331 (t1-Threshold: UTC in 10 ms since
1900-01-01, duration in 100 ms) and the t-Service of the previous cell (SIB19). The traces need the utc_ms column of
generate_orbital_trace.py --start-utc.
"""

from __future__ import annotations

import argparse
import csv

from trace_utils import elevation_deg, parse_position_triplet

UNIX_TO_1900_S = 2208988800  # 1900-01-01 to 1970-01-01, the epoch of t1-Threshold and t-Service


def load_elevation(path: str, ue) -> list[tuple[float, int, float]]:
    """(trace time s, UTC Unix ms, elevation deg) per row."""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        if "utc_ms" not in reader.fieldnames:
            raise ValueError(f"{path}: no utc_ms column, generate it with generate_orbital_trace.py --start-utc")
        scale = 1e-3 if "time_ms" in reader.fieldnames else 1.0
        tcol = "time_ms" if scale != 1.0 else "time_s"
        for r in reader:
            pos = (float(r["pos_x"]), float(r["pos_y"]), float(r["pos_z"]))
            rows.append((float(r[tcol]) * scale, int(r["utc_ms"]), elevation_deg(pos, ue)))
    return rows


def window(rows, min_elev: float) -> tuple[tuple, tuple] | None:
    """First and last row of the first interval above min_elev."""
    above = [r for r in rows if r[2] >= min_elev]
    if not above:
        return None
    first = rows.index(above[0])
    last = first
    while last + 1 < len(rows) and rows[last + 1][2] >= min_elev:
        last += 1
    return rows[first], rows[last]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("traces", nargs="+", help="orbital trace CSVs, in serving order")
    parser.add_argument("--ue", required=True, help="UE ECEF position (m), e.g. '-3310204,-5006785,2157610'")
    parser.add_argument("--min-elev", type=float, default=25.0, help="minimum elevation of a serving satellite (deg)")
    args = parser.parse_args()

    ue = parse_position_triplet(args.ue)
    windows = []
    for path in args.traces:
        rows = load_elevation(path, ue)
        w = window(rows, args.min_elev)
        windows.append(w)
        if w is None:
            print(f"{path}: never above {args.min_elev} deg")
            continue
        peak = max(r[2] for r in rows)
        print(f"{path}: serves {w[0][0]:.0f}-{w[1][0]:.0f} s (trace time), max elevation {peak:.1f} deg")

    for (prev, nxt), path in zip(zip(windows, windows[1:]), args.traces[1:]):
        if prev is None or nxt is None or nxt[0][0] > prev[1][0]:
            print(f"-> {path}: no overlap, no handover window")
            continue
        rise, set_ = nxt[0], prev[1]
        print(f"-> {path}: handover window {rise[0]:.0f}-{set_[0]:.0f} s (trace time)")
        print(f"   condEventT1 t1-Threshold-r17 = {(rise[1] + UNIX_TO_1900_S * 1000) // 10}")
        print(f"   condEventT1 duration-r17     = {(set_[1] - rise[1]) // 100}")
        print(f"   t-Service-r17 (previous cell) = {(set_[1] + UNIX_TO_1900_S * 1000) // 10}")


if __name__ == "__main__":
    main()
