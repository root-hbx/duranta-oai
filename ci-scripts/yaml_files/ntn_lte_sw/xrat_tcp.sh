#!/bin/bash
# TCP across a RAT change of one dual-mode UE between LTE and NR NTN, with packet captures (xrat.sh), under
# build/pcap-xrat/tcp-dl-<lte2ntn|ntn2lte>-<trans|regen>-<stage>-*
#   [REGEN=1] STAGE=base|trigger|access|session [GAP=<s>] xrat_tcp.sh lte2ntn|ntn2lte|lte2lte|ntn2ntn [runs]
# TRAFFIC=DL (default): DN sends to UE; TRAFFIC=UL: UE sends to DN. DN_CONTAINER defaults to the UPF.
# IPERF_JSON=1 uses application receiver intervals for thr_rx.csv; the legacy report uses ue.pcap.

TRAFFIC=${TRAFFIC:-DL}
case $TRAFFIC in DL|UL) ;; *) echo 'TRAFFIC must be DL or UL' >&2; exit 1 ;; esac
KIND=tcp-${TRAFFIC,,} SGI_FILTER='tcp port 5201' SNAP=0 rx=ue.pcap data='tcp dst port 5201'
[ "$TRAFFIC" != UL ] || rx=sgi.pcap
# Keep the exact PID of each iperf process launched by this run.
iperf_start() { local node=$1 log=$2; shift 2
  docker exec "$node" sh -c 'echo "PID=$$"; exec "$@"' sh iperf3 "$@" > "$log" 2>&1 &
  job=$!
  for i in $(seq 50); do
    pid=$(sed -n '1s/^PID=\([0-9]*\)$/\1/p' "$log")
    [ -z "$pid" ] || return 0
    kill -0 "$job" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1; }
traffic() {
  local dn=${DN_CONTAINER:-${CORE_CT}upf} dest=$ip flags=() extra=()
  server=xr-ue client=$dn server_log=$out/iperf_ue.txt client_log=$out/iperf_core.txt
  if [ "$TRAFFIC" = UL ]; then
    server=$dn client=xr-ue server_log=$out/iperf_core.txt client_log=$out/iperf_ue.txt
    dest=${DN_IP:-$(docker exec "$dn" ip -4 -o addr show dev ogstun | awk '{split($4,a,"/"); print a[1]; exit}')}
    [ -n "$dest" ] || { echo 'Set DN_IP to the uplink receiver address' >&2; return 1; }
  fi
  [ "${IPERF_JSON:-0}" != 1 ] || flags=(-J)
  read -r -a extra <<< "$IPERF_EXTRA"
  iperf_start "$server" "$server_log" -s -1 -i 0.1 --forceflush "${flags[@]}" || return 1
  server_job=$job server_pid=$pid
  sleep 1
  kill -0 "$server_job" 2>/dev/null || return 1
  ev "iperf3 $TRAFFIC sender $client receiver $server"
  iperf_start "$client" "$client_log" -c "$dest" -t "${T_RUN:-600}" -b 100M -i 0.1 --forceflush \
    "${flags[@]}" "${extra[@]}" || return 1
  client_job=$job client_pid=$pid; }
iperf_stop() { [ -n "$2" ] || return 0
  for i in $(seq 30); do kill -0 "$2" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$2" 2>/dev/null; then docker exec "$1" kill -INT "$3" 2>/dev/null || true; fi
  wait "$2" 2>/dev/null || true; }
traffic_stop() {
  iperf_stop "$client" "$client_job" "$client_pid"
  iperf_stop "$server" "$server_job" "$server_pid"
  client_job= server_job=; }
# data gaps over 100 ms at the UE (gaps less than 20 ms apart merged), goodput per second, iperf3 totals
report() {
  if [ "${IPERF_JSON:-0}" = 1 ]; then
    python3 -B "$repo/moti_exp/throughput/bw.py" "$server_log" --csv "$out/thr_rx.csv" \
      ${t_change:+--change "$t_change"} --plot "$out/throughput.png"
    return $?
  fi
  docker run --rm -v $out:/cap --entrypoint tcpdump $CORE_IMAGE -r /cap/$rx -tt -nq "$data" 2>/dev/null |
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
