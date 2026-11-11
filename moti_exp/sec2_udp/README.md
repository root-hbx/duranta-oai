# Sec2 UDP experiments

Requires Docker Compose, the existing Open5GS core and test subscription,
NR/LTE builds, iperf3, tcpdump, tshark, and Python with NumPy and Matplotlib.
NR_BUILD and LTE_BUILD select build directories; defaults are build/ and build-lte/.
Runs use the original 25-PRB REGEN / TWO_SAT / IWK configuration. Each run
restarts AMF; reserve exclusive use of the test subscription and core first.
The same-RAT coverage-gap case needs a static UE address configured beforehand.

From the repository root:

~~~bash
campaign=moti_exp/sec2_udp/results/new-campaign
python3 -B moti_exp/sec2_udp/run/campaign.py "$campaign"                 # plan only
python3 -B moti_exp/sec2_udp/run/campaign.py "$campaign" --ids all      # 47 formal runs
~~~

Use --ids event-01 instead of --ids all to run one event in a new campaign.
IDs also include pilots and the two lowload controls added with --low-load-rate
(e.g. 9.66M). Select available capture CPUs with CAPTURE_CPUS. All runs and analysis
execute serially. Existing attempts are preserved; --attempt selects a new attempt number.

For a single run, set SEC2_RUN_DIR and call
bash ci-scripts/yaml_files/ntn_lte_sw/xrat_udp.sh lte2ntn or ntn2lte.
Defaults are UDP downlink at 100 Mbps, 1200-byte datagrams and 180 seconds.
STAGE=base keeps the source RAT; STAGE=session GAP=<seconds> selects a coverage gap.

Analyze collected runs with analysis/analyze.py RUN_DIR TABLE_DIR --protocol udp --gap SECONDS.
The batch command performs this step automatically. Then generate the tables and paper figures:

~~~bash
python3 -B moti_exp/sec2_udp/analysis/assemble.py "$campaign"
python3 -B moti_exp/sec2_udp/analysis/phases.py "$campaign"
python3 -B moti_exp/sec2_udp/analysis/summarize.py "$campaign"
python3 -B moti_exp/sec2_udp/plot/figures.py "$campaign" --acm-layout
~~~

Use --figures to select the individual or directional plots, and --output for a separate
figure directory. Runs, derived tables and figures stay under the Git-ignored results/.
