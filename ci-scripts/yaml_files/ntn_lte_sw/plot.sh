#!/bin/bash
# The figures of plot/ (PDFs in its figs/ directories): LTE X2 from build-lte/pcap-lte-ho, NTN CHO and N2 from
# build/pcap-ntn-cho and build/pcap-ntn-n2ho, LTE <-> NTN RAT changes from build/pcap-xrat,
# the four kinds compared (plot/cmp) from the cmp_batch.sh directories.
#   [PY=<python with matplotlib>] plot.sh
# Exit status: the number of failed scripts.

cd "$(dirname "$0")/../../.."
PY=${PY:-$HOME/miniconda3/envs/sat-env/bin/python} fail=0
for d in plot/lte-lte/x2ho plot/ntn-ntn/cho plot/ntn-ntn/n2ho plot/lte-ntn plot/ntn-lte plot/cmp; do
  for f in bw rtt; do echo "### $(date +%T) $d/$f.py"; $PY $d/$f.py || fail=$((fail + 1)); done
done
exit $fail
