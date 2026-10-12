#!/bin/bash
# TCP during NR NTN handovers, with packet captures (ntn_cap.sh), under build/pcap-ntn/tcp-<dl|ul>-<trans|regen>-*
#   [REGEN=1] [DIR=dl|ul] ntn_tcp.sh base [runs]        RRC_CONNECTED on gNB1, no handover (40 s)
#   [REGEN=1] [DIR=dl|ul] ntn_tcp.sh connected [runs]   N2 handover to PCI 1 and back to PCI 0
# Traffic: iperf3 from the UPF (client: iperf_core) to the UE (server: iperf_ue), 100 Mbit/s offered, 100 ms reports.
# DIR=dl (default) the UPF sends; DIR=ul reverse mode (-R), the UE sends. Captures keep the headers only;
# thr_rx.csv is the receiver goodput per 100 ms (dl: ue.pcap, ul: sgi.pcap).

DIR=${DIR:-dl}
KIND=tcp-$DIR SGI_FILTER='tcp port 5201' SNAP=128 MAC_SNAP=300 T_BASE=40 T_PRE=10 T_POST=15
if [ $DIR = ul ]; then rev=-R rx=sgi.pcap data='tcp src port 5201'; else rev= rx=ue.pcap data='tcp dst port 5201'; fi
traffic() { docker exec nr-ue iperf3 -s -1 -i 0.1 --forceflush > $out/iperf_ue.txt 2>&1 & sleep 1
  ev "iperf3 $DIR with $ip"
  docker exec upf iperf3 -c $ip $rev -t 600 -b 100M -i 0.1 --forceflush > $out/iperf_core.txt 2>&1 & }
traffic_stop() { docker exec upf pkill -INT -f 'iperf3 -c' || true; docker exec nr-ue pkill -INT iperf3 2>/dev/null || true; }
# data gaps over 100 ms at the receiver, goodput per second, and the iperf3 totals
report() { docker run --rm -v $out:/cap --entrypoint tcpdump docker_open5gs -r /cap/$rx -tt -nq "$data" 2>/dev/null |
  awk -v csv=$out/thr_rx.csv '$NF > 0 { t = $1; b = int(t * 10); B[b] += $NF; if (!b0) b0 = b; b1 = b
      if (p && t - p > 0.1) printf "gap %5.0f ms  %.3f -> %.3f\n", (t - p) * 1000, p, t; p = t }
    END { print "time,mbps" > csv
      for (i = b0; i <= b1; i++) { printf "%.1f,%.2f\n", i / 10, B[i] * 8 / 1e5 > csv; s += B[i]
        if ((i - b0) % 10 == 9) { l = l sprintf(" %.1f", s * 8 / 1e6); s = 0 } }
      print "Mbps per s:" l }'
  (cd $out && grep -H 'sender\|receiver\|error' iperf_core.txt iperf_ue.txt) || true; }
source "$(dirname "$0")/ntn_cap.sh"
