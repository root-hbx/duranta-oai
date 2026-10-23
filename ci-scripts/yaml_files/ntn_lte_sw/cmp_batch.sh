#!/bin/bash
# Handover comparison batch (plot/cmp): the four handover kinds in 180 s runs with the same traffic, each TCP dl and
# ping, RUNS runs, then report.txt in each result directory with one line per run.
#   LTE/LTE   X2 handover to PCI 11, eNB2 gain ramp from 40 s (lte_tcp.sh, lte_ping.sh), build-lte/pcap-cmp-lte
#   NTN/NTN   TWO_SAT CHO, trace time 90-271 s (ntn_tcp.sh, ntn_ping.sh), TRANS and REGEN, build/pcap-cmp-cho
#   LTE/NTN   TWO_SAT RAT change at the CHO time, session stage (xrat_tcp.sh, xrat_ping.sh lte2ntn and ntn2lte),
#   NTN/LTE   TRANS and REGEN, build/pcap-cmp-xrat
#   [RUNS=3] cmp_batch.sh
# Exit status: the number of failed invocations.

cd "$(dirname "$0")"
repo=$(cd ../../.. && pwd)
RUNS=${RUNS:-3} fail=0
lte=$repo/build-lte/pcap-cmp-lte cho=$repo/build/pcap-cmp-cho xrat=$repo/build/pcap-cmp-xrat
step() { echo "### $(date +%T) $*"; env "$@" || fail=$((fail + 1)); }

# one line per run: the longest silence of the UPF ping to the UE (to the end of the run if no reply came back), or
# the TCP goodput and receiver gaps, from the traffic start (events.txt)
report() { for d in $(ls -d $1/*-[0-9]-*/ | sort); do
  t0=$(awk '/ (iperf3|ping) / { print $1; exit }' $d/events.txt); [ -n "$t0" ] || t0=$(awk 'NR == 1 { print $1 }' $d/events.txt)
  if [ -f $d/ping_dl.txt ]; then
    awk -F'[][]' -v t0=$t0 -v run=$(basename $d) '/bytes from/ { t = $2; if (p && t - p > m) { m = t - p; s = p; e = "" }; p = t }
      $2 ~ /^[0-9.]+$/ { last = $2 }
      END { if (last - p > m) { m = last - p; s = p; e = " (no reply to the end)" }
        printf "%-44s ping: longest silence %.0f ms from +%.1f s%s\n", run, m * 1000, s - t0, e }' $d/ping_dl.txt
  else
    awk -v run=$(basename $d) -v t0=$t0 '/^gap/ && $4 - t0 > 2 { if ($2 > m) { m = $2; s = $4 } } /^iperf_ue.*receiver/ { r = $7 " " $8 }
      /^Mbps per s:/ { n = NF - 3 } /^source IP traffic after/ { sub(/.*change: /, ""); a = ", source IP traffic after the change: " $0 }
      END { g = "no gap"; if (m) g = sprintf("longest gap %d ms from +%.1f s", m, s - t0)
        printf "%-44s tcp: receiver %s, data in %d s, %s%s\n", run, r, n, g, a }' $d/summary.txt
  fi
done; }

for t in ./lte_tcp.sh ./lte_ping.sh; do step PCAP_DIR=$lte T_PRE=40 T_END=180 $t connected $RUNS; done
for regen in "" 1; do
  for t in ./ntn_tcp.sh ./ntn_ping.sh; do step PCAP_DIR=$cho TWO_SAT=1 REGEN=$regen END=271 $t cho $RUNS; done
  docker rm -f cap-ue cap-sgi cap-mac cap-ngap cap-core >/dev/null 2>&1; ./ntn.sh down >/dev/null 2>&1
  for d in lte2ntn ntn2lte; do
    for t in ./xrat_tcp.sh ./xrat_ping.sh; do step PCAP_DIR=$xrat TWO_SAT=1 REGEN=$regen STAGE=session $t $d $RUNS; done
  done
done
docker rm -f cap-ue cap-sgi cap-mac cap-core >/dev/null 2>&1
for d in $lte $cho $xrat; do report $d | tee $d/report.txt; done
exit $fail
