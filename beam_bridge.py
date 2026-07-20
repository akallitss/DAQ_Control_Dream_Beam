#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Beam-state bridge (banco side of the SPS beam monitor).

NXCALS only runs on the CERN network, so the beam-intensity watcher runs on
lxplus and publishes beam_state.json + per-day CSVs to EOS. banco (on the CEA
network) cannot reach NXCALS but CAN reach EOS via xrootd — this bridge pulls
the published files down every poll so the Flask GUI's /beam tab reads a fresh
state exactly as if a local watcher wrote it.

    lxplus  ── NXCALS ──►  /eos/.../beam_monitor/{beam_state.json, beam_intensity_*.csv}
    banco   ── xrdcp ────►  <repo>/config/beam_state.json  +  BEAM_LOG_DIR/*.csv

Runs in the 'beam_watcher' tmux session (GUI "Start Beam Watcher" button).
Uses the same ~/bin/xrdcp + Kerberos setup as backup_watcher.py.

Config: env vars (all optional)
  SPS_BEAM_EOS_URL   xrootd endpoint         (default root://eosproject.cern.ch)
  SPS_BEAM_EOS_DIR   EOS beam_monitor dir    (default the salsachip path below)
  SPS_BEAM_POLL_S    seconds between pulls    (default 20)
  SPS_BEAM_STALE_S   mark beam data stale after this many s without an EOS update
"""

import os
import sys
import json
import time
import subprocess
import datetime

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
# Point kinit/xrdcp at the repo CERN krb5 config and ~/bin (same as backup_watcher).
os.environ.setdefault('KRB5_CONFIG', os.path.join(_REPO_DIR, 'config', 'krb5_cern.conf'))
os.environ['PATH'] = os.path.expanduser('~/bin') + os.pathsep + os.environ.get('PATH', '')

sys.path.insert(0, _REPO_DIR)
from beam_monitor.beam_intensity_controller import BEAM_STATE_PATH, BEAM_LOG_DIR, BEAM_UNIT

EOS_URL = os.environ.get('SPS_BEAM_EOS_URL', 'root://eosproject.cern.ch')
EOS_DIR = os.environ.get('SPS_BEAM_EOS_DIR',
                         '/eos/project/s/salsachip/Data/T2_tests/beam_monitor')
POLL_S = float(os.environ.get('SPS_BEAM_POLL_S', 20))
STALE_S = float(os.environ.get('SPS_BEAM_STALE_S', 180))
KINIT_INTERVAL = 3600


def _xrdcp(remote_name, local_path):
    """Copy EOS_DIR/remote_name -> local_path. Returns True on success."""
    url = f'{EOS_URL}/{EOS_DIR}/{remote_name}'
    tmp = local_path + '.part'
    r = subprocess.run(['xrdcp', '-f', '-s', url, tmp], capture_output=True, text=True)
    if r.returncode == 0 and os.path.isfile(tmp):
        os.replace(tmp, local_path)
        return True
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def _write_waiting_state(msg):
    """Publish a 'no beam data' state so the GUI shows a clear status, not stale."""
    state = {
        'connected': False,
        'beam_on': None,
        'unit': BEAM_UNIT,
        'last_error': msg,
        'source': 'bridge',
        'updated': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    os.makedirs(os.path.dirname(BEAM_STATE_PATH), exist_ok=True)
    with open(BEAM_STATE_PATH, 'w') as f:
        json.dump(state, f)


def _refresh_kerberos():
    subprocess.run(['kinit', '-R'], capture_output=True)  # renew; ignore failure


def main():
    os.makedirs(BEAM_LOG_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(BEAM_STATE_PATH), exist_ok=True)
    print(f'[beam_bridge] EOS {EOS_URL}/{EOS_DIR}', flush=True)
    print(f'[beam_bridge] -> state {BEAM_STATE_PATH}  logs {BEAM_LOG_DIR}  poll {POLL_S}s', flush=True)
    last_kinit = 0.0
    last_ok = None
    while True:
        now = time.time()
        if now - last_kinit >= KINIT_INTERVAL:
            _refresh_kerberos()
            last_kinit = now

        got_state = _xrdcp('beam_state.json', BEAM_STATE_PATH)
        # today's CSV for the /beam/history plot (best-effort; name matches the watcher)
        day = datetime.date.today().isoformat()
        csv_name = f'beam_intensity_{day}.csv'
        _xrdcp(csv_name, os.path.join(BEAM_LOG_DIR, csv_name))

        if got_state:
            last_ok = now
        elif last_ok is None or now - last_ok > STALE_S:
            _write_waiting_state(
                'no beam_state.json on EOS yet — is the lxplus NXCALS watcher running?'
                if last_ok is None else
                f'beam_state.json not updated for {int(now - last_ok)}s (lxplus watcher / EOS?)')
        time.sleep(POLL_S)


if __name__ == '__main__':
    main()
