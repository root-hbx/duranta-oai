"""The old NY400 sampling/stitching method with explicit packet endpoints and provenance."""

import csv
import json
from pathlib import Path
import numpy as np

WIN = 3600
SPAN = (-30, 60)


def event(run, pattern):
  for line in (Path(run) / "events.txt").read_text().splitlines():
    stamp, text = line.split(" ", 1)
    if pattern in text:
      return float(stamp)
  return None


def timeline(path):
  result, start = [], None
  for row in csv.DictReader(Path(path).open()):
    t, name = int(row["t_s"]), row["event"]
    if name in ("lte2ntn", "ntn2lte"):
      result.append({"t_s": t, "gap_s": 0, "source": name[:3], "target": name[-3:]})
    elif name.startswith("gap-start"):
      start = t, name[-3:]
    else:
      t0, source = start or (0, name[-3:])
      result.append({"t_s": t0, "gap_s": t - t0, "source": source, "target": name[-3:]})
      start = None
  result.sort(key=lambda row: row["t_s"])
  for i, row in enumerate(result):
    row["event_id"] = f"event-{i + 1:02d}"
    row["direction"] = f"{row['source']}2{row['target']}"
    row["stage"] = "session" if row["gap_s"] else "redirect"
  return result


def per_second(times, sizes, change, traffic):
  times, sizes = np.asarray(times), np.asarray(sizes)
  keep = times > traffic
  times, sizes = times[keep] - change, sizes[keep]
  if not len(times):
    raise ValueError("No receiver payloads")
  edges = np.arange(np.ceil(traffic - change), np.floor(times.max()) + 1)
  values, _ = np.histogram(times, edges, weights=sizes)
  return dict(zip(edges[:-1].astype(int).tolist(), (values * 8 / 1e6).tolist()))


def interruption(times, change, gap=0, frames=None):
  """Legacy-compatible maximum gap, plus explicit right-censoring and packet references."""
  times = np.asarray(times)
  order = np.argsort(times, kind="stable")
  times = times[order]
  frames = np.asarray(frames)[order] if frames is not None else order + 1
  selected = (change - 1 < times) & (times < change + gap + 60)
  ts, fs = times[selected], frames[selected]
  if len(ts) < 2:
    return {"status": "insufficient_payloads", "duration_s": None}
  index = int(np.argmax(np.diff(ts)))
  start, end = float(ts[index]), float(ts[index + 1])
  # Maximum-gap endpoints alone cannot prove recovery if all packets predate the trigger.
  recovered = end > change + gap and np.any(times > change + gap + 5)
  return {"status": "observed" if recovered else "right_censored", "duration_s": end - start if recovered else None,
          "largest_observed_gap_s": end - start, "start_epoch": start, "end_epoch": end,
          "start_frame": int(fs[index]), "end_frame": int(fs[index + 1]),
          "window_start_epoch": change - 1, "window_end_epoch": change + gap + 60}


def stitch(events, series, steady):
  values, provenance = np.empty(WIN), []
  for second in range(WIN):
    index = int(np.argmin([max(e["t_s"] - second, 0, second - e["t_s"] - e["gap_s"]) for e in events]))
    row = events[index]
    relative = second - row["t_s"]
    measured = SPAN[0] <= relative < row["gap_s"] + SPAN[1] and relative in series[index]
    rat = row["source"] if relative < 0 else row["target"]
    values[second] = series[index][relative] if measured else steady[rat]
    provenance.append({"second": second, "event_id": row["event_id"], "relative_s": relative,
                       "origin": "measured" if measured else "steady_median", "rat": rat})
  return values, provenance


def write_json(path, value):
  Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
