#!/usr/bin/env python3
"""Overnight 2D mesh x drift map for all three P2 stations (2026-07-28 -> 29).

A full mesh (gain) scan at each drift gap (field), all three P2 stations moving
together on identical setpoints, uRWELLs pinned at their operating point as the
tracking telescope and the per-sub-run beam normalisation.

Axis choice follows BEAM_2D_DRIFT_MODE='follow_mesh': drift follows mesh so the
GAP is constant along each inner scan. That keeps the two axes orthogonal — the
outer index is drift field, the inner index is gain — instead of skewing them,
which is what happens if drift is held at an absolute value while mesh steps and
the gap grows underneath it. No row of the map would then be at constant field.

Grid: 7 mesh x 6 gaps = 42 points. Mesh 390-450 covers the efficiency knee
measured today on the new P2_IN chamber (48.7% at 390 rising to 94.9% at 450),
so the map spans the whole turn-on rather than just the plateau. Gaps 200-450
reach drift 900 V at the top mesh point, which is exactly MAX_HV — tonight's
drift_06_g450 already sat there cleanly on MID/OUT.

Mesh steps UP inside each drift block rather than down. The committed 2D scan
steps down from 450, but stepping up means every block approaches its
high-discharge end with the current already in view, and today's rate
measurement (P2_IN excursions 1 -> 8 -> 26 over the last 20 V) is the reason to
care. The cost is a 450 -> 390 jump at each block boundary, which is just a ramp.

Sizing: 42 x 10 min = 420 DAQ min, ~470 min wall including ramps, so from ~23:00
it lands near 06:50 against an 08:00 stop — about 70 min of margin for the
overruns that a beam dropout causes (a no-beam sub-run ran 15 min for a
requested 10 tonight).

DISK: at tonight's 4688 Hz this is ~11 GB per 10 min sub-run, so ~460 GB for the
grid against ~256 GB reachable even after pruning every completed run. This run
CANNOT hold its own output — scripts/prune_active_run.py must run alongside it.
Backup to EOS moves ~2.6 GB/min against ~1.1 GB/min generated, so pruning keeps
ahead comfortably; that headroom is what makes the grid feasible at all.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

os.environ.setdefault('DAQ_RUN_PLAN', 'commissioning')

from run_config_beam import Config, OPERATING_HV, DET_HV, MAX_HV

RUN_NAME = 'p2_mesh_drift_2d_1'
P2 = ('P2_IN', 'P2_MID', 'P2_OUT')

MESH_POINTS = (390, 400, 410, 420, 430, 440, 450)   # inner axis: gain
DRIFT_GAPS = (200, 250, 300, 350, 400, 450)         # outer axis: field
SUBRUN_MIN = 10
SETTLE_S = 20

# --- Retakes, prepended ----------------------------------------------------
# p2_mesh_drift_eff_1's mesh scan ran 15:40-17:43 on 07-28, straight through the
# H4 beam outage, and flag_beam_quality.py marked 10 of its sub-runs NO_BEAM or
# LOW_BEAM. Five of those ten need no retake: all four drift_* points and eff_00
# are reproduced setpoint-for-setpoint inside the grid below — the m440 column
# repeats P2_IN's nominal 440 and the m450 column repeats MID/OUT's 450, so each
# station's exact (mesh, drift) pair recurs. eff_00 is additionally 1 of 12
# identical repeats, with eff_01..eff_11 already holding 30.9 M events.
#
# These five are the ones the grid does NOT cover: uniform mesh at gap 300,
# either below its 390 floor (355/365/385) or interleaved between its 10 V steps
# (395/405). Taken first, both because they are the lowest-mesh (so lowest
# discharge) points of the night and because it secures them if the 08:00 stop
# cuts the grid short.
#
# events/FEU originally recorded, against the ~1.0 M their good neighbours in the
# same scan got: m355 0, m365 198,776, m385 265,060, m395 132,347, m405 0.
RETAKE_MESH = (355, 365, 385, 395, 405)
RETAKE_GAP = 300        # the gap the eff_1 mesh scan held on all three stations
RETAKE_MIN = 10         # matches the length originally asked for those points


def _hvs(p2_setpoints):
    """{card: {channel: V}} — walks DET_HV so EVERY wired electrode is commanded
    explicitly. A channel omitted from the map is never set and would sit at
    whatever the previous run left on the crate rather than at a known voltage."""
    hvs = {}
    for det_name, det_hv in DET_HV.items():
        for role, (card, chan) in det_hv.items():
            volts = p2_setpoints.get(det_name, {}).get(role,
                                                       OPERATING_HV[det_name][role])
            assert 0 <= volts <= MAX_HV[det_name][role], (
                f'{det_name} {role} {volts} V exceeds max '
                f'{MAX_HV[det_name][role]} V')
            hvs.setdefault(str(card), {})[str(chan)] = volts
    return hvs


sub_runs = []

# Retakes first — see RETAKE_MESH above. Named retake_g300_mNNN rather than
# reusing eff_1's mesh_NN_mNNN, so the two never collide in an analysis glob and
# the provenance stays readable: same setpoints, different run.
for mesh in RETAKE_MESH:
    sub_runs.append({
        'sub_run_name': f'retake_g{RETAKE_GAP}_m{mesh}',
        'run_time': RETAKE_MIN,
        'post_pause_s': 0,
        'settle_time': SETTLE_S,
        'hvs': _hvs({d: {'mesh': mesh, 'drift': mesh + RETAKE_GAP} for d in P2}),
    })

n_retakes = len(sub_runs)

for k, gap in enumerate(DRIFT_GAPS):
    for j, mesh in enumerate(MESH_POINTS):
        sub_runs.append({
            'sub_run_name': f'g{gap}_m{mesh}',
            'run_time': SUBRUN_MIN,
            'post_pause_s': 0,
            'settle_time': SETTLE_S,
            'hvs': _hvs({d: {'mesh': mesh, 'drift': mesh + gap} for d in P2}),
        })

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
config.sub_runs = sub_runs

out_path = f'config/json_run_configs/{RUN_NAME}.json'
config.write_to_file(out_path)

daq_min = sum(s['run_time'] for s in sub_runs)
overhead = len(sub_runs) * 1.2
print(f'wrote {out_path}')
print(f'  detectors read : {config.included_detectors}')
print(f'  active FEUs    : {config.get_active_feus()}')
print(f'  retakes        : {n_retakes} x {RETAKE_MIN} min at gap {RETAKE_GAP} '
      f'(mesh {", ".join(str(m) for m in RETAKE_MESH)}) — FIRST')
print(f'  grid           : {len(MESH_POINTS)} mesh x {len(DRIFT_GAPS)} gaps '
      f'= {len(sub_runs) - n_retakes} points')
print(f'  sub-runs total : {len(sub_runs)}')
print(f'  DAQ time       : {daq_min} min '
      f'({n_retakes * RETAKE_MIN} retake + {daq_min - n_retakes * RETAKE_MIN} grid)')
print(f'  total estimate : ~{daq_min + overhead:.0f} min '
      f'(~{(daq_min + overhead) / 60:.2f} h incl. ramps)')
print(f'  disk at 11 GB/pt: ~{len(sub_runs) * 11} GB generated '
      f'(prune_active_run.py REQUIRED)')
print()
print('  retakes (gap 300, taken before the grid):')
print('    mesh  ' + '  '.join(f'{m:>5}' for m in RETAKE_MESH))
print('    drift ' + '  '.join(f'{m + RETAKE_GAP:>5}' for m in RETAKE_MESH))
print()
print('  gap \\ mesh   ' + '  '.join(f'{m:>5}' for m in MESH_POINTS))
for gap in DRIFT_GAPS:
    print(f'  gap {gap:<3}      ' +
          '  '.join(f'{m + gap:>5}' for m in MESH_POINTS) + '   <- drift V')
print()
print(f'  uRWELLs held at {OPERATING_HV["EIC_uRWELL_front"]["drift"]}/'
      f'{OPERATING_HV["EIC_uRWELL_front"]["resist"]} in all {len(sub_runs)} points')
print(f'  max drift commanded: {max(DRIFT_GAPS) + max(MESH_POINTS)} V '
      f'(MAX_HV = {MAX_HV["P2_MID"]["drift"]} V)')
