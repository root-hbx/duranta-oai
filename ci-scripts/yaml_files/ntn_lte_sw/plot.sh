#!/bin/bash
# The figures of plot/ (PDFs in plot/*/*/figs): LTE X2 from build-lte/pcap-lte-ho, NTN CHO and N2 from
# build/pcap-ntn-cho and build/pcap-ntn-n2ho.
#   [PY=<python with matplotlib>] plot.sh
# Exit status: the number of failed scripts.

cd "$(dirname "$0")/../../.."
PY=${PY:-$HOME/miniconda3/envs/sat-env/bin/python} fail=0
for d in plot/lte-lte/x2ho plot/ntn-ntn/cho plot/ntn-ntn/n2ho; do
  for f in bw rtt; do echo "### $(date +%T) $d/$f.py"; $PY $d/$f.py || fail=$((fail + 1)); done
done
exit $fail
