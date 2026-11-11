#!/usr/bin/env python3
"""Plan and run the fixed NY400 UDP campaign serially."""

import argparse
import json
import os
import signal
import subprocess
from pathlib import Path
import sys

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "analysis"))
from metrics import timeline, write_json


def plan(campaign):
  campaign.mkdir(parents=True, exist_ok=True)
  events = timeline(MODULE / "input/timeline-NY-S2-400.csv")
  pilots = []
  for traffic in ("tcp", "udp"):
    for direction in ("lte2ntn", "ntn2lte"):
      pilots.append({"id": f"pilot-{traffic}-{direction}", "traffic": traffic, "direction": direction,
                     "stage": "redirect", "gap_s": 0, "rate": "100M"})
  for direction in ("lte2ntn", "ntn2lte"):
    pilots.append({"id": f"pilot-steady-{direction}", "traffic": "udp", "direction": direction,
                   "stage": "base", "gap_s": 0, "rate": "100M"})
  gaps = [row for row in events if row["gap_s"]]
  for row in gaps:
    pilots.append({"id": f"pilot-gap{row['gap_s']}", "traffic": "udp", "direction": row["direction"],
                   "stage": "session", "gap_s": row["gap_s"], "rate": "100M"})
  formal = [{**row, "id": row["event_id"], "traffic": "udp", "rate": "100M", "role": "primary"} for row in events]
  for row in gaps:
    for repeat in (2, 3):
      formal.append({**row, "id": f"gap{row['gap_s']}-repeat{repeat}", "traffic": "udp", "rate": "100M",
                     "role": "gap_sensitivity"})
  result = {"scenario": "NY S2-400", "window_utc": ["2026-10-28T01:00:00Z", "2026-10-28T02:00:00Z"],
            "clip_seconds": [1800, 2400], "primary_count": 41, "formal_count": 47, "datagram_bytes": 1200,
            "traffic_duration_s": 180, "pilots": pilots, "formal": formal,
            "low_load": "Select 70% of the smaller measured steady receive rate after pilots; keep separate"}
  path = campaign / "campaign.json"
  if path.exists():
    existing = json.loads(path.read_text())
    if any(existing.get(key) != value for key, value in result.items()):
      raise ValueError("Existing campaign manifest differs; create a new campaign")
    return existing
  path.write_text(json.dumps(result, indent=2) + "\n")
  return result



def execute(campaign, rows, attempt):
  selection_path = campaign / "selection.json"
  selection = json.loads(selection_path.read_text()) if selection_path.exists() else {}
  for row in rows:
    if row.get("role") == "primary" and row["event_id"] in selection:
      raise ValueError(f"Primary event already selected: {row['event_id']}")
    name = f"{row['id']}-attempt{attempt:02d}"
    run = campaign / "runs" / name
    if run.exists():
      raise FileExistsError(run)
    env = {**os.environ, "SEC2_RUN_DIR": str(run), "SEC2_PYTHON": sys.executable,
           "SEC2_CONDITION": json.dumps(row), "PROTO": row["traffic"], "STAGE": row["stage"],
           "RATE": row["rate"], "GAP": str(row["gap_s"]) if row["gap_s"] else ""}
    print(f"{name}: {row['direction']} {row['stage']} {row['rate']}", flush=True)
    with (campaign / f"{name}.console.log").open("x") as log:
      process = subprocess.Popen(["bash", str(MODULE.parents[1] / "ci-scripts/yaml_files/ntn_lte_sw/xrat_udp.sh"),
                                  row["direction"]], env=env, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
      try:
        returncode = process.wait(timeout=780)
      except (subprocess.TimeoutExpired, KeyboardInterrupt):
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=60)
        raise
    table = campaign / "tables" / name
    with (campaign / f"{name}.analysis.log").open("x") as log:
      subprocess.run([sys.executable, "-B", str(MODULE / "analysis/analyze.py"), str(run), str(table),
                      "--protocol", row["traffic"], "--gap", str(row["gap_s"])],
                     stdout=log, stderr=subprocess.STDOUT, check=True)
    metrics = json.loads((table / "metrics.json").read_text())
    if returncode or metrics["quality_flags"] or metrics["status"] in ("source_attach_failed", "no_receiver_payloads"):
      raise RuntimeError(f"Run failed: {name}; inspect its logs and metrics.json")
    if row["stage"] != "base" and metrics["interruption"]["status"] != "observed":
      raise RuntimeError(f"Recovery not observed: {name}")
    if row.get("role") == "primary":
      selection[row["event_id"]] = name
      write_json(selection_path, selection)


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("campaign", type=Path)
  parser.add_argument("--ids", nargs="+", help="Run these IDs, all formal runs, or pilots; omit to plan only")
  parser.add_argument("--attempt", type=int, default=1)
  parser.add_argument("--low-load-rate", help="Add the two lowload controls at this offered rate, e.g. 9.66M")
  args = parser.parse_args()
  campaign = args.campaign.resolve()
  manifest = plan(campaign)
  if args.low_load_rate:
    controls = [{"id": f"lowload-{direction}", "direction": direction, "stage": "redirect", "gap_s": 0,
                 "traffic": "udp", "rate": args.low_load_rate, "role": "low_load_sensitivity"}
                for direction in ("lte2ntn", "ntn2lte")]
    if manifest.get("sensitivity", controls) != controls:
      raise ValueError("Low-load conditions already recorded at another rate")
    manifest["sensitivity"] = controls
    write_json(campaign / "campaign.json", manifest)
  candidates = {row["id"]: row for row in manifest["pilots"] + manifest["formal"] + manifest.get("sensitivity", [])}
  if not args.ids:
    print(f"{len(manifest['formal'])} formal runs; IDs: {' '.join(candidates)}")
  else:
    ids = ([row["id"] for row in manifest["formal"]] if args.ids == ["all"] else
           [row["id"] for row in manifest["pilots"]] if args.ids == ["pilots"] else args.ids)
    execute(campaign, [candidates[key] for key in ids], args.attempt)
