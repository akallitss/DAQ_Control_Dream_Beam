#!/bin/bash
SESSION="daq_control"
CONFIG_PATH="$1"

if [ -z "$CONFIG_PATH" ]; then
  echo "Usage: $0 <config_path>"
  exit 1
fi

# Check if run output directory exists, iterate run name if so
#python iterate_run_num.py "$CONFIG_PATH"  # Not working, skip for now!

# Absolute venv interpreter (3.12), not bare `python`. This command is typed
# into the daq_control tmux pane, where `python` depends on that pane's PATH and
# on the interactive `alias python='python3'` — it happened to resolve to the
# venv only because the tmux server inherited an activated venv. Pin it so a
# freshly-created pane starts runs on the same interpreter (2026-07-25).
COMMAND="/local/home/banco/DAQ_Control_Dream_Beam/.venv/bin/python daq_control.py \"$CONFIG_PATH\""

# Send command to the tmux session
tmux send-keys -t "$SESSION" "$COMMAND" C-m
