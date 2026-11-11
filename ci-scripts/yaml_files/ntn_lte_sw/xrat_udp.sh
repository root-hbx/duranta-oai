#!/bin/bash
# UDP downlink across a RAT change, using the paper's REGEN / same-IP scenario.
#   SEC2_RUN_DIR=<new-directory> [STAGE=redirect|session|base] [GAP=<s>]
#     [RATE=100M] [PROTO=udp|tcp] xrat_udp.sh lte2ntn|ntn2lte|ntn2ntn
# PROTO=tcp runs the TCP control with the same capture settings.

set -e
set +o pipefail
[ "$#" -eq 1 ] || { echo "Usage: $0 lte2ntn|ntn2lte|lte2lte|ntn2ntn" >&2; exit 1; }
case "${1:-}" in lte2ntn|ntn2lte|lte2lte|ntn2ntn) ;; *) sed -n '2,5p' "$0"; exit 1 ;; esac
repo=$(cd "$(dirname "$0")/../../.." && pwd)
export SEC2_MODULE=$repo/moti_exp/sec2_udp
export SEC2_RUN_DIR=$(realpath -m "${SEC2_RUN_DIR:-$SEC2_MODULE/results/run-$(date -u +%Y%m%dT%H%M%S)-$$}")
export SEC2_RUN_ID=$(date +%s%N)-$$ SEC2_PYTHON=${SEC2_PYTHON:-python3}
export NR_BUILD=$(realpath -m "${NR_BUILD:-$repo/build}")
export LTE_BUILD=$(realpath -m "${LTE_BUILD:-$repo/build-lte}")
export CORE=private TWO_SAT=1 REGEN=1 IWK=1
export THROUGHPUT_PROFILE= THREE_SAT= LTE_PRB=25 LTE_RXGAIN=140
export ENB1_EXTRA= GNB1_EXTRA= GNB2_EXTRA= UE_EXTRA= LTE_UE_EXTRA=
export STAGE=${STAGE:-redirect} PROTO=${PROTO:-udp} RATE=${RATE:-100M} DATAGRAM=1200
export T_START=91 T_CHANGE=131 T_RUN=180
case "$PROTO" in udp|tcp) ;; *) echo 'PROTO must be udp or tcp' >&2; exit 1 ;; esac
mkdir -p "$SEC2_MODULE/results"
exec 9>"$SEC2_MODULE/results/.run.lock"
flock -n 9 || { echo 'Another UDP campaign attempt is running' >&2; exit 1; }
[ ! -e "$SEC2_RUN_DIR" ] || { echo "Output directory already exists: $SEC2_RUN_DIR" >&2; exit 1; }
mkdir -p "$(dirname "$SEC2_RUN_DIR")"
mkdir -m 700 "$SEC2_RUN_DIR"
[ -z "$SEC2_CONDITION" ] || printf '%s\n' "$SEC2_CONDITION" > "$SEC2_RUN_DIR/condition.json"
export XRAT_RUN_DIR=$SEC2_RUN_DIR XRAT_TRACE_DIR=$SEC2_RUN_DIR/trace
KIND=$PROTO-dl SNAP=0 rx=ue.pcap SGI_FILTER='port 5201' data="$PROTO dst port 5201"

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
  sec2_processes source
  local server=() flags=()
  # Bind the UDP handshake reply to the UE address used by the sender.
  [ "$PROTO" != udp ] || { server=(-B "$ip"); flags=(-u -l "$DATAGRAM"); }
  iperf_start xr-ue "$out/iperf_ue.txt" -s -1 -i 0.1 --forceflush "${server[@]}" || exit 1
  server_job=$job server_pid=$pid
  sleep 1
  kill -0 "$server_job" 2>/dev/null || exit 1
  ev "iperf3 dl with $ip protocol=$PROTO rate=$RATE datagram=$DATAGRAM"
  iperf_start "${CORE_CT}upf" "$out/iperf_core.txt" -c "$ip" -t 600 -b "$RATE" -i 0.1 \
    --forceflush "${flags[@]}" || exit 1
  client_job=$job client_pid=$pid
}
iperf_stop() { [ -n "$2" ] || return 0
  if kill -0 "$2" 2>/dev/null; then docker exec "$1" kill -INT "$3" 2>/dev/null || true; fi
  wait "$2" 2>/dev/null || true; }
traffic_stop() {
  iperf_stop "${CORE_CT}upf" "$client_job" "$client_pid"
  iperf_stop xr-ue "$server_job" "$server_pid"
  client_job= server_job=
}
report() { echo "Receiver: ue.pcap; sender: sgi.pcap; protocol: $PROTO"; }

export XRAT_CAPTURE=$SEC2_MODULE/run/instrument.sh
source "$(dirname "$0")/xrat.sh" "$@"
