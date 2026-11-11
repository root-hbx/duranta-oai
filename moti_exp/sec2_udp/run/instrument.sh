# Capture the shared xrat run from registration through cleanup.
sec2_cores=(amf mme smf upf sgwc sgwu ausf udm udr nrf nssf bsf pcf scp hss pcrf mongo)
sec2_cells=(xr-ue lte-enb1 ntn-gnb1 ntn-gnb2)
sec2_caps=() sec2_generation=0 sec2_started=0
out=$SEC2_RUN_DIR

sec2_clock() { "$SEC2_PYTHON" "$SEC2_MODULE/run/clock.py" "$out/clocks.jsonl" "$1"; }
ev() { echo "$(date +%s.%N) $*" | tee -a "$out/events.txt"; }
sec2_processes() {
  docker exec xr-ue sh -c 'date +%s.%N; getconf CLK_TCK; ps -eo pid,ppid,lstart,comm;
    for p in /proc/[0-9]*; do c=$(cat "$p/comm" 2>/dev/null); case "$c" in *softmodem*|iperf3)
      printf "process %s %s\n" "$p" "$c"; cat "$p/stat"; readlink "$p/exe";; esac; done' \
    > "$out/logs/processes-$1.txt" 2>&1 || true
}
dc() {
  docker compose --project-directory "$repo/ci-scripts/yaml_files/ntn_lte_sw" -p sec2_udp \
    -f "$repo/ci-scripts/yaml_files/ntn_lte_sw/xrat.yaml" -f "$SEC2_MODULE/run/compose.yaml" "$@" || return 1
  # Capture the first UE access messages, before the softmodem processes are started.
  if [ "$1" = up ] && [[ " $* " == *" xr-ue "* ]]; then cap ue container:xr-ue any "$radio"; fi
}
sec2_owned() {
  [ "$(docker inspect -f '{{index .Config.Labels "org.duranta.sec2"}}' "$1" 2>/dev/null)" = "$SEC2_RUN_ID" ]
}
sec2_archive() {
  local dest=$out/logs/generation-$sec2_generation
  mkdir -p "$dest"
  for c in "${sec2_cells[@]}"; do
    sec2_owned "$c" || continue
    docker logs --timestamps "$c" > "$dest/$c.docker.log" 2>&1 || true
  done
  if sec2_owned xr-ue; then
    for r in lte nr lte-source nr-source; do
      docker exec xr-ue cat /tmp/$r.log > "$dest/xr-ue-$r.log" 2>/dev/null || true
    done
    docker exec xr-ue sh -c 'date +%s.%N; cat /proc/uptime; ip -s -d addr; ip route; ss -tunap' \
      > "$dest/network.txt" 2>&1 || true
  fi
  sec2_generation=$((sec2_generation + 1))
}
reset() {
  sec2_archive
  for c in "${sec2_cells[@]}"; do
    if sec2_owned "$c"; then
      ev "cleanup remove $c"
      docker rm -f "$c" >/dev/null
    fi
  done
}
cap() {
  local name=sec2-cap-$1 cpus=()
  if docker inspect "$name" >/dev/null 2>&1; then sec2_owned "$name"; return; fi
  [ -z "$CAPTURE_CPUS" ] || cpus=(--cpuset-cpus "$CAPTURE_CPUS")
  docker run -d --name "$name" --label "org.duranta.sec2=$SEC2_RUN_ID" "${cpus[@]}" --net "$2" \
    --cap-add NET_ADMIN --cap-add NET_RAW -v "$out:/cap" --entrypoint tcpdump "$CORE_IMAGE" \
    -Z root -U -B 131072 -s 0 -i "$3" -w "/cap/$1.pcap" "$4" >/dev/null || return 1
  sec2_caps+=("$name")
  if [ "$1" = ue ]; then
    docker exec xr-ue sh -c 'printf "timezone "; date +%z; exec stdbuf -oL ip -ts monitor all' \
      > "$out/logs/netlink-xr-ue.log" 2>&1 &
    sec2_netlink_pid=$!
  fi
}
# finish() leaves captures running until all final logs have been collected.
uncap() { :; }
sec2_uncap() {
  for c in "${sec2_caps[@]}"; do
    sec2_owned "$c" || continue
    docker kill -s INT "$c" >/dev/null 2>&1 || true
  done
  for c in "${sec2_caps[@]}"; do
    sec2_owned "$c" || continue
    timeout 10 docker wait "$c" > "$out/logs/$c.exit.txt" 2>&1 || true
    docker logs --timestamps "$c" > "$out/logs/$c.log" 2>&1 || true
    docker rm -f "$c" >/dev/null
  done
}
sec2_begin() {
  export SEC2_TRACE_DIR=${trace_dir:-$SEC2_RUN_DIR/trace}
  mkdir -p "$SEC2_TRACE_DIR"
  if [ "$src" = lte ]; then
    export SAT_TRACE1=../../../build/xrat-trace/$(basename "$SAT_TRACE1")
    export SAT_TRACE2=../../../build/xrat-trace/$(basename "$SAT_TRACE2")
  fi
  for c in "${sec2_cells[@]}" sec2-cap-core sec2-cap-mac sec2-cap-sgi sec2-cap-ue; do
    if docker inspect "$c" >/dev/null 2>&1; then
      echo "Container already exists: $c" >&2
      return 1
    fi
  done
  sec2_started=1
  mkdir -p "$out/logs" "$out/work"
  for c in "${sec2_cells[@]}"; do mkdir -p "$out/work/$c"; done
  sec2_since=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  ev "run begin direction=$dir stage=$STAGE gap=${GAP:-0} protocol=$PROTO rate=$RATE"
  sec2_clock begin
  declare -p CORE REGEN TWO_SAT IWK PROTO RATE DATAGRAM STAGE T_START T_CHANGE T_RUN \
    NR_BUILD LTE_BUILD SAT_TRACE1 SAT_TRACE2 UE_FO UE_DRIFT UE_FO_COMP UE_GNSS \
    GNB1_EXTRA GNB2_EXTRA UE_EXTRA ENB1_EXTRA > "$out/parameters.env"
  printf 'GAP=%q\nCAPTURE_CPUS=%q\n' "$GAP" "$CAPTURE_CPUS" >> "$out/parameters.env"
  cap core host "$core_if" 'not tcp port 4043 and not tcp port 4044 and not udp port 9999' || return 1
  cap mac host "$core_if" 'udp port 9999' || return 1
  cap sgi "container:${CORE_CT}upf" ogstun "$SGI_FILTER" || return 1
  dc config > "$out/compose.resolved.yaml" || return 1
  docker exec "${CORE_CT}upf" iperf3 --version > "$out/iperf-version.txt" 2>&1
}

# Add wall-clock markers around the existing control functions.
for f in run amf_restart lte_up nr_up ue_kill new_cells wait_redirect wait_target; do
  eval "$(declare -f "$f" | sed "1s/^$f /sec2_original_$f /")"
done
run() {
  sec2_begin || exit 1
  local rc=0
  sec2_original_run "$@" || rc=$?
  exit "$rc"
}
amf_restart() {
  ev 'amf restart begin'; sec2_clock before-amf
  sec2_original_amf_restart; local rc=$?
  ev "amf restart end rc=$rc"; sec2_clock after-amf
  return "$rc"
}
lte_up() { ev 'lte process start begin'; sec2_original_lte_up "$@"; local rc=$?; ev "lte process start end rc=$rc"; return "$rc"; }
nr_up() { ev 'nr process start begin'; sec2_original_nr_up "$@"; local rc=$?; ev "nr process start end rc=$rc"; return "$rc"; }
ue_kill() { ev "$1 process kill begin"; sec2_original_ue_kill "$@"; ev "$1 process kill end"; }
new_cells() { ev 'same-RAT cells recreate begin'; sec2_archive; sec2_original_new_cells; ev 'same-RAT cells recreate end'; }
wait_redirect() { sec2_original_wait_redirect "$@"; local rc=$?; ev "wait redirect end rc=$rc"; return "$rc"; }
wait_target() {
  sec2_original_wait_target "$@"; local rc=$?
  ev "wait target end rc=$rc"; sec2_processes target
  return "$rc"
}
wait_utrace() {
  local end=$(( $(date +%s) + 240 )) t
  while [ "$(date +%s)" -lt "$end" ]; do
    t=$(utrace)
    if [[ "$t" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk "BEGIN { exit !($t >= $1) }"; then return 0; fi
    sleep 0.2
  done
  ev "trace timeout target=$1"; return 1
}
sec2_exit() {
  local rc=$?
  trap - EXIT INT TERM
  [ "$sec2_started" = 1 ] || exit "$rc"
  set +e
  ev "attempt exit rc=$rc"
  traffic_stop
  reset
  [ -z "${sec2_netlink_pid:-}" ] || wait "$sec2_netlink_pid"
  sec2_uncap
  for c in "${sec2_cores[@]}"; do
    docker logs --timestamps --since "$sec2_since" "${CORE_CT}$c" > "$out/logs/${CORE_CT}$c.log" 2>&1
  done
  sec2_clock end
  echo "$rc" > "$out/exit-code.txt"
  exit "$rc"
}
trap sec2_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
