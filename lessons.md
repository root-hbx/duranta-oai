# Lessons

Lessons learned from running OAI tests in practice. Each entry records what was observed, the root cause, where it is in the code, how it was confirmed, and what it means for later work.

## L1. LTE: X2 handover in RRC_CONNECTED does not work (UE side)

**Date / branch**: 2026-10-06, `test/ntn-lte-sw` (Step 2: two LTE eNBs + one UE)

**Path tested**: network-controlled X2 handover in RRC_CONNECTED
1. The UE measures the neighbour cell and sends an A3 MeasurementReport.
2. The source eNB sends an X2 Handover Request; the target eNB prepares resources and returns Ack.
3. The source eNB sends the UE an RRCConnectionReconfiguration containing `mobilityControlInfo`.
4. The UE synchronises to the target cell and finishes the handover.

**Setup**:
- Core: Open5GS EPC.
- rfsim topology: `lte-uesoftmodem` is the rfsim server; eNB1 (PCI 10) and eNB2 (PCI 11) both connect to it as clients.
- X2: `enable_x2=yes`; eNB2's X2 peer is eNB1.

**What works**:
- The UE-as-server topology runs, and the UE attaches through eNB1.
- X2 Setup succeeds, and each eNB registers the other as a neighbour (`rrc_eNB_process_x2_setup_*`).
- eNB1 sends A2–A5 measurement configs, and the neighbour list includes PCI 11.
- The UE PHY is configured to measure PCI 11 (`config_ue.c:304`, log "Cell 0 : Nid_cell 11").

**Blockers**: all three are in `openair2/RRC/LTE/rrc_UE.c`, one at each stage of the path.

| # | Stage | Location | Problem | How confirmed |
|---|---|---|---|---|
| 1 | A3 check | `check_trigger_meas_event` (~line 4018) | A neighbour is only evaluated when `eNB_offset < NB_eNB_INST`. `NB_eNB_INST` is `RC.nb_inst`, which is 0 in a standalone UE process, so A3 never fires and no MeasurementReport is sent. The condition dates from the old simulator, where eNB and UE ran in one process. | `rrc_log_level debug` prints `num_adj: 1 eNB_idx: 0, NB_eNB_INST: 0` |
| 2 | L3 filtering | `ue_meas_filtering` | `filter_coeff_rsrp` is 0, so `rsrp_db_filtered` stays 0 and does not follow the measurement | Debug log: `rsrp_coef: 0.0`, raw rsrp -140.8 / -137.8, after L3 filtering 0.0 |
| 3 | Executing the HO command | `rrc_ue_process_mobilityControlInfo` (line 1806) | It only removes SRB1/SRB2/DRB and never re-establishes them. It carries the comment "This function needs some updates". | Code reading (not reached at runtime, because stage 1 blocks) |

**eNB side**: an X2 HO can only be triggered by an A3 report with `measId==4` (`rrc_eNB.c:3521`). The flag `x2_ho_net_control` is dead code: it is declared but nothing sets it. There is no telnet trigger either. So with the UE side blocked, the eNB's X2 HO Request/Ack handling cannot be tested in practice.

**Conclusion**: in today's OAI, LTE has no usable connected-mode inter-eNB handover. Making it work would need at least all three UE fixes, and #3 needs bearer re-establishment plus PHY resync to the target cell. That is not a small change.

**Update**: fixed on `fix/lte-sw`. The full flow works in both directions; see L3.

**The docs agree**: it is easy to read `doc/handover-tutorial.md` as covering LTE, but it does not.
- The tutorial only covers NR: F1 (between DUs of the same gNB) and N2 (between gNBs), using `nr-softmodem` + `nr-uesoftmodem`. It never mentions eNB, X2, `lte-softmodem` or `lte-uesoftmodem`.
- Its "Inter-RAT neighbours (e.g., LTE)" line only names a kind of neighbour configuration. It does not mean handover to LTE is supported.
- `doc/FEATURE_SET.md` is cautious about LTE:
  - The eNB RRC section lists "Handover (experimental)".
  - The LTE UE RRC section lists only SI decoding, RRC connection establishment and feMBMS. There is no handover, measurement reporting or reselection.
- So "the eNB has experimental code, the UE does not implement it" matches what we measured.

**Takeaways**:
- Before saying "OAI supports X", check which stack the doc or feature list covers (NR or LTE), and whether it lists the eNB/gNB side, the UE side, or both. OAI's NR and LTE are two separate code bases, so NR handover support says nothing about LTE.
- In OAI's LTE code, "the code exists" and "a CI test exists" are two different things. X2 HO is only marked experimental in FEATURE_SET, and only a real run shows whether it works.
- When checking a mobility feature, split the path into stages (measure → report → prepare → command → execute) and confirm each stage from logs. That pinpoints the failure precisely.
- Configuration debugging tip: OAI config list items can be created from the command line (e.g. `--eNBs.[0].target_enb_x2_ip_address.[0].ipv4 <ip>`), so a second eNB does not need its own conf file.

## L2. LTE: the UE cannot get back from RRC_IDLE, and has no cell reselection

**Date / branch**: 2026-10-06, `test/ntn-lte-sw` (Step 2, idle-mode part)

**Path tested**: idle-mode mobility
1. The eNB releases the UE after inactivity, and the UE enters RRC_IDLE.
2. Uplink data arrives, the UE sends a Service Request, and the connection comes back.
3. The serving cell disappears, and the UE reselects to the neighbour cell on its own.

**Setup**:
- Same topology as L1.
- Both eNBs run with `--eNBs.[0].rrc_inactivity_threshold 5` (seconds; the default 0 disables the timer).
- When it fires, the eNB sends S1 UE Context Release Request with cause 20 (user inactivity).

**Observations**:

| Stage | Result | Evidence |
|---|---|---|
| Inactivity release | ✅ The eNB sends RRCConnectionRelease, and the MME logs "UE Context Release" | eNB log "Removing UE ... rrc_inactivity_timer timeout"; UE NAS gets `NAS_CONN_RELEASE_IND` |
| UE state after release | ❌ The RRC state does not go to IDLE: the last log line is `RRC_RECONFIGURED`. EMM is still `DEREGISTERED.NORMAL-SERVICE`. | See root cause 1 |
| Recovery on uplink data | ❌ Ping is 100% lost, no Service Request is sent, and the UE logs nothing | See root cause 2 |
| Serving cell disappears (eNB1 killed) | ❌ The UE only logs `[HW] Lost socket`. It does not resync, reselect, or send RA to eNB2, even though eNB2 (PCI 11) is still transmitting. | Root cause 3 |

**Root causes**:
1. **EMM never enters REGISTERED.**
   - `emm_proc_attach_complete` (`openair3/NAS/UE/EMM/Attach.c:694`) is registered as the success callback for delivering Attach Complete. It only runs when the lower layer returns `NAS_UPLINK_DATA_CNF`.
   - LTE RRC never sends `NAS_UPLINK_DATA_CNF`; only the NAS side has a receiving branch, in `nas_ue_task.c:252`.
   - So the network considers the UE attached and the data plane works, but UE NAS stays in DEREGISTERED all along.
2. **No Service Request procedure.**
   - `EMM/ServiceRequestHdl.c` contains only a T3417 timeout handler, and no `emm_proc_service_request` exists anywhere.
3. **No idle-mode reselection.**
   - LTE RRC only prints the SIB3 reselection parameters (`rrc_UE.c:3096-3156`).
   - Release handling only forwards `releaseCause` to NAS (`rrc_UE.c:2066-2078`).
   - On the eNB side, `redirectedCarrierInfo` and `idleModeMobilityControlInfo` are both NULL (`openair2/RRC/LTE/MESSAGES/asn1_msg.c:4007-4008`).

**Conclusion**: the OAI LTE UE effectively supports only one lifecycle: "attach once, then stay connected". After a release it can neither restore the connection nor move to another cell. Together with L1, LTE has no usable mobility at all between eNBs in either connected or idle mode. The only way to change cells is to restart the UE process and do a fresh attach.

**Takeaways**:
- "Data works" does not mean "the state machine is correct": here the network side was attached and ping worked, yet UE NAS stayed DEREGISTERED. Check the state machines on both sides, not just ping.
- For the dual-stack experiments (Step 5): moving between LTE and the other stack can only be done by restarting the LTE UE process (break-before-make). Any timing measurement will include the full startup of the LTE UE (cell search + attach).

## L3. LTE: making X2 handover work end to end (`fix/lte-sw`)

**Date / branch**: 2026-10-06, `fix/lte-sw` (from develop)

**Result (measured)**:
- eNB1 (PCI 10) → eNB2 (PCI 11) → eNB1 works. Each hop completes A3 → MeasurementReport → X2 HO Req/Ack → HO command → RA on the target → ReconfigurationComplete → S1 Path Switch → X2 UE Context Release.
- Ping succeeds after each hop.
- With a 10 ms ping, each HO loses one run of packets in a row: 3, 30 and 84 packets in three runs (30–840 ms of user-plane interruption). There is no X2-U data forwarding.
- The target eNB's MAC stats show UL errors 0.

**Fixes needed, by stage** (each one blocks every later stage):

| Stage | Problem | Fix |
|---|---|---|
| A3 / measurement | See L1 #1 and #2. In addition, RSRP was computed from energy and picked up the serving cell's data REs | A3 per 36.331, L3 filter coefficient, correlation-based CRS RSRP (`lte_ue_measurements.c`) |
| MeasurementReport | `do_MeasurementReport` encoding was broken | Rewritten (`asn1_msg.c`) |
| X2 / SCTP / GTP | X2 HO uses SCTP stream 1 but the listener opened 1 stream; the target asserts on X2-U tunnel creation and on the duplicate SRB2/DRB blocks | 2 streams; no X2-U tunnels; duplicate blocks removed |
| UE executes the HO command | Bearers removed and never re-added; KeNB* not derived | `rrc_ue_process_mobilityControlInfo`: KeNB*, SRB1/2 and DRBs re-established under the new C-RNTI |
| UE PHY on the target | Cell-specific sequences not regenerated; stale HARQ state; **DMRS cyclicShift used as the raw enum** (SIB2 maps it through `dmrs1_tab_ue`) | `phy_config_afterHO_ue` |
| RA on the target | C-RNTI byte order swapped (MAC, PHY, MAC CE); Msg3 carried an RRCConnectionRequest; Msg3 frame compare without mod 1024; target scheduled the UE (0-RB DCI0s) before Msg3 | Msg3 = C-RNTI MAC CE; contention resolved by a UL grant on the C-RNTI (36.321 5.1.4); target adds the UE with `ul_out_of_sync` |
| HO completion | T304 never stopped, so the UE declared HO failure 200 ms after success | T304 stopped when RA completes (36.331 5.3.5.4) |

**Takeaways**:
- **A PUSCH "timing offset" equal to a multiple of N_FFT/12 is not timing.** A DMRS cyclic-shift mismatch shows up in the channel estimate as a delay of `n_cs·N_FFT/12` samples (512/12 ≈ 43). eNB2 reported `sync_pos 45` against a normal 2, at SNR 65 dB with CRC failure. That looked like an rfsim UL alignment problem, but it was the HO path skipping the `dmrs1_tab` mapping. Before suspecting the simulator, diff the two code paths that configure the same parameter (SIB2 vs mobilityControlInfo).
- **First compare the failing case against a working case of the same procedure.** A standalone attach to eNB2 decoded Msg3 fine (sync_pos 2), so the radio path was good and the difference had to be in what the HO path configured.
- **Keep the harness configuration fixed.** One run used the default `lteue.ho.conf` (with a channel model) instead of `lteue.nochan.conf`. A3 then fired as soon as eNB2 joined, while the source link was failing (SR released), and the MeasurementReport never got through. That looked like a new regression.
- `--log_config.phy_log_level debug` produces about 1 GB of eNB logs per minute. Stop the containers right after the event and dump the logs to files.
- PHY code that calls a new MAC function needs a stub in `openair1/SIMULATION/LTE_PHY/dummy_functions.c`, or dlsim/ulsim fail to link.

**Intermittent failures found by repeating the HO (`lte.sh`, 2026-10-06)**:
- About 1 run in 3 handed over to PCI 11 and then lost the link: ping 100 % lost and thousands of `MeasurementReport dropped`. Before the drop fix (a7a2d58266), the same race asserted in `rrc_ue_generate_MeasurementReport`.
- Cause: right after the HO command, before the UE sent its preamble, a RAR with another RAPID arrived. `ue_process_rar` treats a mismatch as a failed initial access and set the C-RNTI to 0. Msg3 then carried C-RNTI 0, contention resolution failed and the UE fell back to RRC_IDLE.
- Fix: a connected UE keeps its C-RNTI in `ue_process_rar` (`rar_tools_ue.c`).
- RRC_IDLE harness: eNB2 started with an X2 target that no longer exists (eNB1 removed) never completed S1 Setup (`3586 -> 00e020` repeated every 5 s, the MME logs no S1 traffic). The UE attached on the radio, but its Attach Request never reached the MME. Start eNB2 without an X2 target in the idle case.
- Takeaway: one passing run is not a result. Loop the scripted scenario a few times and keep the logs of the first failure.

## L4. NR NTN: N2 handover between two trace-LEO gNBs (`fix/ntn-sw`)

**Date / branch**: 2026-10-06, `fix/ntn-sw` (from `test/ntn-lte-sw`). Harness: `ci-scripts/yaml_files/ntn_lte_sw/ntn.sh`.

**Result (measured)**:
- Single link (gNB = rfsim server, SAT_LEO_TRANS trace): PDU session in 3–4 s, ping OK, RTT 53–78 ms; 4 of 5 runs. The failed run (no IP) came right after HO experiments.
- Same link with the UE as rfsim server (needed for 2 gNBs): works after fix 1, and SIB19 now follows the trace.
- Two gNBs, N2 HO triggered by `ci trigger_n2_ho`: RRC/NGAP complete (Handover Required/Command, RRCReconfigurationComplete, Handover Notify, source releases the UE). **The user plane does not survive**: no ping after the HO.
- Terrestrial baseline (band78 pci0/pci1 confs, same host, UE server): N2 HO and ping after HO work.

**Fixes / findings**:

| # | Problem | Status |
|---|---|---|
| 1 | rfsim chose the channel direction by server/client role. With the UE as server, the UE ran the uplink model and called the gNB-only `nr_update_sib19` (`symbol lookup error`), and the gNB never updated SIB19 | Fixed: the direction follows the receiving node (`IS_SOFTMODEM_GNB/ENB`) in `simulator.cpp` |
| 2 | Without `rfsimu_channel_ue1`, the 2nd client falls back to the `ue0` descriptor, shared by both links (start time, delay, Doppler phase): the UE lost sync as soon as gNB2 joined | Workaround: define `rfsimu_channel_ue1` (ntn.yaml) |
| 3 | TRANS: after reconfigurationWithSync the UE sets `initial_fo` to the service-link Doppler of the target ntn-Config (56 kHz, `nr-ue.c:504`), but the rfsim TRANS model also applies the feeder-link Doppler (total 113 kHz): PBCH of the target is never decoded | Open. REGEN (no feeder link) syncs to the target |
| 4 | REGEN: target sync and CFRA OK, then no user data; UE `max RETX reached on SRB 1` ~3 s later, then re-establishment; no SIB19 from the target before T430 expiry | Open |

**Notes**:
- Each rfsim link has its own trace clock (start of that connection). gNB2 joining 12 s later is a satellite 12 s behind on the same orbit; its SIB19 and the UE's `ue1` model agree.
- The terrestrial N2 baseline is inter-frequency (3619.2 → 3319.68 MHz); the NTN pair is intra-frequency (same SSB ARFCN, SSB bitmap 1 vs 2).

## L5. NR NTN: root causes of the Step 4 handover failures (`fix/ntn-sw`)

**Date / branch**: 2026-10-07, `fix/ntn-sw` 11e5651f8d. Logs and the temporary patch: `build/ntn-dig/` (`tmpdig.patch`, reverted).

**Conclusion (measured)**: neither failure is a 3GPP design defect. #1 is an OAI bug, #2 was our trace channel model. With the
two fixes below (#1 still a temporary patch, #2 fixed in `feat/ntn-trace-leo` 2f39c647a9), N2 HO works in REGEN and TRANS. The ping gap is about 1.0–1.1 s (1 of 70 lost at a 0.5 s interval).
The UE re-reads the target SIB19 every ~2.6 s and the link survives more than 30 s (T430 = 5 s).

| # | Symptom | Root cause | Class |
|---|---|---|---|
| 1 | REGEN (also TRANS): the link dies about 5 s after HO with `T430 expired` → UL sync loss. Earlier notes said "SRB1 max RETX ~3 s", which was this | gNB `schedule_nr_other_sib` passes the transmitted-SSB **ordinal** to `other_sib_sched_control`. In NO_BEAM_MODE, `get_ssbidx_from_beam` maps it back to SSB 0, so the otherSI PDCCH uses `type0_PDCCH_CSS_config[0]`. With `ssb_PositionsInBurst_Bitmap 2` (gNB2), SIB19 is never decodable. A single cell with bitmap 2 and no HO shows the same thing. Using the real SSB index fixes it | OAI bug (gNB MAC) |
| 2 | TRANS: target PBCH never decoded | Our trace LEO model (4b43626185) added the feeder-link Doppler on the downlink (total 113 kHz). Per TS 38.300 §16.14.2.2 the UE pre-compensates the service link only and the network handles the feeder link Doppler; the upstream circular-orbit model does the same. `--initial-fo 113269` hid it at initial access, but at HO the UE re-syncs with the service-only Doppler from the ephemeris. Fixed in 2f39c647a9 (service-link Doppler only, `ntn.yaml` UE FO 56634). After the fix: link OK, HO decodes the target PBCH and completes CFRA, then hits #1 | Our model bug (fixed) |
| 3 | UE exits about 1.5 s after start with `unknown option --position0.*` | `position0` is read lazily (on SIB19). `config_check_unknown_cmdlineopt` runs first if SIB19 has not arrived yet | OAI bug (minor) |
| 4 | Sporadic `Write queue full` assert in the UE (rfsim server) at startup | Real-time load; a retry works | Environment |

**Controls**:
- REGEN: 30 s of ping on gNB1 while gNB2 is on air shows 0 % loss. Intra-frequency interference is therefore not the cause.

**Why no protocol issue showed up**: the scenario is too benign.
- Both gNBs use the same trace and the same GW position. The target is the same satellite about 15 s later on the same pass.
- At HO, source and target differ by only about 0.2 kHz of Doppler and 150 µs (REGEN) / 430 µs (TRANS) of TA (7.68 Msps). Real inter-satellite HO has tens of kHz and several ms.
- To stress the NTN-specific mechanisms (target ntn-Config and epoch, T430 across HO, TA/Doppler jump, feeder/GW switch), Step 4 needs distinct satellites and/or gateways.
