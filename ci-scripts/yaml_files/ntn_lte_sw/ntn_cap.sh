# Packet captures of NR NTN handover runs, sourced last by ntn_tcp.sh.
# The caller sets KIND, SGI_FILTER, SNAP, MAC_SNAP, T_BASE, T_PRE, T_POST and defines the traffic hooks:
#   traffic       start the traffic towards the current UE ($ip)
#   traffic_stop  stop it
#   report        print the summary of $out
# Runs: base (no handover, T_BASE s), connected (T_PRE, N2 handover to PCI 1, T_POST, back to PCI 0, T_POST).
# One directory per run under $PCAP_DIR/<KIND>-<trans|regen>-<exp>-<run>-<time>, then a tarball per invocation.
# Captures: core (NGAP/GTP/PFCP headers), ngap (NGAP and PFCP in full), mac (OPT MAC PDUs of all nodes, UDP 9999),
# sgi (UPF ogstun), ue (UE netns). Handover triggers are in events.txt (epoch s), HandoverRequired in ngap.pcap.
source "$(dirname "$0")/ntn.sh"
repo=$(cd ../../.. && pwd)
mode=trans; [ -n "$REGEN" ] && mode=regen
export OPT=${OPT---opt.type wireshark --opt.ip 172.22.0.1} PRACH_THRES=${PRACH_THRES:-450}
PCAP_DIR=${PCAP_DIR:-$repo/build/pcap-ntn}
core_if=br-$(docker network inspect -f '{{.Id}}' docker_open5gs_default | cut -c1-12)
radio='not tcp port 4043 and not udp port 9999 and not tcp port 9090'

ho() { ev "trigger ho $1"; ev "$(./ntn.sh ho $1 2>&1 | tr '\n' ' ')"; }
ev() { echo "$(date +%s.%3N) $*" | tee -a $out/events.txt; }
# cap <name> <net> <iface> <filter> [snaplen]
cap() { docker run -d --rm --name cap-$1 --net $2 --cap-add NET_ADMIN --cap-add NET_RAW -v $out:/cap \
  --entrypoint tcpdump docker_open5gs -Z root -U -s ${5:-0} -i $3 -w /cap/$1.pcap "$4" >/dev/null; }
uncap() { for c in "$@"; do docker kill -s INT cap-$c >/dev/null 2>&1 || true; done; }
up() { for i in 1 2 3; do ./ntn.sh connected && return; echo "retry ntn.sh connected"; done; return 1; }
logs() { mkdir -p $out/logs; for n in "$@"; do docker logs $n > $out/logs/$n.log 2>&1 || true; done; }
finish() { traffic_stop; sleep 1
  uncap core ngap mac sgi ue; logs nr-ue ntn-gnb1 ntn-gnb2; wait
  { echo "UE reconfigurationWithSync: $(grep -c 'Received reconfigurationWithSync' $out/logs/nr-ue.log)," \
      "gNB handover complete: $(cat $out/logs/ntn-gnb*.log | grep -c 'handover for UE .* complete')"; simwall; report; } |
    tee $out/summary.txt
  docker run --rm -v $out:/cap --entrypoint chown docker_open5gs -R $(id -u):$(id -g) /cap; }

# simulated / wall-clock time from the UE "stats sfn" lines (one per 128 frames): rfsim is not paced in real time
simwall() { awk '/stats sfn:/ { split($0, a, "sfn: "); s = int(a[2]); t = $1
    if (pt && (s - ps + 1024) % 1024 == 128) { n++; w += t - pt }
    pt = t; ps = s }
  END { if (!n) exit; r = n * 1.28 / w; printf "sim/wall %.2f over %d x 1.28 s%s\n", r, n, (r < 0.9 || r > 1.1) ? "  WARNING: rates not comparable" : "" }' \
  $out/logs/nr-ue.log; }

run_connected() { up; out=$PCAP_DIR/$KIND-$mode-$1-$2-$(date +%m%d-%H%M%S); mkdir -p $out; runs+=" ${out##*/}"; echo "## $out"
  cap core host $core_if 'not tcp port 4043 and not udp port 9999' $SNAP; cap ngap host $core_if 'sctp or udp port 8805'
  cap mac host $core_if 'udp port 9999' $MAC_SNAP; cap sgi container:upf ogstun "$SGI_FILTER" $SNAP
  cap ue container:nr-ue any "$radio" $SNAP; sleep 1
  ip=$(docker exec nr-ue ip -br -4 addr show oaitun_ue1 | awk '{print $3}' | cut -d/ -f1); ev "UE IP $ip"; traffic
  if [ $1 = base ]; then sleep $T_BASE; else
    sleep $T_PRE; ho 1; sleep $T_POST; ho 0; sleep $T_POST; fi
  finish; }

usage() { sed -n '2,/^$/p' $0; exit 1; }
case $1 in base|connected) ;; *) usage ;; esac
for r in $(seq ${2:-1}); do run_connected $1 $r; done
reset
tar czf $PCAP_DIR/ntn-$KIND-$mode-$1-$(date +%m%d-%H%M%S).tar.gz -C $PCAP_DIR $runs
