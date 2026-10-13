#!/bin/bash
# NR NTN handover stress test: N2 handovers back and forth between PCI 0 and 1 with a ping running
#   [REGEN=1] [LOAD=dl|ul] ntn_ho_loop.sh [n]   n handovers (default 20), GAP s after each one (default 4)
#   LOAD: iperf3 TCP during the handovers, from the UPF to the UE (dl) or reverse (ul)
# After a failed handover the logs are kept and the UE is brought back with ntn.sh connected.
# Output under build/pcap-ntn-hob/<trans|regen>-<time>: results.txt (one line per handover), ping.txt,
# logs/<segment>/ and summary.txt (success rate, ping gaps, MAC timeout counts, last HARQ round stats).
source "$(dirname "$0")/ntn.sh"
repo=$(cd ../../.. && pwd)
mode=trans; [ -n "$REGEN" ] && mode=regen
out=$repo/build/pcap-ntn-hob/$mode${LOAD:+-$LOAD}-$(date +%m%d-%H%M%S); mkdir -p $out
N=${1:-20} GAP=${GAP:-4} seg=0

up() { for i in 1 2 3; do ./ntn.sh connected && break; echo "retry ntn.sh connected"; done
  docker exec -d nr-ue sh -c "ping -D -I oaitun_ue1 -i 0.2 -W 1 192.168.96.1 >> /tmp/ping.txt"; serving=0; load; }
load() { docker exec upf pkill -f 'iperf3 -c' || true; [ -z "$LOAD" ] && return
  ip=$(docker exec nr-ue ip -br -4 addr show oaitun_ue1 | awk '{print $3}' | cut -d/ -f1); docker exec -d nr-ue iperf3 -s; sleep 1
  local rev=; [ $LOAD = ul ] && rev=-R; docker exec -d upf iperf3 -c $ip $rev -t 3600 -b 100M; }
wait_log_n() { for i in $(seq 20); do [ $(count $1 "$2") -gt $3 ] && return; sleep 0.25; done; return 1; }
logs() { seg=$((seg + 1)); mkdir -p $out/logs/$seg
  for n in nr-ue ntn-gnb1 ntn-gnb2; do docker logs $n > $out/logs/$seg/$n.log 2>&1 || true; done
  docker exec nr-ue cat /tmp/ping.txt >> $out/ping.txt 2>/dev/null || true; }

up
for k in $(seq $N); do
  t=$((1 - serving)) t0=$(date +%s.%3N) g=ntn-gnb$((t + 1)); c=$(count $g 'handover for UE .* complete')
  # OK once the target gNB has the RRCReconfigurationComplete (the UE may sync and still fail, e.g., on integrity)
  if r=$(./ntn.sh ho $t 2>&1) && wait_log_n $g 'handover for UE .* complete' $c; then serving=$t res=OK; else res=FAIL; fi
  echo "$k $t0 $(date +%s.%3N) PCI $t $res" | tee -a $out/results.txt
  if [ $res = FAIL ]; then echo "$r" > $out/fail-$k.txt; logs; up; else sleep $GAP; fi
done
logs; LOAD= load; reset

# largest ping gap from each handover trigger to the next one
{ echo "$mode: $(grep -c OK $out/results.txt)/$N handovers OK"
  awk 'NR == FNR { s[NR] = $2; n = NR; next }
    { gsub(/[][]/, "", $1); t = $1 + 0; for (k = 1; k <= n; k++) if (t >= s[k] && (k == n || t < s[k + 1])) {
        if (p[k] && t - p[k] > g[k]) g[k] = t - p[k]; p[k] = t } }
    END { for (k = 1; k <= n; k++) l = l sprintf(" %.1f", g[k]); print "max ping gap s per handover:" l }' \
    $out/results.txt <(grep 'bytes from' $out/ping.txt)
  for p in 'Timeout for UE transmission' 'UL failure after transmission timeout'; do
    echo "$p: $(cat $out/logs/*/ntn-gnb*.log | grep -c "$p")"; done
  for f in $out/logs/*/ntn-gnb*.log; do echo "${f#$out/logs/} $(grep -o 'dlsch_rounds [0-9/]*' $f | tail -1)"; done
} | tee $out/summary.txt
