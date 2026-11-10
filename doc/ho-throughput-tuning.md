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

## Applying the selected settings

Use the existing LTE/NR startup commands and add the following command-line overrides.
LTE and NR must use matching base-station and UE resource settings.

```text
LTE eNB, DL and UL:
  --eNBs.[0].component_carriers.[0].N_RB_DL 100
LTE eNB, UL additionally:
  --MACRLCs.[0].max_ul_rb_index 27
LTE UE:
  -r 100 --ue-rxgain 128

Both NR gNBs:
  --gNBs.[0].servingCellConfigCommon.[0].dl_carrierBandwidth 52
  --gNBs.[0].servingCellConfigCommon.[0].ul_carrierBandwidth 52
  --gNBs.[0].servingCellConfigCommon.[0].initialDLBWPlocationAndBandwidth 14025
  --gNBs.[0].servingCellConfigCommon.[0].initialULBWPlocationAndBandwidth 14025
  --gNBs.[0].force_256qam_off 0
  --gNBs.[0].force_UL256qam_off 1
  --gNBs.[0].maxMIMO_layers 1
NR UE:
  --cells.[0].N_RB_DL 52 --cells.[0].rf_freq 2490830000
  --uecap_file /absolute/run-input/nr-uecap.xml
All participating RFsim nodes:
  --rfsimulator.[0].channelmod_optimized 1

TCP sender, DL and UL:
  -b 100M --fq-rate 35M
TCP sender, UL additionally:
  -w 384K
```

The existing `uecap_ports1.xml` supplies the UE capability structure. Generate a per-run n254/10-MHz capability
from it as follows, then pass its absolute path to the NR UE. This is the transformation used in the measured runs:

```python
from pathlib import Path
import xml.etree.ElementTree as ET

source = Path("targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml")
output = Path("/absolute/run-input/nr-uecap.xml")
tree = ET.parse(source)
root = tree.getroot()
for band in root.findall(".//bandNR"):
    band.text = "254"
for direction in ("DL", "UL"):
    for element in root.findall(".//supportedSubcarrierSpacing" + direction):
        element.clear()
        ET.SubElement(element, "kHz15")
    for element in root.findall(".//supportedBandwidth" + direction):
        element.clear()
        ET.SubElement(ET.SubElement(element, "fr1"), "mhz10")
for parent in root.iter():
    for child in list(parent):
        if child.tag in ("maxNumberMIMO-LayersPDSCH", "pusch-256QAM", "channelBW-90mhz"):
            parent.remove(child)
for element in root.findall(".//supportedModulationOrderUL"):
    element.clear()
    ET.SubElement(element, "qam64")
with output.open("xb") as stream:
    tree.write(stream, encoding="utf-8", xml_declaration=True)
```

For the calibrated two-satellite traces, forward LTE→NTN uses initial frequency offset 50760 Hz and
NTN→LTE uses 52281 Hz at the new carrier. TRANS keeps UE drift −41/−42 and compensation mode 2;
REGEN keeps drift −20/−21 and compensation mode 3. Source-side and target-side IWK requests, A2 thresholds,
propagation and satellite-motion settings follow the existing experiment procedure.

The source provides one implementation for all eight combinations. Change `REGEN`, traffic direction and
LTE→NTN/NTN→LTE per case; keep the radio and TCP settings above. The original Compose files already constrain
container CPUs. The selected measurements additionally place `UEthread_0` on a physical core separate from the NR
worker threads, leaving its SMT sibling free. This is a runtime affinity choice; it adds no UE scheduling code.

Use the repository's CMake/Ninja build for LTE/NR softmodems, `conf2uedata` and the RFsim, coding, parameter and
Telnet modules. Use the same runtime libraries as the build. Experiment admission, shared-core coordination,
subscription selection, container ownership and per-run output paths belong to the local run procedure.

## Measurement conditions

The measurements use a persistent TCP connection and application receiver byte counts over wall-clock time.
For DL, the DN sends and UE receives; for UL, the UE sends and DN receives. Keep packet captures for timing and
retransmission analysis; count overlapping TCP sequence ranges once when deriving payload throughput.

Generate 900-second traces with the existing `tools/ntn/generate_orbital_trace.py`, using
`tools/ntn/data/starlink_dtc_ho_pair.tle` at `2026-10-18T06:56:20Z`. Forward traces start at simulated second 131;
traffic runs for 40 wall seconds before the fade. Reverse traffic starts at simulated second 61 and the fade at
131; the original paper script starts reverse traffic at second 91. Each selected run measures 180 wall seconds.
Record both simulation and wall time, actual modem arguments, receiver goodput, RTT, retransmissions and redirection
recovery alongside each result.
