#!/usr/bin/env python3
"""Flag sub-runs that were taken without beam, so retakes are recorded on disk.

Written 2026-07-28 after the beam dropped out during p2_mesh_drift_eff_1 and
mesh_01_m355 recorded ZERO events while the DAQ ran happily for 15 minutes. That
failure is invisible from the DAQ side -- nothing errors, the sub-run completes,
.subrun_complete is written, and the file is a valid 342-byte fdf with a header
and no events. It only shows up if you look at the event count.

The verdict lives in the sub-run directory, not in a chat log or someone's
memory, because the decision about whether to retake gets made days later by
whoever is doing the analysis.

Idempotent and safe to run repeatedly while the run is in progress: in-progress
sub-runs are skipped (no RunCtrl summary line exists yet) and re-running only
rewrites verdicts.

WHY THE TRIGGER RATE IS A VALID BEAM PROXY HERE: the trigger is the external
TCM/scintillator coincidence, not anything derived from the detectors, so the
rate does not depend on the mesh or drift setpoint being scanned. Across a run
whose sub-runs are all the same length, rate differences are beam differences.
That is what makes the run's own median a fair reference and means this works
without knowing the nominal spill intensity.
"""
import json
import os
import re
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from get_run_events import _read_events_from_logs

RUN_DIR = '/local/home/banco/P2_data/TB_July2026_H4/runs'
RUN_NAME = sys.argv[1] if len(sys.argv) > 1 else 'p2_mesh_drift_eff_1'

# Fractions of the run's own median event count.
EMPTY_FRAC = 0.05     # at or below this the sub-run has no usable statistics
LOW_FRAC = 0.60       # below this it ran through a partial outage or a deep dip
ABS_EMPTY = 10_000    # ...and an absolute floor, for when the median itself is
                      # suspect because most of the run was beam-off

RATE_RE = re.compile(r'RunTime\s+(\d+)h\s*(\d+)m\s*(\d+)s\s+IntRate=\s*([\d.]+)Hz')


def read_runtime(subrun_path):
    """(seconds, Hz) from the RunCtrl summary, or (None, None) if it never
    printed one -- which is itself the signature of a sub-run that took no data."""
    for root in (os.path.join(subrun_path, 'raw_daq_data'), subrun_path):
        if not os.path.isdir(root):
            continue
        for fname in sorted(os.listdir(root)):
            if not fname.startswith('RunCtrl') or not fname.endswith('.log'):
                continue
            try:
                with open(os.path.join(root, fname), errors='replace') as f:
                    for line in f:
                        m = RATE_RE.search(line)
                        if m:
                            h, mnt, s, hz = m.groups()
                            return int(h) * 3600 + int(mnt) * 60 + int(s), float(hz)
            except OSError:
                continue
    return None, None


def hv_of(subrun_cfg):
    """P2 mesh/drift setpoints, carried into the flag file so a retake config can
    be built straight from it without re-deriving anything."""
    hvs = subrun_cfg.get('hvs', {})
    out = {}
    for det, (dc, dch), (mc, mch) in (('P2_IN', ('8', '0'), ('8', '1')),
                                      ('P2_MID', ('8', '2'), ('8', '3')),
                                      ('P2_OUT', ('8', '4'), ('8', '5'))):
        try:
            out[det] = {'mesh': hvs[mc][mch], 'drift': hvs[dc][dch]}
        except KeyError:
            pass
    return out


run_path = os.path.join(RUN_DIR, RUN_NAME)
if not os.path.isdir(run_path):
    sys.exit(f'no such run: {run_path}')

cfg_path = os.path.join(run_path, 'run_config.json')
cfg_subruns = {}
if os.path.isfile(cfg_path):
    with open(cfg_path) as f:
        cfg_subruns = {s['sub_run_name']: s for s in json.load(f).get('sub_runs', [])}

# --- pass 1: gather ------------------------------------------------------
rows = []
for name in sorted(os.listdir(run_path)):
    p = os.path.join(run_path, name)
    if not os.path.isdir(p) or name == 'raw_daq_data':
        continue
    if not os.path.exists(os.path.join(p, '.subrun_complete')):
        rows.append({'name': name, 'state': 'IN_PROGRESS'})
        continue
    raw = os.path.join(p, 'raw_daq_data')
    events = _read_events_from_logs(raw) if os.path.isdir(raw) else None
    if events is None:
        events = _read_events_from_logs(p)
    secs, hz = read_runtime(p)
    rows.append({'name': name, 'state': 'COMPLETE', 'path': p,
                 'events': events or 0, 'seconds': secs, 'hz': hz,
                 'requested_min': cfg_subruns.get(name, {}).get('run_time')})

# --- pass 2: reference ---------------------------------------------------
counts = [r['events'] for r in rows
          if r['state'] == 'COMPLETE' and r['events'] > ABS_EMPTY]
median = statistics.median(counts) if counts else 0

# --- pass 3: verdict + markers ------------------------------------------
retake = []
for r in rows:
    if r['state'] != 'COMPLETE':
        continue
    ev = r['events']
    frac = ev / median if median else 0.0
    r['frac'] = frac
    if ev <= ABS_EMPTY or (median and frac <= EMPTY_FRAC):
        r['verdict'], r['why'] = 'NO_BEAM', (
            f'{ev} events per FEU'
            + (f' = {frac:.1%} of the run median {median:,.0f}' if median else '')
            + ' -- no usable statistics, RETAKE')
    elif median and frac < LOW_FRAC:
        r['verdict'], r['why'] = 'LOW_BEAM', (
            f'{ev:,} events per FEU = {frac:.1%} of the run median '
            f'{median:,.0f} -- partial outage or deep intensity dip, REVIEW')
    else:
        r['verdict'], r['why'] = 'OK', (
            f'{ev:,} events per FEU'
            + (f' = {frac:.0%} of median' if median else ''))

    # a sub-run that ran materially longer than asked was waiting on triggers
    if r['requested_min'] and r['seconds'] and \
            r['seconds'] > r['requested_min'] * 60 * 1.2:
        r['why'] += (f'; ran {r["seconds"] / 60:.1f} min for a requested '
                     f'{r["requested_min"]} min')

    marker = os.path.join(r['path'], '.beam_quality.json')
    with open(marker, 'w') as f:
        json.dump({'sub_run': r['name'], 'verdict': r['verdict'],
                   'events_per_feu': ev, 'run_median_events': median,
                   'fraction_of_median': round(frac, 4),
                   'seconds': r['seconds'], 'int_rate_hz': r['hz'],
                   'requested_min': r['requested_min'],
                   'reason': r['why'],
                   'hv': hv_of(cfg_subruns.get(r['name'], {}))}, f, indent=2)

    loud = os.path.join(r['path'], 'NEEDS_RETAKE')
    if r['verdict'] == 'NO_BEAM':
        with open(loud, 'w') as f:
            f.write(f'{r["name"]}: {r["why"]}\n')
        retake.append(r)
    elif os.path.exists(loud):
        os.remove(loud)      # beam came back mid-analysis / rerun reclassified it

# --- report --------------------------------------------------------------
lines = [f'# Beam quality — {RUN_NAME}', '',
         f'Median events/FEU over sub-runs with beam: {median:,.0f}', '',
         '| sub-run | events/FEU | % median | rate | verdict |',
         '|---|---:|---:|---:|---|']
for r in rows:
    if r['state'] != 'COMPLETE':
        lines.append(f'| {r["name"]} | — | — | — | in progress |')
        continue
    lines.append(f'| {r["name"]} | {r["events"]:,} | {r["frac"]:.0%} | '
                 f'{r["hz"] or 0:.0f} Hz | **{r["verdict"]}** |')
lines += ['']
if retake:
    lines.append('## Retake')
    for r in retake:
        hv = hv_of(cfg_subruns.get(r['name'], {}))
        hv_s = ', '.join(f'{d} {v["mesh"]}/{v["drift"]}' for d, v in hv.items())
        lines.append(f'- **{r["name"]}** — {r["why"]}  \n  HV: {hv_s}')
else:
    lines.append('No sub-run needs a retake.')

report = os.path.join(run_path, 'BEAM_QUALITY.md')
with open(report, 'w') as f:
    f.write('\n'.join(lines) + '\n')

with open(os.path.join(run_path, 'RETAKE_LIST.txt'), 'w') as f:
    for r in retake:
        f.write(r['name'] + '\n')

print('\n'.join(lines))
print(f'\nwrote {report}')
print(f'wrote {os.path.join(run_path, "RETAKE_LIST.txt")}  ({len(retake)} to retake)')
