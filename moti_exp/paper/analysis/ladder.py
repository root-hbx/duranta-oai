"""Signalling ladder of the cross-RAT redirect runs (build/pcap-paper-xrat): one time line from the source fade-out
of the air interface (mac.pcap: RRC, NAS, RAR), the core (core.pcap: S1AP/NGAP with NAS, GTPv2, PFCP), the data plane
(sgi.pcap at the UPF, core.pcap GTP-U, ue.pcap at the UE) and the softmodem logs (sync, PRACH, RA, RRC state), and the
first step of the change that never happened:
  F0 source release, F1 target sync, F2 SIB19 (NR target), F3 RACH, F4 RRC and NAS, F5 session and IP, F6 data plane.
The logs run on CLOCK_MONOTONIC: their epoch offset comes from the source release step of summary.txt.
ladder.py <run dir>...: build/ladder/<run>.txt per run; ladder.py --all: build/ladder/first_fail.csv over all runs (the
first failing step, the marks of each ladder, made if missing).
"""

import csv
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_common import XRAT, event, steps  # noqa: E402

BUILD = Path(__file__).resolve().parents[3] / "build"
OUT = BUILD / "ladder"
CUT = OUT / "cut"
PRE, POST = 1, 20  # cut window (s) around the fade-out
PREFS = ("-o", "mac-lte.attempt_rrc_decode:TRUE", "-o", "mac-nr.attempt_rrc_decode:TRUE",
         # the OAI LTE OPT frames carry a failed CRC status
         "-o", "mac-lte.attempt_to_dissect_crc_failures:TRUE",
         "-o", "nas-eps.null_decipher:TRUE", "-o", "nas-5gs.null_decipher:TRUE",
         "--enable-heuristic", "mac_lte_udp", "--enable-heuristic", "mac_nr_udp")
BROADCAST = ("MIB", "SIB1", "SystemInformationBlockType1", "System Information", "SystemInformation")
UE_HOST = ".0.61"  # xr-ue on the core network (xrat.yaml, any CORE_NET)
# LTE UL-CCCH decodes of padding, no such procedure in the logs; an RRCConnectionRequest counts only right after a RAR
NOISE = ("RRCConnectionReestablishmentRequest", "RRCConnectionResumeRequest", "RRCEarlyDataRequest")
MSG3 = "RRCConnectionRequest"
RAN_HOSTS = (".0.60", ".0.70", ".0.72")  # lte-enb1, ntn-gnb1, ntn-gnb2 (lte.yaml, ntn.yaml)
CORE_SKIP = ("Heartbeat", "HEARTBEAT")
# source release in summary.txt: log, pattern (xrat.sh LTE_REDIR, NR_REDIR)
RELEASE = {"lte": (("lte-enb1", "xr-ue-lte"), "RRCConnectionRelease with redirection to NR"),
           "nr": (("ntn-gnb1", "xr-ue-nr"), "RRCRelease with redirection to E-UTRA")}
# target log lines: name, log, pattern
TARGET_LOG = {
  "nr": (("sync", "xr-ue-nr", "Initial sync successful"), ("SIB1", "xr-ue-nr", "SIB1 decoded"),
         ("SIB19", "xr-ue-nr", "Found SIB19"), ("Msg1", "xr-ue-nr", "placing PRACH"),
         ("gNB PRACH", "ntn-gnb1", "Initiating RA procedure with preamble"),
         ("RAR other RAPID", "xr-ue-nr", "doesn't match the intended RAPID"),
         ("RAR own RAPID", "xr-ue-nr", "Found RAR with the intended"), ("RRC IDLE", "xr-ue-nr", "RRC moved into IDLE"),
         ("RRC CONNECTED", "xr-ue-nr", "State = NR_RRC_CONNECTED"), ("sync failed", "xr-ue-nr", "synch Failed")),
  "lte": (("sync", "xr-ue-lte", "Got synch"), ("resync after sleep(1)", "xr-ue-lte", "Resynchronizing RX"),
          ("SIB1", "xr-ue-lte", "Dumping SIB 1"), ("SIB2", "xr-ue-lte", "Found SIB2"),
          ("RAR", "xr-ue-lte", "Received RAR"), ("RRC CONNECTED", "xr-ue-lte", "State = RRC_CONNECTED")),
}
ACCEPT = r"PDU session establishment accept|Activate default EPS bearer"
# lines of a kind listed one by one at most
MAX_EACH = 4


def tshark(pcap, flt, *fields):
  """Rows of the fields of the packets of pcap matching the display filter flt."""
  cmd = ["tshark", "-r", str(pcap), *PREFS, "-Y", flt, "-T", "fields", "-E", "separator=\t"]
  for f in fields:
    cmd += ["-e", f]
  res = subprocess.run(cmd, capture_output=True, text=True)
  return [line.split("\t") for line in res.stdout.splitlines() if line]


def cut(run, name, t0):
  """The PRE/POST window of run/name around t0 (editcap), cached under build/ladder/cut."""
  CUT.mkdir(parents=True, exist_ok=True)
  dst = CUT / f"{run.name}-{name}"
  if not dst.exists():
    subprocess.run(["editcap", "-A", f"{t0 - PRE:.6f}", "-B", f"{t0 + POST:.6f}", str(run / name), str(dst)],
                   check=True, capture_output=True)
  return dst


def clean(info):
  """Message name of a mac.pcap Info column: RAR with its RAPIDs, else the last RLC part without the MAC details."""
  if "RAR" in info and "RAPID" in info:
    return "RAR RAPID=" + ",".join(re.findall(r"RAPID=(\d+)", info))
  s = info.split("||")[-1]
  s = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", s)
  return re.sub(r"\s+", " ", s).strip(" ,")


def air(run, t0):
  """(t, layer, message) of mac.pcap: broadcast once, each other message once per 100 ms (gNB and UE both log it)
  and MAX_EACH times at most, then a count. LTE frames only as the UE logged them: the eNB also logs its answers to
  false PRACH detections (RARs to no UE, then garbage Msg3), none of them in its log."""
  rows, last, n, rar = [], {}, {}, -1
  for t, src, proto, info in tshark(cut(run, "mac.pcap", t0), "lte_rrc || nr-rrc || mac-nr.rar || mac-lte.rar",
                                    "frame.time_epoch", "ip.src", "frame.protocols", "_ws.col.Info"):
    t, m = float(t), clean(info)
    if not m or m.startswith(NOISE):
      continue
    if "mac-lte" in proto and not src.endswith(UE_HOST):
      continue
    if m.startswith("RAR"):
      rar = t
    if m == MSG3 and t - rar > 0.05:
      continue
    if m.startswith(BROADCAST):
      if m in n:
        continue
    elif t - last.get(m, -1) < 0.1:
      continue
    last[m] = t
    n[m] = n.get(m, 0) + 1
    if n[m] <= MAX_EACH:
      rows.append((t, "air", m))
    elif n[m] == MAX_EACH + 1:
      rows.append((t, "air", f"{m}: more from here"))
  return rows


def core(run, t0):
  """(t, protocol, message) of core.pcap signalling."""
  rows = []
  for t, proto, info in tshark(cut(run, "core.pcap", t0), "s1ap || ngap || pfcp || gtpv2",
                               "frame.time_epoch", "_ws.col.Protocol", "_ws.col.Info"):
    m = re.sub(r"^(SACK \([^)]*\)\s*,\s*)+", "", info).strip(" ,")
    if m and not m.startswith("SACK") and not any(k in m for k in CORE_SKIP):
      rows.append((float(t), proto, m))
  return rows


def log_offset(run, src):
  """Epoch minus CLOCK_MONOTONIC (s), from the source release step of summary.txt; None without it."""
  rel = steps(run).get("release")
  t0 = event(run, "change: source fade-out")
  logs, pat = RELEASE[src]
  if rel is None or t0 is None:
    return None
  for name in logs:
    for line in (run / "logs" / f"{name}.log").read_text(errors="replace").splitlines():
      m = re.match(r"([0-9]+\.[0-9]+) ", line)
      if m and pat in line:
        return t0 + rel - float(m[1])
  return None


def logs(run, tgt, off):
  """(t, log, line) of the target log lines of TARGET_LOG, MAX_EACH of each kind, plus the count of the rest."""
  rows = []
  for name, log, pat in TARGET_LOG[tgt]:
    hits = []
    for line in (run / "logs" / f"{log}.log").read_text(errors="replace").splitlines():
      m = re.match(r"([0-9]+\.[0-9]+) ", line)
      if m and pat in line:
        hits.append((float(m[1]) + off, line[m.end():].strip()))
    for t, text in hits[:MAX_EACH]:
      rows.append((t, f"log {log}", f"{name}: {short(text)}"))
    if len(hits) > MAX_EACH:
      rows.append((hits[MAX_EACH][0], f"log {log}", f"{name}: {len(hits) - MAX_EACH} more, last at {{{hits[-1][0]}}}"))
  return rows


def short(text):
  """The log text without the level prefix, its numbers of interest kept."""
  text = re.sub(r"^\[[A-Z_ ]+\]\s+[A-Z]\s+", "", text)
  return text[:110]


def data(run, t0, ip0, ip1, t_kill, t_ip):
  """Data plane facts: the UPF still sending to the source IP, its TCP retransmissions, GTP-U and UE arrival (from
  t_ip, the target session accept: before it the UE gets the last packets of the source RAT)."""
  sgi, gtp, ue = cut(run, "sgi.pcap", t0), cut(run, "core.pcap", t0), cut(run, "ue.pcap", t0)
  after = f"frame.time_epoch > {t_kill:.6f}"
  ue_after = f"frame.time_epoch > {t_ip:.6f}"
  rows = []
  down = tshark(sgi, f"ip.dst == {ip0} && {after}", "frame.time_epoch")
  if down:
    rows.append((float(down[0][0]), "sgi", f"UPF to the source IP {ip0}: {len(down)} packets in the window, "
                 f"last at {{{float(down[-1][0])}}}"))
  rtx = [float(r[0]) for r in tshark(sgi, f"tcp.analysis.retransmission && ip.dst == {ip0} && {after}",
                                     "frame.time_epoch")]
  if rtx:
    rows.append((rtx[0], "sgi", f"TCP retransmissions to {ip0}: {len(rtx)}, at "
                 + " ".join(f"{{{t}}}" for t in rtx[:8])))
  ips = [ip0]
  if ip1 and ip1 != ip0:
    ips.append(ip1)
  for ip in ips:
    # the outer destination a base station (not the SGW-U of S5-U)
    g = [r for r in tshark(gtp, f"gtp && ip.dst == {ip} && {after}", "frame.time_epoch", "ip.dst")
         if r[1].split(",")[0].endswith(RAN_HOSTS)]
    if g:
      rows.append((float(g[0][0]), "gtp-u", f"first GTP-U to the RAN for {ip} ({len(g)} in the window)"))
    else:
      rows.append((t_kill, "gtp-u", f"no GTP-U to the RAN for {ip} in the window"))
    u = tshark(ue, f"ip.dst == {ip} && (tcp.len > 0 || icmp) && {ue_after}", "frame.time_epoch")
    if u:
      rows.append((float(u[0][0]), "ue", f"first data at the UE to {ip} ({len(u)} in the window)"))
    else:
      rows.append((t_kill, "ue", f"no data at the UE to {ip} in the window"))
  return rows


def first_fail(run):
  """F0..F6 of the first step that never happened, OK if the source IP traffic came back (summary.txt)."""
  s = steps(run)
  text = (run / "summary.txt").read_text()
  tgt = "nr"
  if "-ntn2lte-" in run.name:
    tgt = "lte"
  if "release" not in s:
    return "F0"
  for f, name in (("F1", "sync"), ("F2", "sib19"), ("F3", "rar"), ("F4", "rrc"), ("F5", "ip")):
    if name == "sib19" and tgt == "lte":
      continue
    if name not in s:
      return f
  if "registered" not in s and tgt == "nr":
    return "F4"
  if re.search(r"target IP \S+, different", text) or "source IP traffic after the change: none" in text:
    return "F6"
  return "OK"


def ladder(run):
  """Write build/ladder/<run>.txt and return its text."""
  t0 = event(run, "change: source fade-out")
  t_kill = event(run, "redirection: kill")
  summary = (run / "summary.txt").read_text()
  src = "lte"
  if "-ntn2lte-" in run.name:
    src = "nr"
  tgt = {"lte": "nr", "nr": "lte"}[src]
  ip0 = re.search(r"source \w+ IP (\S+)", summary)[1]
  m = re.search(r"target IP (\d+\.\d+\.\d+\.\d+)", summary)
  ip1 = None
  if m:
    ip1 = m[1]
  rows = air(run, t0) + core(run, t0)
  # the target session accept, else the 2 s grace of xrat.sh after_change
  t_ip = None
  for t, where, msg in rows:
    if where.startswith(("NGAP", "S1AP")) and re.search(ACCEPT, msg):
      t_ip = t
      break
  off = log_offset(run, src)
  if off is not None:
    rows += logs(run, tgt, off)
  if t_kill is not None:
    rows.append((t_kill, "script", f"kill the {src} UE, start the {tgt} UE"))
    if t_ip is None:
      t_ip = t_kill + 2
    rows += data(run, t0, ip0, ip1, t_kill, t_ip)
  rows = [r for r in rows if t0 - PRE <= r[0] <= t0 + POST]
  rows.sort(key=lambda r: r[0])
  lines = [f"# {run.name}", f"# first failing step: {first_fail(run)}", *("# " + x for x in summary.splitlines()[:8]),
           "# t (s from the fade-out)  source  message"]
  for t, where, msg in rows:
    msg = re.sub(r"\{([0-9.]+)\}", lambda mm: f"{float(mm[1]) - t0:+.3f}", msg)
    lines.append(f"{t - t0:+8.3f}  {where:<14} {msg}")
  text = "\n".join(lines) + "\n"
  OUT.mkdir(parents=True, exist_ok=True)
  (OUT / f"{run.name}.txt").write_text(text)
  return text


# marks of a ladder (first matching line, s from the fade-out): name, pattern on "<source> <message>"; {ip}: target IP
MARKS = (("release", r"^\S+ +UEContextReleaseCommand"), ("kill", r"^script "), ("sync", r"^log \S+ +sync: "),
         ("sib19", r"SIB19: Found"), ("msg1", r"Msg1: PRACH"), ("rar", r"RAR own RAPID|^log xr-ue-lte +RAR: "),
         ("rrc", r"RRC CONNECTED: "), ("nas_accept", r"^(NGAP|S1AP)\S* +.*(Registration accept|Attach accept)"),
         ("ip_accept", rf"^(NGAP|S1AP)\S* +.*({ACCEPT})"),
         ("gtp_ran", r"^gtp-u +first GTP-U to the RAN for {ip} "), ("ue_data", r"^ue +first data at the UE to {ip} "))
# counts: name, pattern with the count of the "N more" line
COUNTS = (("msg1_n", r"Msg1: "), ("rar_other_n", r"RAR other RAPID: "))


def marks(text):
  """The MARKS and COUNTS of a ladder text, a mark missing from it is absent."""
  ip = re.search(r"target IP (\d+\.\d+\.\d+\.\d+)", text)
  out, rows = {}, []
  for line in text.splitlines():
    m = re.match(r"^\s*([+-][0-9.]+)  (.*)$", line)
    if m:
      rows.append((float(m[1]), m[2]))
  for name, pat in MARKS:
    if "{ip}" in pat:
      if ip is None:
        continue
      pat = pat.replace("{ip}", re.escape(ip[1]))
    for t, rest in rows:
      if re.search(pat, rest):
        out[name] = t
        break
  for name, pat in COUNTS:
    n = 0
    for _, rest in rows:
      if re.search(pat, rest):
        more = re.search(pat + r"(\d+) more", rest)
        if more:
          n += int(more[1])
        else:
          n += 1
    out[name] = n
  rtx = re.search(r"TCP retransmissions to \S+: (\d+)", text)
  if rtx:
    out["rtx_n"] = int(rtx[1])
  return out


def all_xrat():
  """Every redirect run of the paper xrat directory."""
  return sorted(p for p in (BUILD / XRAT).iterdir() if p.is_dir() and "-redirect-" in p.name)


if __name__ == "__main__":
  args = sys.argv[1:]
  if args == ["--all"]:
    cols = ["run", "first_fail"] + [m[0] for m in MARKS] + [c[0] for c in COUNTS] + ["rtx_n"]
    with open(OUT / "first_fail.csv", "w", newline="") as f:
      w = csv.DictWriter(f, cols)
      w.writeheader()
      for run in all_xrat():
        txt = OUT / f"{run.name}.txt"
        if txt.exists():
          text = txt.read_text()
        else:
          text = ladder(run)
        w.writerow({"run": run.name, "first_fail": first_fail(run), **marks(text)})
    print(f"wrote {OUT / 'first_fail.csv'}")
  else:
    for a in args:
      print(ladder(Path(a).resolve()))
