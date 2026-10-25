#!/bin/bash
# The figures of moti_exp/ (PDFs in its figs/ directories):
#   legacy/  the survey plots: LTE X2 from build-lte/pcap-lte-ho, NTN CHO and N2 from build/pcap-ntn-cho and
#            build/pcap-ntn-n2ho, LTE <-> NTN RAT changes from build/pcap-xrat, the four kinds compared (cmp) from the
#            cmp_batch.sh directories
#   paper/   the paper figures from the paper_batch.sh directories: perf/ (phenomena), analysis/ (causes)
#   [PY=<python with matplotlib>] [PARTS="legacy paper"] plot.sh
# Exit status: the number of failed scripts.

cd "$(dirname "$0")/../../.."
PY=${PY:-$HOME/miniconda3/envs/sat-env/bin/python} fail=0
py() { echo "### $(date +%T) $1"; $PY $1 || fail=$((fail + 1)); }
if [[ ${PARTS:-legacy paper} == *legacy* ]]; then
  for d in lte-lte/x2ho ntn-ntn/cho ntn-ntn/n2ho lte-ntn ntn-lte cmp; do
    for f in bw rtt; do py moti_exp/legacy/$d/$f.py; done
  done
fi
if [[ ${PARTS:-legacy paper} == *paper* ]]; then
  for f in perf/bw perf/rtt analysis/anchor analysis/gnss analysis/breakdown; do py moti_exp/paper/$f.py; done
fi
exit $fail
