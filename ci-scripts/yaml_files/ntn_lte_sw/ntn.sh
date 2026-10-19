#!/bin/bash
# NR NTN (trace LEO) link and mobility between two gNBs
#   ntn.sh link           gNB1 (rfsim server) + UE: registration and PDU session
#   ntn.sh connected      UE (rfsim server) registers via gNB1 (PCI 0), then gNB2 (PCI 1) joins the radio
#   ntn.sh ho <0|1|2>     N2 handover of the UE to PCI 0, 1 or 2 (from PCI 0 if not 0), triggered at the serving gNB
#   HO_IF=xn ntn.sh ho ..  Xn handover instead of N2 (the gNBs set up Xn with each other)
#   ntn.sh down
#   REGEN=1 ntn.sh ...    regenerative satellite instead of transparent
#   TWO_SAT=1 ntn.sh ...  gNB1 and gNB2 on two satellites (tools/ntn/data/ho_pair_sat0/1.csv, handover window 120-182 s),
#                         one trace clock from the UE start; without it both on the sample trace
#   THREE_SAT=1 ntn.sh ...  TWO_SAT and gNB3 (PCI 2) on a third satellite (ho_pair_sat2.csv, elevation 15 deg or more
#                         until 183 s), a neighbour of gNB1 with Xn
#   GNB2=0 ntn.sh connected   without gNB2 (no co-channel neighbour)
#   NBR_ATT=<dB> ntn.sh ...    the other cells are NBR_ATT dB weaker (default 20), both equal during a handover
#   ntn.sh ping [n]       ping the core network from the UE
#   ntn.sh tn <node> <cmd>  telnet command to a softmodem
set -e
cd "$(dirname "$0")"
[ -n "$THREE_SAT" ] && export TWO_SAT=1 SAT_TRACE3=ho_pair_sat2.csv GNB1_NEIGHBOURS=0xe02/12345680/2
if [ -n "$TWO_SAT" ]; then
  # tools/ntn/calc_ntn_parameters.py for ho_pair_sat0.csv (gNB1 serves at the start)
  export SAT_TRACE1=ho_pair_sat0.csv SAT_TRACE2=ho_pair_sat1.csv UE_FO=52230 UE_GNSS="--ntn-gnss-utc-start 1792306580000"
  export SAT_CLOCK=$(for k in 0 1 2 3; do echo --channelmod.modellist_rfsimu_1.[$k].sat_trace_common_clock 1; done)
  if [ -n "$REGEN" ]; then
    export SAT=SAT_LEO_REGEN TA_COMMON=0 TA_DRIFT=0 KOFFSET=${KOFFSET:-14} PROP_DELAY=3 UE_DRIFT=-21 UE_FO_COMP=3
  else
    export TA_COMMON=1208136 TA_DRIFT=-209614 KOFFSET=${KOFFSET:-27} PROP_DELAY=5 UE_DRIFT=-42
  fi
elif [ -n "$REGEN" ]; then
  export SAT=SAT_LEO_REGEN TA_COMMON=0 TA_DRIFT=0 KOFFSET=${KOFFSET:-10} PROP_DELAY=4 UE_FO=56634 UE_DRIFT=-23 UE_FO_COMP=3
fi
dc() { docker compose -p ntn_sw -f ntn.yaml "$@"; }
tn() { docker exec $1 bash -c "exec 3<>/dev/tcp/127.0.0.1/9090; echo '$2' >&3; timeout ${3:-1} cat <&3" | tr -d '\r'; }
count() { docker logs $1 2>&1 | grep -c "$2" || true; }
wait_log() { for i in $(seq ${3:-60}); do docker logs $1 2>&1 | grep -q "$2" && return; sleep 1; done; echo "timeout: $1 '$2'"; return 1; }
ue_ip() { for i in $(seq ${1:-90}); do docker exec nr-ue ip -br -4 addr show oaitun_ue1 2>/dev/null | grep -q '\.' && return; sleep 1; done; echo "timeout: UE IP"; return 1; }
ping_ue() { docker exec nr-ue ping -I oaitun_ue1 -c ${1:-5} -W 2 192.168.96.1 | tail -2; }
reset() { dc down 2>/dev/null; docker rm -f nr-ue ntn-gnb1 ntn-gnb2 ntn-gnb3 >/dev/null 2>&1 || true; }
# beam <0|1|2|edge>: UE in the beam of PCI 0, 1 or 2, the other cells NBR_ATT dB weaker both ways, or at the beam edge
# (all 0 dB). UE channel models 1/2/3 are gNB1/gNB2/gNB3, gNB model 0 is the UE
# one telnet client at a time per softmodem (concurrent ones crash it), the softmodems in parallel
beam() { local a=-${NBR_ATT:-20} g=(0 0 0); [ $1 = edge ] || g=($a $a $a) g[$1]=0; gains ${g[@]}; }
# gains <dB of gNB1> <dB of gNB2> [<dB of gNB3>]
gains() { ploss() { tn $1 "channelmod modify $2 ploss $3" 0.2 >/dev/null; }
  { ploss nr-ue 1 $1; ploss nr-ue 2 $2; [ -z "$THREE_SAT" ] || ploss nr-ue 3 ${3:-0}; } & ploss ntn-gnb1 0 $1 &
  ploss ntn-gnb2 0 $2 & [ -z "$THREE_SAT" ] || ploss ntn-gnb3 0 ${3:-0} & wait; }
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
    dc up -d ntn-gnb2; wait_log ntn-gnb2 'Connection to 172.22.0.71'
    if [ -n "$THREE_SAT" ]; then dc up -d ntn-gnb3; wait_log ntn-gnb3 'Connection to 172.22.0.71'; fi
    beam 0; echo "gNB2${THREE_SAT:+ and gNB3} on air"
    [ "$HO_IF" = xn ] || exit 0
    wait_log ntn-gnb1 'Xn peer .* setup complete'
    [ -z "$THREE_SAT" ] || for i in $(seq 60); do [ $(count ntn-gnb1 'Xn peer .* setup complete') -ge 2 ] && break; sleep 1; done
    echo "Xn up" ;;
  ho)
    src=ntn-gnb1; [ $2 = 0 ] && src=ntn-gnb2
    n=$(count nr-ue 'Processing reconfigurationWithSync'); m=$(count nr-ue "Initial sync successful, PCI: $2")
    # the handover happens at the beam edge, then the UE is in the target beam
    beam edge; tn $src "ci trigger_${HO_IF:-n2}_ho $2,1" | grep -i handover
    for i in $(seq 50); do sleep 0.2; [ $(count nr-ue 'Processing reconfigurationWithSync') -gt $n ] && break; done
    for i in $(seq 100); do [ $(count nr-ue "Initial sync successful, PCI: $2") -gt $m ] && beam $2 && echo "UE synced to PCI $2" && exit; sleep 0.2; done
    echo "HO to PCI $2 FAILED"; exit 1 ;;
  ping) ping_ue $2 ;;
  tn) tn $2 "$3" ;;
  down) reset ;;
  *) sed -n 2,15p ntn.sh; exit 1 ;;
esac
