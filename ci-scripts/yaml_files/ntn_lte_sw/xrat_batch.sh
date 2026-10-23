#!/bin/bash
# LTE <-> NR NTN RAT change batch: TRANS and REGEN, each lte2ntn and ntn2lte, each stage base, trigger, access and
# session, each TCP dl and ping (xrat_tcp.sh, xrat_ping.sh), RUNS runs, under PCAP_DIR, then PCAP_DIR/report.txt with
# one line per run.
#   [RUNS=3] [PCAP_DIR=build/pcap-xrat] [MODES="trans regen"] [STAGES="base trigger access session"] [TYPES="tcp ping"]
#   xrat_batch.sh      STAGES may also name redirect (xrat.sh)
# Exit status: the number of failed invocations.

cd "$(dirname "$0")"
repo=$(cd ../../.. && pwd)
export PCAP_DIR=${PCAP_DIR:-$repo/build/pcap-xrat} RUNS=${RUNS:-3} fail=0
step() { echo "### $(date +%T) $*"; env "$@" || fail=$((fail + 1)); }

# one line per run from its summary.txt: source traffic after the change, NR source failure, target steps and IP,
# old session, receiver goodput / ping loss
report() { for d in $(ls -d $PCAP_DIR/*-[0-9]-*/ | sort); do
  awk -v run=$(basename $d) '/^source IP traffic/ { sub(/.*change: /, ""); after = $0 }
    /^source nr radio link failure/ { sub(/.*failure: /, ""); rlf = " | src rlf " $0 }
    /^source redirection/ { sub(/.*redirection: /, ""); rlf = " | src redirect " $0 }
    /^target .* steps/ { sub(/^target /, ""); tgt = " | " $0 }
    /^target IP/ { sub(/^target IP /, ""); ip = " | ip " $0 }
    /^source session/ { sess = " | old session " $NF }
    /receiver/ && !/ 0.00 bits/ { mbps = " | " $7 " Mbps" }
    /packet loss/ { for (i = 1; i <= NF; i++) if ($i ~ /%$/) loss = " | ping loss " $i }
    END { printf "%-44s after %s%s%s%s%s%s%s\n", run, after ? after : "-", rlf, tgt, ip, sess, mbps, loss }' $d/summary.txt
done; }

for mode in ${MODES:-trans regen}; do
  regen=; [ $mode = regen ] && regen=1
  for d in lte2ntn ntn2lte; do
    for s in ${STAGES:-base trigger access session}; do
      for t in ${TYPES:-tcp ping}; do step REGEN=$regen STAGE=$s ./xrat_$t.sh $d $RUNS; done
    done
  done
done
docker rm -f cap-ue cap-sgi cap-mac cap-core >/dev/null 2>&1
report | tee $PCAP_DIR/report.txt
exit $fail
