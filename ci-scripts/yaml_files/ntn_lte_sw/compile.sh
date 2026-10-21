#!/bin/bash
# The binaries of the ntn_lte_sw harnesses, built with ninja in the ntn-dev image: LTE in build-lte (lte-softmodem,
# lte-uesoftmodem, conf2uedata), NR in build (nr-softmodem, nr-uesoftmodem), each with all the shared libraries the
# softmodems load at run time (rfsimulator, telnetsrv, params_libconfig, ldpc, ...).
#   [CLEAN=1] compile.sh lte|nr|all     CLEAN=1: ninja -t clean first, a build from scratch

self=$(realpath "$0")
cd "$(dirname "$self")"
repo=$(cd ../../.. && pwd)
set -o pipefail
# ninja_in <build dir> <cpm cache> <targets...>
ninja_in() { local dir=$1 cpm=$2; shift 2; echo "### $(date +%T) $dir: ${CLEAN:+clean, }ninja $*"
  docker run --rm -v $repo:/oai-ran:ro -v $repo/$dir:/build -v $cpm:/cpm -w /build ntn-dev \
    sh -c "${CLEAN:+ninja -t clean >/dev/null && }ninja $* \$(ninja -t targets all | grep -oE '^lib[^:/]+[.]so' | sort -u)" \
    2>&1 | tail -3; }
case "$1" in
  lte) ninja_in build-lte $repo/build-lte/cpm lte-softmodem lte-uesoftmodem conf2uedata ;;
  nr) ninja_in build $HOME/.cache/cpm nr-softmodem nr-uesoftmodem ;;
  all) "$self" lte && "$self" nr ;;
  *) sed -n 5p "$self"; exit 1 ;;
esac
