#!/bin/bash
# TCP during LTE mobility, with packet captures (lte_cap.sh), under build-lte/pcap/tcp-<dl|ul>-*
#   [DIR=dl|ul] lte_tcp.sh base [runs]        RRC_CONNECTED on eNB1, no handover (40 s)
#   [DIR=dl|ul] lte_tcp.sh connected [runs]   X2 handover to PCI 11 and back to PCI 10
#   [DIR=dl|ul] lte_tcp.sh idle [runs]        attach via eNB1, UE restart, attach via eNB2 (break-before-make)
# Traffic: iperf3 from the PGW-U (client: iperf_core) to the UE (server: iperf_ue), 100 Mbit/s offered, 100 ms reports.
# DIR=dl (default) the PGW-U sends; DIR=ul reverse mode (-R), the UE sends. Captures keep full packets (MAC: 300 B);
# thr_rx.csv is the receiver goodput per 100 ms (dl: ue*.pcap, ul: sgi.pcap).

DIR=${DIR:-dl}
KIND=tcp-$DIR SGI_FILTER='tcp port 5201' SNAP=0 MAC_SNAP=300 T_BASE=40 T_PRE=10 T_POST=15
# rx: receiver captures and their data filter; tx: sender captures and the pattern of their data packets
if [ $DIR = ul ]; then rev=-R rx='sgi.pcap' data='tcp src port 5201' tx='ue*.pcap' txdata='IP [0-9.]+[.]5201 >'
else rev= rx='ue*.pcap' data='tcp dst port 5201' tx='sgi.pcap' txdata='> [0-9.]+[.]5201:'; fi
traffic() { ue_ip
  docker exec lte-ue iperf3 -s -1 -i 0.1 --forceflush > $out/iperf_ue$1.txt 2>&1 & sleep 1
  ev "iperf3 $DIR with $ip"
  docker exec upf iperf3 -c $ip $rev -t 600 -b 100M -i 0.1 --forceflush > $out/iperf_core$1.txt 2>&1 & }
traffic_stop() { docker exec upf pkill -INT -f 'iperf3 -c' || true; docker exec lte-ue pkill -INT iperf3 2>/dev/null || true; }
# tcpdump -r <files> <args...>: the packets of several captures (idle has ue1/ue2)
dump() { local f=$1; shift; docker run --rm -v $out:/cap --entrypoint sh docker_open5gs -c \
  'f=$1; shift; for p in /cap/$f; do tcpdump -r $p "$@" 2>/dev/null; done' sh "$f" "$@"; }
# data gaps over 100 ms at the receiver (gaps less than 20 ms apart merged), goodput per second, iperf3 totals,
# RTT at the sender from the TCP timestamps (first data packet with a TSval to the first ACK echoing it)
report() { dump "$rx" -tt -nq "$data" |
  awk -v csv=$out/thr_rx.csv 'function flush() { if (gn) printf "gap %5.0f ms  %.3f -> %.3f  (%d)\n", (ge - gs) * 1000, gs, ge, gn; gn = 0 }
    $NF > 0 { t = $1; b = int(t * 10); B[b] += $NF; if (!b0) b0 = b; b1 = b
      if (p && t - p > 0.1) { if (gn && p - ge < 0.02) ge = t; else { flush(); gs = p; ge = t }; gn++ }
      p = t }
    END { flush(); print "time,mbps" > csv
      for (i = b0; i <= b1; i++) { printf "%.1f,%.2f\n", i / 10, B[i] * 8 / 1e5 > csv; s += B[i]
        if ((i - b0) % 10 == 9) { l = l sprintf(" %.1f", s * 8 / 1e6); s = 0 } }
      print "Mbps per s:" l }'
  dump "$tx" -tt -n 'tcp port 5201' |
  awk -v txdata="$txdata" '/ val / { t = $1; match($0, /val [0-9]+ ecr [0-9]+/); split(substr($0, RSTART, RLENGTH), v, " ")
      if ($0 ~ txdata) { if (!(v[2] in s)) s[v[2]] = t } else if (v[4] in s && t - s[v[4]] < 10) {
        b = int(t / 5); r = (t - s[v[4]]) * 1000; if (!b0) b0 = b; if (r > m[b]) m[b] = r; n[b]++; a[b] += r } }
    END { for (i = b0; i in n; i++) l = l sprintf(" %.0f/%.0f", a[i] / n[i], m[i]); print "RTT ms per 5 s (mean/max):" l }'
  (cd $out && grep -H 'sender\|receiver\|error' iperf_core*.txt iperf_ue*.txt) || true; }
source "$(dirname "$0")/lte_cap.sh"
