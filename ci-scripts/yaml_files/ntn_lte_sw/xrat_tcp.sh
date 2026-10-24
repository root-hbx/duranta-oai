#!/bin/bash
# TCP across a RAT change of one dual-mode UE between LTE and NR NTN, with packet captures (xrat.sh), under
# build/pcap-xrat/tcp-dl-<lte2ntn|ntn2lte>-<trans|regen>-<stage>-*
#   [REGEN=1] STAGE=base|trigger|access|session xrat_tcp.sh lte2ntn|ntn2lte [runs]
# Traffic: iperf3 from the UPF (client: iperf_core) to the source UE IP (server: iperf_ue in xr-ue), 100 Mbit/s offered,
# 100 ms reports. thr_rx.csv is the goodput at the UE per 100 ms (ue.pcap).

KIND=tcp-dl SGI_FILTER='tcp port 5201' SNAP=0 rx=ue.pcap data='tcp dst port 5201'
traffic() { docker exec xr-ue iperf3 -s -1 -i 0.1 --forceflush > $out/iperf_ue.txt 2>&1 & sleep 1
  ev "iperf3 dl with $ip"
  docker exec ${CORE_CT}upf iperf3 -c $ip -t 600 -b 100M -i 0.1 --forceflush > $out/iperf_core.txt 2>&1 & }
traffic_stop() { docker exec ${CORE_CT}upf pkill -INT -f 'iperf3 -c' || true; docker exec xr-ue pkill -INT iperf3 2>/dev/null || true; }
# data gaps over 100 ms at the UE (gaps less than 20 ms apart merged), goodput per second, iperf3 totals
report() { docker run --rm -v $out:/cap --entrypoint tcpdump $CORE_IMAGE -r /cap/$rx -tt -nq "$data" 2>/dev/null |
  awk -v csv=$out/thr_rx.csv 'function flush() { if (gn) printf "gap %5.0f ms  %.3f -> %.3f  (%d)\n", (ge - gs) * 1000, gs, ge, gn; gn = 0 }
    $NF > 0 { t = $1; b = int(t * 10); B[b] += $NF; if (!b0) b0 = b; b1 = b
      if (p && t - p > 0.1) { if (gn && p - ge < 0.02) ge = t; else { flush(); gs = p; ge = t }; gn++ }
      p = t }
    END { flush(); print "time,mbps" > csv
      for (i = b0; i <= b1; i++) { printf "%.1f,%.2f\n", i / 10, B[i] * 8 / 1e5 > csv; s += B[i]
        if ((i - b0) % 10 == 9) { l = l sprintf(" %.1f", s * 8 / 1e6); s = 0 } }
      print "Mbps per s:" l }'
  (cd $out && grep -H 'sender\|receiver\|error' iperf_core.txt iperf_ue.txt) || true; }
source "$(dirname "$0")/xrat.sh"
