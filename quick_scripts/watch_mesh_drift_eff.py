#!/usr/bin/env python3
"""Live HV/current watcher for p2_mesh_drift_eff_1 (2026-07-28 evening).

Tails every sub-run's hv_monitor.csv as daq_control writes it, raises ALARMs the
moment something goes wrong, and prints a one-line summary per completed sub-run.

Why the thresholds are per-phase and per-role rather than one global number: the
mesh imon distribution on these chambers is NOT a smooth DC current -- the median
sits on the ~0.002 uA readback floor at every voltage and the mean is driven by a
sparse tail of micro-discharges. So a mesh channel is judged by the RATE of
excursions above EXC_UA and by their amplitude, never by its mean. Drift channels
behave the opposite way: they carry a genuine steady leakage, so for them a mean
above a threshold IS the signal.

Deliberately watched here beyond the routine:
  * mesh_09/mesh_10 push P2_MID and P2_OUT to 435/445 V. Every committed scan so
    far stepped DOWN to 450 from a known nominal; nothing has taken MID/OUT above
    450, and 445 is the closest approach yet made from below with them both live.
  * drift_06_g450 puts MID and OUT drift at 900 V, which is exactly MAX_HV. There
    is no headroom left at that point, so 8:2 and 8:4 get a tighter drift limit.
  * P2_IN is a new chamber whose discharge rate tripled every 10 V from 420 to
    450 today; the mesh scan re-crosses that region on a 5 V-offset grid.
"""
import csv
import os
import subprocess
import sys
import time
from datetime import datetime

RUN = '/local/home/banco/P2_data/TB_July2026_H4/runs/p2_mesh_drift_eff_1'
POLL = 20

MESH = {'8:1': 'P2_IN mesh', '8:3': 'P2_MID mesh', '8:5': 'P2_OUT mesh'}
DRIFT = {'8:0': 'P2_IN drift', '8:2': 'P2_MID drift', '8:4': 'P2_OUT drift'}
URW = {'8:6': 'uRW_f drift', '8:7': 'uRW_b drift',
       '12:0': 'uRW_f resist', '12:1': 'uRW_b resist'}
ALL = {**MESH, **DRIFT, **URW}

EXC_UA = 0.3        # a mesh sample above this counts as a micro-discharge
SPIKE_UA = 3.0      # single mesh sample this large is worth interrupting for
DRIFT_UA = 1.0      # drift leakage above this is not normal
DRIFT_UA_TIGHT = 0.5   # for MID/OUT drift once they are at the 900 V ceiling
DROOP_V = 5.0       # vmon this far under v0 = the supply is being loaded down
DROOP_N = 5         # ...for this many consecutive samples


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def daq_alive():
    """True while OUR daq_control is up.

    -a is essential: bare `pgrep -f` prints PIDs only, so matching a name
    against its output is always False. Matching the config path rather than
    'daq_control.py' also keeps the long-lived dream_daq_control.py -- which
    that pattern hits as a substring -- from reading as our run.
    """
    r = subprocess.run(['pgrep', '-af', 'daq_control.py'],
                       capture_output=True, text=True)
    return any('p2_mesh_drift_eff_1' in ln for ln in r.stdout.splitlines())


class SubRun:
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.offset = 0
        self.hdr = None
        self.n = 0
        # 'arrived': monitoring starts the instant the ramp does, so the first
        # samples of every sub-run legitimately show vmon far below setpoint and
        # a charging current. Nothing is judged on a channel until it has once
        # reached within DROOP_V of its setpoint; before that it is ramping, not
        # sagging. Without this, every sub-run opens with ten false alarms.
        self.stats = {ch: {'n': 0, 'sum': 0.0, 'max': 0.0, 'exc': 0,
                           'droop': 0, 'droop_run': 0, 'off': 0, 'v0': None,
                           'vmax_dev': 0.0, 'arrived': False,
                           'n_on': 0, 'sum_on': 0.0} for ch in ALL}
        self.alarmed = set()
        # the drift ceiling point is the one place MID/OUT have zero headroom
        self.tight = name == 'drift_06_g450'

    def _alarm(self, key, msg):
        if key not in self.alarmed:
            self.alarmed.add(key)
            log(f'*** ALARM  {self.name}  {msg}')

    def read(self):
        try:
            with open(self.path) as f:
                if self.hdr is None:
                    self.hdr = f.readline().rstrip('\n').split(',')
                    self.offset = f.tell()
                f.seek(self.offset)
                rows = list(csv.reader(f))
                self.offset = f.tell()
        except FileNotFoundError:
            return
        idx = {c: i for i, c in enumerate(self.hdr)}
        for row in rows:
            if len(row) != len(self.hdr):
                continue          # partial line, it will be complete next poll
            self.n += 1
            for ch, label in ALL.items():
                try:
                    pw = int(float(row[idx[f'{ch} power']]))
                    v0 = float(row[idx[f'{ch} v0']])
                    vmon = float(row[idx[f'{ch} vmon']])
                    imon = float(row[idx[f'{ch} imon']])
                except (KeyError, ValueError, IndexError):
                    continue
                s = self.stats[ch]
                s['v0'] = v0
                s['n'] += 1
                s['sum'] += imon

                dev = v0 - vmon

                if pw == 0:
                    s['off'] += 1
                    # A channel commanded on that reads off has tripped -- but
                    # only once it had actually got there. Powering off after
                    # arrival is a trip; never having arrived is a ramp we
                    # caught early or a channel the crate reports as absent.
                    if v0 > 0 and s['arrived']:
                        self._alarm(f'trip{ch}',
                                    f'{label} ({ch}) POWER OFF at v0={v0:.0f} V '
                                    f'-- TRIP')
                    continue

                if not s['arrived']:
                    if dev <= DROOP_V:
                        s['arrived'] = True
                    continue          # still ramping: nothing to judge yet

                s['n_on'] += 1
                s['sum_on'] += imon
                s['max'] = max(s['max'], imon)
                s['vmax_dev'] = max(s['vmax_dev'], dev)
                if dev > DROOP_V:
                    s['droop_run'] += 1
                    s['droop'] += 1
                    if s['droop_run'] >= DROOP_N:
                        self._alarm(f'droop{ch}',
                                    f'{label} ({ch}) vmon {vmon:.1f} V is '
                                    f'{dev:.1f} V under setpoint {v0:.0f} V for '
                                    f'{s["droop_run"]} s -- being loaded down')
                else:
                    s['droop_run'] = 0

                if ch in MESH:
                    if imon >= EXC_UA:
                        s['exc'] += 1
                    if imon >= SPIKE_UA:
                        self._alarm(f'spike{ch}{int(imon)}',
                                    f'{label} ({ch}) spike {imon:.2f} uA at '
                                    f'{v0:.0f} V')
                else:
                    lim = DRIFT_UA_TIGHT if (self.tight and ch in ('8:2', '8:4')) \
                        else DRIFT_UA
                    if imon >= lim and s['n_on'] > 10:
                        mean = s['sum_on'] / s['n_on']
                        if mean >= lim:
                            self._alarm(f'leak{ch}',
                                        f'{label} ({ch}) mean {mean:.2f} uA at '
                                        f'{v0:.0f} V (limit {lim} uA)')

    def summary(self):
        parts = []
        for ch, label in list(MESH.items()) + list(DRIFT.items()):
            s = self.stats[ch]
            if not s['n_on']:
                continue
            # on-setpoint samples only, so the ramp's charging current does not
            # inflate the number that gets compared between points
            mean = s['sum_on'] / s['n_on']
            tag = f'{label.split()[0]}'
            if ch in MESH:
                parts.append(f'{tag} m{s["v0"]:.0f}: '
                             f'mean {mean:.3f} max {s["max"]:.2f} '
                             f'exc {s["exc"]}')
            else:
                parts.append(f'd{s["v0"]:.0f} {mean:.3f}')
        droop = max((self.stats[c]['vmax_dev'] for c in ALL), default=0.0)
        off = sum(self.stats[c]['off'] for c in ALL)
        return (f'{self.name}  {self.n} samples | ' + ' | '.join(parts) +
                f' | max droop {droop:.2f} V' + (f' | OFF samples {off}' if off else ''))


log('watching p2_mesh_drift_eff_1 (30 sub-runs: 11 mesh + 7 drift + 12 eff)')
log(f'mesh excursion >{EXC_UA} uA counted; spike >{SPIKE_UA} uA, drift mean '
    f'>{DRIFT_UA} uA ({DRIFT_UA_TIGHT} for MID/OUT on drift_06_g450), '
    f'droop >{DROOP_V} V for {DROOP_N}s, any trip -> ALARM')

seen = {}
done = set()
idle = 0
while True:
    if not os.path.isdir(RUN):
        time.sleep(POLL)
        continue
    for name in sorted(os.listdir(RUN)):
        d = os.path.join(RUN, name)
        csv_path = os.path.join(d, 'hv_monitor.csv')
        if not os.path.isdir(d) or not os.path.exists(csv_path):
            continue
        if name not in seen:
            seen[name] = SubRun(name, csv_path)
            log(f'--> {name} started')
        seen[name].read()
        if name not in done and os.path.exists(os.path.join(d, '.subrun_complete')):
            done.add(name)
            seen[name].read()
            log('DONE ' + seen[name].summary())

    if not daq_alive():
        idle += 1
        if idle >= 3:      # three consecutive misses, not one scheduling blip
            break
    else:
        idle = 0
    time.sleep(POLL)

log(f'daq_control has exited -- {len(done)}/30 sub-runs completed')
for name in sorted(done):
    log('  ' + seen[name].summary())
