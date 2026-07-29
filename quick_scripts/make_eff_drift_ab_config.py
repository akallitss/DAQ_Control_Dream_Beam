#!/usr/bin/env python3
"""Build the interleaved drift A/B efficiency run (2026-07-27, P2_IN removed).

Two working points at mesh 450 — drift 750 (today's committed nominal) and
drift 800 (gap 350) — alternated in short blocks rather than run as two
contiguous halves.

Why interleave: the difference being measured is ~1-3 %, which is the same size
as the run-to-run systematic (the 5 V mesh scan of 07-25 shows +-3 % scatter
between adjacent points whose true efficiency differs by ~1 %). Two contiguous
halves would confound that difference with anything drifting on a half-run
timescale — SPS intensity, and detector conditioning, which is still measurably
happening (P2_MID mesh median current fell 0.012 -> 0.006 uA across the 17
sub-runs of eff_nominal_1 earlier today).

The block order is balanced so both settings have the SAME mean position in
time, which cancels any drift linear in time:

    750 800 800 | 800 800 750 | 750 800 800 | 800 800 750
    750 at blocks 1,6,7,12  -> mean 6.5
    800 at blocks 2,3,4,5,8,9,10,11 -> mean 6.5

2:1 split toward 800 because 750 already has 3.08 h of depth from eff_nominal_1
this morning, while drift 800 has only a single 8-minute point in the whole
campaign (drift_mesh_2d_2/dm2_06_04).
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

RUN_NAME = 'eff_drift_ab_1'
BLOCK_MIN = 19                     # minutes of DAQ per block
DRIFT_A, DRIFT_B = 750, 800        # MID/OUT drift setpoints, mesh fixed at 450
ORDER = [DRIFT_A, DRIFT_B, DRIFT_B,
         DRIFT_B, DRIFT_B, DRIFT_A,
         DRIFT_A, DRIFT_B, DRIFT_B,
         DRIFT_B, DRIFT_B, DRIFT_A]
AB_DETS = ('P2_MID', 'P2_OUT')     # P2_IN is out of the beam line and parked at 0 V


def hvs_for(drift_v):
    """{card: {channel: V}} with AB_DETS drift at drift_v, everything else at
    its operating point. Mirrors _operating_hvs(): walks DET_HV, so P2_IN's
    parked 0 V is emitted and set_hvs() actively powers 8:0/8:1 off."""
    hvs = {}
    for det_name, det_hv in DET_HV.items():
        for role, (card, chan) in det_hv.items():
            volts = OPERATING_HV[det_name][role]
            if det_name in AB_DETS and role == 'drift':
                volts = drift_v
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
# RunCtrl's own fdf staging dir, NOT run_out_dir: pointing it at the run
# output tree makes the DAQ write its fdfs loose in each subrun while
# copy_on_fly writes a second copy into raw_daq_data/ — a full duplicate
# that the Disk Space tab cannot attribute to a component or reclaim.
# Same re-derivation as run_config_beam.py:1103.
config.dream_daq_info['run_directory'] = f'{config.base_out_dir}dream_run/{RUN_NAME}/'
config.dream_daq_info['data_out_dir'] = config.run_out_dir
config.power_off_hv_at_end = True
config.resume = False
config.start_time = None

config.sub_runs = [
    {
        'sub_run_name': f'ab_{i:02d}_d{drift}',
        'run_time': BLOCK_MIN,
        'post_pause_s': 0,
        'hvs': hvs_for(drift),
    }
    for i, drift in enumerate(ORDER)
]

out_path = f'config/json_run_configs/{RUN_NAME}.json'
config.write_to_file(out_path)

n_a = ORDER.count(DRIFT_A)
n_b = ORDER.count(DRIFT_B)
mean_a = sum(i for i, d in enumerate(ORDER) if d == DRIFT_A) / n_a
mean_b = sum(i for i, d in enumerate(ORDER) if d == DRIFT_B) / n_b
print(f'wrote {out_path}')
print(f'  detectors      : {config.included_detectors}')
print(f'  active FEUs    : {config.get_active_feus()}')
print(f'  blocks         : {len(ORDER)} x {BLOCK_MIN} min = {len(ORDER) * BLOCK_MIN} min DAQ')
print(f'  drift {DRIFT_A} V    : {n_a} blocks ({n_a * BLOCK_MIN} min), mean block index {mean_a:.2f}')
print(f'  drift {DRIFT_B} V    : {n_b} blocks ({n_b * BLOCK_MIN} min), mean block index {mean_b:.2f}')
print(f'  balanced       : {"YES" if abs(mean_a - mean_b) < 1e-9 else "NO — drift will not cancel"}')
print(f'  power off at end: {config.power_off_hv_at_end}')
print('  order          : ' + ' '.join(str(d) for d in ORDER))
print('donzo')
