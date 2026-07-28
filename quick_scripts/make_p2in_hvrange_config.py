#!/usr/bin/env python3
"""Build the P2_IN HV-range characterisation scan (2026-07-28 morning beam).

A NEW metallic-mesh Micromegas was installed in the P2_IN position. Same strip
mapping and the same HV connector mapping as the chambers it replaces, so the
DET_HV channel map (8:0 drift, 8:1 mesh) and the FEU-3 cabling are unchanged --
but it is an individual chamber that has never been powered here, so its gain
curve and its discharge limit are unmeasured. This run measures both.

Why this shape:

  * Mesh stepped UP from far below turn-on, not down from a nominal. Every
    committed scan in this campaign (BEAM_SCAN_DETS) steps mesh DOWN from a
    known-good operating point; that is exactly what you cannot do with a
    chamber whose operating point is the unknown. The first four points sit
    below amplification turn-on and are pure conditioning -- they exist to put
    voltage across the amplification gap in small steps while we watch the
    current, not to collect physics.

  * The drift gap is held at 300 V from the FIRST point (drift = mesh + 300),
    rather than ramping the mesh alone with the drift off. Two reasons: it is
    the configuration the chamber will actually be operated in, and it means
    every point yields hits as well as a current reading, so the conditioning
    points also show us where gain switches on.

  * Only P2_IN moves. P2_MID, P2_OUT and both uRWELL references hold at their
    nominal 750/450 and 620/420 for the whole run, which makes them a clean
    4-plane tracking telescope for a per-point P2_IN efficiency AND a per-point
    beam-intensity normalisation. That reference is why this scan needs no
    repeated anchor block of the kind drift_transparency_1 used -- a change in
    SPS intensity moves the reference planes too and divides out.

  * The final point REPEATS mesh 390. The reference planes handle beam drift,
    but they say nothing about whether the CHAMBER changed over the ramp. For a
    brand-new detector being taken to its limit for the first time, conditioning
    or damage is the systematic worth measuring, and the only way to see it is
    to come back to a point already visited and compare.

Ceiling: mesh stops at 450 V, the committed MAX_HV['P2_IN'] and the voltage
P2_MID/P2_OUT run every day. This is the same construction, so there is no
physics reason to go above what the known-good chambers use, and the run is
allowed to STOP EARLY but never to go higher. Max drift reached is 450+300 =
750 V, well under the 900 V drift ceiling.

Watch 8:0 / 8:1 imon in each sub-run's hv_monitor.csv (written every 10 s).
NOTE: monitor_hvs() only LOGS -- it does not abort. A rising current will not
stop the run by itself; the crate trip and the operator are the interlock.
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

RUN_NAME = 'p2in_hvrange_1'
SCAN_DET = 'P2_IN'          # the new chamber -- the only detector that moves
GAP_V = 300                 # drift - mesh, held constant at the campaign nominal
SETTLE_S = 20               # let the mesh stabilise after each ramp before DAQ

# (mesh_V, minutes). Below turn-on the points are short -- they buy a current
# reading, not statistics. Above ~350 they are long enough for a gain and
# efficiency measurement, and the step shrinks 20 -> 10 V approaching the
# ceiling because gain is exponential and the discharge onset, if there is one,
# will be up there.
POINTS = [
    (200, 2), (250, 2), (300, 2), (330, 2),          # conditioning, no gain yet
    (350, 9), (370, 9), (390, 9),                    # turn-on
    (400, 9), (410, 9), (420, 9), (430, 9),          # gain curve, 10 V steps
    (440, 9), (450, 9),                              # ceiling (MAX_HV)
    (390, 9),                                        # RETURN point -- chamber changed?
]
RETURN_IDX = len(POINTS) - 1   # last entry is the repeat, flagged in its name


def hvs_for(mesh_v):
    """{card: {channel: V}} with SCAN_DET at (mesh_v, mesh_v + GAP_V) and every
    other detector at its operating point. Mirrors _operating_hvs(): walks
    DET_HV, not included_detectors, so every channel is emitted explicitly and
    the reference planes are actively held rather than left wherever the
    previous run put them."""
    hvs = {}
    for det_name, det_hv in DET_HV.items():
        for role, (card, chan) in det_hv.items():
            volts = OPERATING_HV[det_name][role]
            if det_name == SCAN_DET:
                volts = mesh_v if role == 'mesh' else mesh_v + GAP_V
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
        'sub_run_name': (f'hv_{i:02d}_m{mesh}'
                         + ('_return' if i == RETURN_IDX else '')),
        'run_time': minutes,
        'post_pause_s': 0,
        'settle_time': SETTLE_S,
        'hvs': hvs_for(mesh),
    }
    for i, (mesh, minutes) in enumerate(POINTS)
]

out_path = f'config/json_run_configs/{RUN_NAME}.json'
config.write_to_file(out_path)

daq_min = sum(m for _, m in POINTS)
# ~45 s per sub-run boundary (ramp + settle + DAQ stop/start), measured across
# the campaign, plus the initial ramp from off.
overhead_min = len(POINTS) * 0.75 + 2
print(f'wrote {out_path}')
print(f'  scanned        : {SCAN_DET}  (mesh {POINTS[0][0]} -> {max(m for m, _ in POINTS)} V, '
      f'drift = mesh + {GAP_V})')
print(f'  held at nominal: '
      f'{[d for d in DET_HV if d != SCAN_DET]}')
print(f'  detectors read : {config.included_detectors}')
print(f'  active FEUs    : {config.get_active_feus()}')
print(f'  sub-runs       : {len(POINTS)}')
print(f'  DAQ time       : {daq_min} min')
print(f'  total estimate : ~{daq_min + overhead_min:.0f} min '
      f'(incl. ~{overhead_min:.0f} min of ramps/boundaries)')
print()
print('  point   mesh  drift   min')
for i, (mesh, minutes) in enumerate(POINTS):
    tag = '  <- RETURN (repeat of point 06)' if i == RETURN_IDX else ''
    print(f'  {i:>5}   {mesh:>4}  {mesh + GAP_V:>5}   {minutes:>3}{tag}')
