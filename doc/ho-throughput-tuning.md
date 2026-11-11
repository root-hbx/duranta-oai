<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LTE–NTN throughput profiles

The tuned profile uses the existing A2 release/redirection procedure and IWK session request in both directions.
One UE container keeps its application socket while its LTE and NR modem processes run in sequence.
The same source and build support TRANS/REGEN, DL/UL and LTE→NTN/NTN→LTE: eight cases.
CHO retains its existing protocol implementation; the experiments here exercise ordinary cross-RAT redirection.

## Source changes

The NR UE fix separates system-information and user-data reception in
`openair1/PHY/defs_nr_UE.h`, `nr_init_ue.c`, `fapi_nr_ue_l1.c`, `phy_procedures_nr_ue.c`
and `nr_dlsch_decoding.c`. Each has its own configuration and HARQ context; SI decoding finishes before its
temporary LLR storage is reused. In `openair2/LAYER2/NR_MAC_UE/nr_ue_procedures.c`, SI uses a local transport-block
context and DCI 1_1 sets the data PDU type before deriving codeword/HARQ information.

These receive-state problems occur in the inherited OAI implementation. In particular,
[OAI e41766d7](https://github.com/openairinterface/openairinterface5g/commit/e41766d7f646ae52d8d9e130d6c3e75248b39de3)
introduced the two-codeword processing that reads the reused request entry's type before assigning DLSCH.
The intervening Duranta HO/CHO changes leave this receive callback and DCI ordering intact.
In the failing NTN capture, an SI grant with HARQ ID 0 and TBS 272 is followed in the same slot by a data grant
with HARQ ID 0 and TBS 24072; both reached the shared context. The fix separates these receive contexts and sets the DCI PDU type before consulting its codeword state.

RFsim keeps `rxAddInput()` as the default and adds `rxAddInputOptimized()` behind:

```text
--rfsimulator.[0].channelmod_optimized 1
```

The optimized function advances the Doppler rotation by complex multiplication and recalculates its phase every
256 samples. A single-transmitter, single-tap path removes the inner antenna/tap loops. Orbit interpolation,
propagation delay, SIB19, noise draws and the phase accumulator keep their original equations/order.
Selection is explicit for any bandwidth. Setting the option to `0`, or omitting it, selects the original function.
The tuned experiment profile enables it on all participating RFsim nodes, matching the tested configuration.

## Parameters

| Setting | Baseline profile | Tuned DL | Tuned UL |
|---|---:|---:|---:|
| LTE DL PRB | 25 | 100 | 100 |
| LTE UE receive gain | 140 | 128 | 128 |
| LTE `max_ul_rb_index` | 22 (48-RB cap) | 22 | 27 (72-RB cap) |
| NR DL/UL PRB, SCS | 25, 15 kHz | 52, 15 kHz | 52, 15 kHz |
| NR initial BWP RIV | original | 14025 | 14025 |
| NR UE carrier | original | 2490830000 Hz | 2490830000 Hz |
| NR capability | original fallback | n254, 10 MHz, one layer | same |
| NR DL/UL maximum MCS | 27/27 | 27/27 | 27/27 |
| NR DL 256-QAM / UL 256-QAM | original | enabled / disabled | same |
| NTN UL sync validity | 5 s | 5 s | 5 s |
| TCP application rate request | 100 Mbps | 100 Mbps | 100 Mbps |
| TCP socket pacing | original | 35 Mbps | 35 Mbps |
| TCP socket buffer request | original | original | 384 KiB |
| RFsim channel calculation | legacy | optimized | optimized |

LTE receive gain calibrates the bandwidth-dependent RSRP conversion used by the existing A2 trigger and power
control. RFsim beam/channel gains remain unchanged. The NR UE main thread is assigned a separate physical core
from its workers; select CPU IDs from the actual host topology at runtime. Host CPU IDs belong to the experiment site configuration.
The kernel may adjust the requested socket buffer; `ss` samples retain its effective values.

TRANS retains common TA 1208136, drift −209614, Koffset 27 and initial propagation delay 5 ms. REGEN retains
common TA/drift 0, Koffset 14 and propagation delay 3 ms. Doppler/drift aids follow the original trace calibration;
52-PRB initial frequency offset is scaled to its carrier. Scale the original initial frequency offset by 2490830000 / 2488400000.

## Recorded selection

The frozen November 3–4 campaign used the receive fix and optimized RFsim on the `d0039c84` source baseline.
Its selected profiles produced these mean receiver rates in fixed wall-time windows:

| Case | LTE Mbps | NTN Mbps |
|---|---:|---:|
| TRANS DL, LTE→NTN | 34.79 | 34.51 |
| TRANS DL, NTN→LTE | 35.00 | 33.39 |
| TRANS UL, LTE→NTN | 23.99 | 22.63 |
| TRANS UL, NTN→LTE | 23.99 | 22.86 |
| REGEN DL, LTE→NTN | 34.70 | 34.99 |
| REGEN DL, NTN→LTE | 34.99 | 34.96 |
| REGEN UL, LTE→NTN | 23.96 | 30.37 |
| REGEN UL, NTN→LTE | 23.57 | 31.59 |

The original 24 confirmation attempts yielded 23 complete measurements and 21 meeting all frozen criteria.
One additional, separately declared REGEN DL measurement supplied its third complete sample. The table keeps the
complete low-rate REGEN reverse-UL sample and the TRANS forward-DL sample with startup TBS warnings. NR simulated
time advanced about 1.22–1.32 seconds per wall second; LTE ranged about 0.51–0.85 depending on direction/load.
The rates describe these RFsim runs. This integration keeps the same selected parameters and adds an explicit
algorithm selector; its checks are recorded separately from the frozen campaign.

## Running the eight cases

The reusable settings are in
[`throughput.env`](../ci-scripts/yaml_files/ntn_lte_sw/throughput.env).
[`throughput.sh`](../ci-scripts/yaml_files/ntn_lte_sw/throughput.sh) selects those settings and calls the existing
`xrat_tcp.sh` / `xrat.sh` A2 redirection procedure. `xrat.yaml` extends the original LTE/NTN Compose files.
Future experiments can source the same parameter file after setting their orbit calibration and capability path.

### Prepare the existing experiment environment

Use the same environment as `xrat.sh`: the `ntn-dev` runtime image (or `OAI_IMG`), Docker Compose v2, and the
orbitango EPC/5GC selected by `CORE=private` in `core.sh`, with a matching LTE/NR subscription and UE data routes.
Coordinate an exclusive experiment slot: the original procedure initializes the AMF per run, uses fixed RAN
names/addresses and privileged containers, and captures on the host/core interfaces. The wrapper checks for
occupied RAN/capture names; subscription and core use are coordinated locally.

Build the current source with CMake/Ninja in that image, including the LTE/NR softmodems, `conf2uedata` and their
dynamic modules. Point `LTE_BUILD` and `NR_BUILD` to the matching build directories. Both may point to the same
build containing all five executables. `compile.sh` rebuilds the original, already configured `build`/`build-lte`
layout. The new runs mount binaries/modules read-only and keep runtime files inside the containers.

The measured inputs are included alongside the original files:

- `tools/ntn/data/ho_pair_sat0_900.csv` and `ho_pair_sat1_900.csv`: the original satellite pair extended to 900 s.
- `targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_n254_10mhz.xml`: n254, 15-kHz, 10-MHz, one layer, 256-QAM DL/64-QAM UL.

Runs read these files directly. LTE→NTN uses the original script's cut at simulated second 131, saving each
slice under a new `build/throughput-trace.*` directory. The original 360-second traces remain unchanged.

Select CPUs using `lscpu -e=CPU,CORE,SOCKET`. Set `UE_CPUS`, `NR_MAIN_CPUS` and `NR_WORKER_CPUS`; main and workers
use different physical cores inside `UE_CPUS`, leaving the main CPU's SMT sibling unused. Set `ENB_CPUS`,
`GNB1_CPUS` and `GNB2_CPUS` for the other reserved cores. `xrat.sh` applies the main/worker layout after NR access.
The original container CPU defaults are preserved for other experiments. Host-side plotting requires Matplotlib.

### Run one case or the matrix

From the repository root, with the environment above prepared:

```bash
# One TRANS downlink, LTE to NTN, one repetition.
bash ci-scripts/yaml_files/ntn_lte_sw/throughput.sh lte2ntn 1

# One REGEN uplink, NTN to LTE.
REGEN=1 TRAFFIC=UL bash ci-scripts/yaml_files/ntn_lte_sw/throughput.sh ntn2lte 1

# TRANS/REGEN × DL/UL × both directions, one repetition of each.
bash ci-scripts/yaml_files/ntn_lte_sw/throughput.sh all 1

# Same current source at the 25/25 PRB reference settings.
THROUGHPUT_PROFILE=baseline bash ci-scripts/yaml_files/ntn_lte_sw/throughput.sh all 1
```

`THROUGHPUT_PROFILE=tuned` is the default. `baseline` selects 25/25 PRB and the original RFsim function while
retaining the current NR UE correctness fix. The historical pre-fix baseline belongs to its original commit.
Use `PCAP_DIR` to select a result root; the default is `build/pcap-throughput`. The default DN is the UPF, as in
the original scripts; `DN_CONTAINER` and `DN_IP` select an independently prepared DN with working UE routes.
Traffic cleanup signals only this run's iperf PIDs. The batch is serial and stops on a failed case.

Each run preserves `parameters.env`, both iperf JSON logs (`iperf_ue.txt`, `iperf_core.txt`), `events.txt`,
modem logs, PCAPs, `summary.txt`, `thr_rx.csv` and `throughput.png` in a separate case directory.
The CSV preserves the 100-ms receiver intervals; the figure aggregates their bytes into 1-second wall-time bins,
as in the existing paper throughput plots. The summary's full-run mean includes the interruption.
DL receives at the UE; UL receives at the DN. Sender JSON retains TCP retransmission,
RTT and congestion-window fields provided by iperf. See the [iperf3 manual](https://software.es.net/iperf/invoking.html)
for its endpoint and interval semantics. `moti_exp/throughput/bw.py` also accepts a saved receiver log for offline
CSV/plot generation; choose new output names when rerunning it.

## Measurement conditions

The measurements use a persistent TCP connection and application receiver byte counts over wall-clock time.
For DL, the DN sends and UE receives; for UL, the UE sends and DN receives. Keep packet captures for timing and
retransmission analysis; count overlapping TCP sequence ranges once when deriving payload throughput.

The checked-in 900-second traces were generated from `tools/ntn/data/starlink_dtc_ho_pair.tle` at
`2026-10-18T06:56:20Z` using `tools/ntn/generate_orbital_trace.py` and Skyfield 1.55.
Forward traces start at simulated second 131;
traffic runs for 40 wall seconds before the fade. Reverse traffic starts at simulated second 61 and the fade at
131; the original paper script starts reverse traffic at second 91. Each selected run measures 180 wall seconds.
Record both simulation and wall time, actual modem arguments, receiver goodput, RTT, retransmissions and redirection
recovery alongside each result.
