"""Streaming classic-PCAP reader for receiver application payloads, with packet provenance.

No tshark caches are created beside historical data. Fragmented IP is reported rather than
silently counted as a complete datagram. The campaign uses 1200-byte UDP datagrams.
"""

from dataclasses import dataclass
import csv
import gzip
import json
from pathlib import Path
import socket
import struct


@dataclass(frozen=True)
class Packet:
  frame: int
  epoch: float
  src: str
  dst: str
  sport: int
  dport: int
  payload_bytes: int
  token: str


def read_pcap(path, protocol="udp", destination=None, port=5201, audit=None):
  """Yield DL payload packets with their original PCAP frame number and timestamp.

  UDP token is the first 12 payload bytes, preserved verbatim, not an assumed iperf ID layout.
  Frame records and truncated/fragmented counters remain observable for quality checks.
  """
  if audit is None:
    audit = {}
  audit.update(frames=0, matched=0, truncated=0, fragmented_ipv4=0, fragmented_application_ip=0)
  magics = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
            b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}
  with Path(path).open("rb") as stream:
    header = stream.read(24)
    if len(header) != 24 or header[:4] not in magics:
      raise ValueError(f"Not a supported classic PCAP: {path}")
    endian, scale = magics[header[:4]]
    linktype = struct.unpack(endian + "I", header[20:24])[0] & 0xffff
    audit["linktype"] = linktype
    if linktype not in (1, 101, 113, 276, 0):
      raise ValueError(f"Unsupported PCAP link type {linktype}")
    while raw := stream.read(16):
      if len(raw) != 16:
        raise ValueError("Partial PCAP record header")
      sec, frac, captured, original = struct.unpack(endian + "IIII", raw)
      data = stream.read(captured)
      if len(data) != captured:
        raise ValueError("Partial PCAP packet")
      audit["frames"] += 1
      if captured < original:
        audit["truncated"] += 1
      if linktype == 1:
        if len(data) < 14:
          continue
        kind, offset = struct.unpack_from("!H", data, 12)[0], 14
        while kind in (0x8100, 0x88a8) and len(data) >= offset + 4:
          kind = struct.unpack_from("!H", data, offset + 2)[0]
          offset += 4
      elif linktype == 113:
        if len(data) < 16:
          continue
        kind, offset = struct.unpack_from("!H", data, 14)[0], 16
      elif linktype == 276:
        if len(data) < 20:
          continue
        kind, offset = struct.unpack_from("!H", data)[0], 20
      else:
        kind, offset = 0x800, 4 if linktype == 0 else 0
      if kind != 0x800 or len(data) < offset + 20 or data[offset] >> 4 != 4:
        continue
      ip_hlen = (data[offset] & 15) * 4
      total, fragment = struct.unpack_from("!HxxH", data, offset + 2)
      number = data[offset + 9]
      src = socket.inet_ntoa(data[offset + 12:offset + 16])
      dst = socket.inet_ntoa(data[offset + 16:offset + 20])
      if fragment & 0x3fff:
        audit["fragmented_ipv4"] += 1
        if number == {"udp": 17, "tcp": 6}[protocol] and destination and dst == destination:
          audit["fragmented_application_ip"] += 1
        continue
      if number != {"udp": 17, "tcp": 6}[protocol]:
        continue
      start = offset + ip_hlen
      if len(data) < start + 8:
        continue
      sport, dport = struct.unpack_from("!HH", data, start)
      if dport != port or (destination and dst != destination):
        continue
      if protocol == "udp":
        length = struct.unpack_from("!H", data, start + 4)[0] - 8
        token = data[start + 8:start + 20].hex()
      else:
        if len(data) < start + 20:
          continue
        length = total - ip_hlen - (data[start + 12] >> 4) * 4
        token = data[start + 4:start + 8].hex()
      if length <= 0:
        continue
      audit["matched"] += 1
      yield Packet(audit["frames"], sec + frac * scale, src, dst, sport, dport, length, token)


def export(path, target, protocol="udp", destination=None):
  audit = {}
  target = Path(target)
  target.parent.mkdir(parents=True, exist_ok=True)
  with gzip.open(target, "wt", newline="", compresslevel=1) as stream:
    writer = csv.writer(stream)
    writer.writerow(list(Packet.__dataclass_fields__))
    for packet in read_pcap(path, protocol, destination, audit=audit):
      writer.writerow((packet.frame, packet.epoch, packet.src, packet.dst, packet.sport, packet.dport,
                       packet.payload_bytes, packet.token))
  target.with_suffix(".audit.json").write_text(json.dumps(audit, indent=2) + "\n")
  return audit
