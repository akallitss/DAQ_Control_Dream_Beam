#!/usr/bin/env python3
"""Stage the P2_IN drift (transparency) scan as a GUI run — 2026-07-28.

The orthogonal partner to p2in_hvrange_2. That scan held the drift gap fixed at
300 V and moved the mesh, so it isolates GAIN; this holds the mesh FIXED at the
operating point that scan found and moves ONLY the drift electrode, so the one
thing changing is the drift field (electron transparency / primary collection).

The 300 V gap used throughout p2in_hvrange_2 is inherited from P2_MID/P2_OUT and
has never been verified for THIS chamber. That is the open question this answers.

Usage — set the mesh once p2in_hvrange_2 shows where the plateau is:

    DAQ_P2IN_MESH_OP=420 python3 quick_scripts/make_p2in_drift_gui_config.py

Then refresh the GUI and press Start Run. Nothing here touches the crate.

Ordering: drift steps UP from a gap of 150 V, not down from 450. Same reasoning
as the mesh scan — this chamber is new, so approach the high-field end from
below and watch the current on the way up rather than starting at the extreme.
GUI drift_scan computes drift = drift_start - i*step_v, so a NEGATIVE step_v
steps up.

Run name: a NEW one (p2in_drift_1). This is not optional. iterate_run_num.py
renames a colliding run by editing run_config_beam.py, but the name now comes
from gui_run_config.json, which it does not touch — so reusing an existing name
would silently write a second run into the first one's directory.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

import gui_run_config as grc

RUN_NAME = 'p2in_drift_1'
SCAN_DET = 'P2_IN'
HELD_AT_NOMINAL = {'P2_MID': {'mesh': 450, 'drift': 750},
                   'P2_OUT': {'mesh': 450, 'drift': 750}}

# The operating mesh from p2in_hvrange_2. Deliberately env-driven with no
# committed default guess: it must be set from the measured plateau.
MESH_OP = int(os.environ.get('DAQ_P2IN_MESH_OP', '0'))
if MESH_OP <= 0:
    raise SystemExit('set DAQ_P2IN_MESH_OP to the operating mesh from '
                     'p2in_hvrange_2, e.g. DAQ_P2IN_MESH_OP=420')

GAP_START = 150          # first point: gap 150 V
GAP_STEP = 75            # V per point
POINTS = 5               # gaps 150, 225, 300, 375, 450
SUBRUN_MIN = 5           # ~31 min total incl. ramps — sized to the tail of the
                         # 2026-07-28 morning window (run 2 ends ~13:20, beam
                         # stops 14:00)

gui = grc.defaults_from_code()
gui['run_name'] = RUN_NAME
gui['notes'] = (f'P2_IN drift/transparency scan at fixed mesh {MESH_OP} V. '
                f'Gap {GAP_START} -> {GAP_START + (POINTS - 1) * GAP_STEP} V. '
                f'Tests whether the 300 V gap inherited from MID/OUT is right '
                f'for this chamber.')
gui['run_type'] = 'drift_scan'

for det in gui['detectors']:
    name = det['name']
    if name == SCAN_DET:
        det['scan'] = True
    elif name in HELD_AT_NOMINAL:
        det['scan'] = False               # pins them; see make_p2in_gui_config.py
        det['fixed_hv'] = dict(HELD_AT_NOMINAL[name])

gui['run_types']['drift_scan'] = {
    'subrun_min': SUBRUN_MIN,
    'step_v': -GAP_STEP,                  # negative => drift steps UP
    'points': POINTS,
    'mesh_fixed': {SCAN_DET: MESH_OP},
    'drift_start': {SCAN_DET: MESH_OP + GAP_START},
}

gui['enabled'] = False
ok, errs = grc.validate(gui)
if not ok:
    print('VALIDATION FAILED — nothing written:')
    for e in errs:
        print(f'  - {e}')
    raise SystemExit(1)

gui['enabled'] = True
with open(grc.GUI_CONFIG_PATH, 'w') as f:
    json.dump(gui, f, indent=4)

print(f'wrote {grc.GUI_CONFIG_PATH}  (enabled=True)')
print(f'  run_name  : {RUN_NAME}')
print(f'  mesh fixed: {MESH_OP} V   (P2_IN only; MID/OUT/uRWELLs held)')
print()
subs = grc.build_sub_runs(gui)
total = sum(s['run_time'] for s in subs)
print(f'  sub-runs  : {len(subs)}   DAQ {total:.0f} min   '
      f'total ~{total + len(subs) * 0.75 + 2:.0f} min')
print()
for s in subs:
    h = s['hvs']
    mesh_v, drift_v = h['8']['1'], h['8']['0']
    print(f'  {s["sub_run_name"]:<22} t={s["run_time"]:<3} '
          f'P2_IN mesh={mesh_v} drift={drift_v} gap={drift_v - mesh_v}  '
          f'MID={h["8"]["3"]}/{h["8"]["2"]} OUT={h["8"]["5"]}/{h["8"]["4"]}')
