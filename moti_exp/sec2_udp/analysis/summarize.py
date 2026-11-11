#!/usr/bin/env python3
"""Export every attempt, formal direction summaries and separate sensitivity observations."""

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np

from metrics import write_json


def statistics(values):
  return {"n": len(values), "mean_s": float(np.mean(values)), "median_s": float(np.median(values)),
          "min_s": float(min(values)), "max_s": float(max(values)),
          "p10_p90_s": np.percentile(values, [10, 90]).tolist()}


def attempt_condition(run, manifest):
  path = run / "condition.json"
  if path.exists():
    return json.loads(path.read_text()), "condition.json"
  # Initial manual pilots record their conditions in the run-begin event.
  matches = [row for row in manifest["pilots"]
             if re.fullmatch(re.escape(row["id"]) + r"-attempt\d+", run.name)]
  if len(matches) != 1:
    raise ValueError(f"Missing condition for undeclared manual pilot: {run.name}")
  condition = matches[0]
  lines = (run / "events.txt").read_text().splitlines()
  starts = [line.split(" run begin ", 1)[1] for line in lines if " run begin " in line]
  if len(starts) != 1:
    raise ValueError(f"Ambiguous run-begin evidence: {run.name}")
  actual = dict(item.split("=", 1) for item in starts[0].split())
  expected = {"direction": condition["direction"], "stage": condition["stage"],
              "gap": str(condition["gap_s"]), "protocol": condition["traffic"], "rate": condition["rate"]}
  if actual != expected:
    raise ValueError(f"Pilot declaration disagrees with run-begin evidence: {run.name}")
  source = "campaign.pilots+events.txt"
  return condition, source


def summarize(campaign):
  manifest = json.loads((campaign / "campaign.json").read_text())
  selected = json.loads((campaign / "selection.json").read_text())
  primary = json.loads((campaign / "tables/outages.json").read_text())
  attempts = []
  for run in sorted(path for path in (campaign / "runs").iterdir() if path.is_dir()):
    condition, condition_source = attempt_condition(run, manifest)
    table = campaign / "tables" / run.name
    metrics_path = table / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    interruption = metrics.get("interruption", {})
    stats = metrics.get("capture_stats", {})
    row = {"attempt": run.name, "condition_id": condition["id"], "condition_source": condition_source,
           "role": condition.get("role", "pilot"), "selected_primary": run.name in selected.values(),
           "direction": condition["direction"], "protocol": condition["traffic"], "rate": condition["rate"],
           "coverage_gap_s": condition["gap_s"], "result_status": metrics.get("status", "not_analyzed"),
           "interruption_status": interruption.get("status"), "outage_s": interruption.get("duration_s"),
           "quality_flags": ";".join(metrics.get("quality_flags", [])),
           "source_ip": metrics.get("source_ip"), "target_ip": metrics.get("target_ip"),
           "source_steady_mbps": metrics.get("steady_before_mbps"),
           "sender_max_gap_s": metrics.get("sender_during_interruption", {}).get("largest_gap_s"),
           **{f"{name}_capture_drops": stats.get(f"sec2-cap-{name}", {}).get("dropped_by_kernel")
              for name in ("ue", "sgi", "core", "mac")}}
    attempts.append(row)
  with (campaign / "tables/all-attempts.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(attempts[0]))
    writer.writeheader()
    writer.writerows(attempts)
  directions = {direction: statistics([row["duration_s"] for row in primary
                                      if row["direction"] == direction and not row["gap_s"]])
                for direction in ("lte2ntn", "ntn2lte")}
  gaps = {}
  for gap in (3, 24, 41):
    ids = {row["id"] for row in manifest["formal"] if row["gap_s"] == gap}
    rows = [row for row in attempts if row["condition_id"] in ids]
    # Every attempted formal repeat stays visible. Ambiguity or failures require explicit review.
    complete = (len(rows) == 3 and {row["condition_id"] for row in rows} == ids
                and all(row["interruption_status"] == "observed" and not row["quality_flags"] for row in rows))
    gaps[str(gap)] = {"status": "observed" if complete else "review_required", "attempts": rows}
    if complete:
      gaps[str(gap)]["total_interruption"] = statistics([row["outage_s"] for row in rows])
      gaps[str(gap)]["beyond_coverage_gap"] = statistics([row["outage_s"] - gap for row in rows])
  phases = json.loads((campaign / "tables/phase-samples.json").read_text())
  phase_rows = [{"event_id": row["event_id"], "attempt": row["attempt"], "direction": row["direction"],
                 "outage_s": row["outage"]["duration_s"], **row["phase_s"]}
                for row in phases if row["status"] == "observed"]
  result = {"direct_by_direction": directions, "coverage_gap_repeats": gaps,
            "low_load": [row for row in attempts if row["role"] == "low_load_sensitivity"],
            "attempt_count_including_pilots_and_failures": len(attempts),
            "primary_count": len(primary), "phase_sample_count": len(phase_rows)}
  write_json(campaign / "tables/supplementary.json", result)
  return result


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("campaign", type=Path)
  args = parser.parse_args()
  print(json.dumps(summarize(args.campaign), indent=2))
