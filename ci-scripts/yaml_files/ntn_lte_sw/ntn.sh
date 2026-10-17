#!/bin/bash
# NR NTN (trace LEO) link and mobility between two gNBs
#   ntn.sh link           gNB1 (rfsim server) + UE: registration and PDU session
#   ntn.sh connected      UE (rfsim server) registers via gNB1 (PCI 0), then gNB2 (PCI 1) joins the radio
#   ntn.sh ho <0|1>       N2 handover of the UE to PCI 0 or 1, triggered at the serving gNB
#   HO_IF=xn ntn.sh ho ..  Xn handover instead of N2 (the gNBs set up Xn with each other)
#   ntn.sh down
#   REGEN=1 ntn.sh ...    regenerative satellite instead of transparent
#   GNB2=0 ntn.sh connected   without gNB2 (no co-channel neighbour)
#   NBR_ATT=<dB> ntn.sh ...    the other cell is NBR_ATT dB weaker (default 20), both equal during a handover
#   ntn.sh ping [n]       ping the core network from the UE
#   ntn.sh tn <node> <cmd>  telnet command to a softmodem
set -e
cd "$(dirname "$0")"
[ -n "$REGEN" ] && export SAT=SAT_LEO_REGEN TA_COMMON=0 TA_DRIFT=0 KOFFSET=${KOFFSET:-10} PROP_DELAY=4 UE_FO=56634 UE_DRIFT=-23 UE_FO_COMP=3
dc() { docker compose -p ntn_sw -f ntn.yaml "$@"; }
tn() { docker exec $1 bash -c "exec 3<>/dev/tcp/127.0.0.1/9090; echo '$2' >&3; timeout ${3:-1} cat <&3" | tr -d '\r'; }
count() { docker logs $1 2>&1 | grep -c "$2" || true; }
wait_log() { for i in $(seq ${3:-60}); do docker logs $1 2>&1 | grep -q "$2" && return; sleep 1; done; echo "timeout: $1 '$2'"; return 1; }
ue_ip() { for i in $(seq ${1:-90}); do docker exec nr-ue ip -br -4 addr show oaitun_ue1 2>/dev/null | grep -q '\.' && return; sleep 1; done; echo "timeout: UE IP"; return 1; }
ping_ue() { docker exec nr-ue ping -I oaitun_ue1 -c ${1:-5} -W 2 192.168.96.1 | tail -2; }
reset() { dc down 2>/dev/null; docker rm -f nr-ue ntn-gnb1 ntn-gnb2 >/dev/null 2>&1 || true; }
# beam <0|1|edge>: UE in the beam of PCI 0 or 1, the other cell NBR_ATT dB weaker both ways, or at the beam edge (both 0 dB)
# UE channel models 1/2 are gNB1/gNB2, gNB model 0 is the UE
# one telnet client at a time per softmodem (concurrent ones crash it), the softmodems in parallel
beam() { local a=-${NBR_ATT:-20} g1=0 g2=0; if [ $1 = 0 ]; then g2=$a; elif [ $1 = 1 ]; then g1=$a; fi
  ploss() { tn $1 "channelmod modify $2 ploss $3" 0.2 >/dev/null; }
  { ploss nr-ue 1 $g1; ploss nr-ue 2 $g2; } & ploss ntn-gnb1 0 $g1 & ploss ntn-gnb2 0 $g2 & wait; }
# a fresh AMF per run: after a failed handover it may keep the old gNB association and refuse the new gNB2
amf_restart() { t=$(date +%s); docker restart amf >/dev/null
  for i in $(seq 30); do docker logs --since $t amf 2>&1 | grep -q 'NF registered' && return; sleep 1; done; echo "timeout: amf"; return 1; }

[ "$0" = "$BASH_SOURCE" ] || return 0  # sourced for its helpers (ntn_cap.sh)
case $1 in
  link)
    reset; amf_restart
    dc up -d ntn-gnb1; wait_log ntn-gnb1 'Received NGSetupResponse'
    t0=$(date +%s); dc up -d nr-ue; ue_ip
    echo "PDU session up in $(( $(date +%s) - t0 )) s"; ping_ue 5 ;;
  connected)
    reset; amf_restart; export UE_RFSIM=server GNB1_RFSIM=172.22.0.71
    dc up -d ntn-gnb1; wait_log ntn-gnb1 'Received NGSetupResponse'
    t0=$(date +%s); dc up -d nr-ue; ue_ip; echo "PDU session up in $(( $(date +%s) - t0 )) s"
    [ "$GNB2" = 0 ] && exit
    dc up -d ntn-gnb2; wait_log ntn-gnb2 'Connection to 172.22.0.71'; beam 0; echo "gNB2 on air"
    [ "$HO_IF" = xn ] && wait_log ntn-gnb1 'Xn peer .* setup complete' && echo "Xn up" ;;
  ho)
    src=ntn-gnb$(( 2 - $2 ))
    n=$(count nr-ue 'Processing reconfigurationWithSync'); m=$(count nr-ue "Initial sync successful, PCI: $2")
    # the handover happens at the beam edge, then the UE is in the target beam
    beam edge; tn $src "ci trigger_${HO_IF:-n2}_ho $2,1" | grep -i handover
    for i in $(seq 50); do sleep 0.2; [ $(count nr-ue 'Processing reconfigurationWithSync') -gt $n ] && break; done
    for i in $(seq 100); do [ $(count nr-ue "Initial sync successful, PCI: $2") -gt $m ] && beam $2 && echo "UE synced to PCI $2" && exit; sleep 0.2; done
    echo "HO to PCI $2 FAILED"; exit 1 ;;
  ping) ping_ue $2 ;;
  tn) tn $2 "$3" ;;
  down) reset ;;
  *) sed -n 2,11p ntn.sh; exit 1 ;;
esac
