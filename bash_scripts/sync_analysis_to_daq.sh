#!/bin/bash
# Push cosmic-bench analysis products from the laptop to the DAQ machine so the
# flask GUI's Analysis tab can browse them.
#
# Run FROM THE LAPTOP (where P2_basket_analysis/cosmic_bench_analysis writes its
# Analysis tree). Syncs every detector's outputs for the given run (default: the
# Fe55 telescope scan) into <BASE_DATA_DIR>/analysis/<detN>/<run>/ on the DAQ
# host — the flask app serves ANALYSIS_DIR = <BASE_DATA_DIR>/analysis.
#
# Usage:  bash_scripts/sync_analysis_to_daq.sh [run_name]

set -e

RUN_NAME="${1:-p2_fe55_det2_det3_mesh_scan_7-18-26}"
ANALYSIS_ROOT="/local/home/ak271430/Documents/PostDocSaclay/data/Cosmic_Bench/Analysis"
DAQ_HOST="banco_daplxa"
DAQ_ANALYSIS_DIR="/local/home/banco/P2_data/Fe55/analysis"

synced=0
for det_dir in "$ANALYSIS_ROOT"/det*/; do
    det=$(basename "$det_dir")
    src="$det_dir$RUN_NAME"
    [ -d "$src" ] || continue
    echo "== $det/$RUN_NAME =="
    ssh "$DAQ_HOST" "mkdir -p '$DAQ_ANALYSIS_DIR/$det'"
    rsync -rt --delete "$src/" "$DAQ_HOST:$DAQ_ANALYSIS_DIR/$det/$RUN_NAME/"
    synced=$((synced + 1))
done

if [ "$synced" -eq 0 ]; then
    echo "No analysis outputs found for run '$RUN_NAME' under $ANALYSIS_ROOT/det*/"
    exit 1
fi
echo "Synced $synced detector tree(s) to $DAQ_HOST:$DAQ_ANALYSIS_DIR"
