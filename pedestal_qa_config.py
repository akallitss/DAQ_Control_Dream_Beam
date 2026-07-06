#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone pedestal QA watcher configuration for the P2 SPS beam test.
Edit the constants below, then run this script to regenerate config/pedestal_qa_config.json.
The flask UI's Ped QA toggle reads that JSON to launch pedestal_watcher.py.
"""

import json
import os

from run_config_beam import BASE_DATA_DIR, RECONSTRUCTION_BUILD

BASE_DATA    = BASE_DATA_DIR
DAQ_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    # Top-level directory containing all pedestals_<datetime>/ run dirs
    # (run_config_pedestals.py writes each pedestal run here)
    'pedestals_dir': f'{BASE_DATA}pedestals/',

    # Python interpreter with uproot/numpy/matplotlib (this repo's venv,
    # same one qa_watcher uses for p2_daq_analysis/detector_qa.py)
    'analysis_python': f'{DAQ_REPO_DIR}/.venv/bin/python',

    # C++ decode executable from mm_dream_reconstruction (site-dependent)
    'decode_exe': f'{RECONSTRUCTION_BUILD}decoder/decode',

    # Subdirectory of each pedestal run dir where PNGs/PDF/summary.json land
    'output_inner_dir': 'ped_qa',

    # Watcher behavior
    'poll_interval':  10,  # seconds between scans
    'quiet_sec':      60,  # run is considered finished when no file in its subrun
                           # dir has been modified for this many seconds
    'memory_kill_pct': 80, # kill the analysis if system RAM usage exceeds this %
                           # (killed runs are retried next poll)
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'pedestal_qa_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
