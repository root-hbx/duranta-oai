#!/bin/bash
# Paper experiments (moti_exp/paper) on the private core, 180 s runs, each TCP dl and ping, RUNS runs, TRANS and REGEN
# for the NTN parts, then report.txt in each result directory with one line per run.
#   S  same RAT: lte, LTE/LTE X2 (lte_tcp.sh, lte_ping.sh), build-lte/pcap-paper-lte; cho, NTN/NTN TWO_SAT CHO
#      (ntn_tcp.sh, ntn_ping.sh), build/pcap-paper-cho
#   A  anchor: TWO_SAT RAT change by A2 release with redirection (xrat.sh redirect), lte2ntn and ntn2lte, without and
#      with interworking without N26 (TAG noiwk, iwk), build/pcap-paper-xrat
#   G  access: as A lte2ntn iwk, the NR UE GNSS position GNSS_KM km off (TAG iwk-g<km>km), build/pcap-paper-xrat
#   [RUNS=3] [PARTS="lte cho a g"] [MODES="trans regen"] [GNSS_KM="2 5 15 50 500"] paper_batch.sh
# Exit status: the number of failed invocations.

cd "$(dirname "$0")"
repo=$(cd ../../.. && pwd)
export CORE=private RUNS=${RUNS:-3}
lte=$repo/build-lte/pcap-paper-lte cho=$repo/build/pcap-paper-cho xrat=$repo/build/pcap-paper-xrat fail=0
parts=${PARTS:-lte cho a g}
step() { echo "### $(date +%T) $*"; env "$@" || fail=$((fail + 1)); }
clean() { docker rm -f cap-ue cap-sgi cap-mac cap-ngap cap-core >/dev/null 2>&1; }

# one line per run: the longest silence of the UPF ping to the UE (to the end of the run if no reply came back), or
# the TCP goodput and longest receiver gap, from the traffic start (events.txt); for a RAT change the target access
# steps, GNSS error and IP (summary.txt)
report() { for d in $(ls -d $1/*-[0-9]-*/ | sort); do
  t0=$(awk '/ (iperf3|ping) / { print $1; exit }' $d/events.txt); [ -n "$t0" ] || t0=$(awk 'NR == 1 { print $1 }' $d/events.txt)
  x=$(awk '/^target .* steps/ { sub(/^target /, ""); s = " | " $0 } /^target nr GNSS/ { g = " | gnss " $(NF - 1) " m" }
    /^target IP/ { sub(/^target IP /, ""); i = " | ip " $0 } END { printf "%s%s%s", s, g, i }' $d/summary.txt)
  if [ -f $d/ping_dl.txt ]; then
    awk -F'[][]' -v t0=$t0 -v run=$(basename $d) -v x="$x" '/bytes from/ { t = $2; if (p && t - p > m) { m = t - p; s = p; e = "" }; p = t }
      $2 ~ /^[0-9.]+$/ { last = $2 }
      END { if (last - p > m) { m = last - p; s = p; e = " (no reply to the end)" }
        printf "%-52s ping: longest silence %.0f ms from +%.1f s%s%s\n", run, m * 1000, s - t0, e, x }' $d/ping_dl.txt
  else
    awk -v run=$(basename $d) -v t0=$t0 -v x="$x" '/^gap/ && $4 - t0 > 2 { if ($2 > m) { m = $2; s = $4 } }
      /^iperf_ue.*receiver/ { r = $7 " " $8 } /^Mbps per s:/ { n = NF - 3 }
      END { g = "no gap"; if (m) g = sprintf("longest gap %d ms from +%.1f s", m, s - t0)
        printf "%-52s tcp: receiver %s, data in %d s, %s%s\n", run, r, n, g, x }' $d/summary.txt
  fi
done; }

[[ $parts == *lte* ]] && for t in ./lte_tcp.sh ./lte_ping.sh; do step PCAP_DIR=$lte T_PRE=36 T_END=180 $t connected $RUNS; done
if [[ $parts == *cho* ]]; then
  for mode in ${MODES:-trans regen}; do
    regen=; [ $mode = regen ] && regen=1
    for t in ./ntn_tcp.sh ./ntn_ping.sh; do step PCAP_DIR=$cho TWO_SAT=1 REGEN=$regen END=271 $t cho $RUNS; done
  done
  clean; ./ntn.sh down >/dev/null 2>&1
fi
for mode in ${MODES:-trans regen}; do
  regen=; [ $mode = regen ] && regen=1
  x="PCAP_DIR=$xrat TWO_SAT=1 REGEN=$regen STAGE=redirect"
  [[ $parts == *a* ]] && for d in lte2ntn ntn2lte; do
    for t in ./xrat_tcp.sh ./xrat_ping.sh; do
      step $x TAG=noiwk $t $d $RUNS
      step $x TAG=iwk IWK=1 $t $d $RUNS
    done
  done
  [[ $parts == *g* ]] && for km in ${GNSS_KM:-2 5 15 50 500}; do
    for t in ./xrat_tcp.sh ./xrat_ping.sh; do step $x TAG=iwk-g${km}km IWK=1 GNSS_ERR=$((km * 1000)) $t lte2ntn $RUNS; done
  done
done
clean
for d in $lte $cho $xrat; do [ -d $d ] && report $d | tee $d/report.txt; done
exit $fail
