#!/bin/bash
# ICMP across a RAT change of one dual-mode UE between LTE and NR NTN, with packet captures (xrat.sh), under
# build/pcap-xrat/ping-<lte2ntn|ntn2lte>-<trans|regen>-<stage>-*
#   [REGEN=1] STAGE=base|trigger|access|session xrat_ping.sh lte2ntn|ntn2lte [runs]
# Traffic: ICMP every 10 ms from the UPF to the source UE IP (ping_dl): it stops when that IP is no longer the UE's.

KIND=ping SGI_FILTER=icmp SNAP=0 rx=sgi.pcap data="icmp[icmptype] = icmp-echoreply"
traffic() { ev "ping with $ip"; docker exec upf ping -i 0.01 -D -O $ip > $out/ping_dl.txt 2>&1 & }
traffic_stop() { docker exec upf pkill -INT -f 'ping -i 0.01' || true; }
# runs of lost requests (send time of the first one), reply gaps over 200 ms, RTT per 5 s (mean/max), ping statistics
report() { f=$out/ping_dl.txt; awk -F'[][]' '{ split($3, q, "icmp_seq="); s = q[2] + 0 }
    /no answer yet/ { if (!(s in T)) T[s] = $2 }
    /bytes from/ { t = $2; R[s] = 1; if (s > mx) mx = s
      if (p && t - p > 0.2) printf "gap %5.0f ms  %.3f -> %.3f\n", (t - p) * 1000, p, t; p = t
      split($3, v, "time="); r = v[2] + 0; b = int(t / 5); if (!b0) b0 = b; if (r > m[b]) m[b] = r; n[b]++; a[b] += r }
    /transmitted|rtt/ { print }
    END { for (i = 1; i <= mx + 1; i++) if (i <= mx && !(i in R)) { if (!l0) l0 = i }
        else if (l0) { if (i - l0 > 1) printf "lost %3d  from %.3f (seq %d)\n", i - l0, T[l0], l0; l0 = 0 }
      for (i = b0; i in n; i++) l = l sprintf(" %.0f/%.0f", a[i] / n[i], m[i]); print "RTT ms per 5 s (mean/max):" l }' $f; }
source "$(dirname "$0")/xrat.sh"
