"""Extract every matching occurrence; retain raw files and independently version presentation groups."""

import json
from datetime import datetime
from pathlib import Path
import re

PATTERNS = {
  "a2": r"A2 report",
  "redirect_release": r"RRCConnectionRelease with redirection|RRCRelease with redirection",
  "nas_release": r"NR_NAS_CONN_RELEASE_IND|RRC_CONNECTION_RELEASE_IND",
  "rrc_idle": r"State = (?:NR_)?RRC_IDLE",
  "sync": r"Got synch|Initial sync successful",
  "rx_resynchronizing": r"Resynchronizing RX",
  "mib": r"Found MIB|MIB decoded|MIB received",
  "sib1": r"Found SIB1\b|SIB1 decoded|Received SIB1\b",
  "sib19": r"Found SIB19",
  "rach_preamble": r"RA preamble|RA-PREAMBLE|Generating PRACH|PRACH.*preamble",
  "rar": r"Received RAR|Found RAR with the intended",
  "rrc_connected": r"State = (?:NR_)?RRC_CONNECTED",
  "rrc_setup_complete": r"Generating RRC(?:Connection)?SetupComplete",
  "security": r"SecurityMode|Security Mode|security mode",
  "registered": r"Registration Accept|Attach Accept",
  "session": r"PDU Session Establishment Accept|Send Attach Complete",
  "gtp": r"Create Session|Modify Bearer|PFCP.*(Establishment|Modification)|Created Session|Removed Session",
  "timeout": r"Timer T3\d+ expired|T3\d+ expired|[Tt]imeout|[Rr]etransmi",
  "failure": r"AssertFatal|Assertion|Segmentation fault|[Ff]atal|[Ff]ailed|[Rr]eject",
}


def docker_epoch(stamp):
  # Python 3.10 accepts microseconds; keep the original nanosecond text in the source log.
  stamp = re.sub(r"(\.\d{6})\d+", r"\1", stamp)
  return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def netlink_events(path, run):
  """ip monitor observation times are userspace timestamps, not exact kernel execution times."""
  rows, stamp, zone = [], None, None
  for number, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
    if raw.startswith("timezone "):
      zone = raw.split()[1]
    match = re.match(r"^\[(\d{4}-[^]]+)\]\s*(.*)", raw)
    if match:
      text = match[1].replace(" ", "T")
      if zone:
        stamp = docker_epoch(text + zone[:3] + ":" + zone[3:])
      raw_text = match[2]
    else:
      raw_text = raw.rstrip()
    if "oaitun_" in raw_text:
      rows.append({"kind": "netlink", "epoch": stamp, "clock_domain": "ip_monitor_observation",
                   "uncertainty_s": None, "file": str(path.relative_to(run)), "line": number, "text": raw.rstrip()})
  return rows


def process_events(path, run, clocks):
  """Decode Linux /proc/PID/stat start ticks using the recorded boot clock; retain tick uncertainty."""
  samples = [row for row in clocks if "boottime_ns" in row]
  if not samples:
    return []
  offsets = [(row["epoch_ns"] - row["boottime_ns"]) / 1e9 for row in samples]
  offset = sum(offsets) / len(offsets)
  lines = path.read_text(errors="replace").splitlines()
  hz = int(lines[1])
  rows = []
  for number, raw in enumerate(lines, 1):
    match = re.match(r"^(\d+) \((.*)\) (.*)$", raw)
    if not match:
      continue
    fields = match[3].split()
    ticks = int(fields[19])
    rows.append({"kind": "process_start", "epoch": offset + ticks / hz, "pid": int(match[1]),
                 "parent_pid": int(fields[1]), "comm": match[2], "clock_domain": "proc_stat_boot_ticks",
                 "uncertainty_s": 1 / hz + max(abs(x - offset) for x in offsets),
                 "file": str(path.relative_to(run)), "line": number, "text": raw})
  return rows


def clock_offset(path):
  records = [json.loads(line) for line in Path(path).read_text().splitlines()]
  offsets = [(r["epoch_ns"] - (r["monotonic_before_ns"] + r["monotonic_after_ns"]) / 2) / 1e9 for r in records]
  widths = [(r["monotonic_after_ns"] - r["monotonic_before_ns"]) / 2e9 for r in records]
  if len({r["boot_id"] for r in records}) != 1:
    raise ValueError("Host reboot within attempt")
  offset = sum(offsets) / len(offsets)
  return offset, max(abs(x - offset) + w for x, w in zip(offsets, widths))


def extract(run):
  run = Path(run)
  offset, error = clock_offset(run / "clocks.jsonl")
  events = []
  clocks = [json.loads(line) for line in (run / "clocks.jsonl").read_text().splitlines()]
  for path in sorted((run / "logs").glob("processes-*.txt")):
    events.extend(process_events(path, run, clocks))
  for path in sorted((run / "logs").rglob("*.log")):
    if path.stat().st_size == 0:
      continue
    if path.name == "netlink-xr-ue.log":
      events.extend(netlink_events(path, run))
      continue
    first_monotonic = True
    for number, raw in enumerate(path.open(errors="replace"), 1):
      match = re.match(r"^(\d+\.\d+)\s", raw)
      # Docker-timestamped core/RAN lines keep their text, even when there is no softmodem clock.
      mono = float(match[1]) if match else None
      if mono is None:
        match = re.match(r"^\S+Z\s+(\d+\.\d+)\s", raw)
        mono = float(match[1]) if match else None
      observed_epoch = None
      docker_time = re.match(r"^(\d{4}-\d\d-\d\dT\S+Z)\s", raw)
      if docker_time:
        observed_epoch = docker_epoch(docker_time[1])
      if first_monotonic and mono is not None:
        events.append({"kind": "first_timestamped_log", "epoch": mono + offset, "monotonic": mono,
                       "uncertainty_s": error, "clock_domain": "monotonic", "file": str(path.relative_to(run)),
                       "line": number, "text": raw.rstrip()})
        first_monotonic = False
      for kind, pattern in PATTERNS.items():
        if re.search(pattern, raw):
          events.append({"kind": kind, "epoch": mono + offset if mono is not None else observed_epoch,
                         "monotonic": mono, "uncertainty_s": error if mono is not None else None,
                         "clock_domain": "monotonic" if mono is not None else "docker_log_observation",
                         "file": str(path.relative_to(run)), "line": number, "text": raw.rstrip()})
  for number, raw in enumerate((run / "events.txt").read_text().splitlines(), 1):
    stamp, text = raw.split(" ", 1)
    events.append({"kind": "harness", "epoch": float(stamp), "clock_domain": "realtime", "uncertainty_s": None,
                   "file": "events.txt", "line": number, "text": text})
  return sorted(events, key=lambda e: (e["epoch"] is None, e["epoch"] or 0, e["file"], e["line"]))


def intervals(boundaries, start, end):
  """Clip a sequential, evidence-selected partition to the actual no-payload interval."""
  if end < start:
    raise ValueError("Reversed outage endpoints")
  if any(a[1] > b[1] for a, b in zip(boundaries, boundaries[1:])):
    raise ValueError("Phase boundaries are not chronological")
  rows = []
  for (name, a, evidence_a), (_, b, evidence_b) in zip(boundaries, boundaries[1:]):
    lo, hi = max(a, start), min(b, end)
    rows.append({"phase": name, "raw_start": a, "raw_end": b, "overlap_s": max(0, hi - lo),
                 "evidence_start": evidence_a, "evidence_end": evidence_b})
  return rows
