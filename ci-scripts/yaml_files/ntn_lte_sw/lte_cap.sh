# Packet captures of LTE mobility runs, sourced last by lte_ping.sh and lte_tcp.sh.
# The caller sets KIND, SGI_FILTER, SNAP, MAC_SNAP, T_BASE, T_PRE, T_POST and defines the traffic hooks:
#   traffic <n>   start the traffic towards the current UE (n: empty, or 1 / 2 before / after the idle break)
#   traffic_stop  stop it
#   report        print the summary of $out
# Runs: base (no handover, T_BASE s), connected (T_PRE, X2 handover to PCI 11, T_POST, back to PCI 10, T_POST; with
# T_END set, at T_PRE eNB2 -20 -> SWAP_GAIN dB, the UE hands over to PCI 11 on its own, then eNB1 -> -20 dB, until
# T_END s after the traffic start), idle (attach via eNB1, T_PRE,
# UE restart, attach via eNB2, T_POST).
# One directory per run under $PCAP_DIR/<KIND>-<exp>-<run>-<time>, then a tarball per invocation.
# Captures: core (S1/X2/GTP/PFCP/Diameter), mac (OPT MAC PDUs of all nodes, UDP 9999), sgi (PGW-U ogstun), ue (UE netns).
# OPT goes to the bridge gateway: a local --opt.ip binds UDP 9999, which the LTE UE needs for its PDCP PC5 socket.
source "$(dirname "$0")/lte.sh"
repo=$(cd ../../.. && pwd)
export BUILD_DIR=${BUILD_DIR:-$repo/build-lte} OPT=${OPT---opt.type wireshark --opt.ip $CORE_NET.0.1}
PCAP_DIR=${PCAP_DIR:-$repo/build-lte/pcap}
core_if=br-$(docker network inspect -f '{{.Id}}' $CORE_NETWORK | cut -c1-12)

# ramp eNB2 until the handover to PCI $1; the gains then stay at the cell edge
ho() { ev "trigger ho $1"; ev "$(./lte.sh ho $1)"; }
# swap <pci>: eNB2 jumps to SWAP_GAIN dB (A3 offset + hysteresis is 1 dB), after the handover eNB1 drops to -20 dB, so
# the two eNBs interfere only until the handover (a slow source fade makes the handover command too late)
swap() { local n=$(docker logs lte-ue 2>&1 | grep -c "handover to PCI $1" || true) end=$((SECONDS + 20)) ho=
  tn lte-enb2 "rfsimu setbeamgains ${SWAP_GAIN:-1.5}" >/dev/null; ev "eNB2 at ${SWAP_GAIN:-1.5} dB"
  while [ -z "$ho" ] && [ $SECONDS -lt $end ]; do
    [ $(docker logs lte-ue 2>&1 | grep -c "handover to PCI $1") -le $n ] || { ho=1; ev "handover to PCI $1"; }
  done
  [ -n "$ho" ] || ev "no handover"
  tn lte-enb1 "rfsimu setbeamgains -20" >/dev/null; ev "eNB1 at -20 dB"; }
ev() { echo "$(date +%s.%3N) $*" | tee -a $out/events.txt; }
# cap <name> <net> <iface> <filter> [snaplen]
cap() { docker run -d --rm --name cap-$1 --net $2 --cap-add NET_ADMIN --cap-add NET_RAW -v $out:/cap \
  --entrypoint tcpdump $CORE_IMAGE -Z root -U -s ${5:-0} -i $3 -w /cap/$1.pcap "$4" >/dev/null; }
cap_ue() { cap ue$1 container:lte-ue any 'not tcp port 4043 and not udp port 9999' $SNAP; }
uncap() { for c in "$@"; do docker kill -s INT cap-$c >/dev/null 2>&1 || true; done; }
ue_ip() { for i in $(seq 30); do ip=$(docker exec lte-ue ip -br -4 addr show oaitun_ue1 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
  [ -n "$ip" ] && { ev "UE IP $ip"; return; }; sleep 1; done; ev "no UE IP"; return 1; }
logs() { mkdir -p $out/logs; for n in "$@"; do docker logs $n > $out/logs/$n$suffix.log 2>&1 || true; done; }
finish() { traffic_stop; sleep 1
  uncap core mac sgi ue ue1 ue2; logs lte-ue lte-enb1 lte-enb2; wait
  report | tee $out/summary.txt
  docker run --rm -v $out:/cap --entrypoint chown $CORE_IMAGE -R $(id -u):$(id -g) /cap; }

start() { out=$PCAP_DIR/$KIND-$1-$2-$(date +%m%d-%H%M%S); mkdir -p $out; suffix=; echo "## $out"
  cap core host $core_if 'not tcp port 4043 and not udp port 9999' $SNAP
  cap mac host $core_if 'udp port 9999' $MAC_SNAP; cap sgi container:${CORE_CT}upf ogstun "$SGI_FILTER" $SNAP; }

run_connected() { ./lte.sh connected; start $1 $2; cap_ue ''; sleep 1; traffic; local t0=$(date +%s)
  if [ $1 = base ]; then sleep $T_BASE
  elif [ -n "$T_END" ]; then sleep $T_PRE; swap 11; local s=$(( t0 + T_END - $(date +%s) )); [ $s -le 0 ] || sleep $s
  else sleep $T_PRE; ho 11; sleep $T_POST; ho 10; sleep $T_POST; fi
  finish; }

run_idle() { late_net; reset; start idle $1
  dc up -d lte-ue; cap_ue 1; sleep 3; dc up -d lte-enb1; attached; traffic 1; sleep $T_PRE
  suffix=1 logs lte-ue lte-enb1; uncap ue1; ev "break: remove UE and eNB1"; dc rm -sf lte-ue lte-enb1
  dc up -d lte-ue; cap_ue 2; sleep 3; ENB2_RFSIM=$CORE_NET.0.61 ENB2_GAIN=0 ENB2_X2= dc up -d lte-enb2
  attached; ev "attached via eNB2"; traffic 2; sleep $T_POST; suffix=2 finish; }

usage() { sed -n '2,/^$/p' $0; exit 1; }
[ -n "$1" ] || usage
for r in $(seq ${2:-1}); do
  case $1 in base|connected) run_connected $1 $r ;; idle) run_idle $r ;; *) usage ;; esac
done
reset
tar czf $PCAP_DIR/lte-$KIND-$1-$(date +%m%d-%H%M%S).tar.gz -C $PCAP_DIR $(cd $PCAP_DIR && ls -d $KIND-$1-*/)
