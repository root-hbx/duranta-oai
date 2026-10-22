# RAT change of one dual-mode UE between LTE and NR NTN (xrat.yaml), with packet captures; sourced last by xrat_tcp.sh
# and xrat_ping.sh. The caller sets KIND, SGI_FILTER, SNAP, rx (receiver capture) and data (its data filter), and
# defines the traffic hooks:
#   traffic       start the traffic towards the source UE IP $ip
#   traffic_stop  stop it
#   report        print the traffic summary of $out
# Run: the UE attaches on the source RAT (lte2ntn: LTE, ntn2lte: NR), traffic, T_PRE s, then by STAGE:
#   base     no change
#   trigger  the source cell fades out (gain ramp to -60 dB), nothing else: OAI has no inter-RAT handover command
#   access   radio loss: the source UE process is killed (no detach) and the target one started at once; an NR target
#            gets its pre-compensation aids (initial Doppler and drift) but a GNSS position 500 km off
#   session  as access, with a valid GNSS position
# then T_POST s. The eNB and the gNB run from the start, as two satellites in view. events.txt: the change, the target
# UE IP and its probe from the UPF. summary.txt, relative to the change: the source IP traffic after it, the radio link
# failure of an NR source (trigger), the target access steps (UE log), the target IP and the old session at the SMF.
# One directory per run under $PCAP_DIR/<KIND>-<lte2ntn|ntn2lte>-<trans|regen>-<stage>-<run>-<time>, then a tarball
# per invocation. Captures: core (S1AP/NGAP/GTP/PFCP/GTPv2), mac (OPT MAC PDUs, first 300 B), sgi (UPF ogstun),
# ue (xr-ue netns).
source "$(dirname "$0")/ntn.sh"
repo=$(cd ../../.. && pwd)
mode=trans; [ -n "$REGEN" ] && mode=regen
export OPT=${OPT---opt.type wireshark --opt.ip 172.22.0.1}
PCAP_DIR=${PCAP_DIR:-$repo/build/pcap-xrat}
STAGE=${STAGE:-base} T_PRE=${T_PRE:-10} T_POST=${T_POST:-60}
core_if=br-$(docker network inspect -f '{{.Id}}' docker_open5gs_default | cut -c1-12)
radio='not tcp port 4043 and not udp port 9999 and not tcp port 9090'
GNSS_ERR=500000  # m, the access stage GNSS position error (x)
NR_STEPS=("sync|Initial sync successful" "sib19|Found SIB19" "rar|Found RAR with the intended" \
  "rrc|State = NR_RRC_CONNECTED" "registered|Registration Accept" "ip|PDU Session Establishment Accept")
LTE_STEPS=("sync|Got synch" "rar|Received RAR" "rrc|State = RRC_CONNECTED" "ip|Send Attach Complete")
# radio link failure handling of an NR source (the OAI LTE UE logs none)
NR_LOSS=("T310|Timer T310 expired" "T311|T311 expired" "released|NR_NAS_CONN_RELEASE_IND")

dc() { docker compose -p xrat -f xrat.yaml "$@"; }
reset() { dc down 2>/dev/null || true
  docker rm -f xr-ue lte-enb1 ntn-gnb1 lte-ue lte-enb2 nr-ue ntn-gnb2 ntn-gnb3 >/dev/null 2>&1 || true; }
ev() { echo "$(date +%s.%3N) $*" | tee -a $out/events.txt; }
# cap <name> <net> <iface> <filter> [snaplen]
cap() { docker run -d --rm --name cap-$1 --net $2 --cap-add NET_ADMIN --cap-add NET_RAW -v $out:/cap \
  --entrypoint tcpdump docker_open5gs -Z root -U -s ${5:-0} -i $3 -w /cap/$1.pcap "$4" >/dev/null; }
uncap() { for c in "$@"; do docker kill -s INT cap-$c >/dev/null 2>&1 || true; done; }
uelog() { docker exec xr-ue cat /tmp/$1.log 2>/dev/null || true; }
ue_ip() { docker exec xr-ue ip -br -4 addr show oaitun_ue1 2>/dev/null | awk '{print $3}' | cut -d/ -f1; }
wait_ip() { for i in $(seq ${1:-60}); do ip=$(ue_ip); [ -n "$ip" ] && return; sleep 1; done; ip=; return 1; }
# wait_target <s>: the target UE log has its last access step (the old tun interface may outlive the killed process)
wait_target() { local p=${NR_STEPS[-1]#*|}; [ $tgt = lte ] && p=${LTE_STEPS[-1]#*|}
  for i in $(seq $(( $1 * 5 ))); do docker exec xr-ue grep -q "$p" /tmp/$tgt.log 2>/dev/null && { sleep 1; wait_ip 5; return; }
    sleep 0.2; done; return 1; }

# the UE processes in xr-ue, log in /tmp/<lte|nr>.log; nr_up <GNSS x error (m)>
lte_up() { docker exec -d xr-ue bash -c 'mkdir -p /tmp/lte && cd /tmp/lte &&
  /build-lte/conf2uedata -c $CONF/lteue.usim-open5gs.conf -o . > /dev/null &&
  exec /build-lte/lte-uesoftmodem -O $CONF/lteue.rfsim.conf --rfsim --rfsimulator.[0].serveraddr server \
  --rfsimulator.[0].sync_clients 1 -C 2680000000 -r 25 --ue-rxgain 140 --ue-txgain 120 \
  --log_config.global_log_options level,nocolor,time $OPT > /tmp/lte.log 2>&1'; }
nr_up() { docker exec -d xr-ue bash -c "exec /build/nr-uesoftmodem -O \$CONF/nrue.uicc.ntn-leo.conf --rfsim \
  --rfsimulator.[0].serveraddr \$RFSIM \$PROP_DELAY \$CHAN --channelmod.modellist_rfsimu_1.[0].sat_trace_file \$TRACE0 \
  --position0.x $(awk "BEGIN { printf \"%.1f\", -3310204.0 + $1 }") --position0.y -5006785.1 --position0.z 2157610.7 \
  --uicc0.imsi 001010000000001 --uicc0.key 465B5CE8B199B49FAA5F0A2EE238A6BC \
  --uicc0.opc E8ED289DEBA952E4283B54E88E6183CA --uicc0.pdu_sessions.[0].dnn internet \
  --time-sync-I 0.1 --ntn-initial-time-drift ${UE_DRIFT:--46} --initial-fo ${UE_FO:-56634} \
  --cont-fo-comp ${UE_FO_COMP:-2} \$LOG \$OPT > /tmp/nr.log 2>&1"; }
ue_kill() { docker exec xr-ue pkill -9 -f $1-uesoftmodem || true; }
# source cell fade-out, 1 s per step: eNB beam gain, NR path loss of both directions (the UE is the rfsim client of
# the gNB: its channel model 0, the gNB's 1)
fade() { for g in -5 -10 -20 -30 -40 -60; do
    if [ $src = lte ]; then tn lte-enb1 "rfsimu setbeamgains $g" >/dev/null
    else tn xr-ue "channelmod modify 0 ploss $g" 0.2 >/dev/null; tn ntn-gnb1 "channelmod modify 1 ploss $g" 0.2 >/dev/null; fi
    ev "source gain $g dB"; sleep 1; done; }

attach_source() { reset; amf_restart; dc up -d xr-ue ntn-gnb1 2>/dev/null
  wait_log ntn-gnb1 'Received NGSetupResponse'
  if [ $src = lte ]; then lte_up; sleep 3; else nr_up 0; fi
  dc up -d lte-enb1 2>/dev/null
  wait_ip 90 || { echo "no source UE IP"; return 1; }; }

# epoch s of a monotonic log time stamp (the softmodem log time is CLOCK_MONOTONIC = /proc/uptime)
off() { awk -v e=$(date +%s.%N) '{ printf "%.6f", e - $1 }' /proc/uptime; }
# steps <array> <rat>: first time of each step in the UE log of <rat>, relative to the change
steps() { local o=$(off) s; local -n st=$1
  for s in "${st[@]}"; do
    awk -v o=$o -v c=$t_change -v n="${s%%|*}" -v p="${s#*|}" 'index($0, p) && $1 ~ /^[0-9.]+$/ {
      printf "%s +%.3f s  ", n, $1 + o - c; f = 1; exit } END { if (!f) printf "%s -  ", n }' $out/logs/xr-ue-$2.log
  done; echo; }
# packets of the source IP traffic at the receiver after the change (2 s grace for the ones in flight)
after_change() { docker run --rm -v $out:/cap --entrypoint tcpdump docker_open5gs -r /cap/$rx -tt -nq \
    "($data) and host $ip0" 2>/dev/null |
  awk -v c=$t_change '$1 > c + 2 { n++; l = $1 } END { if (n) printf "%d packets, last at +%.1f s\n", n, l - c
    else print "none" }'; }

finish() { traffic_stop; sleep 1
  uncap core mac sgi ue; mkdir -p $out/logs
  for n in lte-enb1 ntn-gnb1; do docker logs $n > $out/logs/$n.log 2>&1 || true; done
  for r in lte nr; do uelog $r > $out/logs/xr-ue-$r.log; done
  { echo "$dir $mode $STAGE: source $src IP $ip0"
    if [ -n "$t_change" ]; then
      echo "source IP traffic after the change: $(after_change)"
      [ $STAGE != trigger ] || [ $src != nr ] || echo "source nr radio link failure: $(steps NR_LOSS nr)"
      if [ -n "$tgt" ]; then
        local n; [ $tgt = nr ] && n=NR_STEPS || n=LTE_STEPS
        echo "target $tgt steps: $(steps $n $tgt)"
        echo "target IP ${ip1:-none}${ip1:+, $([ "$ip1" = "$ip0" ] && echo same || echo different), UPF probe $probe}"
        echo "source session at the SMF: $(docker logs --since ${t_change%.*} smf 2>&1 | grep -q "Removed Session.*IPv4:\[$ip0\]" &&
          echo removed || echo kept)"
      fi
    fi
    report; } | tee $out/summary.txt
  docker run --rm -v $out:/cap --entrypoint chown docker_open5gs -R $(id -u):$(id -g) /cap; }

run() { local r=$1; t_change= tgt= ip1= probe=
  attach_source || return 1
  out=$PCAP_DIR/$KIND-$dir-$mode-$STAGE-$r-$(date +%m%d-%H%M%S); mkdir -p $out; runs+=" ${out##*/}"; echo "## $out"
  cap core host $core_if 'not tcp port 4043 and not udp port 9999' $SNAP
  cap mac host $core_if 'udp port 9999' 300; cap sgi container:upf ogstun "$SGI_FILTER" $SNAP
  cap ue container:xr-ue any "$radio" $SNAP; sleep 1
  ip0=$ip; ev "UE IP $ip ($src)"; traffic; sleep $T_PRE
  case $STAGE in
    trigger) t_change=$(date +%s.%N); ev "change: source fade-out"; fade ;;
    access|session) tgt=nr; [ $src = nr ] && tgt=lte
      t_change=$(date +%s.%N); ev "change: kill the $src UE, start the $tgt UE"; ue_kill $src
      if [ $tgt = lte ]; then lte_up; elif [ $STAGE = access ]; then nr_up $GNSS_ERR; else nr_up 0; fi ;;
  esac
  if [ -n "$tgt" ]; then
    if wait_target $T_POST; then ip1=$ip; ev "target UE IP $ip1"
      probe=$(docker exec upf ping -c 5 -i 0.2 -W 2 $ip1 | awk '/transmitted/ { print $4 "/" $1 }'); ev "UPF probe $probe"
    fi
    sleep $(awk -v e=$(date +%s.%N) -v c=$t_change -v p=$T_POST 'BEGIN { d = c + p - e; print (d > 0 ? d : 0) }')
  else sleep $T_POST; fi
  finish; }

usage() { sed -n '2,/^$/p' $0; exit 1; }
case $1 in lte2ntn) src=lte ;; ntn2lte) src=nr ;; *) usage ;; esac
case $STAGE in base|trigger|access|session) ;; *) usage ;; esac
dir=$1
for r in $(seq ${2:-1}); do run $r || echo "run $r: no source attach"; done
reset
[ -z "$runs" ] || tar czf $PCAP_DIR/xrat-$KIND-$dir-$mode-$STAGE-$(date +%m%d-%H%M%S).tar.gz -C $PCAP_DIR $runs
