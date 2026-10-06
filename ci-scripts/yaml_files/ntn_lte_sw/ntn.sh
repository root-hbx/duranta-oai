#!/bin/bash
# NR NTN (trace LEO) link and mobility between two gNBs
#   ntn.sh link           gNB1 (rfsim server) + UE: registration and PDU session
#   ntn.sh connected      UE (rfsim server) registers via gNB1 (PCI 0), then gNB2 (PCI 1) joins the radio
#   ntn.sh ho <0|1>       N2 handover of the UE to PCI 0 or 1, triggered at the serving gNB
#   ntn.sh down
#   REGEN=1 ntn.sh ...    regenerative satellite instead of transparent
#   ntn.sh ping [n]       ping the core network from the UE
#   ntn.sh tn <node> <cmd>  telnet command to a softmodem
set -e
cd "$(dirname "$0")"
[ -n "$REGEN" ] && export SAT=SAT_LEO_REGEN TA_COMMON=0 TA_DRIFT=0 PROP_DELAY=4 UE_FO=56634 UE_DRIFT=-23 UE_FO_COMP=3
dc() { docker compose -p ntn_sw -f ntn.yaml "$@"; }
tn() { docker exec $1 bash -c "exec 3<>/dev/tcp/127.0.0.1/9090; echo '$2' >&3; timeout 1 cat <&3" | tr -d '\r'; }
wait_log() { for i in $(seq ${3:-60}); do docker logs $1 2>&1 | grep -q "$2" && return; sleep 1; done; echo "timeout: $1 '$2'"; return 1; }
ue_ip() { for i in $(seq ${1:-90}); do docker exec nr-ue ip -br -4 addr show oaitun_ue1 2>/dev/null | grep -q '\.' && return; sleep 1; done; echo "timeout: UE IP"; return 1; }
ping_ue() { docker exec nr-ue ping -I oaitun_ue1 -c ${1:-5} -W 2 192.168.96.1 | tail -2; }
reset() { dc down 2>/dev/null; docker rm -f nr-ue ntn-gnb1 ntn-gnb2 >/dev/null 2>&1 || true; }

case $1 in
  link)
    reset
    dc up -d ntn-gnb1; wait_log ntn-gnb1 'Received NGSetupResponse'
    t0=$(date +%s); dc up -d nr-ue; ue_ip
    echo "PDU session up in $(( $(date +%s) - t0 )) s"; ping_ue 5 ;;
  connected)
    reset; export UE_RFSIM=server GNB1_RFSIM=172.22.0.71
    dc up -d ntn-gnb1; wait_log ntn-gnb1 'Received NGSetupResponse'
    t0=$(date +%s); dc up -d nr-ue; ue_ip; echo "PDU session up in $(( $(date +%s) - t0 )) s"
    dc up -d ntn-gnb2; wait_log ntn-gnb2 'Connection to 172.22.0.71'; echo "gNB2 on air" ;;
  ho)
    src=ntn-gnb$(( 2 - $2 ))
    n=$(docker logs nr-ue 2>&1 | grep -c 'Processing reconfigurationWithSync' || true)
    tn $src "ci trigger_n2_ho $2,1" | grep -i handover
    for i in $(seq 10); do sleep 1; [ $(docker logs nr-ue 2>&1 | grep -c 'Processing reconfigurationWithSync') -gt $n ] && break; done
    wait_log nr-ue "Initial sync successful, PCI: $2" 20 && echo "UE synced to PCI $2" ;;
  ping) ping_ue $2 ;;
  tn) tn $2 "$3" ;;
  down) reset ;;
  *) sed -n 2,10p ntn.sh; exit 1 ;;
esac
