#!/usr/bin/env python3
"""Auto-launch the P2_IN drift scan when p2in_hvrange_2 finishes (2026-07-28).

Waits for run 2's daq_control to exit, then generates the drift-scan GUI config
and starts it through the same path the GUI uses (run_config_beam.py ->
run_config_beam.json -> bash_scripts/start_run.sh, which types the command into
the daq_control tmux pane).

This refuses to launch unless EVERY precondition holds. Auto-starting a run on a
new detector unattended is only acceptable if it cannot fire in a state nobody
looked at, so each check below aborts loudly rather than proceeding:

  * run 2 completed all its sub-runs. If it ended early -- a trip, a stop, a
    crash -- that is exactly the situation where a human must look before more
    voltage goes on the chamber, so we do NOT chain.
  * the operating mesh has been chosen and written to MESH_FILE. There is no
    default: the drift scan is meaningless at a guessed mesh, and a wrong value
    silently produces a useless dataset.
  * there is enough beam window left. A 31 min run started after LATEST_START
    would run past the 14:00 stop and take its last points with no beam.
  * daq_control is really gone. start_run.sh uses `tmux send-keys`, which QUEUES
    into the pane rather than failing if something is still running -- the
    2026-07-27 pedestal click-storm mechanism. Waiting for the process to exit
    is what stops this from queueing a second run behind the first.
  * the generated config is the one we meant (name + sub-run count) before it is
    handed to start_run.sh.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

RUN2 = '/local/home/banco/P2_data/TB_July2026_H4/runs/p2in_hvrange_2'
RUN2_POINTS = 11
MESH_FILE = os.path.join(REPO, '.p2in_mesh_op')   # written once the plateau is known
NEW_RUN = 'p2in_drift_1'
NEW_SUBRUNS = 5
LATEST_START = '13:35'      # past this, the drift scan would outlive the beam
VENV_PY = os.path.join(REPO, '.venv', 'bin', 'python')
POLL = 15
SETTLE_S = 25               # let HV power-off and file copies finish


def log(msg):
    print(f'[chain {datetime.now():%H:%M:%S}] {msg}', flush=True)


def daq_alive():
    return subprocess.run(['pgrep', '-f', 'daq_control.py'],
                          capture_output=True).returncode == 0


def abort(msg):
    log(f'ABORT — {msg}')
    log('drift scan NOT started; launch by hand if you still want it')
    sys.exit(1)


log(f'armed; waiting for p2in_hvrange_2 to finish (latest start {LATEST_START})')
while daq_alive():
    time.sleep(POLL)
log('daq_control has exited')
time.sleep(SETTLE_S)

# --- preconditions -------------------------------------------------------
done = [d for d in os.listdir(RUN2)
        if os.path.isfile(os.path.join(RUN2, d, '.subrun_complete'))]
if len(done) < RUN2_POINTS:
    abort(f'run 2 completed only {len(done)}/{RUN2_POINTS} sub-runs — it ended '
          f'early, so a human should look before more HV goes on the chamber')
log(f'run 2 completed {len(done)}/{RUN2_POINTS} sub-runs')

if not os.path.isfile(MESH_FILE):
    abort(f'no operating mesh chosen ({MESH_FILE} absent)')
try:
    mesh_op = int(open(MESH_FILE).read().strip())
except ValueError:
    abort(f'{MESH_FILE} does not contain an integer')
if not 350 <= mesh_op <= 450:
    abort(f'operating mesh {mesh_op} V outside the measured 350-450 V range')
log(f'operating mesh: {mesh_op} V')

now = datetime.now().strftime('%H:%M')
if now > LATEST_START:
    abort(f'it is {now}, past the {LATEST_START} cutoff — a 31 min run would '
          f'outlive the beam window')

# --- generate ------------------------------------------------------------
env = dict(os.environ, DAQ_P2IN_MESH_OP=str(mesh_op))
r = subprocess.run([sys.executable, 'quick_scripts/make_p2in_drift_gui_config.py'],
                   capture_output=True, text=True, env=env)
if r.returncode != 0:
    abort(f'drift config generation failed:\n{r.stdout}\n{r.stderr}')
log('drift GUI config written')

r = subprocess.run([VENV_PY, 'run_config_beam.py'], capture_output=True, text=True)
if r.returncode != 0:
    abort(f'run_config_beam.py failed:\n{r.stderr}')

cfg_path = 'config/json_run_configs/run_config_beam.json'
with open(cfg_path) as f:
    cfg = json.load(f)
if cfg.get('run_name') != NEW_RUN or len(cfg.get('sub_runs', [])) != NEW_SUBRUNS:
    abort(f'generated config is not what we meant: run_name='
          f'{cfg.get("run_name")!r}, {len(cfg.get("sub_runs", []))} sub-runs '
          f'(expected {NEW_RUN!r}, {NEW_SUBRUNS})')
log(f'verified config: {cfg["run_name"]}, {len(cfg["sub_runs"])} sub-runs')

if daq_alive():
    abort('daq_control reappeared — refusing to queue a run behind it')

# --- launch --------------------------------------------------------------
r = subprocess.run(['bash', 'bash_scripts/start_run.sh',
                    os.path.join(REPO, cfg_path)],
                   capture_output=True, text=True)
if r.returncode != 0:
    abort(f'start_run.sh failed:\n{r.stderr}')
log(f'LAUNCHED {NEW_RUN} at mesh {mesh_op} V — 5 points, gaps 150-450 V, ~31 min')
