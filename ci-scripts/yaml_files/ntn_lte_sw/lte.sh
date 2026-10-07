#!/bin/bash
# LTE mobility between two eNBs, see doc/lte-ue-enb-handover.md
#   lte.sh connected      UE attaches via eNB1, then eNB2 joins the radio (RRC_CONNECTED setup)
#   lte.sh ho <10|11>     ramp eNB2's gain until the UE hands over to PCI 10 or 11
#   lte.sh idle           UE attaches via eNB1, then restarts and attaches via eNB2 (RRC_IDLE, break-before-make)
#   lte.sh ping [n]       ping the core network from the UE
#   lte.sh tn <node> <cmd>  telnet command to a softmodem
#   lte.sh down
set -e
cd "$(dirname "$0")"
dc() { docker compose -p ntn_lte_sw -f lte.yaml "$@"; }
tn() { docker exec $1 bash -c "exec 3<>/dev/tcp/127.0.0.1/9090; echo '$2' >&3; timeout 1 cat <&3" | tr -d '\r'; }
wait_log() { for i in $(seq ${3:-60}); do docker logs $1 2>&1 | grep -q "$2" && return; sleep 1; done; echo "timeout: $1 '$2'"; return 1; }
attached() { local t0=$(date +%s); wait_log lte-ue 'Send Attach Complete'
  echo "attached via PCI $(docker logs lte-ue 2>&1 | grep -m1 -o 'NidCell [0-9]*' | cut -d' ' -f2) in $(( $(date +%s) - t0 )) s"; }
ping_ue() { docker exec lte-ue ping -I oaitun_ue1 -c ${1:-5} -W 2 192.168.96.1 | tail -2; }
reset() { dc down 2>/dev/null; docker rm -f lte-ue lte-enb1 lte-enb2 >/dev/null 2>&1 || true; }
late_net() { docker network inspect rfsim-late >/dev/null 2>&1 || docker network create --subnet 10.77.0.0/24 rfsim-late >/dev/null; }

[ "$0" = "$BASH_SOURCE" ] || return 0  # sourced for its helpers (lte_pcap.sh)
case $1 in
  connected)
    late_net; reset
    dc up -d lte-ue; sleep 3; dc up -d lte-enb1; sleep 1; dc up -d lte-enb2
    attached; sleep 3
    docker network connect --ip 10.77.0.61 rfsim-late lte-ue
    wait_log lte-enb2 'Connection to 10.77.0.61'; echo "eNB2 on air" ;;
  ho)
    [ "$2" = 11 ] && gains="-6 -4 -3 -2 -1.5 -1 -0.5 0 0.5 1 1.5 2 3" || gains="0 -1 -2 -3 -4 -6 -8"
    n=$(docker logs lte-ue 2>&1 | grep -c "handover to PCI $2" || true)
    for g in $gains; do
      tn lte-enb2 "rfsimu setbeamgains $g" >/dev/null; sleep 2
      [ $(docker logs lte-ue 2>&1 | grep -c "handover to PCI $2") -gt $n ] && { echo "handover to PCI $2 at eNB2 gain $g dB"; exit 0; }
    done
    echo "no handover"; exit 1 ;;
  idle)
    late_net; reset
    dc up -d lte-ue; sleep 3; dc up -d lte-enb1; attached; ping_ue 3
    t0=$(date +%s); dc rm -sf lte-ue lte-enb1
    dc up -d lte-ue; sleep 3; ENB2_RFSIM=172.22.0.61 ENB2_GAIN=0 ENB2_X2= dc up -d lte-enb2; attached
    echo "eNB1 -> eNB2 took $(( $(date +%s) - t0 )) s"; ping_ue 3 ;;
  ping) ping_ue $2 ;;
  tn) tn $2 "$3" ;;
  down) dc down ;;
  *) sed -n 2,8p lte.sh; exit 1 ;;
esac
