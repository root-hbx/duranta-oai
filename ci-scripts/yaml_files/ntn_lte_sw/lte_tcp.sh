#!/bin/bash
# Downlink TCP during LTE mobility, with packet captures (lte_cap.sh), under build-lte/pcap/tcp-*
#   lte_tcp.sh base [runs]        RRC_CONNECTED on eNB1, no handover (40 s)
#   lte_tcp.sh connected [runs]   X2 handover to PCI 11 and back to PCI 10
#   lte_tcp.sh idle [runs]        attach via eNB1, UE restart, attach via eNB2 (break-before-make)
# Traffic: iperf3 from the PGW-U (client, sender: iperf_core) to the UE (server, receiver: iperf_ue), 100 Mbit/s offered,
# 100 ms reports. Captures keep the headers only; thr_ue.csv is the UE goodput per 100 ms from ue*.pcap.

KIND=tcp SGI_FILTER='tcp port 5201' SNAP=128 MAC_SNAP=300 T_BASE=40 T_PRE=10 T_POST=15
traffic() { ue_ip
  docker exec lte-ue iperf3 -s -1 -i 0.1 --forceflush > $out/iperf_ue$1.txt 2>&1 & sleep 1
  ev "iperf3 to $ip"
  docker exec upf iperf3 -c $ip -t 600 -b 100M -i 0.1 --forceflush > $out/iperf_core$1.txt 2>&1 & }
traffic_stop() { docker exec upf pkill -INT -f 'iperf3 -c' || true; docker exec lte-ue pkill -INT iperf3 2>/dev/null || true; }
# data gaps over 100 ms at the UE, goodput per second, and the iperf3 totals
report() { docker run --rm -v $out:/cap --entrypoint sh docker_open5gs -c \
    'for f in /cap/ue*.pcap; do tcpdump -r $f -tt -nq "tcp dst port 5201" 2>/dev/null; done' |
  awk -v csv=$out/thr_ue.csv '$NF > 0 { t = $1; b = int(t * 10); B[b] += $NF; if (!b0) b0 = b; b1 = b
      if (p && t - p > 0.1) printf "gap %5.0f ms  %.3f -> %.3f\n", (t - p) * 1000, p, t; p = t }
    END { print "time,mbps" > csv
      for (i = b0; i <= b1; i++) { printf "%.1f,%.2f\n", i / 10, B[i] * 8 / 1e5 > csv; s += B[i]
        if ((i - b0) % 10 == 9) { l = l sprintf(" %.1f", s * 8 / 1e6); s = 0 } }
      print "Mbps per s:" l }'
  (cd $out && grep -H 'sender\|error' iperf_core*.txt; grep -H 'receiver\|error' iperf_ue*.txt) || true; }
source "$(dirname "$0")/lte_cap.sh"
