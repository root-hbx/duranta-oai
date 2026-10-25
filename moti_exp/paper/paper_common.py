"""The paper figures from the paper_batch.sh runs (ci-scripts/yaml_files/ntn_lte_sw), all on the private core, 180 s
from the traffic start: LTE/LTE X2 (build-lte/pcap-paper-lte), NTN/NTN CHO (build/pcap-paper-cho), LTE/NTN and NTN/LTE
RAT changes by A2 release with redirection (build/pcap-paper-xrat) with a new IP (TAG noiwk) or the same IP
(interworking without N26, TAG iwk), and the lte2ntn same-IP runs with a GNSS position error (TAG iwk-g<km>km).
perf/ shows the phenomena, analysis/ the causes. The packet helpers are those of legacy/cmp/cmp_common.py.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "legacy" / "cmp"))
from cmp_common import MODES, T_RUN, fig, ping_dl, plt, runs, save, t_start, tcp_fields, tcp_rtt, thpt_1s  # noqa: E402,F401

import numpy as np  # noqa: E402

XRAT = "pcap-paper-xrat"
GNSS_KM = (0, 2, 5, 15, 50, 500)
# label, color, line style, result dir (under build/), run name with {kind} and {mode}
LTE_LTE = ("LTE/LTE", "black", "--", "../build-lte/pcap-paper-lte", "{kind}-connected")
NTN_NTN = ("NTN/NTN", "gray", "--", "pcap-paper-cho", "{kind}-{mode}-cho")
NEW_IP = (
  ("LTE/NTN, new IP", "#000080", ":", XRAT, "{kind}-lte2ntn-{mode}-redirect-noiwk"),
  ("NTN/LTE, new IP", "#B22222", ":", XRAT, "{kind}-ntn2lte-{mode}-redirect-noiwk"),
)
SAME_IP = (
  ("LTE/NTN, same IP", "#000080", "-", XRAT, "{kind}-lte2ntn-{mode}-redirect-iwk"),
  ("NTN/LTE, same IP", "#B22222", "-", XRAT, "{kind}-ntn2lte-{mode}-redirect-iwk"),
)
CURVES = (LTE_LTE, NTN_NTN) + NEW_IP + SAME_IP


def gnss_curve(km):
  """The lte2ntn same-IP runs with the NR UE GNSS position km off (0: the A group runs)."""
  if km == 0:
    return SAME_IP[0]
  return (f"LTE/NTN, GNSS {km} km off", "#000080", "-", XRAT, f"{{kind}}-lte2ntn-{{mode}}-redirect-iwk-g{km}km")


def all_runs(kind, mode, c):
  """The runs of curve c for kind tcp-dl or ping."""
  return runs(c[3], c[4].format(kind=kind, mode=mode))


def run1(kind, mode, c):
  """Run 1 of curve c, None if there is none."""
  r = all_runs(kind, mode, c)
  if r:
    return r[0]
  return None


def out(f, sub, name):
  """figs/<sub>/<name>.pdf."""
  (HERE / "figs").mkdir(exist_ok=True)
  save(f, HERE / "figs" / sub, name)


def finish(f, ax, sub, name, **legend):
  """Legend as in legacy/cmp, then out."""
  ax.legend(**legend, ncol=1, framealpha=0.6, fontsize=6, handlelength=2.2)
  out(f, sub, name)


def event(run, text):
  """Epoch s of the first events.txt line containing text, None if there is none."""
  for line in (run / "events.txt").read_text().splitlines():
    t, e = line.split(" ", 1)
    if text in e:
      return float(t)
  return None


def steps(run):
  """The RAT change steps of summary.txt in s after the change (source fade-out): a2, release (source), then the
  target ones (sync, sib19, rar, rrc, registered, ip); a step missing from the logs is absent."""
  out = {}
  for line in (run / "summary.txt").read_text().splitlines():
    if line.startswith(("source redirection:", "target nr steps:", "target lte steps:")):
      for name, t in re.findall(r"(\w+) \+([0-9.]+) s", line.split(":", 1)[1]):
        out[name] = float(t)
  return out


def first_data(run, after):
  """Epoch s of the first TCP dl payload at the UE (ue.pcap) after the epoch s after, None if none came."""
  for t, dport, n, _, _ in tcp_fields(run, "ue.pcap"):
    if t > after and n > 0 and dport == 5201:
      return t
  return None

