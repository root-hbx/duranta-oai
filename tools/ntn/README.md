# NTN trace tools

Helpers for the trace-driven LEO satellite channel model of the rfsimulator
(`sat_trace_file` in the `channelmod` section, see
[ntn-configuration.md](../../doc/ntn-configuration.md#trace-driven-leo-satellite)).

| Script | Purpose | Dependencies |
| --- | --- | --- |
| `download_tle.py` | download/filter TLEs from CelesTrak | - |
| `generate_orbital_trace.py` | propagate one TLE (SGP4) over a pass, write the trace CSV | `numpy`, `skyfield` |
| `calc_ntn_parameters.py` | SIB19 / UE parameters matching a trace and ground positions | - |
| `find_handover_pair.py` | two satellites of a TLE set serving a ground position one after the other | `numpy`, `skyfield` |
| `calc_service_windows.py` | service windows of several traces, handover window and CHO `condEventT1` / `t-Service` values | - |

`data/` holds the default TLE (`default_leo600.tle`, a 600 km polar orbit), a
300 s sample trace (`orbital_trace_s_300.csv`, Starlink DTC pass), and a pair of
Starlink DTC satellites serving the same UE one after the other
(`starlink_dtc_ho_pair.tle`, traces `ho_pair_sat0.csv` / `ho_pair_sat1.csv` from 2026-10-18T06:56:20Z).

## Trace format

CSV with header. Columns are identified by name, the order does not matter:

```
time_s,pos_x,pos_y,pos_z,vel_x,vel_y,vel_z
0,-4165355.542,-5103358.603,1412186.247,4093.643,-1684.703,5943.4
1,-4161259.338,-5105040.275,1418128.68,4098.808,-1678.667,5941.548
```

- `time_s` or `time_ms`: time since trace start, strictly increasing
- `utc_ms` (optional): UTC of the row, ms since 1970-01-01
- `pos_*`: satellite ECEF position (m), `vel_*`: satellite ECEF velocity (m/s)

## Workflow

```bash
cd tools/ntn
pip install numpy skyfield

# 1. TLEs, e.g. Starlink Direct-to-Cell
python3 download_tle.py starlink --name-contains "[DTC]" -o starlink_dtc.tle

# 2. 600 s trace of the first pass of satellite 0 over a ground station (ECEF, m),
#    starting 385 s before culmination (--start-offset)
python3 generate_orbital_trace.py -g s -t 600 --tle starlink_dtc.tle --sat-index 0 \
  --gnd-position '{"x": -2706714.0, "y": -4261882.0, "z": 3885680.0}' -o trace.csv

# 3. gNB/UE configuration values for this trace (UE and gNB positions as in channelmod)
python3 calc_ntn_parameters.py trace.csv --ue=-2706714,-4261882,3885680 --gnb=-2706714,-4261882,3885680 --fc 2488400000
```

`-g ms` writes a trace with 1 ms resolution (`time_ms`), `-t` is then in ms.

### Two satellites, one time line (handover)

```bash
# 1. a satellite pair: A serves at the trace start, B rises 120 s later, before A sets
python3 find_handover_pair.py --tle starlink_dtc.tle --ue=-3310204,-5006785,2157610 --min-elev 25 --overlap 40 120

# 2. both traces from the same UTC start (printed by step 1), TLE indices A and B
python3 generate_orbital_trace.py -t 360 --tle starlink_dtc.tle --sat-index <A> --start-utc <start> \
  --gnd-position '{"x": -3310204, "y": -5006785, "z": 2157610}' -o sat0.csv
python3 generate_orbital_trace.py -t 360 --tle starlink_dtc.tle --sat-index <B> --start-utc <start> \
  --gnd-position '{"x": -3310204, "y": -5006785, "z": 2157610}' -o sat1.csv

# 3. handover window and the CHO time trigger (condEventT1) between them
python3 calc_service_windows.py sat0.csv sat1.csv --ue=-3310204,-5006785,2157610 --min-elev 25
```

In the rfsimulator, set `sat_trace_common_clock = 1` in the channel models of both satellites so that all links and
nodes follow the same trace time.
