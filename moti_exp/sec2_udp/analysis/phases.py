#!/usr/bin/env python3
"""Group observed direct-change intervals after retaining the full atomic/fine event tables."""

import argparse
import csv
import json
from pathlib import Path
import re

import numpy as np

from fine_timeline import fine_timeline
from events import PATTERNS
from metrics import write_json

RULE = {
  "version": 2,
  "basis": "Six adjacent intervals between receiver payloads and local UE log markers",
  "phases": [
    {"id": "rat_switch", "label": "RAT Switch", "vertical_label": "RAT\nSwitch", "color": "#237B9F",
     "definition": "Last receiver payload to first timestamped target UE log; observed RAT transition interval"},
    {"id": "prep_sync", "label": "Prep. & Sync", "vertical_label": "Prep. &\nSync", "color": "#8073AC",
     "definition": "First timestamped target UE log to first successful target synchronization; includes target preparation"},
    {"id": "si_prep", "label": "SI & Prep.", "vertical_label": "SI &\nPrep.", "color": "#71BFB2",
     "definition": "Successful target synchronization to first target PRACH record; includes RX and access preparation"},
    {"id": "ra_rrc", "label": "RA & RRC", "vertical_label": "RA &\nRRC", "color": "#AD0B08",
     "definition": "First target PRACH record to first target RRCSetupComplete/RRCConnectionSetupComplete generation log"},
    {"id": "nas_session", "label": "NAS & Session", "vertical_label": "NAS &\nSession", "color": "#EC817E",
     "definition": "Target SetupComplete generation log to target PDU Session Establishment Accept/Send Attach Complete"},
    {"id": "data", "label": "Data Resume", "vertical_label": "Data\nResume", "color": "#FEE066",
     "definition": "Target NAS session/attach completion to first received payload after the outage"}],
  "policy": "Missing or nonchronological boundaries remain unresolved"
}


def setup_complete_events(run, target_log, atomic):
  """Supplement older atomic tables from retained logs using their existing clock reference."""
  references = [row for row in atomic if row["file"] == target_log and row["kind"] == "first_timestamped_log"
                and row.get("monotonic") is not None and row.get("epoch") is not None]
  path = run / target_log
  if not references or not path.is_file():
    return []
  reference = min(references, key=lambda row: row["epoch"])
  offset = reference["epoch"] - reference["monotonic"]
  result = []
  with path.open(errors="replace") as stream:
    for number, line in enumerate(stream, 1):
      if not re.search(PATTERNS["rrc_setup_complete"], line):
        continue
      match = re.match(r"^(?:\S+Z\s+)?(\d+\.\d+)\s", line)
      if match:
        mono = float(match[1])
        result.append({"kind": "rrc_setup_complete", "epoch": mono + offset, "monotonic": mono,
                       "uncertainty_s": reference.get("uncertainty_s"), "clock_domain": "monotonic",
                       "file": target_log, "line": number, "text": line.rstrip(),
                       "evidence_source": "retained_target_ue_log"})
  return result


def partition(table, source, target, raw_run=None, output=None):
  fine_path = table / "fine-timeline.json"
  fine = json.loads(fine_path.read_text()) if fine_path.exists() else fine_timeline(table, source, target)
  if fine["status"] != "observed":
    return {"status": "unresolved", "reason": fine["status"]}
  outage = fine["outage"]
  atomic = [json.loads(line) for line in (table / "atomic-events.jsonl").read_text().splitlines()]
  metrics = json.loads((table / "metrics.json").read_text())
  if metrics["gap_s"]:
    return {"status": "coverage_gap_separate", "fine_timeline": "fine-timeline.json"}
  target_name = "nr" if target == "ntn" else "lte"
  target_log = f"logs/xr-ue-{target_name}.log"
  if not any(row["file"] == target_log and row["kind"] == "rrc_setup_complete" for row in atomic):
    run = raw_run if raw_run is not None else table.parents[1] / "runs" / table.name
    atomic.extend(setup_complete_events(run, target_log, atomic))
  selected = []
  previous = metrics["change_epoch"]
  for kind in ("first_timestamped_log", "sync", "rach_preamble", "rrc_setup_complete", "session"):
    candidates = [r for r in atomic if r["file"] == target_log and r["kind"] == kind
                  and r["epoch"] is not None and previous <= r["epoch"] <= outage["end_epoch"] + 1]
    if not candidates:
      return {"status": "unresolved", "reason": f"Missing target {kind}", "selected_boundaries": selected,
              "outage": outage}
    marker = min(candidates, key=lambda r: r["epoch"])
    selected.append(marker)
    previous = marker["epoch"]
  if selected[-1]["epoch"] < outage["start_epoch"]:
    return {"status": "unresolved", "reason": "Largest packet gap occurs after target access; causality unconfirmed",
            "selected_boundaries": selected, "outage": outage}
  points = [outage["start_epoch"]] + [r["epoch"] for r in selected] + [outage["end_epoch"]]
  clipped = np.clip(points, outage["start_epoch"], outage["end_epoch"])
  if np.any(np.diff(clipped) < 0):
    return {"status": "unresolved", "reason": "Nonchronological boundaries", "selected_boundaries": selected}
  durations = {phase["id"]: float(value) for phase, value in zip(RULE["phases"], np.diff(clipped))}
  if abs(sum(durations.values()) - outage["duration_s"]) > 1e-6:
    raise ValueError("Grouped phases do not sum to the packet gap")
  # Keep all pre-outage protocol/control events separately, without counting them in the interruption.
  precursors = [r for r in fine["events"] if r["epoch"] < outage["start_epoch"]]
  result = {"status": "observed", "rule_version": RULE["version"], "source": source, "target": target,
            "phase_s": durations, "outage": outage, "selected_boundaries": selected, "precursors": precursors}
  destination = output or table
  destination.mkdir(parents=True, exist_ok=True)
  write_json(destination / "phase-partition.json", result)
  return result


def aggregate(campaign, output=None):
  output = output or campaign / "tables"
  output.mkdir(parents=True, exist_ok=True)
  manifest = json.loads((campaign / "campaign.json").read_text())
  selection = json.loads((campaign / "selection.json").read_text())
  samples = []
  for row in manifest["formal"]:
    if row["role"] != "primary":
      continue
    table = campaign / "tables" / selection[row["event_id"]]
    result = partition(table, row["source"], row["target"], campaign / "runs" / selection[row["event_id"]],
                       output / selection[row["event_id"]])
    samples.append({"event_id": row["event_id"], "attempt": selection[row["event_id"]], "direction": row["direction"],
                    **result})
  direct = [row for row in samples if row["status"] != "coverage_gap_separate"]
  if not direct:
    raise ValueError("No primary direct-change samples are available")
  unresolved = [row for row in direct if row["status"] != "observed"]
  if unresolved:
    raise ValueError(f"Review {len(unresolved)} unresolved phase partitions; do not silently exclude samples")
  write_json(output / f"phase-rules-v{RULE['version']}.json", RULE)
  write_json(output / "phase-samples.json", samples)
  phase_rows = [{"event_id": row["event_id"], "attempt": row["attempt"], "direction": row["direction"],
                 "outage_s": row["outage"]["duration_s"], **row["phase_s"]} for row in direct]
  with (output / "direct-phase-durations.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(phase_rows[0]))
    writer.writeheader()
    writer.writerows(phase_rows)
  groups = []
  for direction, label in ((None, "All direct changes"), ("lte2ntn", "LTE→NTN"), ("ntn2lte", "NTN→LTE")):
    rows = [row for row in direct if direction is None or row["direction"] == direction]
    values = {p["id"]: [row["phase_s"][p["id"]] for row in rows] for p in RULE["phases"]}
    groups.append({"id": direction or "pooled", "label": label, "n": len(rows),
                   "phase_mean_s": {key: float(np.mean(v)) for key, v in values.items()},
                   "phase_median_s": {key: float(np.median(v)) for key, v in values.items()},
                   "phase_p10_p90_s": {key: np.percentile(v, [10, 90]).tolist() for key, v in values.items()},
                   "mean_total_s": float(np.mean([row["outage"]["duration_s"] for row in rows]))})
  result = {"rule_version": RULE["version"], "statistic": "mean", "presentation": "phase_bars", "phases": RULE["phases"],
            "groups": groups, "samples": len(direct), "excluded_from_main_c": "Geometric no-coverage events",
            "aggregation": "Phase means sum to the mean direct outage; the CDF uses all primary durations"}
  write_json(output / "breakdown.json", result)
  return result


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("campaign", type=Path)
  parser.add_argument("--output", type=Path, help="Separate output directory for phase tables; default: CAMPAIGN/tables")
  args = parser.parse_args()
  print(json.dumps(aggregate(args.campaign, args.output), indent=2))
