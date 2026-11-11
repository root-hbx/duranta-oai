#!/usr/bin/env python3
"""Save fine observed intervals independently of any grouping chosen for figure c."""

import argparse
import json
from pathlib import Path

from events import intervals
from metrics import write_json


def fine_timeline(table, source, target):
  metrics = json.loads((table / "metrics.json").read_text())
  atomic = [json.loads(line) for line in (table / "atomic-events.jsonl").read_text().splitlines()]
  outage = metrics.get("interruption", {})
  if outage.get("status") != "observed":
    return {"status": "no_observed_endpoints", "events": [], "intervals": []}
  change, end = metrics["change_epoch"], outage["end_epoch"]
  start = outage["start_epoch"]
  target_name = "nr" if target == "ntn" else target
  source_name = "nr" if source == "ntn" else source
  target_log = f"logs/xr-ue-{target_name}.log"
  source_log = f"logs/xr-ue-{source_name}{'-source' if source == target else ''}.log"
  selected = []
  for row in atomic:
    if row["epoch"] is None or not change - 2 <= row["epoch"] <= end + 1:
      continue
    if row["kind"] == "harness" or (row["file"].count("/") == 1 and row["kind"] not in ("failure", "timeout")):
      selected.append({**row, "relative_change_s": row["epoch"] - change,
                       "role": "target" if row["file"] in (target_log, "logs/processes-target.txt") else
                               "source" if row["file"] in (source_log, "logs/processes-source.txt") else "control_or_ran"})
  # Keep simultaneous observations as separate markers on the same timeline.
  markers = [(row["kind"], row["epoch"], {"file": row["file"], "line": row["line"], "text": row["text"]})
             for row in selected]
  markers += [("last_receiver_payload", start, {"file": "ue.pcap", "frame": outage["start_frame"]}),
              ("first_receiver_payload", end, {"file": "ue.pcap", "frame": outage["end_frame"]})]
  markers.sort(key=lambda row: row[1])
  spans = intervals(markers, start, end)
  if abs(sum(row["overlap_s"] for row in spans) - outage["duration_s"]) > 1e-6:
    raise ValueError("Fine timeline does not partition the packet gap")
  result = {"status": "observed", "source": source, "target": target, "outage": outage,
            "description": "Observed event intervals clipped to the receiver packet gap",
            "events": selected, "intervals": spans}
  write_json(table / "fine-timeline.json", result)
  return result


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("table", type=Path)
  parser.add_argument("--source", choices=("lte", "ntn"), required=True)
  parser.add_argument("--target", choices=("lte", "ntn"), required=True)
  args = parser.parse_args()
  result = fine_timeline(args.table, args.source, args.target)
  print(result["status"], len(result["events"]), "events", len(result["intervals"]), "intervals")
