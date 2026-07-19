#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI run-configuration override for the P2 SPS beam DAQ.

This module is a *pure additive override* on top of run_config_beam.py. It reads
a single operator-editable JSON file, config/gui_run_config.json, that the Flask
"Run Setup" tab writes. run_config_beam.py imports this module lazily at the end
of _set_defaults and, ONLY when the file exists and has "enabled": true, replaces
its run_name / gas / detectors / included_detectors / sub_runs with the values
built here.

Hard guarantee: when config/gui_run_config.json is ABSENT, has "enabled": false,
or fails to parse, load() returns None and run_config_beam.py behaves EXACTLY as
before — the generated run_config_beam.json is byte-identical.

See the data-model docstring on GUI_CONFIG_PATH below for the file schema.

@author: Alexandra Kallitsopoulou
"""

import json
import os
import re

# run_config_beam only imports THIS module lazily (inside _set_defaults), so
# importing it here at module top does not create an import cycle: whichever
# module is imported first finishes loading before the other one is touched.
import run_config_beam as rcb

# ---------------------------------------------------------------------------
# The single GUI-editable file. Schema (all keys optional except enabled):
# {
#   "enabled": true,
#   "run_name": "run_1", "operator": "", "notes": "", "gas": "Ar/Iso 95/5",
#   "trigger_mode": "external",            # "external" | "self"
#   "telescope_spacing_mm": 300,
#   "detectors": [
#     {"name":"P2_OUT","included":true,"z_mm":0,"description":"...",
#      "hv_channels":{"mesh":[8,1],"drift":[8,0]},
#      "hv_max":{"mesh":420,"drift":700},
#      "dream_feus":{"c_4_bot":[3,1], ...},
#      "orientation":"rotated_inverted"}, ...
#   ],
#   "run_type": "mesh_scan",               # mesh_scan | drift_scan | long_run | pedestals
#   "run_types": { ... per-type parameters ... }
# }
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
GUI_CONFIG_PATH = os.path.join(_HERE, 'config', 'gui_run_config.json')

RUN_TYPES = ['mesh_scan', 'drift_scan', 'long_run', 'pedestals']
TRIGGER_MODES = ['external', 'self']
GAS_PRESETS = ['Ar/Iso 95/5', 'Ar/CO2/Iso 93/5/2', 'Ar/CF4 90/10']

# Seconds to settle after the HV ramp before a pedestal DAQ start (mirrors
# run_config_pedestals.py's ped_settle_time).
PED_SETTLE_TIME = 30

_RUN_NAME_RE = re.compile(r'[A-Za-z0-9._-]+')

# When True, load() returns None even if the file exists. Used by
# dump_defaults_from_code() so instantiating the beam Config gives pure CODE
# defaults (the override hook calls load(), which must not fire here).
_suppress_override = False


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load():
    """Return the GUI config dict, or None if absent / disabled / unparseable.

    None means "behave exactly like the code defaults" — the additive-override
    contract. Any error (missing file, bad JSON, enabled false) yields None so
    run_config_beam.py never breaks because of this file.
    """
    if _suppress_override:
        return None
    try:
        if not os.path.isfile(GUI_CONFIG_PATH):
            return None
        with open(GUI_CONFIG_PATH) as f:
            gui = json.load(f)
        if not isinstance(gui, dict) or not gui.get('enabled', False):
            return None
        return gui
    except Exception as e:
        print(f'gui_run_config.load(): ignoring {GUI_CONFIG_PATH}: {e}')
        return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _suffix(det_name):
    """P2_OUT -> 'out' (used to build sub-run names like out420_mid510)."""
    return det_name.rsplit('_', 1)[-1].lower()


def _channel(pair):
    """[card, chan] / (card, chan) -> (int, int), else None."""
    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
        try:
            return (int(pair[0]), int(pair[1]))
        except (TypeError, ValueError):
            return None
    return None


def _det_hv_channels(det):
    """{'mesh':(card,chan), 'drift':(card,chan)} for the electrodes present."""
    out = {}
    hc = det.get('hv_channels') or {}
    for electrode in ('mesh', 'drift'):
        ch = _channel(hc.get(electrode))
        if ch is not None:
            out[electrode] = ch
    return out


def _included_dets(gui):
    """Included detectors in file order."""
    return [d for d in (gui.get('detectors') or []) if d.get('included')]


def _run_type_cfg(gui):
    rt_name = gui.get('run_type')
    rt = (gui.get('run_types') or {}).get(rt_name, {}) or {}
    return rt_name, rt


# ---------------------------------------------------------------------------
# Schedule generation — the single source of truth for the HV setpoint schedule.
# Returns a list of entries:
#   {'name': str, 'run_time': minutes, 'post_pause_s': int, ['settle_time': int,]
#    'setpoints': {det_name: {'mesh': V, 'drift': V}}}
# build_sub_runs(), preview() and validate() all derive from this so they never
# disagree.
# ---------------------------------------------------------------------------
def _iter_schedule(gui):
    rt_name, rt = _run_type_cfg(gui)
    included = _included_dets(gui)
    entries = []

    if rt_name == 'mesh_scan':
        points = int(rt.get('points', 0) or 0)
        step = rt.get('step_v', 0) or 0
        dur = rt.get('subrun_min', 0) or 0
        start = rt.get('start', {}) or {}
        dets = [d for d in included if d['name'] in start]
        for i in range(points):
            off = i * step
            setpoints, bits = {}, []
            for d in dets:
                s = start[d['name']]
                mesh_v = s.get('mesh', 0) - off
                drift_v = s.get('drift', 0) - off
                setpoints[d['name']] = {'mesh': mesh_v, 'drift': drift_v}
                bits.append(f'{_suffix(d["name"])}{mesh_v}')
            entries.append({'name': f'mesh_{i:02d}_' + '_'.join(bits),
                            'run_time': dur, 'post_pause_s': 0,
                            'setpoints': setpoints})

    elif rt_name == 'drift_scan':
        points = int(rt.get('points', 0) or 0)
        step = rt.get('step_v', 0) or 0
        dur = rt.get('subrun_min', 0) or 0
        mesh_fixed = rt.get('mesh_fixed', {}) or {}
        drift_start = rt.get('drift_start', {}) or {}
        dets = [d for d in included
                if d['name'] in mesh_fixed and d['name'] in drift_start]
        for i in range(points):
            off = i * step
            setpoints, bits = {}, []
            for d in dets:
                mesh_v = mesh_fixed[d['name']]
                drift_v = drift_start[d['name']] - off
                setpoints[d['name']] = {'mesh': mesh_v, 'drift': drift_v}
                bits.append(f'{_suffix(d["name"])}{drift_v}')
            entries.append({'name': f'drift_{i:02d}_' + '_'.join(bits),
                            'run_time': dur, 'post_pause_s': 0,
                            'setpoints': setpoints})

    elif rt_name == 'long_run':
        n = int(rt.get('n_subruns', 0) or 0)
        dur = rt.get('subrun_min', 0) or 0
        hv = rt.get('hv', {}) or {}
        dets = [d for d in included if d['name'] in hv]
        for i in range(n):
            setpoints, bits = {}, []
            for d in dets:
                mesh_v = hv[d['name']].get('mesh', 0)
                drift_v = hv[d['name']].get('drift', 0)
                setpoints[d['name']] = {'mesh': mesh_v, 'drift': drift_v}
                bits.append(f'{_suffix(d["name"])}{mesh_v}')
            entries.append({'name': f'long_{i:02d}_' + '_'.join(bits),
                            'run_time': dur, 'post_pause_s': 0,
                            'setpoints': setpoints})

    elif rt_name == 'pedestals':
        voltage = rt.get('voltage', 0) or 0
        dur = rt.get('subrun_min', 0) or 0
        setpoints = {}
        for d in included:
            if _det_hv_channels(d):
                setpoints[d['name']] = {'mesh': voltage, 'drift': voltage}
        entries.append({'name': 'pedestals', 'run_time': dur,
                        'settle_time': PED_SETTLE_TIME, 'setpoints': setpoints})

    return entries


# ---------------------------------------------------------------------------
# Builders used by the run_config_beam.py override hook
# ---------------------------------------------------------------------------
def build_detectors(gui):
    """(detectors_list, included_detectors) in run_config_beam.py's shape.

    Every detector in the GUI file is emitted (so write_all_detectors_to_json
    still writes them all); `included` is the subset with included=true.
    """
    detectors = []
    included = []
    for d in (gui.get('detectors') or []):
        name = d.get('name')
        if not name:
            continue
        hv_channels = _det_hv_channels(d)
        dream_feus = {}
        for conn, pair in (d.get('dream_feus') or {}).items():
            ch = _channel(pair)
            if ch is not None:
                dream_feus[conn] = ch
        orientation = d.get('orientation', 'rotated_inverted')
        dream_feu_orientation = {conn: orientation for conn in dream_feus}
        detectors.append({
            'name': name,
            'description': d.get('description', ''),
            'det_type': 'P2',
            'resist_type': 'none',
            'det_center_coords': {'x': 0, 'y': 0, 'z': d.get('z_mm', 0)},
            'det_orientation': {'x': 0, 'y': 0, 'z': 0},
            'hv_channels': hv_channels,
            'dream_feus': dream_feus,
            'dream_feu_orientation': dream_feu_orientation,
        })
        if d.get('included'):
            included.append(name)
    return detectors, included


def build_sub_runs(gui):
    """sub_runs list for the selected run_type (mirrors run_config_beam.py /
    run_config_pedestals.py sub-run dict shapes)."""
    channels = {d['name']: _det_hv_channels(d) for d in _included_dets(gui)}
    sub_runs = []
    for e in _iter_schedule(gui):
        hvs = {}
        for det_name, sp in e['setpoints'].items():
            ch = channels.get(det_name, {})
            for electrode in ('mesh', 'drift'):
                if electrode in ch and sp.get(electrode) is not None:
                    card, chan = ch[electrode]
                    hvs.setdefault(str(card), {})[str(chan)] = sp[electrode]
        sub = {'sub_run_name': e['name'], 'run_time': e['run_time'], 'hvs': hvs}
        if 'settle_time' in e:
            sub['settle_time'] = e['settle_time']   # pedestals (no post_pause_s)
        else:
            sub['post_pause_s'] = e.get('post_pause_s', 0)
        sub_runs.append(sub)
    return sub_runs


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate(gui):
    """(ok, errors). Checks required fields, wiring, run-name safety and the
    HV-limit (every setpoint <= that detector's hv_max)."""
    errors = []
    if not isinstance(gui, dict):
        return False, ['config is not an object']

    run_name = gui.get('run_name')
    if not run_name or not str(run_name).strip():
        errors.append('run_name is empty')
    elif not _RUN_NAME_RE.fullmatch(str(run_name)):
        errors.append(f'run_name {run_name!r} is not filesystem-safe '
                      '(use letters, digits, . _ - only)')

    rt_name, rt = _run_type_cfg(gui)
    if rt_name not in RUN_TYPES:
        errors.append(f'run_type must be one of {RUN_TYPES}, got {rt_name!r}')
    if gui.get('trigger_mode', 'external') not in TRIGGER_MODES:
        errors.append(f'trigger_mode must be one of {TRIGGER_MODES}')

    included = _included_dets(gui)
    if not included:
        errors.append('no detectors are included in the run')

    # Per-detector wiring checks (included detectors only).
    for d in included:
        name = d.get('name', '?')
        ch = _det_hv_channels(d)
        if 'mesh' not in ch or 'drift' not in ch:
            errors.append(f'{name}: needs both mesh and drift HV channels (card, chan)')
        dream_feus = d.get('dream_feus') or {}
        if not dream_feus:
            errors.append(f'{name}: needs at least one dream_feus connector')
        for conn, pair in dream_feus.items():
            if _channel(pair) is None:
                errors.append(f'{name}: dream_feus[{conn}] must be [feu, dreamconn] integers')

    # Per-run-type: each included detector must have its setpoints defined.
    if rt_name == 'mesh_scan':
        start = rt.get('start', {}) or {}
        for d in included:
            if d['name'] not in start:
                errors.append(f'{d["name"]}: no mesh_scan start setpoint defined')
    elif rt_name == 'drift_scan':
        mesh_fixed = rt.get('mesh_fixed', {}) or {}
        drift_start = rt.get('drift_start', {}) or {}
        for d in included:
            if d['name'] not in mesh_fixed or d['name'] not in drift_start:
                errors.append(f'{d["name"]}: no drift_scan mesh_fixed/drift_start setpoint defined')
    elif rt_name == 'long_run':
        hv = rt.get('hv', {}) or {}
        for d in included:
            if d['name'] not in hv:
                errors.append(f'{d["name"]}: no long_run HV setpoint defined')

    # HV-LIMIT check: every setpoint in the generated schedule must be <= that
    # detector's hv_max for that electrode. Reports the offending sub-run.
    hv_max = {d.get('name'): (d.get('hv_max') or {}) for d in included}
    for e in _iter_schedule(gui):
        for det_name, sp in e['setpoints'].items():
            limits = hv_max.get(det_name, {})
            for electrode in ('mesh', 'drift'):
                v = sp.get(electrode)
                lim = limits.get(electrode)
                if v is not None and lim is not None and v > lim:
                    errors.append(
                        f'{e["name"]}: {det_name} {electrode} {v} V exceeds max {lim} V')

    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Preview (for the UI, no side effects)
# ---------------------------------------------------------------------------
def preview(gui):
    """Sub-run table + totals, for the Live Preview panel."""
    sub_runs = []
    total_min = 0.0
    for e in _iter_schedule(gui):
        row = {'name': e['name'], 'run_time': e['run_time'], 'detectors': {}}
        for det_name, sp in e['setpoints'].items():
            row['detectors'][det_name] = {'mesh': sp.get('mesh'), 'drift': sp.get('drift')}
        sub_runs.append(row)
        total_min += (e['run_time'] or 0)
    return {
        'run_type': gui.get('run_type'),
        'sub_runs': sub_runs,
        'n_subruns': len(sub_runs),
        'total_min': round(total_min, 4),
    }


# ---------------------------------------------------------------------------
# Seed a GUI config from the CURRENT run_config_beam.py code defaults
# ---------------------------------------------------------------------------
def defaults_from_code():
    """Build (do not write) a gui_run_config dict seeded from run_config_beam.py
    constants and its default detectors. Suppresses the override so the seeded
    Config reflects the CODE defaults even if a GUI file already exists."""
    global _suppress_override
    _suppress_override = True
    try:
        cfg = rcb.Config()
    finally:
        _suppress_override = False

    scan_start = rcb.SCAN_START

    detectors = []
    for det in cfg.detectors:
        name = det['name']
        hc = det.get('hv_channels') or {}
        hv_channels = {}
        for electrode in ('mesh', 'drift'):
            ch = _channel(hc.get(electrode))
            if ch is not None:
                hv_channels[electrode] = [ch[0], ch[1]]
        dream_feus = {}
        for conn, pair in (det.get('dream_feus') or {}).items():
            ch = _channel(pair)
            if ch is not None:
                dream_feus[conn] = [ch[0], ch[1]]
        orient_map = det.get('dream_feu_orientation') or {}
        orientation = next(iter(orient_map.values()), 'rotated_inverted')
        hv_max = dict(scan_start.get(name, {'mesh': 700, 'drift': 700}))
        detectors.append({
            'name': name,
            'included': name in cfg.included_detectors,
            'z_mm': det.get('det_center_coords', {}).get('z', 0),
            'description': det.get('description', ''),
            'hv_channels': hv_channels,
            'hv_max': hv_max,
            'dream_feus': dream_feus,
            'orientation': orientation,
        })

    # Per-run-type defaults, seeded from the code constants where they exist.
    mesh_start = {name: dict(sp) for name, sp in scan_start.items()}
    mesh_fixed = {name: sp['mesh'] for name, sp in scan_start.items()}
    drift_start = {name: sp['drift'] for name, sp in scan_start.items()}
    long_hv = {name: {'mesh': sp['mesh'], 'drift': sp['drift']}
               for name, sp in scan_start.items()}

    run_types = {
        'mesh_scan': {
            'subrun_min': rcb.SCAN_SUBRUN_MIN,
            'step_v': rcb.SCAN_STEP_V,
            'points': rcb.SCAN_POINTS,
            'start': mesh_start,
        },
        'drift_scan': {
            'subrun_min': rcb.SCAN_SUBRUN_MIN,
            'step_v': rcb.SCAN_STEP_V,
            'points': rcb.SCAN_POINTS,
            'mesh_fixed': mesh_fixed,
            'drift_start': drift_start,
        },
        'long_run': {
            'subrun_min': rcb.SUBRUN_MIN,
            'n_subruns': rcb.N_SUBRUNS,
            'hv': long_hv,
        },
        'pedestals': {
            'voltage': 200,
            'subrun_min': round(10.0 / 60, 6),   # 10 s, mirrors run_config_pedestals
        },
    }

    return {
        'enabled': False,   # seeded disabled: the operator flips it on in the GUI
        'run_name': cfg.run_name,
        'operator': '',
        'notes': '',
        'gas': cfg.gas,
        'trigger_mode': rcb.TRIGGER_MODE,
        'telescope_spacing_mm': rcb.TELESCOPE_SPACING_MM,
        'detectors': detectors,
        'run_type': 'mesh_scan',
        'run_types': run_types,
    }


def dump_defaults_from_code():
    """Write config/gui_run_config.json seeded from the code defaults and return
    the dict. Overwrites any existing file."""
    gui = defaults_from_code()
    os.makedirs(os.path.dirname(GUI_CONFIG_PATH), exist_ok=True)
    with open(GUI_CONFIG_PATH, 'w') as f:
        json.dump(gui, f, indent=4)
    return gui


if __name__ == '__main__':
    import sys
    if '--dump' in sys.argv:
        g = dump_defaults_from_code()
        print(f'Wrote {GUI_CONFIG_PATH} (enabled={g["enabled"]})')
    else:
        g = defaults_from_code()
        print(json.dumps(g, indent=2))
