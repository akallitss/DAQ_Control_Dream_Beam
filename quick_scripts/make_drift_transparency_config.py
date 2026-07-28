#!/usr/bin/env python3
"""Build the drift-transparency scan (2026-07-27 evening, ~50 min before access).

Push the drift gap PAST the 450 V ceiling that has truncated every drift curve
in the campaign so far, and look for the mesh-transparency turnover.

Why this is new territory: MAX_HV caps drift at 900 V, so at the operating mesh
of 450 the largest reachable gap is 900-450 = 450 V. The overnight 2D map
(drift_mesh_2d_2) therefore stopped its gap axis at 450 for EVERY mesh row --
including the low-mesh rows, which had headroom left over. The result is that
no point in the whole campaign sits above gap 450:

    mesh 450 -> gap 450 is the ceiling (drift 900)   [measured, 8 min]
    mesh 390 -> gap 510 is the ceiling (drift 900)   [never measured above 450]

So the entire gap 450-510 region is unexplored, and it is only reachable by
trading mesh down.

Why mesh 390 specifically:
  * It has a full existing drift curve to attach to -- the 2D map covers gaps
    150/200/250/300/350/400/450 at mesh 390 (drift 540...840), so the new points
    extend a measured curve instead of floating free.
  * The turnover being hunted is set by the FIELD RATIO E_amp/E_drift. Lowering
    mesh lowers E_amp, which moves the turnover DOWN in drift field, i.e. into
    the range we can actually reach. At mesh 450 it may well sit above gap 450
    and be permanently out of reach behind the 900 V ceiling.
  * Still enough gain to measure efficiency cleanly (the 07-26 low_mesh_scan_1
    ran MID+OUT down to 330 at gap 300 without trouble).

Design -- monotonic scan bracketed by a repeated anchor:

    840(anchor) 860 880 900 840(anchor)
    gap:  450    470 490 510   450

The two anchor blocks are the same setpoint at the start and end of the run, so
the difference between them is a DIRECT measurement of anything that drifted
over the ~40 minutes (SPS intensity, detector conditioning), which is the
systematic the interleaved eff_drift_ab_1 design was built to defeat earlier
today. Their mean position in time is the centre of the run, so a linear drift
cancels out of the anchor-corrected curve. The anchor is also the one setpoint
that already exists in the 2D map, which ties this run to this morning's data.

Safety: drift 900 V is the committed MAX_HV and is proven -- drift_mesh_2d_2 ran
mesh 450 / drift 900 overnight with zero trips. The CHANNEL voltage here is
identical (900 V); only the gap voltage is larger, and mesh 390 draws less than
the 450 the crate has been running all day. Mesh never exceeds 450 V anywhere.
"""
import os
import sys

# Run from anywhere: the repo root holds run_config_beam and is where the
# relative paths in the generated config are resolved from.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

os.environ.setdefault('DAQ_RUN_PLAN', 'commissioning')

from run_config_beam import Config, OPERATING_HV, DET_HV, MAX_HV

RUN_NAME = 'drift_transparency_1'
BLOCK_MIN = 6                      # minutes of DAQ per block
SCAN_MESH = 390                    # fixed mesh for MID/OUT (well under the 450 ceiling)
ANCHOR_DRIFT = 840                 # gap 450 -- the existing 2D-map point
# 4 new points at <=20 V spacing across the unexplored gap 450-510 band, rather
# than 3 at 20 V: the turnover is a SHAPE, and 6 min still buys ~400 k triggers
# per point at the measured ~1150 Hz, far past where statistics is the limit.
ORDER = [840, 860, 875, 890, 900, 840]  # gaps 450, 470, 485, 500, 510, 450
SCAN_DETS = ('P2_MID', 'P2_OUT')   # P2_IN is out of the beam line and parked at 0 V


def hvs_for(drift_v):
    """{card: {channel: V}} with SCAN_DETS at (SCAN_MESH, drift_v) and everything
    else at its operating point. Mirrors _operating_hvs(): walks DET_HV, so
    P2_IN's parked 0 V is emitted and set_hvs() actively powers 8:0/8:1 off, and
    the uRWELL references stay pinned at 620/420 as the tracking telescope."""
    hvs = {}
    for det_name, det_hv in DET_HV.items():
        for role, (card, chan) in det_hv.items():
            volts = OPERATING_HV[det_name][role]
            if det_name in SCAN_DETS:
                if role == 'drift':
                    volts = drift_v
                elif role == 'mesh':
                    volts = SCAN_MESH
            assert 0 <= volts <= MAX_HV[det_name][role], (
                f'{det_name} {role} {volts} V exceeds max '
                f'{MAX_HV[det_name][role]} V')
            hvs.setdefault(str(card), {})[str(chan)] = volts
    return hvs


config = Config()
config.run_name = RUN_NAME
config.run_out_dir = f'{config.data_out_dir}{RUN_NAME}/'
config.processor_info['run_dir'] = config.run_out_dir
config.hv_info['run_out_dir'] = config.run_out_dir
config.dream_daq_info['run_directory'] = config.run_out_dir
config.dream_daq_info['data_out_dir'] = config.run_out_dir
config.power_off_hv_at_end = True
config.resume = False
config.start_time = None

config.sub_runs = [
    {
        'sub_run_name': f'tr_{i:02d}_d{drift}' + ('_anchor' if drift == ANCHOR_DRIFT else ''),
        'run_time': BLOCK_MIN,
        'post_pause_s': 0,
        'hvs': hvs_for(drift),
    }
    for i, drift in enumerate(ORDER)
]

out_path = f'config/json_run_configs/{RUN_NAME}.json'
config.write_to_file(out_path)

anchors = [i for i, d in enumerate(ORDER) if d == ANCHOR_DRIFT]
print(f'wrote {out_path}')
print(f'  detectors      : {config.included_detectors}')
print(f'  active FEUs    : {config.get_active_feus()}')
print(f'  scan dets      : {SCAN_DETS} at fixed mesh {SCAN_MESH} V')
print(f'  blocks         : {len(ORDER)} x {BLOCK_MIN} min = {len(ORDER) * BLOCK_MIN} min DAQ')
print(f'  drift points   : {ORDER}')
print(f'  drift gaps     : {[d - SCAN_MESH for d in ORDER]} V')
print(f'  NEW gaps       : {sorted({d - SCAN_MESH for d in ORDER if d - SCAN_MESH > 450})} V '
      f'(nothing in the campaign is above gap 450)')
print(f'  anchor blocks  : {anchors} at drift {ANCHOR_DRIFT} (gap {ANCHOR_DRIFT - SCAN_MESH}) '
      f'-- mean index {sum(anchors) / len(anchors):.2f} of {len(ORDER) - 1}')
print(f'  max mesh       : {SCAN_MESH} V (limit 450)')
print(f'  max drift      : {max(ORDER)} V (limit {MAX_HV["P2_MID"]["drift"]})')
print(f'  power off at end: {config.power_off_hv_at_end}')
print('donzo')
