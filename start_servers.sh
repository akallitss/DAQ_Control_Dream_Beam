#!/bin/bash

# Source the venv
source .venv/bin/activate

# Interactive shells on banco export LD_LIBRARY_PATH/PYTHONPATH for unrelated
# lab software (ISEG SDK etc.), which shadows the ROOT libraries the
# reconstruction binaries were built against ("symbol lookup error" from
# decode under the watcher). Scrub them for every session we start.
ENVCLEAN="env -u LD_LIBRARY_PATH -u PYTHONPATH"

# Start sessions. 3rd arg = tmux scrollback cap in LINES (memory-saving).
# hv_control is very chatty (HV monitor every monitor_interval seconds), so
# keep it short. The others keep a longer buffer for debugging.
bash_scripts/start_tmux.sh hv_control "$ENVCLEAN python hv_control.py" 500
bash_scripts/start_tmux.sh dream_daq "$ENVCLEAN python dream_daq_control.py" 20000
bash_scripts/start_tmux.sh processor_watcher "$ENVCLEAN python processor_watcher.py config/processor_config.json" 20000
bash_scripts/start_tmux.sh daq_control "echo 'Daq control session started'" 20000
bash_scripts/start_tmux.sh flask_server "$ENVCLEAN flask_app/start_flask.sh" 5000
