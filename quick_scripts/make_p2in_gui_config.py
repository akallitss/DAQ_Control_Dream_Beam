#!/usr/bin/env python3
"""Seed config/gui_run_config.json for the P2_IN HV-range physics scan (2026-07-28).

Why this exists: the GUI's Start Run reads config/gui_run_config.json, and that
file had never existed on this setup -- the GUI had always been driving
run_config_beam.py's RUN_PLAN instead. Authoring it by hand would mean
re-transcribing every detector's HV channel map, FEU/connector map and
orientation, which is exactly the kind of copy that silently rots. So this
starts from gui_run_config.defaults_from_code() (which derives all of that from
run_config_beam.py's own DET_HV / dream_feus / DET_Z_MM tables) and changes ONLY
the scan roles and the mesh_scan parameters.

What it changes, and why each one matters:

  * P2_IN  -> scan=True. It is the new, uncharacterised chamber and the only
    detector whose HV moves. The code default has it scan=False at 0 V, which is
    the park applied in run_config_beam.py earlier today.

  * P2_MID / P2_OUT -> scan=False, pinned at their nominal 750/450 via fixed_hv.
    This is the one edit that is NOT cosmetic. _iter_schedule() steps only
    detectors that are BOTH scan=True and present in run_types.mesh_scan.start,
    while the "held" block at the end writes a fixed setpoint only for detectors
    with scan=False. Leaving MID/OUT at the default scan=True while omitting
    them from start would put them in neither set: they would be commanded
    NOWHERE and sit at whatever the previous run happened to leave on the crate.
    scan=False is what actively pins them.

  * The two uRWELL references are already scan=False at 620/420 in the code
    defaults -- the tracking telescope, untouched.

Scan shape: mesh 350 -> 450 in 10 V steps at a constant 300 V drift gap, which
is what the GUI's mesh_scan does natively (mesh and drift both step by i*step_v,
so drift - mesh is invariant; a NEGATIVE step_v steps UP).

Starting at 350 rather than 200: p2in_hvrange_1 already measured 200/250/300/330
/350 with the mesh drawing 0.001-0.003 uA mean, so the conditioning half of the
plan is done and does not need repeating. Starting AT 350 makes that point a
deliberate overlap between the two runs -- the same setpoint ~20 minutes apart,
which recovers part of what the dropped return point was for (did the chamber
change?), and gives a direct cross-check for stitching the two datasets.

The GUI's mesh_scan is uniform step / uniform duration, so the 14-point variable
schedule of p2in_hvrange_1 is not expressible here; this is the physics half at
a single fine 10 V step.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

import gui_run_config as grc

RUN_NAME = 'p2in_hvrange_2'
SCAN_DET = 'P2_IN'
HELD_AT_NOMINAL = {'P2_MID': {'mesh': 450, 'drift': 750},
                   'P2_OUT': {'mesh': 450, 'drift': 750}}
MESH_START, DRIFT_START = 350, 650      # gap 300 V
STEP_V = -10                            # negative => steps UP
POINTS = 11                             # 350, 360, ... 450
SUBRUN_MIN = 8

gui = grc.defaults_from_code()

gui['run_name'] = RUN_NAME
gui['notes'] = ('P2_IN HV range, physics half: new metallic-mesh Micromegas, '
                'mesh 350->450 at constant 300 V gap. Continues p2in_hvrange_1 '
                '(200-350 conditioning, mesh current 0.001-0.003 uA).')
gui['run_type'] = 'mesh_scan'

for det in gui['detectors']:
    name = det['name']
    if name == SCAN_DET:
        det['scan'] = True                     # the only detector that moves
    elif name in HELD_AT_NOMINAL:
        det['scan'] = False                    # <- actively pins them; see docstring
        det['fixed_hv'] = dict(HELD_AT_NOMINAL[name])
    # uRWELL references: already scan=False at 620/420 from the code defaults

gui['run_types']['mesh_scan'] = {
    'subrun_min': SUBRUN_MIN,
    'step_v': STEP_V,
    'points': POINTS,
    'start': {SCAN_DET: {'mesh': MESH_START, 'drift': DRIFT_START}},
}

# Validate BEFORE enabling, so a bad config can never be picked up by a
# Start Run click that lands while this script is running.
gui['enabled'] = False
ok, errs = grc.validate(gui)   # NB: returns (ok, errors), not a bare error list
if not ok:
    print('VALIDATION FAILED — nothing written:')
    for e in errs:
        print(f'  - {e}')
    raise SystemExit(1)

gui['enabled'] = True
with open(grc.GUI_CONFIG_PATH, 'w') as f:
    json.dump(gui, f, indent=4)

print(f'wrote {grc.GUI_CONFIG_PATH}  (enabled={gui["enabled"]})')
print(f'  run_name : {gui["run_name"]}')
print()
for det in gui['detectors']:
    role = 'SCANNED' if det.get('scan') else 'held   '
    print(f'  {role}  {det["name"]:<18} '
          f'{"" if det.get("scan") else det.get("fixed_hv")}')
print()
subs = grc.build_sub_runs(gui)
print(f'  sub-runs : {len(subs)}   '
      f'DAQ {sum(s["run_time"] for s in subs):.0f} min   '
      f'total ~{sum(s["run_time"] for s in subs) + len(subs) * 0.75 + 2:.0f} min')
print()
for s in subs:
    h = s['hvs']
    print(f'  {s["sub_run_name"]:<34} t={s["run_time"]:<3} '
          f'P2_IN={h["8"]["1"]}/{h["8"]["0"]} '
          f'MID={h["8"]["3"]}/{h["8"]["2"]} OUT={h["8"]["5"]}/{h["8"]["4"]} '
          f'uRW={h["8"]["6"]},{h["8"]["7"]}/{h["12"]["0"]},{h["12"]["1"]}')
