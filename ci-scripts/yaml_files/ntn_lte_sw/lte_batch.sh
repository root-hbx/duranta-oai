#!/bin/bash
# LTE X2 handover batch: TCP dl, TCP ul and ping (lte_tcp.sh, lte_ping.sh), each base 1 + connected RUNS, under
# PCAP_DIR, then PCAP_DIR/report.txt with one line per run (X2 handovers and path switches in core.pcap, receiver
# goodput, gaps over 100 ms, ping loss and RTT).
#   [RUNS=3] [PCAP_DIR=build-lte/pcap-lte-ho] lte_batch.sh
# Exit status: the number of failed runs.

cd "$(dirname "$0")"
repo=$(cd ../../.. && pwd)
export PCAP_DIR=${PCAP_DIR:-$repo/build-lte/pcap-lte-ho}
RUNS=${RUNS:-3} fail=0
step() { echo "### $(date +%T) $*"; env "$@" || fail=$((fail + 1)); }

# one line per run from its summary.txt and core.pcap
report() { for d in $(ls -d $PCAP_DIR/*-[0-9]-*/ | sort); do
  ho=$(tshark -r $d/core.pcap -Y 'x2ap || s1ap' -T fields -e _ws.col.Info 2>/dev/null |
    awk '/HandoverRequestAcknowledge/ { a++ } /PathSwitchRequestAcknowledge/ { p++ } END { printf "x2 ack %d, path switch %d", a, p }')
  awk -v run=$(basename $d) -v ho="$ho" '/^== ping_/ { s = substr($2, 6, 2) }
    /^gap/ { n[s]++; if ($2 > m[s]) m[s] = $2 }
    /packet loss/ { for (i = 1; i <= NF; i++) if ($i ~ /%$/) loss[s] = $i }
    /^rtt/ { split($4, r, "/"); avg[s] = r[2]; max[s] = r[3] }
    /receiver/ && !/ 0.00 bits/ { mbps = $7 }
    END { printf "%-36s %s", run, ho
      if (mbps != "") printf "  %s Mbps, gaps %d (max %d ms)", mbps, n[""], m[""]
      split("dl ul", S, " ")
      for (i = 1; i <= 2; i++) if ((s = S[i]) in loss)
        printf "  %s: loss %.2f%%, avg/max %.0f/%.0f ms, gaps %d (max %d ms)", s, loss[s], avg[s], max[s], n[s], m[s]
      print "" }' $d/summary.txt
done; }

for t in "DIR=dl ./lte_tcp.sh" "DIR=ul ./lte_tcp.sh" "./lte_ping.sh"; do
  step $t base 1
  step $t connected $RUNS
done
./lte.sh down >/dev/null 2>&1
report | tee $PCAP_DIR/report.txt
exit $fail
