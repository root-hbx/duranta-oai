#!/bin/bash
# NR NTN conditional handover (Xn, condEventT1 + condEventA3) between the two satellites of TWO_SAT=1, with a ping running
#   [REGEN=1] ntn_cho_loop.sh [n]   n runs (default 10), one handover from PCI 0 (sat 0) to PCI 1 (sat 1) per run
#   THREE_SAT=1 [TARGET=1|2]  two candidates, PCI 1 (sat 1) and PCI 2 (sat 2, ntn.sh): the beam ramp makes TARGET
#                 (default 1) the strongest, the other candidate OTHER_GAP dB (default 3) below it; the source cancels the
#                 other candidate at its gNB after the execution
# Trace time line (s, tools/ntn/calc_service_windows.py: sat 1 rises at 120 s, sat 0 sets at 182 s):
#   PREP (default 100)  gNB1 prepares the CHO (ci trigger_cho)
#   T1, T1_DUR (120, 30)  condEventT1 window
#   BEAM, RAMP (122, 10)  the UE moves from the sat 0 beam to the target one in RAMP s: the gains of the cells cross in
#                 STEP dB steps (default 4), from the target NBR_ATT dB weaker to sat 0 NBR_ATT dB weaker; condEventA3 follows
#   REPLACE  if set, the source replaces the prepared candidate then (ci replace_cho), with the T1 window
#     REPLACE_T1="<start>,<duration>" (s) if set
#   END (165)  last check
# CHO recovery (attemptCondReconfig) after a radio link failure in the source: no condEventA3 before it (15 dB, 5.12 s)
#   GNB1_EXTRA="--gNBs.[0].nr_cho_configuration.attempt_cond_reconfig 1 --gNBs.[0].nr_cho_configuration.a3_offset 30
#   --gNBs.[0].nr_cho_configuration.a3_time_to_trigger 15" BEAM=130 RAMP=1 STEP=60 NBR_ATT=60 END=150 ntn_cho_loop.sh
# Output under build/pcap-ntn-cho/<trans|regen>-<time>: results.txt (one line per run), logs/<run>/, summary.txt.
# results.txt: run, OK|FAIL, trace time of the execution (s), interruption (ms, CHO execution at the UE to the
# RRCReconfigurationComplete at the target), largest ping gap (s), max predicted Common TA error of the target (us),
# with THREE_SAT the PCI the UE executed and whether the other candidate was cancelled (cancel|-), then the PRACH
# attempts of the UE towards the executed cell and the arrival delay of the first preamble its gNB detected (ns,
# negative if early)
export TWO_SAT=1 HO_IF=xn
source "$(dirname "$0")/ntn.sh"
cd "$(dirname "$0")"
repo=$(cd ../../.. && pwd)
mode=trans; [ -n "$REGEN" ] && mode=regen
out=$repo/build/pcap-ntn-cho/$mode-$(date +%m%d-%H%M%S); mkdir -p $out
N=${1:-10}
cho_setup
replace_arg=1
TARGET=${TARGET:-1} cands=1 other=
[ -n "$THREE_SAT" ] && cands=1,2 other=$((3 - TARGET))
tgt=ntn-gnb$((TARGET + 1)) gnbs="ntn-gnb1 ntn-gnb2${THREE_SAT:+ ntn-gnb3}"
if [ -n "$REPLACE_T1" ]; then replace_arg="1,$(t1_threshold ${REPLACE_T1%,*}),$(( ${REPLACE_T1#*,} * 10 ))"; fi

for k in $(seq $N); do
  mkdir -p $out/logs/$k
  for i in 1 2 3; do ./ntn.sh connected > $out/logs/$k/connected.txt 2>&1 && break; done
  docker exec -d nr-ue sh -c "ping -D -I oaitun_ue1 -i 0.2 -W 1 192.168.96.1 > /tmp/ping.txt"
  wait_trace $PREP; tn ntn-gnb1 "ci trigger_cho 1,$cands" > /dev/null
  if [ -n "$REPLACE" ]; then wait_trace $REPLACE; tn ntn-gnb1 "ci replace_cho $replace_arg" > /dev/null; fi
  cho_ramp $TARGET $other
  wait_trace $END
  for n in nr-ue $gnbs; do docker logs $n > $out/logs/$k/$n.log 2>&1 || true; done
  docker ps -a --format '{{.Names}} {{.Status}}' | grep -E 'nr-ue|ntn-gnb' > $out/logs/$k/status.txt || true
  docker exec nr-ue cat /tmp/ping.txt > $out/logs/$k/ping.txt 2>/dev/null || true

  exe=$(grep -m1 'CHO: execute candidate' $out/logs/$k/nr-ue.log || true)
  done_=$(grep -m1 'Xn handover complete' $out/logs/$k/$tgt.log || true)
  res=FAIL t_exe=- intr=- pci=$(sed -n 's/.*CHO: execute candidate [0-9]*, PCI \([0-9]*\).*/\1/p' <<< "$exe") cancel=-
  [ -n "$other" ] && grep -q 'Xn handover cancelled by the source' $out/logs/$k/ntn-gnb$((other + 1)).log && cancel=cancel
  replaced=y
  [ -n "$REPLACE" ] && ! grep -q 'conditional handover modification' $out/logs/$k/ntn-gnb1.log && replaced=
  if [ -n "$exe" ] && [ -n "$done_" ] && [ -n "$replaced" ] && { [ -z "$other" ] || [ $cancel = cancel ]; }; then
    res=OK
    t_exe=$(cho_exe_time "$exe") intr=$(cho_interruption "$exe" "$done_")
  fi
  gap=$(grep 'bytes from' $out/logs/$k/ping.txt | grep -o '^\[[0-9.]*\]' | tr -d '[]' |
    awk 'NR > 1 && $1 - p > g { g = $1 - p } { p = $1 } END { printf "%.1f", g }')
  ta=$({ grep -o 'Common TA prediction.*max error [0-9.]*' $out/logs/$k/$tgt.log || true; } | tail -1 | awk '{ print $NF }')
  prach=- delay=-
  if [ -n "$exe" ]; then
    t0=$(wall "$exe") exe_gnb=$out/logs/$k/ntn-gnb$(( ${pci:-$TARGET} + 1 )).log
    prach=$(awk -v t0=$t0 '$1 + 0 > t0 && /PRACH \[UE/ { n++ } $1 + 0 > t0 && /Found RAR with the intended RAPID|T304 expired/ { exit }
      END { print n + 0 }' $out/logs/$k/nr-ue.log)
    delay=$({ grep 'Initiating RA procedure' $exe_gnb || true; } | awk -v t0=$t0 '$1 + 0 > t0' |
      sed -n '1s/.*(\(-*[0-9]*\) ns).*/\1/p')
  fi
  echo "$k $res ${t_exe} ${intr} ${gap} ${ta:--}${other:+ ${pci:--} $cancel} $prach ${delay:--}" | tee -a $out/results.txt
done
reset

{ echo "$mode: $(grep -c ' OK ' $out/results.txt)/$N CHO OK (${other:+candidates PCI 1 and 2, target $TARGET, }PREP $PREP s, T1 $T1-$((T1 + T1_DUR)) s, BEAM $BEAM s + $RAMP s${REPLACE:+, REPLACE $REPLACE s ${REPLACE_T1:+T1 $REPLACE_T1}})"
  awk '$2 == "OK" { n++; t += $3; i += $4; if ($4 > im) im = $4 } { g += $5; if ($5 > gm) gm = $5 }
    END { if (n) printf "execution at %.1f s, interruption mean %.0f ms max %.0f ms\n", t / n, i / n, im
      printf "largest ping gap mean %.1f s max %.1f s\n", g / NR, gm }' $out/results.txt
} | tee $out/summary.txt
