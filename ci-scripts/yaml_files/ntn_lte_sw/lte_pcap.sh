#!/bin/bash
# Packet captures of LTE mobility, one directory per run under $PCAP_DIR (default build-lte/pcap)
#   lte_pcap.sh base [runs]        RRC_CONNECTED on eNB1, no handover (30 s)
#   lte_pcap.sh connected [runs]   X2 handover to PCI 11 and back to PCI 10
#   lte_pcap.sh idle [runs]        attach via eNB1, UE restart, attach via eNB2 (break-before-make)
# Traffic: ICMP every 10 ms from the UE (ping_ul) and from the PGW-U towards the UE (ping_dl).
# Captures: core (S1/X2/GTP/PFCP/Diameter), mac (OPT MAC PDUs of all nodes, UDP 9999), sgi (PGW-U ogstun), ue (UE netns).
# OPT goes to the bridge gateway: a local --opt.ip binds UDP 9999, which the LTE UE needs for its PDCP PC5 socket.
source "$(dirname "$0")/lte.sh"
repo=$(cd ../../.. && pwd)
export BUILD_DIR=${BUILD_DIR:-$repo/build-lte} OPT=${OPT---opt.type wireshark --opt.ip 172.22.0.1}
PCAP_DIR=${PCAP_DIR:-$repo/build-lte/pcap}
core_if=br-$(docker network inspect -f '{{.Id}}' docker_open5gs_default | cut -c1-12)

# ramp eNB2 until the handover to PCI $1; the gains then stay at the cell edge
ho() { ev "trigger ho $1"; ev "$(./lte.sh ho $1)"; }
ev() { echo "$(date +%s.%3N) $*" | tee -a $out/events.txt; }
cap() { docker run -d --rm --name cap-$1 --net $2 --cap-add NET_ADMIN --cap-add NET_RAW -v $out:/cap \
  --entrypoint tcpdump docker_open5gs -Z root -U -i $3 -w /cap/$1.pcap "$4" >/dev/null; }
cap_ue() { cap ue$1 container:lte-ue any 'not tcp port 4043 and not udp port 9999'; }
uncap() { for c in "$@"; do docker kill -s INT cap-$c >/dev/null 2>&1 || true; done; }
ue_ip() { for i in $(seq 30); do ip=$(docker exec lte-ue ip -br -4 addr show oaitun_ue1 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
  [ -n "$ip" ] && { ev "UE IP $ip"; return; }; sleep 1; done; ev "no UE IP"; return 1; }
pings() { ue_ip
  docker exec lte-ue ping -I oaitun_ue1 -i 0.01 -D -O 192.168.96.1 > $out/ping_ul$1.txt 2>&1 &
  docker exec upf ping -i 0.01 -D -O $ip > $out/ping_dl$1.txt 2>&1 & }
logs() { mkdir -p $out/logs; for n in "$@"; do docker logs $n > $out/logs/$n$suffix.log 2>&1 || true; done; }
# reply gaps over 100 ms (the interval is 10 ms) and the ping statistics
gaps() { for f in $out/ping_*.txt; do echo "== ${f##*/}"; awk -F'[][]' '/bytes from/ { t = $2; if (p && t - p > 0.1)
  printf "gap %5.0f ms  %.3f -> %.3f\n", (t - p) * 1000, p, t; p = t } /transmitted|rtt/' $f; done; }
finish() { docker exec lte-ue pkill -INT ping 2>/dev/null; docker exec upf pkill -INT -f 'ping -i 0.01' || true; sleep 1
  uncap core mac sgi ue ue1 ue2; logs lte-ue lte-enb1 lte-enb2; wait
  gaps | tee $out/summary.txt
  docker run --rm -v $out:/cap --entrypoint chown docker_open5gs -R $(id -u):$(id -g) /cap; }

start() { out=$PCAP_DIR/$1-$2-$(date +%m%d-%H%M%S); mkdir -p $out; suffix=; echo "## $out"
  cap core host $core_if 'not tcp port 4043 and not udp port 9999'
  cap mac host $core_if 'udp port 9999'; cap sgi container:upf ogstun icmp; }

run_connected() { ./lte.sh connected; start $1 $2; cap_ue ''; sleep 1; pings
  if [ $1 = base ]; then sleep 30; else
    sleep 5; ho 11; sleep 10; ho 10; sleep 10; fi
  finish; }

run_idle() { late_net; reset; start idle $1
  dc up -d lte-ue; cap_ue 1; sleep 3; dc up -d lte-enb1; attached; pings 1; sleep 5
  suffix=1 logs lte-ue lte-enb1; uncap ue1; ev "break: remove UE and eNB1"; dc rm -sf lte-ue lte-enb1
  dc up -d lte-ue; cap_ue 2; sleep 3; ENB2_RFSIM=172.22.0.61 ENB2_GAIN=0 ENB2_X2= dc up -d lte-enb2
  attached; ev "attached via eNB2"; pings 2; sleep 10; suffix=2 finish; }

[ -n "$1" ] || { sed -n 2,6p $0; exit 1; }
for r in $(seq ${2:-1}); do
  case $1 in base|connected) run_connected $1 $r ;; idle) run_idle $r ;; *) sed -n 2,6p $0; exit 1 ;; esac
done
reset
tar czf $PCAP_DIR/lte-pcap-$1-$(date +%m%d-%H%M%S).tar.gz -C $PCAP_DIR $(cd $PCAP_DIR && ls -d $1-*/)
