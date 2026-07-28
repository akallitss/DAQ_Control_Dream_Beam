#!/usr/bin/env python3
"""Build the 2026-07-28 evening run: mesh scan -> drift scan -> 2 h efficiency.

One config, three phases, ~5 h 25 m, sized to the 15:15 -> 21:00 beam window
(Alexandra). Written as a single run so it goes start-to-finish on one click with
nobody needing to be present at a phase boundary.

PHASE 1 - mesh scan, 11 points, mesh 345 -> 445 in 10 V steps, 10 min each.
    All THREE P2 stations step together, with drift following so each station's
    drift gap stays at 300 V. This is the standard BEAM_SCAN shape (gain isolated
    by holding the gap fixed), but note it moves all three rather than the
    committed BEAM_SCAN_DETS = MID+OUT: P2_IN is included on request, which also
    interleaves it 5 V off today's p2in_hvrange_2 grid (350..450) and so doubles
    the sampling of the new chamber's turn-on.

PHASE 2 - drift scan, 7 points, gaps 150 -> 450 V in 50 V steps, 10 min each.
    Mesh FIXED at each station's own operating value, drift stepped alone, so the
    only thing changing is the drift field (electron transparency / primary
    collection). This is the axis that has never been measured for the new P2_IN
    chamber -- every one of today's 17 mesh points held its gap at 300 V, a value
    inherited from MID/OUT. Stepping UP from the low-field end rather than down
    from 450, so the high-field points are approached with the current in view.

PHASE 3 - efficiency, 12 x 10 min = 2 h at the committed operating points.
    Deliberately LAST and built from identical sub-runs: if the beam stops early
    or the window slips, truncating it costs statistics and nothing else -- no
    scan point is left half-measured. P2_IN sits at the 440/740 point measured
    today (p2in_hvrange_1 + _2); MID/OUT at their established 450/750.

The uRWELL references are held at 620/420 in EVERY sub-run of all three phases --
they are the tracking telescope, and they also give a per-sub-run beam-intensity
normalisation, which is what let today's 380 V point survive a 40% SPS dip with
no correction.

Volume: ~0.35 GB per DAQ minute measured on p2in_hvrange_2, so ~106 GB for the
300 DAQ minutes here. 256 GB free at build time.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

os.environ.setdefault('DAQ_RUN_PLAN', 'commissioning')

from run_config_beam import Config, OPERATING_HV, DET_HV, MAX_HV

RUN_NAME = 'p2_mesh_drift_eff_1'
P2 = ('P2_IN', 'P2_MID', 'P2_OUT')

# --- phase 1: mesh scan, gap held ---
MESH_START, MESH_STOP, MESH_STEP = 345, 445, 10
MESH_GAP = 300
MESH_MIN = 10
MESH_SETTLE = 20            # s to let the mesh settle after each ramp

# --- phase 2: drift scan, mesh fixed ---
DRIFT_GAPS = (150, 200, 250, 300, 350, 400, 450)
DRIFT_MIN = 10
DRIFT_SETTLE = 20

# --- phase 3: efficiency ---
EFF_SUBRUNS, EFF_MIN = 12, 10
# no settle: every eff sub-run is at the SAME HV, so set_hvs() returns as soon as
# it confirms the channels are already on setpoint and there is nothing to wait for.
EFF_SETTLE = 0


def _hvs(p2_setpoints):
    """{card: {channel: V}} from {det: {'mesh': V, 'drift': V}} for the P2
    stations, with every other detector at its operating point.

    Walks DET_HV, not included_detectors, so EVERY wired electrode is commanded
    explicitly — including the uRWELL 'resist' channels on card 12. An omitted
    channel is never set and would sit at whatever the previous run left on the
    crate rather than at a known voltage.
    """
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

# ---- phase 1 -------------------------------------------------------------
mesh_points = list(range(MESH_START, MESH_STOP + 1, MESH_STEP))
for i, mesh in enumerate(mesh_points):
    sub_runs.append({
        'sub_run_name': f'mesh_{i:02d}_m{mesh}',
        'run_time': MESH_MIN,
        'post_pause_s': 0,
        'settle_time': MESH_SETTLE,
        'hvs': _hvs({d: {'mesh': mesh, 'drift': mesh + MESH_GAP} for d in P2}),
    })

# ---- phase 2 -------------------------------------------------------------
# Mesh fixed at each station's OWN operating point — P2_IN's is 440, MID/OUT 450,
# so the drift values differ per detector while the gap is common. The sub-run is
# therefore named by gap, which is the quantity actually being scanned.
for i, gap in enumerate(DRIFT_GAPS):
    sub_runs.append({
        'sub_run_name': f'drift_{i:02d}_g{gap}',
        'run_time': DRIFT_MIN,
        'post_pause_s': 0,
        'settle_time': DRIFT_SETTLE,
        'hvs': _hvs({d: {'mesh': OPERATING_HV[d]['mesh'],
                         'drift': OPERATING_HV[d]['mesh'] + gap} for d in P2}),
    })

# ---- phase 3 -------------------------------------------------------------
for i in range(EFF_SUBRUNS):
    sub_runs.append({
        'sub_run_name': f'eff_{i:02d}',
        'run_time': EFF_MIN,
        'post_pause_s': 0,
        'settle_time': EFF_SETTLE,
        'hvs': _hvs({}),                      # everything at OPERATING_HV
    })

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
config.sub_runs = sub_runs

out_path = f'config/json_run_configs/{RUN_NAME}.json'
config.write_to_file(out_path)

daq_min = sum(s['run_time'] for s in sub_runs)
overhead = len(sub_runs) * 0.75 + 2
print(f'wrote {out_path}')
print(f'  detectors read : {config.included_detectors}')
print(f'  active FEUs    : {config.get_active_feus()}')
print(f'  sub-runs       : {len(sub_runs)}  '
      f'({len(mesh_points)} mesh + {len(DRIFT_GAPS)} drift + {EFF_SUBRUNS} eff)')
print(f'  DAQ time       : {daq_min} min')
print(f'  total estimate : ~{daq_min + overhead:.0f} min '
      f'(~{(daq_min + overhead) / 60:.2f} h incl. ramps/boundaries)')
print(f'  data estimate  : ~{daq_min * 0.352:.0f} GB')
print()
hdr = f'  {"sub-run":<16}{"min":>4}  ' + '  '.join(f'{d:>16}' for d in P2) + '   uRW'
print(hdr)
for s in sub_runs:
    h = s['hvs']
    cells = []
    for d in P2:
        mc, mch = DET_HV[d]['mesh']
        dc, dch = DET_HV[d]['drift']
        m, dr = h[str(mc)][str(mch)], h[str(dc)][str(dch)]
        cells.append(f'{m}/{dr} (g{dr - m})'.rjust(16))
    urw = f'{h["8"]["6"]}/{h["12"]["0"]}'
    print(f'  {s["sub_run_name"]:<16}{s["run_time"]:>4}  ' + '  '.join(cells) + f'   {urw}')
