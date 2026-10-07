#!/bin/bash
# ICMP during LTE mobility, with packet captures (lte_cap.sh), under build-lte/pcap/ping-*
#   lte_ping.sh base [runs]        RRC_CONNECTED on eNB1, no handover (30 s)
#   lte_ping.sh connected [runs]   X2 handover to PCI 11 and back to PCI 10
#   lte_ping.sh idle [runs]        attach via eNB1, UE restart, attach via eNB2 (break-before-make)
# Traffic: ICMP every 10 ms from the UE (ping_ul) and from the PGW-U towards the UE (ping_dl).

KIND=ping SGI_FILTER=icmp SNAP=0 MAC_SNAP=0 T_BASE=30 T_PRE=5 T_POST=10
traffic() { ue_ip
  docker exec lte-ue ping -I oaitun_ue1 -i 0.01 -D -O 192.168.96.1 > $out/ping_ul$1.txt 2>&1 &
  docker exec upf ping -i 0.01 -D -O $ip > $out/ping_dl$1.txt 2>&1 & }
traffic_stop() { docker exec lte-ue pkill -INT ping 2>/dev/null; docker exec upf pkill -INT -f 'ping -i 0.01' || true; }
# reply gaps over 100 ms (the interval is 10 ms) and the ping statistics
report() { for f in $out/ping_*.txt; do echo "== ${f##*/}"; awk -F'[][]' '/bytes from/ { t = $2; if (p && t - p > 0.1)
  printf "gap %5.0f ms  %.3f -> %.3f\n", (t - p) * 1000, p, t; p = t } /transmitted|rtt/' $f; done; }
source "$(dirname "$0")/lte_cap.sh"
