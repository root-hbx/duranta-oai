#!/usr/bin/env python3
"""Analyze one run and export packet metrics and timestamped events."""

import argparse
import csv
import gzip
import json
from pathlib import Path
import re
import subprocess

import numpy as np

from events import extract
from metrics import event, interruption, per_second, write_json
from packets import export

def packet_table(run, output, filename, protocol, destination, datagram=1200):
  target = output / f"{Path(filename).stem}-payload.csv.gz"
  audit = export(run / filename, target, protocol, destination)
  audit["excluded_udp_control_or_other_lengths"] = 0
  # Keep both arrival bytes (legacy comparable) and unique datagram bytes available.
  times, sizes, frames, tokens = [], [], [], []
  with gzip.open(target, "rt") as stream:
    for row in csv.DictReader(stream):
      if protocol == "udp" and int(row["payload_bytes"]) != datagram:
        audit["excluded_udp_control_or_other_lengths"] += 1
        continue
      times.append(float(row["epoch"]))
      sizes.append(int(row["payload_bytes"]))
      frames.append(int(row["frame"]))
      tokens.append(row["token"])
  return np.array(times), np.array(sizes), np.array(frames), tokens, audit


def capture_stats(run):
  result = {}
  for path in (run / "logs").glob("sec2-cap-*.log"):
    text = path.read_text(errors="replace")
    result[path.stem] = {name.replace(" ", "_"): int(match[1]) if (match := re.search(r"(\d+) packets? " + name, text))
                        else None for name in ("captured", "received by filter", "dropped by kernel")}
  return result


def control_packets(run, output):
  command = ["tshark", "-n", "--disable-protocol", "gtp", "-r", str(run / "core.pcap"),
             "-Y", "ngap or s1ap or pfcp or gtpv2",
             "-T", "fields", "-E", "header=y", "-E", "separator=\t", "-E", "quote=d"]
  for field in ("frame.number", "frame.time_epoch", "ip.src", "ip.dst", "_ws.col.Protocol", "_ws.col.Info", "frame.protocols"):
    command += ["-e", field]
  with (output / "control-packets.tsv").open("w") as stream, (output / "tshark-control.stderr").open("w") as errors:
    subprocess.run(command, stdout=stream, stderr=errors, check=True)


def evidence(run, output, result):
  atomic = extract(run) if (run / "clocks.jsonl").exists() else []
  with (output / "atomic-events.jsonl").open("w") as stream:
    for row in atomic:
      stream.write(json.dumps(row, ensure_ascii=False) + "\n")
  result["atomic_events"] = len(atomic)
  if (run / "core.pcap").exists():
    control_packets(run, output)


def analyze(run, output, protocol, gap=0):
  run, output = run.resolve(), output.resolve()
  if output == run or run in output.parents:
    raise ValueError("Derived tables must be outside the raw run directory")
  output.mkdir(parents=True, exist_ok=True)
  raw_events = (run / "events.txt").read_text()
  match = re.search(r"UE IP ([\d.]+) \((?:lte|nr)\)", raw_events)
  result = {"run": str(run), "protocol": protocol, "gap_s": gap, "quality_flags": [],
            "udp_data_datagram_bytes": 1200 if protocol == "udp" else None,
            "exit_code": int((run / "exit-code.txt").read_text()) if (run / "exit-code.txt").exists() else None,
            "capture_stats": capture_stats(run)}
  if result["exit_code"] != 0:
    result["quality_flags"].append("attempt_did_not_exit_cleanly")
  for name in ("ue", "sgi", "core", "mac"):
    if f"sec2-cap-{name}" not in result["capture_stats"]:
      result["quality_flags"].append(f"sec2-cap-{name}:missing_capture_statistics")
  for name, stats in result["capture_stats"].items():
    if stats["dropped_by_kernel"] is None:
      result["quality_flags"].append(f"{name}:missing_capture_statistics")
    elif stats["dropped_by_kernel"]:
      result["quality_flags"].append(f"{name}:capture_drops")
  if not match:
    result["status"] = "source_attach_failed"
    evidence(run, output, result)
    write_json(output / "metrics.json", result)
    return result
  source_ip = match[1]
  target_match = re.search(r"target UE IP ([\d.]+)", raw_events)
  result["source_ip"] = source_ip
  result["target_ip"] = target_match[1] if target_match else None
  if target_match and target_match[1] != source_ip:
    result["quality_flags"].append("target_ip_changed")
  times, sizes, frames, tokens, audit = packet_table(run, output, "ue.pcap", protocol, source_ip)
  result["receiver_pcap_audit"] = audit
  if audit["fragmented_application_ip"]:
    result["quality_flags"].append("fragmented_application_packets_need_reassembly")
  if audit["truncated"]:
    result["quality_flags"].append("receiver_capture_contains_truncated_frames")
  result["receiver_payload_packets"] = len(times)
  if not len(times):
    result["status"] = "no_receiver_payloads"
    evidence(run, output, result)
    write_json(output / "metrics.json", result)
    return result
  order = np.argsort(times, kind="stable")
  times, sizes, frames = times[order], sizes[order], frames[order]
  tokens = [tokens[i] for i in order]
  if protocol == "udp":
    seen, unique = set(), []
    for i, token in enumerate(tokens):
      if token not in seen:
        unique.append(i)
        seen.add(token)
    result["duplicate_payload_tokens"] = len(tokens) - len(unique)
    result["datagram_sizes_bytes"] = {str(n): int((sizes == n).sum()) for n in np.unique(sizes)}
  traffic, change = event(run, "iperf3 dl"), event(run, "change:")
  result["traffic_epoch"], result["change_epoch"] = traffic, change
  result["packet_gap_p50_p99_max_s"] = np.percentile(np.diff(times), [50, 99, 100]).tolist() if len(times) > 1 else []
  result["status"] = "steady_observed" if change is None else "change_observed"
  if change is not None:
    result["interruption"] = interruption(times, change, gap, frames)
    result["per_second_mbps"] = per_second(times, sizes, change, traffic)
    result["steady_before_mbps"] = float(np.median([v for k, v in result["per_second_mbps"].items() if -30 <= k < -5]))
    if protocol == "udp":
      result["unique_per_second_mbps"] = per_second(times[unique], sizes[unique], change, traffic)
  else:
    result["per_second_mbps"] = per_second(times, sizes, traffic, traffic)
    result["steady_before_mbps"] = float(np.median([v for k, v in result["per_second_mbps"].items() if 10 <= k < 160]))
  # Sender continuity is a separate observation, not inferred from receiver absence.
  tx, txsizes, txframes, txtokens, txaudit = packet_table(run, output, "sgi.pcap", protocol, source_ip)
  result["sender_pcap_audit"] = txaudit
  if txaudit["truncated"] or txaudit["fragmented_application_ip"]:
    result["quality_flags"].append("sender_capture_truncation_or_application_fragmentation")
  result["sender_payload_packets"] = len(tx)
  if change is not None and result["interruption"].get("start_epoch") is not None:
    a, b = result["interruption"]["start_epoch"], result["interruption"]["end_epoch"]
    inside = tx[(a <= tx) & (tx <= b)]
    result["sender_during_interruption"] = {"packets": len(inside),
      "largest_gap_s": float(np.diff(np.r_[a, inside, b]).max()) if len(inside) else b - a}
    if protocol == "udp" and result["sender_during_interruption"]["largest_gap_s"] > .1:
      result["quality_flags"].append("sender_gap_exceeds_100ms_review_before_attributing_to_handover")
  if protocol == "udp" and len(tx):
    sent = set(txtokens)
    received = set(tokens)
    result["token_matching"] = {"sent_unique": len(sent), "received_unique": len(received),
                                 "matched_unique": len(sent & received), "receiver_only": len(received - sent)}
    if received - sent:
      result["quality_flags"].append("receiver_payload_tokens_missing_from_sender_capture")
  evidence(run, output, result)
  write_json(output / "metrics.json", result)
  return result


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("run", type=Path)
  parser.add_argument("output", type=Path)
  parser.add_argument("--protocol", choices=("tcp", "udp"), default="udp")
  parser.add_argument("--gap", type=int, default=0)
  args = parser.parse_args()
  result = analyze(args.run, args.output, args.protocol, args.gap)
  print(json.dumps({k: v for k, v in result.items() if not k.endswith("per_second_mbps")}, indent=2))
