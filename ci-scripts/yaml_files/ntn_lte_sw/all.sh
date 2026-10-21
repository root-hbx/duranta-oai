#!/bin/bash
# All LTE-LTE and NTN-NTN experiments from scratch: clean build, LTE X2 batch, NTN CHO and N2 batches (log: run.log
# in their result directories), figures. Stops if the build fails; the final rc is the number of failed runs.
#   [RUNS=3] all.sh

cd "$(dirname "$0")"
repo=$(cd ../../.. && pwd)
rc=0
step() { echo "### $(date '+%F %T') start $*"; "$@"; local r=$?; echo "### $(date '+%F %T') end rc=$r"; rc=$((rc + r))
  return $r; }
# batch <result dir> <script...>
batch() { local dir=$1; shift; mkdir -p $dir; "$@" > $dir/run.log 2>&1; }
echo "### $(git branch --show-current) $(git rev-parse --short HEAD)"; git status --short
step env CLEAN=1 ./compile.sh all || { echo "DONE rc=$rc"; exit $rc; }
step batch $repo/build-lte/pcap-lte-ho ./lte_batch.sh
step batch $repo/build/pcap-ntn-cho ./ntn_batch.sh cho
step batch $repo/build/pcap-ntn-n2ho ./ntn_batch.sh connected
step ./plot.sh
echo "DONE rc=$rc"
