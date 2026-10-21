#!/bin/bash
# ICMP during NR NTN handovers, with packet captures (ntn_cap.sh), under build/pcap-ntn/ping-<trans|regen>-*
#   [REGEN=1] ntn_ping.sh base [runs]        RRC_CONNECTED on gNB1, no handover (40 s)
#   [REGEN=1] ntn_ping.sh connected [runs]   N2 handover to PCI 1 and back to PCI 0
#   TWO_SAT=1 [REGEN=1] ntn_ping.sh cho|base [runs]   time-based CHO to PCI 1 (sat 1) / no handover,
#                         trace time 90-165 s (ntn_cap.sh)
# Traffic: ICMP every 10 ms from the UE (ping_ul) and from the UPF towards the UE (ping_dl), no other load:
# the RTT is the end-to-end one without queueing.

KIND=ping SGI_FILTER=icmp SNAP=0 T_BASE=40 T_PRE=10 T_POST=15
traffic() { ev "ping with $ip"
  docker exec nr-ue ping -I oaitun_ue1 -i 0.01 -D -O 192.168.96.1 > $out/ping_ul.txt 2>&1 &
  docker exec upf ping -i 0.01 -D -O $ip > $out/ping_dl.txt 2>&1 & }
traffic_stop() { docker exec nr-ue pkill -INT ping 2>/dev/null; docker exec upf pkill -INT -f 'ping -i 0.01' || true; }
# runs of lost requests (send time of the first one), reply gaps over 200 ms (the RTT jitters by ~100 ms),
# RTT per 5 s (mean/max) and the ping statistics
report() { for f in $out/ping_*.txt; do echo "== ${f##*/}"; awk -F'[][]' '{ split($3, q, "icmp_seq="); s = q[2] + 0 }
    /no answer yet/ { if (!(s in T)) T[s] = $2 }
    /bytes from/ { t = $2; R[s] = 1; if (s > mx) mx = s
      if (p && t - p > 0.2) printf "gap %5.0f ms  %.3f -> %.3f\n", (t - p) * 1000, p, t; p = t
      split($3, v, "time="); r = v[2] + 0; b = int(t / 5); if (!b0) b0 = b; if (r > m[b]) m[b] = r; n[b]++; a[b] += r }
    /transmitted|rtt/ { print }
    END { for (i = 1; i <= mx + 1; i++) if (i <= mx && !(i in R)) { if (!l0) l0 = i }
        else if (l0) { if (i - l0 > 1) printf "lost %3d  from %.3f (seq %d)\n", i - l0, T[l0], l0; l0 = 0 }
      for (i = b0; i in n; i++) l = l sprintf(" %.0f/%.0f", a[i] / n[i], m[i]); print "RTT ms per 5 s (mean/max):" l }' $f
  done; }
source "$(dirname "$0")/ntn_cap.sh"
