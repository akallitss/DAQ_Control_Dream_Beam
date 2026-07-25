#!/bin/bash

# NOTE: make_dream_ped_cfg.py is currently an empty no-op. The pedestal cfg is
# the pre-made dream_config/Tcm_Mx17_May_ped.cfg referenced by
# run_config_pedestals.py, so this call is disabled. Re-enable it if/when
# make_dream_ped_cfg.py is implemented to generate that pedestal cfg.
# python make_dream_ped_cfg.py

# Absolute venv interpreter (3.12). app.py launches this script with
# subprocess.Popen (no shell), so bare `python` resolved to /usr/bin/python 3.8
# while the rest of the DAQ ran on the venv (2026-07-25).
/local/home/banco/DAQ_Control_Dream_Beam/.venv/bin/python run_config_pedestals.py

CONFIG_PATH="run_config_pedestals.json"

bash_scripts/start_run.sh "$CONFIG_PATH"
