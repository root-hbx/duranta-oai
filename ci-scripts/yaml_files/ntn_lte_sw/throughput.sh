#!/bin/bash
# Eight ordinary cross-RAT TCP cases, using the selected profile and the original xrat procedure.
#   throughput.sh all [runs]
#   [REGEN=1] [TRAFFIC=DL|UL] throughput.sh lte2ntn|ntn2lte [runs]
#   THROUGHPUT_PROFILE=baseline|tuned (default tuned).
# Each experiment initializes the AMF, uses xrat's RAN containers, and saves its own result directory.
set -e
self=$(realpath "$0")
cd "$(dirname "$self")"
repo=$(cd ../../.. && pwd)
case ${1:-} in all|lte2ntn|ntn2lte) ;; *) sed -n '2,6p' "$self"; exit 1 ;; esac
case ${TRAFFIC:-DL} in DL|UL) ;; *) echo "TRAFFIC must be DL or UL" >&2; exit 1 ;; esac
case ${THROUGHPUT_PROFILE:-tuned} in baseline|tuned) ;; *) echo "Unknown throughput profile" >&2; exit 1 ;; esac
export THROUGHPUT_PROFILE=${THROUGHPUT_PROFILE:-tuned} CORE=${CORE:-private}
export TWO_SAT=1 STAGE=redirect IWK=1 T_RUN=${T_RUN:-180} IPERF_JSON=1
export PCAP_DIR=${PCAP_DIR:-$repo/build/pcap-throughput} TAG=${TAG:-$THROUGHPUT_PROFILE}

export SAT_TRACE1=ho_pair_sat0_900.csv SAT_TRACE2=ho_pair_sat1_900.csv
export NR_UECAP=/oai-ran/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_n254_10mhz.xml
export XRAT_TRACE_DIR=${XRAT_TRACE_DIR:-$repo/build/throughput-trace}
mkdir -p "$(dirname "$XRAT_TRACE_DIR")"
if [ "$THROUGHPUT_PROFILE" = tuned ]; then
  : "${UE_CPUS:?Select the UE container CPUs from the host topology}"
  : "${NR_MAIN_CPUS:?Select the NR main-thread CPU}"
  : "${NR_WORKER_CPUS:?Select separate NR worker CPUs}"
fi

if [ "$1" = all ]; then
  for mode in trans regen; do
    regen=; [ "$mode" != regen ] || regen=1
    for direction in lte2ntn ntn2lte; do
      for traffic in DL UL; do
        REGEN=$regen TRAFFIC=$traffic bash "$self" "$direction" "${2:-1}"
      done
    done
  done
  exit 0
fi
export TRAFFIC=${TRAFFIC:-DL}
# The harness owns these names during the reserved experiment slot.
containers=$(docker ps -a --format '{{.Names}}')
for name in xr-ue lte-enb1 ntn-gnb1 ntn-gnb2 cap-core cap-mac cap-sgi cap-ue; do
  if grep -qx "$name" <<< "$containers"; then
    echo "Container $name already exists; reserve the xrat bench before running" >&2
    exit 1
  fi
done
# The faster NR clock needs the earlier reverse traffic start to retain the measured pre-change window.
if [ "$1" = ntn2lte ]; then export T_START=${T_START:-61}; else export T_START=${T_START:-91}; fi
exec bash ./xrat_tcp.sh "$1" "${2:-1}"
