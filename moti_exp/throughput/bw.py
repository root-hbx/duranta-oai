#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-CSSL-1.0
"""Export and plot application receiver goodput from iperf3 JSON over wall time."""
import argparse
import csv
import json
import math
from pathlib import Path


def receiver_intervals(path):
    text = path.read_text()
    data, _ = json.JSONDecoder().raw_decode(text[text.index('{'):])  # Logs can include process diagnostics.
    rows = []
    epoch = data['start']['timestamp']['timesecs']
    intervals = data['intervals']
    if data.get('error') and len(intervals) > 1 and intervals[-1] == intervals[-2]:
        intervals = intervals[:-1]  # iperf can repeat its final interval on interruption.
    previous_end = 0.0
    for interval in intervals:
        total = interval['sum']
        if total.get('sender', False):
            raise ValueError('Use the receiving endpoint log')
        if total['start'] < previous_end - 1e-4:
            raise ValueError('Overlapping application intervals')
        seconds = total['seconds']
        if seconds > 0:
            rows.append((epoch + total['start'], seconds, total['bytes']))
        previous_end = total['end']
    if not rows:
        raise ValueError('No application receive intervals')
    return data, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receiver', type=Path)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--plot', type=Path)
    parser.add_argument('--change', type=float, help='change epoch from events.txt')
    args = parser.parse_args()
    data, rows = receiver_intervals(args.receiver)
    with args.csv.open('x') as stream:
        writer = csv.writer(stream)
        writer.writerow(('time', 'mbps'))
        writer.writerows((f'{t:.6f}', f'{b * 8 / dt / 1e6:.6f}') for t, dt, b in rows)
    duration = sum(dt for _, dt, _ in rows)
    goodput = sum(b for _, _, b in rows) * 8 / duration / 1e6
    print(f'Application receiver: {goodput:.3f} Mbps over {duration:.3f} wall seconds (including the change)')
    if args.plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        zero = args.change if args.change is not None else rows[0][0]
        bins = {}
        for t, dt, b in rows:
            start = t - rows[0][0]
            for k in range(math.floor(start), math.ceil(start + dt)):
                overlap = max(0, min(start + dt, k + 1) - max(start, k))
                received, covered = bins.get(k, (0, 0))
                bins[k] = (received + b * overlap / dt, covered + overlap)
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.plot([rows[0][0] + k - zero for k in bins],
                [b * 8 / dt / 1e6 for b, dt in bins.values()], lw=1)
        ax.set(xlabel='Time from change (s)' if args.change is not None else 'Time (s)',
               ylabel='Throughput (Mbps)', ylim=(0, None))
        if args.change is not None:
            ax.axvline(0, color='gray', ls='--', lw=.8)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=200)
        plt.close(fig)
    if data.get('error'):
        raise RuntimeError(data['error'])


if __name__ == '__main__':
    main()
