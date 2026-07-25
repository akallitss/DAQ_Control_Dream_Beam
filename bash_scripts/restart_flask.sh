#!/bin/bash
# Restart ONLY the Flask GUI server (tmux session `flask_server`). The DAQ, HV,
# and every watcher session keep running — this is the "Restart GUI" button, not
# a full DAQ restart (that's restart_daq_tmux_processes.sh, which cycles
# daq_control / dream_daq / hv_control too).
#
# Runs detached via `screen` because Flask is serving the very request that
# triggered this: killing the flask_server session kills the serving process, so
# the kill+relaunch must live in a process that outlives it. The GUI is
# unreachable for ~3 s while it cycles; nothing else is touched.
#
# The session is recreated through start_tmux.sh with the same command and
# scrollback cap start_servers.sh uses, so a GUI restarted from the button is
# indistinguishable from one started at boot — including the env scrub
# (LD_LIBRARY_PATH / PYTHONPATH shadow the ROOT libraries) and start_flask.sh's
# absolute venv interpreter.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

screen -dmS restart_flask bash -c '
  sleep 2
  tmux kill-session -t flask_server 2>/dev/null
  cd "'"$REPO_DIR"'" || exit 1
  bash_scripts/start_tmux.sh flask_server "env -u LD_LIBRARY_PATH -u PYTHONPATH flask_app/start_flask.sh" 5000
'
