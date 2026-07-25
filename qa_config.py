#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone QA watcher configuration for the P2 SPS beam test.
Edit the constants below, then run this script to regenerate config/qa_config.json.
The flask UI's Start QA Watcher button reads that JSON to launch qa_watcher.py.
"""

import json
import os

from run_config_beam import BASE_DATA_DIR

BASE_DATA = BASE_DATA_DIR
# QA lives in this repo (p2_daq_analysis/detector_qa.py) and runs with this
# repo's venv — no external analysis repository needed at the beam.
DAQ_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    # Top-level directory containing all run_N/ subdirectories
    'runs_dir': f'{BASE_DATA}runs/',

    # Repository containing the QA script and the venv to run it with.
    # qa_script_rel_path / qa_python_rel_path are relative to analysis_dir.
    'analysis_dir': DAQ_REPO_DIR,
    'qa_script_rel_path': 'p2_daq_analysis/detector_qa.py',
    'qa_python_rel_path': '.venv/bin/python',

    # Subdirectory name for combined hits files (must match processor_config)
    'combined_hits_inner_dir': 'combined_hits_root',

    # QA file mode:
    #   'all'      — rerun QA with all accumulated files whenever a new one appears (default)
    #   'first'    — run QA once per subrun using only file_num=0 (fast for long runs)
    #   'per_file' — independent QA plot set for each file_num
    'qa_file_mode': 'first',

    # Run filtering
    'include_runs': None,  # e.g. ['run_1', 'run_2'] — only process these; None = all
    'exclude_runs': None,  # e.g. ['run_0']          — skip these

    # Watcher behavior
    'poll_interval':   10,  # seconds between scans
    'combined_settle_s': 20,  # a combined_hits file must be untouched this long before QA reads it
                              # (mid-write it opens with NO keys -> QA silently skips every detector)
    'stale_run_days':   1,  # runs with no new combined_hits for this many days are skipped
    'memory_kill_pct': 80,  # kill the QA process if system RAM usage exceeds this % (retried next poll)
    'qa_timeout_s':  1800,  # kill the QA process after this long; null = no timeout.
                            # The run loop is serial, so without this a single wedged
                            # subrun blocks every other run (drift_scan_2/drift_850 sat
                            # in the mean/RMS step for 15.5 h on 2026-07-24).
    'qa_max_attempts':  2,  # give up on a subrun after this many failed attempts, so one
                            # that always fails cannot be retried forever

    # Waveform plots — both OFF. These dominate the QA runtime: with the mean/RMS
    # step enabled a subrun took ~60-90 min, so the watcher fell permanently behind
    # the DAQ. The 8 per-detector hit plots still run and take seconds.
    'wf_mean_rms':    False,  # per-strip waveform mean/RMS colour maps
    'wf_event_plots': False,  # per-event waveform figures on beam runs
    'wf_step_size':   50000,  # events per batch for the mean/RMS reduction when enabled.
                              # Measured on a 2.34 M-entry decoded file: 778 s at the old
                              # 200 vs 18 s at 50000 for identical output (~400 MB peak).

    # CPU throttling — keep QA from starving the DAQ.
    'cpu_nice':         19,          # nice level (also ionice idle class); null = no niceing
    'cpu_affinity':   None,          # CPU cores QA may use (taskset); null = all cores
    'qa_threads':        4,          # numpy/BLAS thread cap; null = len(cpu_affinity)
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'qa_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
