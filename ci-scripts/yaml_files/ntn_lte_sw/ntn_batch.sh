#!/bin/bash
# NR NTN handover batch: TRANS and REGEN, each TCP dl, TCP ul and ping (ntn_tcp.sh, ntn_ping.sh), each base 1 + exp
# RUNS, under PCAP_DIR, then PCAP_DIR/report.txt with one line per run (handovers, CHO trigger, interruption in
# mac.pcap, sim/wall, receiver goodput, gaps over 100 ms, ping loss and RTT).
#   [RUNS=3] [PCAP_DIR=build/pcap-ntn-cho] ntn_batch.sh cho          TWO_SAT=1, time-based CHO to PCI 1
#   [RUNS=3] [PCAP_DIR=build/pcap-ntn-n2ho] ntn_batch.sh connected   N2 handover to PCI 1 and back to PCI 0
# Interruption: per change of gNB, the last uplink C-RNTI MAC PDU at the source to the first at the target (Msg3
# on CCCH, LCID 0 / 52, overheard by the source excluded). Exit status: the number of failed runs.

cd "$(dirname "$0")"
source ./core.sh
repo=$(cd ../../.. && pwd)
exp=$1 RUNS=${RUNS:-3} fail=0
case "$exp" in
  cho) export TWO_SAT=1 PCAP_DIR=${PCAP_DIR:-$repo/build/pcap-ntn-cho} ;;
  connected) unset TWO_SAT; export PCAP_DIR=${PCAP_DIR:-$repo/build/pcap-ntn-n2ho} ;;
  *) sed -n '5,6p' $0; exit 1 ;;
esac
step() { echo "### $(date +%T) $*"; env "$@" || fail=$((fail + 1)); }

# one line per run from its summary.txt and mac.pcap
mac_ul="(ip.src == $CORE_NET.0.70 || ip.src == $CORE_NET.0.72) && mac-nr.direction == 0 && mac-nr.rnti-type == 3
  && !(mac-nr.ulsch.lcid == 0 || mac-nr.ulsch.lcid == 52)"
report() { for d in $(ls -d $PCAP_DIR/*-[0-9]-*/ | sort); do
  mac=$(tshark -r $d/mac.pcap --enable-heuristic mac_nr_udp -Y "$mac_ul" -T fields -e frame.time_epoch -e ip.src \
    2>/dev/null | awk '$2 != p && p { l = l sprintf("%s%.0f", l ? "/" : "", ($1 - t) * 1000) } { p = $2; t = $1 }
      END { print l ? l : "-" }')
  awk -v run=$(basename $d) -v mac="$mac" '/^== ping_/ { s = substr($2, 6, 2) }
    /^UE reconfigurationWithSync/ { sub(/,/, "", $3); ho = $3 "/" $NF }
    /^CHO executed/ { cho = " at trace " $5 " s" }
    /^sim\/wall/ { sw = $2 }
    /^gap/ { n[s]++; if ($2 > m[s]) m[s] = $2 }
    /packet loss/ { for (i = 1; i <= NF; i++) if ($i ~ /%$/) loss[s] = $i }
    /^rtt/ { split($4, r, "/"); avg[s] = r[2]; max[s] = r[3] }
    /receiver/ && !/ 0.00 bits/ { mbps = $7 }
    END { printf "%-38s ho %s%s, mac %s ms, sim/wall %s", run, ho, cho, mac, sw
      if (mbps != "") printf "  %s Mbps, gaps %d (max %d ms)", mbps, n[""], m[""]
      split("dl ul", S, " ")
      for (i = 1; i <= 2; i++) if ((s = S[i]) in loss)
        printf "  %s: loss %.2f%%, avg/max %.0f/%.0f ms, gaps %d (max %d ms)", s, loss[s], avg[s], max[s], n[s], m[s]
      print "" }' $d/summary.txt
done; }

for regen in "" 1; do
  for t in "DIR=dl ./ntn_tcp.sh" "DIR=ul ./ntn_tcp.sh" "./ntn_ping.sh"; do
    step REGEN=$regen $t base 1
    step REGEN=$regen $t $exp $RUNS
    docker rm -f cap-ue cap-sgi cap-mac cap-ngap cap-core >/dev/null 2>&1; ./ntn.sh down >/dev/null 2>&1
  done
done
report | tee $PCAP_DIR/report.txt
exit $fail
