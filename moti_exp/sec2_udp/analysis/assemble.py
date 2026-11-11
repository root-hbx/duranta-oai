#!/usr/bin/env python3
"""Assemble the declared primary attempts; never substitute convenient or successful repeats."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from metrics import stitch, write_json


def assemble(campaign):
  manifest = json.loads((campaign / "campaign.json").read_text())
  selection = json.loads((campaign / "selection.json").read_text())
  events = [row for row in manifest["formal"] if row["role"] == "primary"]
  if len(events) != 41 or set(selection) != {row["event_id"] for row in events}:
    raise ValueError("Exactly the 41 predeclared event IDs must be mapped to attempts")
  series, durations, details = [], [], []
  steady_pool = {"lte": [], "ntn": []}
  for row in events:
    attempt = selection[row["event_id"]]
    condition = json.loads((campaign / "runs" / attempt / "condition.json").read_text())
    for key in ("id", "event_id", "direction", "traffic", "stage", "rate", "gap_s", "role"):
      if condition[key] != row[key]:
        raise ValueError(f"Attempt manifest mismatch ({key}): {attempt}")
    data = json.loads((campaign / "tables" / attempt / "metrics.json").read_text())
    if data["protocol"] != "udp" or data["gap_s"] != row["gap_s"]:
      raise ValueError(f"Attempt condition mismatch: {attempt}")
    if data["quality_flags"] or data.get("interruption", {}).get("status") != "observed":
      raise ValueError(f"Review invalid or censored attempt explicitly before plotting: {attempt}")
    values = {int(key): value for key, value in data["per_second_mbps"].items()}
    series.append(values)
    durations.append(data["interruption"]["duration_s"])
    if not row["gap_s"]:
      steady_pool[row["source"]].extend(value for second, value in values.items() if -30 <= second < -5)
    details.append({**row, "attempt": attempt, **data["interruption"]})
  steady = {rat: float(np.median(values)) for rat, values in steady_pool.items()}
  values, provenance = stitch(events, series, steady)
  intervals = np.diff([row["t_s"] for row in events]).tolist()
  measured_starts = []
  for row in details:
    metric = json.loads((campaign / "tables" / row["attempt"] / "metrics.json").read_text())
    measured_starts.append(row["t_s"] + row["start_epoch"] - metric["change_epoch"])
  measured_intervals = np.diff(measured_starts).tolist()
  result = {"protocol": "udp", "events": events, "hour_series_mbps": values.tolist(),
            "durations_s": durations, "intervals_s": intervals, "steady_mbps": steady,
            "measured_outage_start_intervals_s": measured_intervals,
            "measured_interval_median_s": float(np.median(measured_intervals)),
            "steady_pool": "All 38 primary direct-change runs; old TCP pool contained 40",
            "duration_median_s": float(np.median(durations)), "duration_mean_s": float(np.mean(durations)),
            "interval_median_s": float(np.median(intervals)), "interval_mean_s": float(np.mean(intervals))}
  write_json(campaign / "tables/analysis.json", result)
  write_json(campaign / "tables/outages.json", details)
  with (campaign / "tables/hour-provenance.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(provenance[0]) + ["throughput_mbps"])
    writer.writeheader()
    for row, value in zip(provenance, values):
      writer.writerow({**row, "throughput_mbps": value})
  return result


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("campaign", type=Path)
  args = parser.parse_args()
  data = assemble(args.campaign)
  print({key: value for key, value in data.items() if key.endswith("median_s") or key.endswith("mean_s")})
