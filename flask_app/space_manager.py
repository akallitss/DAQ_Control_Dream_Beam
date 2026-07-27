#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disk-space management for the P2 SPS DREAM data (adapted from Dylan Neff's
nTof_x17_DAQ space_manager).

Provides a read-only *scan/check* and a heavily guarded *delete* for freeing
space in the local run store, plus a *restore* that pulls runs back from EOS:

  <source_dir>/<runs_subdir>/<run>/<subrun>/<component>/   processed runs -> EOS
  <source_dir>/dream_run/<run>/<subrun>/                   raw acquisition staging

Deletion works at three granularities, all sharing one safety model:

  * whole run        delete_run() / delete_runs()
  * component        delete_components() over (run, subrun, component) triples,
                     e.g. "drop the decoded waveforms from every subrun of
                     highstat_eff_1 but keep the combined hits"

COMPONENTS (see the table below) are the deletable pieces of a subrun:

  dream_run           raw .fdf acquisition staging. NOT backed up to EOS (it is
                      in backup_config's exclude_dirs) — it is a plain
                      shutil.copy duplicate of raw_daq_data made by
                      dream_daq_control. Safe iff every staged .fdf is present
                      on EOS under that subrun's raw_daq_data at matching size
                      (the same bytes, just the authoritative copy). The small
                      non-.fdf staging artifacts (.prg/.cfg/.par/.log) are
                      reproducible and do not block deletion.
  raw_fdf             the *.fdf files inside raw_daq_data. Deliberately NOT the
                      whole directory: run_time.txt, RunCtrl_*.log,
                      pedestal_run.txt, *.cfg and *.prg live there too, are
                      negligible in size, and are the run's provenance — they
                      are always preserved.
  decoded_root        decoded waveforms (whole directory)
  hits_root           per-FEU hits, pre-combination (whole directory)
  combined_hits_root  combined hits, the physics product (whole directory)

Safety model — a thing is only ever "safe to delete" when its data is provably
preserved on EOS: EVERY file it covers must be present on EOS at matching size
(relative path + byte size; data is write-once). This is exactly the check
backup_watcher uses. backup_watcher is push-only (it never removes anything
from EOS), so deleting locally cannot propagate to the backup.

Extra guards beyond x17's:
  * the run named in config/current_run_state.json (actively acquiring) is
    never deletable;
  * the NEWEST run on disk (by mtime) is never deletable — between runs the
    state file may already point at the next run while this one still has
    files in flight;
  * a subrun missing its .subrun_complete marker is never deletable (possibly
    still being written / crashed mid-subrun). At run granularity ANY
    incomplete subrun blocks the whole run; at component granularity only the
    offending subrun is blocked.

Nothing here trusts a caller-supplied verdict: every delete entry point
re-runs the full verification itself, against a FRESH EOS listing, immediately
before it removes anything, and refuses any path that does not resolve inside
the expected root.

Performance — every xrdfs invocation costs ~5-10 s of connect + Kerberos
handshake against eosproject, regardless of how much it lists (a
non-recursive ls of a 3-entry directory measured 5-10 s while network RTT is
0.4 ms). So this module issues exactly ONE `xrdfs ls -l -R` for the entire
runs tree and partitions the result in memory, instead of one call per run.
That turned a linear-in-run-count scan into a constant one (~8 s total), and
is what makes per-component verification affordable at all — the naive form
would need a call per component per subrun.

All locations come from config/backup_config.json (source_dir, runs_subdir,
xrootd_url, eos_dir), so this always agrees with the backup watcher about
what is backed up where. EOS access is native xrootd (xrdfs/xrdcp) — the
legacy FUSE mount is not used anywhere.
"""

import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# Repo root = parent of flask_app/ (this module lives in flask_app/).
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# banco has no CERN realm in its system krb5.conf and xrdfs/xrdcp live in
# ~/bin — same environment fixes backup_watcher.py applies for itself.
os.environ.setdefault('KRB5_CONFIG', os.path.join(REPO_DIR, 'config', 'krb5_cern.conf'))
os.environ['PATH'] = os.path.expanduser('~/bin') + os.pathsep + os.environ.get('PATH', '')

BACKUP_CONFIG_PATH = os.path.join(REPO_DIR, 'config', 'backup_config.json')
CURRENT_RUN_STATE  = os.path.join(REPO_DIR, 'config', 'current_run_state.json')
DELETE_LOG         = os.path.join(REPO_DIR, 'logs', 'space_manager.log')

# Run dirs are named by the GUI run builder as <base>_<index> (run_19,
# drift_scan_1, beam_nominal_meshscan_1, p2in_check_1, ...). The charset is
# deliberately tight — letters/digits/underscore/hyphen only, no dots or
# slashes — because this regex is also a delete-path guard. backup_watcher
# itself syncs every dir under runs/ without a name filter, so anything the
# builder creates is backed up and must be visible here.
RUN_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*_\d+$')
# Subruns are <base>_NN (beam_commissioning_00). Same guard role as above.
SUBRUN_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*$')

# Single managed disk. The label/root/fs are resolved from backup_config.json
# at call time (see _cfg) so a regenerated config is picked up without a
# restart; only the key + label live here.
DISKS = {
    'data': {'label': 'Data disk (runs)'},
}

# The raw acquisition staging tree, a sibling of runs/ under source_dir. Named
# in backup_config's exclude_dirs, i.e. never uploaded to EOS.
DREAM_RUN_SUBDIR = 'dream_run'


# --- Components -------------------------------------------------------------
# 'dir'    : directory under the subrun that holds the component ('' = the
#            subrun dir itself, used by dream_run whose files are loose).
# 'suffix' : if set, only files with this suffix belong to the component; the
#            rest of the directory is left alone. This is what keeps
#            run_time.txt & friends when raw FDFs are dropped.
# 'tree'   : which local tree the component lives in — 'runs' or 'dream_run'.
# 'derived': True for products the processor can recompute from an earlier
#            stage; used only for UI wording.
COMPONENTS = {
    'dream_run': {
        'label': 'Raw staging (dream_run)',
        'blurb': 'Duplicate .fdf copies left behind by acquisition. Never backed up — '
                 'verified against the raw_daq_data copy on EOS.',
        'tree': 'dream_run', 'dir': '', 'suffix': None,
        'derived': False, 'order': 0,
    },
    'raw_fdf': {
        'label': 'Raw FDFs',
        'blurb': 'The *.fdf files in raw_daq_data. Logs, run_time.txt, pedestal_run.txt, '
                 '*.cfg and *.prg in the same directory are always kept.',
        'tree': 'runs', 'dir': 'raw_daq_data', 'suffix': '.fdf',
        'derived': False, 'order': 1,
    },
    'decoded_root': {
        'label': 'Decoded waveforms',
        'blurb': 'decoded_root/ — full waveforms, re-derivable from the FDFs.',
        'tree': 'runs', 'dir': 'decoded_root', 'suffix': None,
        'derived': True, 'order': 2,
    },
    'hits_root': {
        'label': 'Per-FEU hits',
        'blurb': 'hits_root/ — per-FEU hits before combination.',
        'tree': 'runs', 'dir': 'hits_root', 'suffix': None,
        'derived': True, 'order': 3,
    },
    'combined_hits_root': {
        'label': 'Combined hits',
        'blurb': 'combined_hits_root/ — the physics product. Also the processor\'s '
                 '"already done" marker (see reprocess_warnings).',
        'tree': 'runs', 'dir': 'combined_hits_root', 'suffix': None,
        'derived': True, 'order': 4,
    },
}

COMPONENT_ORDER = sorted(COMPONENTS, key=lambda c: COMPONENTS[c]['order'])

# Directories under a subrun that belong to a component (for classifying the
# local walk). dream_run is a separate tree so it is not in here.
_DIR_TO_COMPONENT = {c['dir']: k for k, c in COMPONENTS.items()
                     if c['tree'] == 'runs' and c['dir']}

# processor_watcher._get_processed_file_nums() treats the LAST enabled pipeline
# stage's output directory as the "this file_num is done" marker. With the
# default do_combine=True that is combined_hits_root: delete it while the FDFs
# are still on disk and the watcher will re-decode, re-analyze and re-combine
# the subrun from scratch. Deleting decoded_root/hits_root while
# combined_hits_root survives is invisible to it.
REPROCESS_SENTINEL = 'combined_hits_root'
REPROCESS_INPUT    = 'raw_fdf'


# --- Config ----------------------------------------------------------------

def _cfg():
    """(runs_root, fs_path, xrootd_url, eos_runs_dir) from the backup watcher's
    config — the one source of truth for what is backed up where.

    NOTE this returns only the PRIMARY (write) destination. For the question
    "is this local file safe to delete?" use _verify_locations() instead: data
    written before a destination change still lives at the old path and is just
    as safe, but is invisible here.
    """
    with open(BACKUP_CONFIG_PATH) as f:
        cfg = json.load(f)
    source_dir = Path(cfg['source_dir'])
    runs_root = source_dir / cfg.get('runs_subdir', 'runs')
    url = cfg.get('xrootd_url', 'root://eospublic.cern.ch').rstrip('/')
    eos_runs = str(Path(cfg['eos_dir']) / cfg.get('runs_subdir', 'runs'))
    return runs_root, str(source_dir), url, eos_runs


def _verify_locations() -> list:
    """Every EOS location that counts as a backup, primary (write) first.

    A run campaign outlives any single destination — quota fills, allocations
    move — and data pushed to the old path is still perfectly good. Verifying
    against the current eos_dir alone therefore reports historical runs as
    "not backed up" and permanently blocks their local disk from being
    reclaimed. That is what happened on 2026-07-27: the move to nTOF stranded
    ~66 GB on salsachip and the drift_mesh_2d_2 remnant on user EOS.

    Extra locations come from the optional 'verify_eos_locations' key in
    backup_config.py — a list of {'xrootd_url': ..., 'eos_dir': ...}. They are
    READ-ONLY: nothing is ever written or deleted there, they only answer
    "does a copy of this file already exist?". Absent key == old behaviour.

    Returns [{'url':…, 'eos_runs':…, 'label':…}], primary at index 0.
    """
    with open(BACKUP_CONFIG_PATH) as f:
        cfg = json.load(f)
    subdir = cfg.get('runs_subdir', 'runs')

    def _loc(url, eos_dir):
        url = (url or 'root://eospublic.cern.ch').rstrip('/')
        eos_runs = str(Path(eos_dir) / subdir)
        return {'url': url, 'eos_runs': eos_runs, 'label': f'{url}/{eos_runs}'}

    locs = [_loc(cfg.get('xrootd_url'), cfg['eos_dir'])]
    seen = {locs[0]['label']}
    for extra in cfg.get('verify_eos_locations') or []:
        try:
            loc = _loc(extra.get('xrootd_url'), extra['eos_dir'])
        except (KeyError, TypeError):
            continue          # a malformed entry must never break verification
        if loc['label'] not in seen:
            seen.add(loc['label'])
            locs.append(loc)
    return locs


def _runs_root() -> Path:
    return _cfg()[0]


def _dream_run_root() -> Path:
    return Path(_cfg()[1]) / DREAM_RUN_SUBDIR


# --- Size maps -------------------------------------------------------------

def _local_size_map(root: Path) -> dict:
    """{relpath: size} for every regular file under root — the FULL tree, so
    processing outputs (decoded_root/hits_root/combined_hits_root) and dotfile
    markers are all included, matching what backup_watcher syncs (same rglob)."""
    out = {}
    for f in root.rglob('*'):
        try:
            if f.is_file() and not f.is_symlink():
                out[f.relative_to(root).as_posix()] = f.stat().st_size
        except OSError:
            pass
    return out


def _remote_size_map(eos_dir: str, url: str = None):
    """{relpath: size} for every file under eos_dir on EOS via native xrdfs,
    or None on a listing error (so the caller can treat 'could not verify' as
    NOT safe). `url` defaults to the primary endpoint; pass it explicitly to
    list one of the extra _verify_locations() on another EOS instance.

    An absent directory lists cleanly as empty ({}), which correctly reads as
    'nothing backed up'. A genuine xrdfs failure (auth, network) returns None.
    Parses `xrdfs <url> ls -l -R` lines the same way backup_watcher does:
    '<flags> <owner> <group> <size> <date> <time> <path>'.
    """
    if url is None:
        _, _, url, _ = _cfg()
    try:
        result = subprocess.run(
            ['xrdfs', url, 'ls', '-l', '-R', eos_dir],
            capture_output=True, text=True,
        )
    except OSError:
        return None   # xrdfs not installed / not on PATH -> cannot verify
    if result.returncode != 0:
        err = (result.stderr or '').lower()
        if 'not found' in err or 'no such file' in err or '3011' in err:
            return {}
        return None
    base = eos_dir.rstrip('/') + '/'
    sizes = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[0].startswith('d'):
            continue
        try:
            size = int(parts[3])
        except ValueError:
            continue
        path = parts[-1]
        if path.startswith(base):
            sizes[path[len(base):]] = size
    return sizes


# One recursive listing of the WHOLE runs tree, briefly cached. See the module
# docstring: the cost of xrdfs is per-invocation, not per-entry, so this single
# call replaces what used to be one call per run. The TTL only exists so that
# the scan -> preflight -> confirm click path does not pay for it three times;
# every delete re-lists with force=True and never trusts the cache.
_REMOTE_TTL = 90.0

# Past this, a replayed check is shown as stale and the page nudges for a fresh
# one. 30 min: long enough that reloading the tab never re-lists, short enough
# that a verdict predating a run's worth of new backups is not presented as
# current.
STALE_CHECK_S = 1800.0
# 'src' records, for files NOT found at the primary location, which entry of
# _verify_locations() they came from — so a restore knows where to pull from.
_remote_cache = {'t': 0.0, 'map': None, 'src': {}}

# The same listing, persisted, so it survives a page reload AND a Flask
# restart. A verification now costs ~32 s (one xrdfs ls -R per verify
# location), which is far too long to repeat every time someone opens the tab —
# and repeating it tells you nothing new, because the answer only changes when
# the backup watcher pushes something. So the tab reports the last result with
# its age ("good as of 2 minutes ago") and leaves re-checking to a deliberate
# click. Deletion never trusts this: delete_components() always re-lists with
# force=True and re-verifies every item.
_REMOTE_CACHE_FILE = os.path.join(REPO_DIR, 'config', 'space_remote_cache.json')


def _age_h(seconds) -> str:
    """'2 minutes', '35 seconds', '3 hours' — for 'good as of X ago'."""
    if seconds is None:
        return 'never'
    s = int(max(0, seconds))
    if s < 60:
        return f'{s} second{"" if s == 1 else "s"}'
    if s < 3600:
        m = s // 60
        return f'{m} minute{"" if m == 1 else "s"}'
    if s < 86400:
        h = s // 3600
        return f'{h} hour{"" if h == 1 else "s"}'
    d = s // 86400
    return f'{d} day{"" if d == 1 else "s"}'


def _save_remote_cache(m: dict, src: dict, t: float):
    tmp = _REMOTE_CACHE_FILE + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump({'t': t, 'map': m, 'src': src}, f)
        os.replace(tmp, _REMOTE_CACHE_FILE)   # atomic: never a half-written cache
    except OSError:
        pass                                   # cache is an optimisation, never required


def _load_remote_cache():
    """(map, src, t) from the persisted listing, or (None, {}, 0.0)."""
    try:
        with open(_REMOTE_CACHE_FILE) as f:
            d = json.load(f)
        m = d.get('map')
        if not isinstance(m, dict):
            return None, {}, 0.0
        return m, (d.get('src') or {}), float(d.get('t') or 0.0)
    except (OSError, ValueError, TypeError):
        return None, {}, 0.0


def _has_cached_listing() -> bool:
    """Is there any listing — in memory or persisted — to replay?"""
    if _remote_cache['map'] is not None:
        return True
    m, _, _ = _load_remote_cache()
    return m is not None


def last_check() -> dict:
    """When the EOS listing currently available was taken.
    {'at': epoch|None, 'age_s': float|None, 'at_h': 'HH:MM:SS'|''}."""
    t = _remote_cache['t'] if _remote_cache['map'] is not None else 0.0
    if not t:
        _, _, t = _load_remote_cache()
    if not t:
        return {'at': None, 'age_s': None, 'at_h': ''}
    return {'at': t, 'age_s': max(0.0, time.time() - t),
            'at_h': time.strftime('%H:%M:%S', time.localtime(t))}

# How long the last EOS listing took, and how many entries it returned. Used
# ONLY to drive the progress estimate in the GUI. `xrdfs ls -R` cannot be
# tracked for real: measured against this EOS, all 4533 lines arrive in a 0.06 s
# burst after 8.83 s of silence, because the whole cost is connect + Kerberos +
# the server-side directory walk, which emits nothing until it is finished.
# So the listing phase can honestly show only an elapsed-vs-typical estimate;
# the phases after it are counted for real.
SCAN_HINT_PATH = os.path.join(REPO_DIR, 'logs', 'space_scan_hint.json')
DEFAULT_LISTING_S = 9.0


def _read_hint() -> dict:
    try:
        with open(SCAN_HINT_PATH) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_hint(**kw):
    h = _read_hint()
    h.update(kw)
    try:
        os.makedirs(os.path.dirname(SCAN_HINT_PATH), exist_ok=True)
        with open(SCAN_HINT_PATH, 'w') as f:
            json.dump(h, f)
    except Exception:
        pass


def listing_estimate_s() -> float:
    """Best guess at how long the next EOS listing will take, from the last one."""
    try:
        v = float(_read_hint().get('listing_s') or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if 0.5 <= v <= 120 else DEFAULT_LISTING_S


def _noop_progress(phase, done, total, msg, item=None):
    pass


def _remote_runs_map(force: bool = False, progress=None, allow_stale: bool = False):
    """{'<run>/<subrun>/<component>/<file>': size} for the entire EOS runs tree,
    or None if the listing failed. Failures are never cached.

    allow_stale=True answers from the persisted listing regardless of age and
    never contacts EOS — for read-only views that would rather show a dated
    answer with its age than block. force=True always overrides it.
    """
    progress = progress or _noop_progress
    now = time.time()
    if (not force and _remote_cache['map'] is not None
            and now - _remote_cache['t'] < _REMOTE_TTL):
        progress('listing', 1, 1, f"{len(_remote_cache['map'])} files (cached)")
        return _remote_cache['map']

    # allow_stale: answer from the persisted listing at ANY age rather than
    # contacting EOS. This is what a page reload uses — it wants "what did the
    # last check say, and how old is it", not a fresh 32 s listing.
    if allow_stale and not force:
        m, src, t = _load_remote_cache()
        if m is not None:
            _remote_cache.update(map=m, src=src, t=t)
            age = max(0.0, time.time() - t)
            progress('listing', 1, 1, f'{len(m)} files from the check {_age_h(age)} ago')
            return m

    # One listing per verify location, merged. A file is backed up if a copy
    # exists in ANY of them — the union is sound for the only question this
    # map answers ("is deleting the local copy safe?"), because each file is
    # judged on its own copy, not on where its siblings happen to live.
    #
    # Merge rule is FIRST WINS, primary first. If two locations disagree about
    # a file's size (one holds a truncated copy) the primary's size is the one
    # compared against local, so a bad primary copy reports mismatch -> not
    # safe -> nothing is deleted. That is pessimistic, never permissive, which
    # is the only direction an error here may take.
    locs = _verify_locations()
    progress('listing', 0, None, f'contacting EOS (xrdfs) — {len(locs)} location(s)…')
    t0 = time.time()
    merged, src, failed = {}, {}, []
    for i, loc in enumerate(locs):
        m = _remote_size_map(loc['eos_runs'], url=loc['url'])
        if m is None:
            failed.append(loc['label'])
            continue
        for rel, sz in m.items():
            if rel not in merged:
                merged[rel] = sz
                if i:
                    src[rel] = i     # primary is index 0 == the default
    dt = time.time() - t0

    # Only a total failure is unverifiable. A single location that fails can
    # merely shrink the map, i.e. make data look LESS backed up than it is —
    # safe. Returning None on a partial failure would instead freeze pruning
    # whenever a secondary instance is down, which is the bug this replaces.
    if len(failed) == len(locs):
        progress('listing', 1, 1, 'EOS listing FAILED (all locations)')
        return None

    _remote_cache['map'] = merged
    _remote_cache['src'] = src
    _remote_cache['t'] = time.time()
    # Only a listing that reached every location is worth persisting: a partial
    # one would be replayed later as "good as of X" while quietly understating
    # what is backed up.
    if not failed:
        _save_remote_cache(merged, src, _remote_cache['t'])
    _write_hint(listing_s=round(dt, 2), entries=len(merged))
    msg = f'{len(merged)} files listed across {len(locs) - len(failed)}/{len(locs)} location(s) in {dt:.1f}s'
    if failed:
        msg += f' — UNREACHABLE: {", ".join(failed)}'
    progress('listing', 1, 1, msg)
    return merged


def _remote_source(rel: str):
    """(xrootd_url, eos_runs_dir) the merged listing found `rel` in, so a
    restore pulls from wherever the file actually is. Anything not recorded
    came from the primary."""
    locs = _verify_locations()
    i = _remote_cache['src'].get(rel, 0)
    if i >= len(locs):
        i = 0
    return locs[i]['url'], locs[i]['eos_runs']


def _partition_by_run(rmap: dict) -> dict:
    """{run: {relpath-within-run: size}} from the flat whole-tree listing."""
    out = {}
    for k, v in rmap.items():
        run, _, rest = k.partition('/')
        if rest:
            out.setdefault(run, {})[rest] = v
    return out


def invalidate_remote_cache():
    _remote_cache['map'] = None
    _remote_cache['src'] = {}
    _remote_cache['t'] = 0.0


# --- Local tree ------------------------------------------------------------

def _component_of(rel_parts):
    """Classify a path relative to a RUN root into (subrun, component).

    component is None for files that belong to no deletable component — the
    run-level loose files (dream_daq.log, run_config.json), the subrun-level
    loose files (hv_monitor.csv, .subrun_complete) and the non-.fdf contents of
    raw_daq_data. Those are always preserved.
    """
    if len(rel_parts) < 2:
        return None, None                      # <run>/<file>
    subrun = rel_parts[0]
    if len(rel_parts) == 2:
        return subrun, None                    # <subrun>/<file>
    comp = _DIR_TO_COMPONENT.get(rel_parts[1])
    if comp is None:
        return subrun, None                    # unknown subdir -> not deletable
    suffix = COMPONENTS[comp]['suffix']
    if suffix and not rel_parts[-1].lower().endswith(suffix):
        return subrun, None                    # e.g. run_time.txt in raw_daq_data
    return subrun, comp


def _local_tree() -> dict:
    """Walk the runs tree ONCE and the dream_run tree once, and return

      {run: {'subruns': {subrun: {'components': {comp: {rel: size}},
                                  'other': {'files': n, 'size': b}}},
             'other': {'files': n, 'size': b}}}

    where component relpaths are relative to the RUN root, so they line up
    directly with _partition_by_run() keys for the EOS comparison.
    """
    runs_root = _runs_root()
    tree = {}

    def _run_entry(run):
        return tree.setdefault(run, {'subruns': {}, 'other': {'files': 0, 'size': 0}})

    def _sub_entry(run, subrun):
        e = _run_entry(run)['subruns'].setdefault(
            subrun, {'components': {}, 'other': {'files': 0, 'size': 0}})
        return e

    if runs_root.is_dir():
        for f in runs_root.rglob('*'):
            try:
                if not f.is_file() or f.is_symlink():
                    continue
                size = f.stat().st_size
                rel = f.relative_to(runs_root).as_posix()
            except OSError:
                continue
            parts = rel.split('/')
            run = parts[0]
            if not RUN_NAME_RE.match(run):
                continue
            subrun, comp = _component_of(parts[1:])
            if subrun is None:
                e = _run_entry(run)['other']
                e['files'] += 1
                e['size'] += size
                continue
            se = _sub_entry(run, subrun)
            if comp is None:
                se['other']['files'] += 1
                se['other']['size'] += size
            else:
                se['components'].setdefault(comp, {})['/'.join(parts[1:])] = size

    # dream_run/<run>/<subrun>/<file> — a separate tree, mapped onto the same
    # run/subrun grid so the UI can show it as just another component.
    dr_root = _dream_run_root()
    if dr_root.is_dir():
        for f in dr_root.rglob('*'):
            try:
                if not f.is_file() or f.is_symlink():
                    continue
                size = f.stat().st_size
                rel = f.relative_to(dr_root).as_posix()
            except OSError:
                continue
            parts = rel.split('/')
            if len(parts) < 3:
                continue                       # loose file, not in a subrun
            run, subrun = parts[0], parts[1]
            if not RUN_NAME_RE.match(run):
                continue
            _sub_entry(run, subrun)['components'].setdefault(
                'dream_run', {})['/'.join(parts[1:])] = size

    return tree


# --- Helpers ---------------------------------------------------------------

def _run_key(name: str):
    """Sort key grouping runs by family, then index: drift_scan_1, drift_scan_2,
    meshscan_fine_1, ... (a bare first-number sort would interleave families)."""
    m = re.match(r'^(.*)_(\d+)$', name)
    return (m.group(1), int(m.group(2))) if m else (name, -1)


# Line markers in daq_control's tmux pane (same source flask_app/daq_status.py
# scrapes). Scanned newest-first: the first marker hit decides. Anything not
# matching either list (e.g. periodic [status] lines) keeps scanning.
_DAQ_IDLE_FLAGS = ('Run complete', 'donzo', 'Daq control session started')
_DAQ_BUSY_FLAGS = ('Dream DAQ starting', 'Prepping DAQs', 'Ramping HVs for',
                   'Starting DAQ Control', 'Finished with sub run', '[pause]',
                   'Stopping DAQ process', 'Dream DAQ taking pedestals')


def daq_mid_run() -> bool:
    """True while daq_control's tmux pane shows a run in progress. Defaults to
    False when tmux/pane is missing or no marker is visible — a live run is
    still protected then by the newest-run and incomplete-subrun guards, and
    defaulting True would recreate the stale-'acquiring' problem this solves."""
    try:
        out = subprocess.run(
            ['tmux', 'capture-pane', '-pS', '-50', '-t', 'daq_control:0.0'],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    if out.returncode != 0:
        return False
    for line in reversed(out.stdout.splitlines()):
        if any(f in line for f in _DAQ_IDLE_FLAGS):
            return False
        if any(f in line for f in _DAQ_BUSY_FLAGS):
            return True
    return False


def active_run() -> str:
    """Name of the run currently being acquired (never deletable), or ''.

    config/current_run_state.json is a LAST-SEEN-run tracker — the GUI writes
    it for its event counter and never clears it when a run ends — so the name
    only counts as active while daq_control actually shows a run in progress.
    """
    try:
        with open(CURRENT_RUN_STATE) as f:
            name = json.load(f).get('run_name', '') or ''
    except Exception:
        return ''
    return name if (name and daq_mid_run()) else ''


def newest_run() -> str:
    """Name of the run dir with the most recent mtime (never deletable — it may
    still be receiving files even if the state file already points elsewhere),
    or ''."""
    newest, newest_t = '', -1.0
    try:
        for p in _runs_root().iterdir():
            if p.is_dir() and RUN_NAME_RE.match(p.name):
                try:
                    t = p.stat().st_mtime
                except OSError:
                    continue
                if t > newest_t:
                    newest, newest_t = p.name, t
    except OSError:
        pass
    return newest


def subrun_complete(run: str, subrun: str) -> bool:
    """True when daq_control's end-of-subrun marker is present."""
    return (_runs_root() / run / subrun / '.subrun_complete').is_file()


def incomplete_subruns(run_root: Path) -> list:
    """Subrun dirs under run_root missing their .subrun_complete marker
    (daq_control writes it when a subrun finishes cleanly). A run with any
    incomplete subrun may still be mid-write — never deletable."""
    out = []
    try:
        for sub in sorted(run_root.iterdir()):
            if sub.is_dir() and not (sub / '.subrun_complete').is_file():
                out.append(sub.name)
    except OSError:
        pass
    return out


def _dir_size(root: Path) -> int:
    total = 0
    for f in root.rglob('*'):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def human(n: int) -> str:
    f = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(f) < 1024 or unit == 'TB':
            return f"{f:.1f} {unit}" if unit != 'B' else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"


def disk_usage() -> dict:
    """Free/used/total for the data filesystem."""
    out = {}
    try:
        _, fs_path, _, _ = _cfg()
    except Exception as e:
        return {'data': {'label': DISKS['data']['label'], 'error': str(e)}}
    for key, d in DISKS.items():
        try:
            u = shutil.disk_usage(fs_path)
            # ext4 reserves ~5% for root (47.7 GB of this 937 GB filesystem).
            # The DAQ writes as `banco`, so those bytes are NOT headroom, and
            # measuring against the raw total reported 87.7% while df and the
            # Overview tab both said 93% — the tab understated fullness by ~48
            # GB, in the one direction that can get a run killed. Percentages
            # are therefore taken against what is actually writable, which is
            # what df -h and psutil.disk_usage().percent report.
            usable = u.used + u.free
            out[key] = {'label': d['label'], 'fs': fs_path,
                        'total': u.total, 'used': u.used, 'free': u.free,
                        'usable': usable, 'reserved': u.total - usable,
                        'pct': round(100.0 * u.used / usable, 1) if usable else 0.0}
        except OSError as e:
            out[key] = {'label': d['label'], 'fs': fs_path, 'error': str(e)}
    return out


# --- Verification ----------------------------------------------------------

def _verify_files(local: dict, remote: dict) -> dict:
    """Compare a {rel: size} local set against the run's EOS map."""
    ok = missing = mismatch = 0
    for rel, sz in local.items():
        rsz = remote.get(rel)
        if rsz == sz:
            ok += 1
        elif rsz is None:
            missing += 1
        else:
            mismatch += 1
    return {'ok': ok, 'missing': missing, 'mismatch': mismatch}


def _verify_component(comp: str, local: dict, remote: dict) -> dict:
    """Verdict for one component of one subrun.

    `local` is {relpath-within-run: size} for the component's files; `remote`
    is the EOS map for that run. dream_run is special: its files live outside
    the backed-up tree, so each .fdf is looked up under the sibling
    raw_daq_data path instead, and its non-.fdf staging artifacts are
    reproducible and never block.
    """
    res = {'component': comp, 'files': len(local), 'size': sum(local.values()),
           'ok': 0, 'missing': 0, 'mismatch': 0, 'safe': False, 'reason': ''}
    if not local:
        res['reason'] = 'nothing present'
        return res

    if comp == 'dream_run':
        fdf = {rel: sz for rel, sz in local.items() if rel.lower().endswith('.fdf')}
        res['staging_files'] = len(local) - len(fdf)
        if not fdf:
            # Pure staging artifacts with no raw data to protect.
            res['safe'] = True
            res['reason'] = f'{len(local)} reproducible staging file(s), no .fdf'
            return res
        # <subrun>/<name>.fdf  ->  <subrun>/raw_daq_data/<name>.fdf on EOS
        mapped = {}
        for rel, sz in fdf.items():
            parts = rel.split('/')
            mapped['/'.join([parts[0], 'raw_daq_data', parts[-1]])] = sz
        counts = _verify_files(mapped, remote)
        res.update(counts)
        if counts['missing'] == 0 and counts['mismatch'] == 0:
            res['safe'] = True
            res['reason'] = (f"all {counts['ok']} .fdf verified on EOS via raw_daq_data"
                             + (f"; {res['staging_files']} staging file(s) reproducible"
                                if res['staging_files'] else ''))
        else:
            res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                             f"size-mismatched under raw_daq_data on EOS")
        return res

    counts = _verify_files(local, remote)
    res.update(counts)
    if counts['missing'] == 0 and counts['mismatch'] == 0:
        res['safe'] = True
        res['reason'] = f"all {counts['ok']} files verified on EOS"
    else:
        res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                         f"size-mismatched on EOS")
    return res


def verify_run(disk: str, run: str, force: bool = False) -> dict:
    """Compare a local run against EOS, file by file (relpath + size) over the
    complete run tree — raw subrun data, processing outputs, loose files and
    markers alike. Sources the single whole-tree EOS listing."""
    runs_root, _, _, _ = _cfg()
    root = runs_root / run
    res = {'run': run, 'disk': disk, 'size': 0, 'files': 0,
           'ok': 0, 'missing': 0, 'mismatch': 0,
           'safe': False, 'reason': '', 'unverifiable': False}
    if not root.is_dir():
        res['reason'] = 'run directory not found locally'
        return res
    local = _local_size_map(root)
    res['files'] = len(local)
    res['size'] = sum(local.values())
    rmap = _remote_runs_map(force=force)
    if rmap is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list runs on EOS (Kerberos/network?) — NOT safe'
        return res
    remote = _partition_by_run(rmap).get(run, {})
    counts = _verify_files(local, remote)
    res.update(counts)
    if (counts['missing'] == 0 and counts['mismatch'] == 0
            and counts['ok'] == len(local) and len(local) > 0):
        res['safe'] = True
        res['reason'] = f"all {counts['ok']} files verified on EOS"
    elif len(local) == 0:
        res['reason'] = 'run directory is empty'
    else:
        res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                         f"size-mismatched on EOS")
    return res


def _apply_local_guards(v: dict, run: str, act: str, newest: str) -> dict:
    """Downgrade a verify verdict for runs that must never be deleted no matter
    what EOS says: the active run, the newest run on disk, and runs with
    incomplete subruns. The guard text is APPENDED to the EOS verdict, never
    replacing it — the guard says why the run is not deletable, not whether it
    is backed up, and hiding the backup status reads as 'not on EOS' in the
    GUI."""
    v['active'] = (run == act)
    v['newest'] = (run == newest)
    guard = ''
    if v['active']:
        guard = 'currently acquiring — never deletable while active'
    elif v['newest']:
        guard = 'newest run on disk (possibly still being written) — refusing'
    else:
        inc = incomplete_subruns(_runs_root() / run)
        if inc:
            guard = (f'{len(inc)} subrun(s) missing .subrun_complete '
                     f'(possibly mid-write) — refusing')
    if guard:
        v['safe'] = False
        v['reason'] = f"{v['reason']} · {guard}" if v.get('reason') else guard
    return v


def _run_guard(run: str, act: str, newest: str) -> str:
    """Run-wide reason this run may not be touched at all, or ''. Unlike
    _apply_local_guards this does NOT consider incomplete subruns: at component
    granularity an incomplete subrun blocks only itself."""
    if run == act:
        return 'currently acquiring — never deletable while active'
    if run == newest:
        return 'newest run on disk (possibly still being written) — refusing'
    return ''


# --- Scan ------------------------------------------------------------------

def list_runs(disk: str) -> list:
    root = _runs_root()
    if not root.is_dir():
        return []
    runs = [p.name for p in root.iterdir() if p.is_dir() and RUN_NAME_RE.match(p.name)]
    return sorted(runs, key=_run_key)


def local_scan(disk: str) -> dict:
    """What is on the disk right now — local stat() only, no EOS access, so it
    is instant and works even when Kerberos/network is down. Reports each run's
    size plus the local guard flags (active / newest / incomplete subruns).
    Only the full EOS scan() can mark a run safe to delete."""
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    act = active_run()
    newest = newest_run()
    root = _runs_root()
    results = []
    for run in list_runs(disk):
        rroot = root / run
        local = _local_size_map(rroot)
        inc = incomplete_subruns(rroot)
        r = {'run': run, 'disk': disk,
             'size': sum(local.values()), 'files': len(local),
             'active': run == act, 'newest': run == newest,
             'incomplete': len(inc)}
        r['size_h'] = human(r['size'])
        if r['active']:
            r['note'] = 'currently acquiring — never deletable while active'
        elif r['newest']:
            r['note'] = 'newest run on disk — never deletable'
        elif inc:
            r['note'] = f'{len(inc)} subrun(s) missing .subrun_complete'
        else:
            r['note'] = 'not yet verified against EOS'
        results.append(r)
    total = sum(r['size'] for r in results)
    return {
        'disk': disk, 'label': DISKS[disk]['label'],
        'runs': results, 'n_runs': len(results),
        'total_bytes': total, 'total_h': human(total),
        'active_run': act,
        'usage': disk_usage().get(disk, {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def scan(disk: str, runs=None, force: bool = True, progress=None) -> dict:
    """Verify every run (or a subset); return per-run verdicts. One EOS listing
    for the whole tree, not one per run — this is the check the user explicitly
    asks for, so it re-lists by default rather than reusing the cache."""
    progress = progress or _noop_progress
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    names = runs if runs else list_runs(disk)
    act = active_run()
    newest = newest_run()
    # One shared listing for every run below.
    _remote_runs_map(force=force, progress=progress)
    results = []
    for i, run in enumerate(names):
        progress('verify', i, len(names), f'verifying {run}')
        v = verify_run(disk, run)
        v = _apply_local_guards(v, run, act, newest)
        v['size_h'] = human(v.get('size', 0))
        results.append(v)
    progress('verify', len(names), len(names), 'verification complete')
    safe_bytes = sum(r['size'] for r in results if r['safe'])
    return {
        'disk': disk, 'label': DISKS[disk]['label'],
        'runs': results,
        'n_runs': len(results),
        'n_safe': sum(1 for r in results if r['safe']),
        'safe_bytes': safe_bytes, 'safe_bytes_h': human(safe_bytes),
        'active_run': act,
        'usage': disk_usage().get(disk, {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def component_scan(verify: bool = True, force: bool = False, progress=None,
                   allow_stale: bool = False) -> dict:
    """The run -> subrun -> component tree with a delete verdict on every
    component.

    verify=False skips EOS entirely (instant, works offline): sizes and local
    guards are filled in but nothing is marked safe. verify=True issues ONE
    recursive EOS listing and verifies every component from it.

    allow_stale=True verifies against the LAST listing however old it is,
    without contacting EOS, and reports its age in 'checked_age_h'. That is
    what a page reload wants: re-running a 32 s check on every load costs real
    time and changes nothing, since the verdict only moves when the backup
    watcher pushes. The result is advisory — delete_components() re-lists with
    force=True and re-verifies every item before removing anything.

    progress(phase, done, total, msg) is called through the three phases —
    'scan' (local walk), 'listing' (the opaque EOS call) and 'verify' (per run,
    genuinely counted).
    """
    progress = progress or _noop_progress
    progress('scan', 0, None, 'walking the local run tree…')
    act = active_run()
    newest = newest_run()
    tree = _local_tree()
    progress('scan', 1, 1, f'{len(tree)} run(s) on disk')

    # A replay with nothing to replay must not silently become a live 32 s
    # listing — that is the page-load block this whole path exists to avoid.
    # Fall back to the unverified view; the user clicks "Run safety check".
    if allow_stale and not force and not _has_cached_listing():
        verify = False

    rmap = (_remote_runs_map(force=force, progress=progress, allow_stale=allow_stale)
            if verify else None)
    unverifiable = verify and rmap is None
    by_run = _partition_by_run(rmap) if rmap is not None else {}

    chk = last_check() if verify and not unverifiable else {}

    runs_out = []
    names = sorted(tree, key=_run_key)
    for idx, run in enumerate(names):
        progress('verify', idx, len(names), f'verifying {run}')
        rentry = tree[run]
        remote = by_run.get(run, {})
        guard = _run_guard(run, act, newest)

        subs_out = []
        run_comp = {c: {'size': 0, 'files': 0, 'safe_size': 0, 'n_safe': 0, 'n_total': 0}
                    for c in COMPONENT_ORDER}
        for subrun in sorted(rentry['subruns']):
            sentry = rentry['subruns'][subrun]
            complete = subrun_complete(run, subrun)
            sub_guard = guard or ('' if complete else
                                  'missing .subrun_complete (possibly mid-write) — refusing')

            comps_out = {}
            for comp in COMPONENT_ORDER:
                local = sentry['components'].get(comp)
                if not local:
                    continue
                if verify and not unverifiable:
                    v = _verify_component(comp, local, remote)
                else:
                    v = {'component': comp, 'files': len(local),
                         'size': sum(local.values()), 'ok': 0, 'missing': 0,
                         'mismatch': 0, 'safe': False,
                         'reason': ('could not list EOS (Kerberos/network?) — NOT safe'
                                    if unverifiable else 'not yet verified against EOS')}
                if sub_guard:
                    v['safe'] = False
                    v['reason'] = f"{v['reason']} · {sub_guard}" if v['reason'] else sub_guard
                v['size_h'] = human(v['size'])
                comps_out[comp] = v

                agg = run_comp[comp]
                agg['size'] += v['size']
                agg['files'] += v['files']
                agg['n_total'] += 1
                if v['safe']:
                    agg['n_safe'] += 1
                    agg['safe_size'] += v['size']

            sub_size = sum(v['size'] for v in comps_out.values()) + sentry['other']['size']
            subs_out.append({
                'subrun': subrun, 'run': run, 'complete': complete,
                'guard': sub_guard, 'components': comps_out,
                'other': sentry['other'], 'other_h': human(sentry['other']['size']),
                'size': sub_size, 'size_h': human(sub_size),
            })

        run_comp = {c: v for c, v in run_comp.items() if v['n_total']}
        for c, v in run_comp.items():
            v['size_h'] = human(v['size'])
            v['safe_size_h'] = human(v['safe_size'])
        run_size = (sum(v['size'] for v in run_comp.values())
                    + rentry['other']['size']
                    + sum(s['other']['size'] for s in subs_out))
        runs_out.append({
            'run': run, 'guard': guard,
            'active': run == act, 'newest': run == newest,
            'components': run_comp, 'subruns': subs_out,
            'n_subruns': len(subs_out),
            'other': rentry['other'], 'other_h': human(rentry['other']['size']),
            'size': run_size, 'size_h': human(run_size),
        })

    progress('verify', len(names), len(names), 'verification complete')

    totals = {c: {'size': 0, 'safe_size': 0} for c in COMPONENT_ORDER}
    for r in runs_out:
        for c, v in r['components'].items():
            totals[c]['size'] += v['size']
            totals[c]['safe_size'] += v['safe_size']
    for c, v in totals.items():
        v['size_h'] = human(v['size'])
        v['safe_size_h'] = human(v['safe_size'])

    grand = sum(r['size'] for r in runs_out)
    safe_total = sum(v['safe_size'] for v in totals.values())
    return {
        'runs': runs_out, 'n_runs': len(runs_out),
        'components': {c: dict(COMPONENTS[c], key=c) for c in COMPONENT_ORDER},
        'component_order': COMPONENT_ORDER,
        'totals': totals,
        'total_bytes': grand, 'total_h': human(grand),
        'safe_bytes': safe_total, 'safe_bytes_h': human(safe_total),
        'verified': bool(verify and not unverifiable),
        'unverifiable': unverifiable,
        'active_run': act, 'newest_run': newest,
        'reprocess_sentinel': REPROCESS_SENTINEL,
        'usage': disk_usage().get('data', {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # When the EOS data behind these verdicts was taken, so the page can
        # say "good as of 2 minutes ago" instead of implying it just checked.
        'checked_at': chk.get('at_h', ''),
        'checked_age_s': chk.get('age_s'),
        'checked_age_h': _age_h(chk.get('age_s')),
        'checked_stale': bool(chk.get('age_s') is not None
                              and chk['age_s'] > STALE_CHECK_S),
    }


# --- Delete ----------------------------------------------------------------

def _log_delete(msg: str):
    try:
        os.makedirs(os.path.dirname(DELETE_LOG), exist_ok=True)
        with open(DELETE_LOG, 'a') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except Exception:
        pass


def delete_run(disk: str, run: str) -> dict:
    """Delete one run directory, but ONLY after re-verifying, here, that it is
    safe. Never trusts a caller verdict.

    Guards, in order:
      1. disk is known; run matches RUN_NAME_RE.
      2. target resolves to a real directory sitting DIRECTLY under the runs
         root (no symlinks, no traversal, no partial-name tricks).
      3. run is not the active run, not the newest run on disk, and has no
         subrun missing its .subrun_complete marker.
      4. a fresh verify_run() says SAFE (every file on EOS at matching size).
    """
    if disk not in DISKS:
        return {'success': False, 'message': f'unknown disk {disk!r}'}
    if not RUN_NAME_RE.match(run or ''):
        return {'success': False, 'message': f'invalid run name {run!r}'}

    root = _runs_root().resolve()
    target = _runs_root() / run
    try:
        rtarget = target.resolve()
    except OSError as e:
        return {'success': False, 'message': f'cannot resolve path: {e}'}
    if target.is_symlink():
        return {'success': False, 'message': 'refusing to delete a symlink'}
    if not rtarget.is_dir():
        return {'success': False, 'message': f'{run} is not a directory on {disk}'}
    if rtarget.parent != root or rtarget == root:
        return {'success': False, 'message': 'path is not a run directly under the runs root'}

    verdict = verify_run(disk, run, force=True)
    verdict = _apply_local_guards(verdict, run, active_run(), newest_run())
    if not verdict['safe']:
        _log_delete(f"REFUSED {disk}/{run}: {verdict['reason']}")
        return {'success': False, 'message': f"not safe to delete: {verdict['reason']}",
                'verdict': verdict}

    size = _dir_size(rtarget)
    try:
        shutil.rmtree(rtarget)
    except Exception as e:
        _log_delete(f"ERROR deleting {disk}/{run}: {e}")
        return {'success': False, 'message': f'delete failed: {e}'}

    _log_delete(f"DELETED {disk}/{run}  freed={human(size)}  ({verdict['reason']})")
    return {'success': True, 'run': run, 'disk': disk,
            'freed_bytes': size, 'freed_h': human(size),
            'message': f'Deleted {disk}/{run}, freed {human(size)}'}


def delete_runs(disk: str, runs: list) -> dict:
    """Delete several runs; each is independently re-verified. Stops nothing on
    a single failure — reports per-run outcomes."""
    results = []
    freed = 0
    for run in runs:
        r = delete_run(disk, run)
        results.append(r)
        if r.get('success'):
            freed += r.get('freed_bytes', 0)
    return {'results': results, 'freed_bytes': freed, 'freed_h': human(freed),
            'n_deleted': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


# --- Component delete ------------------------------------------------------

def _component_path(run: str, subrun: str, comp: str):
    """(path, root) for a component of a subrun, or (None, None) if the names
    or the component key are not valid. The caller still has to confirm the
    resolved path sits inside root."""
    if comp not in COMPONENTS:
        return None, None
    if not RUN_NAME_RE.match(run or '') or not SUBRUN_NAME_RE.match(subrun or ''):
        return None, None
    spec = COMPONENTS[comp]
    if spec['tree'] == 'dream_run':
        root = _dream_run_root()
        return root / run / subrun, root
    root = _runs_root()
    return root / run / subrun / spec['dir'], root


def _normalize_items(items):
    """[(run, subrun, component)] from the wire format, de-duplicated and with
    unknown/invalid entries dropped."""
    out, seen = [], set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        run = it.get('run')
        subrun = it.get('subrun')
        comp = it.get('component')
        if comp not in COMPONENTS:
            continue
        if not RUN_NAME_RE.match(run or '') or not SUBRUN_NAME_RE.match(subrun or ''):
            continue
        key = (run, subrun, comp)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _component_contents(run: str, subrun: str, comp: str) -> dict:
    """{relpath-within-run: (size, Path)} for the files this component covers,
    read fresh from disk (not from a cached scan). Keys line up with the EOS
    map; the Paths are what a delete actually unlinks, so verification and
    removal are guaranteed to be talking about the same file set."""
    path, root = _component_path(run, subrun, comp)
    if path is None or not path.is_dir():
        return {}
    spec = COMPONENTS[comp]
    out = {}
    for f in path.rglob('*'):
        try:
            if not f.is_file() or f.is_symlink():
                continue
            if spec['suffix'] and not f.name.lower().endswith(spec['suffix']):
                continue
            size = f.stat().st_size
        except OSError:
            continue
        inner = f.relative_to(path).as_posix()
        rel = (f"{subrun}/{inner}" if spec['tree'] == 'dream_run'
               else f"{subrun}/{spec['dir']}/{inner}")
        out[rel] = (size, f)
    return out


def _component_local_files(run: str, subrun: str, comp: str) -> dict:
    """{relpath-within-run: size} — the verification view of _component_contents."""
    return {rel: sz for rel, (sz, _) in _component_contents(run, subrun, comp).items()}


def preflight_components(items) -> dict:
    """Dry-run a component selection: what it would free, what is refused, and
    which subruns the processor would reprocess afterwards.

    Uses the cached EOS listing (this runs on every selection change); the real
    delete re-verifies against a fresh one. allow_stale so that a selection
    click can never stall on a 32 s listing just because the 90 s memory TTL
    happened to lapse — being advisory is the whole point of a preflight.
    """
    triples = _normalize_items(items)
    act, newest = active_run(), newest_run()
    rmap = _remote_runs_map(allow_stale=True)
    by_run = _partition_by_run(rmap) if rmap is not None else {}

    ok_items, refused = [], []
    freed = 0
    selected = {}                        # {(run, subrun): {components}}
    for run, subrun, comp in triples:
        selected.setdefault((run, subrun), set()).add(comp)
        local = _component_local_files(run, subrun, comp)
        entry = {'run': run, 'subrun': subrun, 'component': comp,
                 'label': COMPONENTS[comp]['label'],
                 'size': sum(local.values()), 'files': len(local)}
        entry['size_h'] = human(entry['size'])

        guard = _run_guard(run, act, newest)
        if not guard and not subrun_complete(run, subrun):
            guard = 'missing .subrun_complete (possibly mid-write)'
        if guard:
            entry['reason'] = guard
            refused.append(entry)
            continue
        if not local:
            entry['reason'] = 'nothing present'
            refused.append(entry)
            continue
        if rmap is None:
            entry['reason'] = 'could not list EOS (Kerberos/network?) — NOT safe'
            refused.append(entry)
            continue
        v = _verify_component(comp, local, by_run.get(run, {}))
        if not v['safe']:
            entry['reason'] = v['reason']
            refused.append(entry)
            continue
        entry['reason'] = v['reason']
        ok_items.append(entry)
        freed += entry['size']

    # Deleting the processor's "done" marker while its input FDFs survive makes
    # the watcher redo the whole pipeline for that subrun. Only warn when the
    # FDFs will actually still be there afterwards.
    warnings = []
    for (run, subrun), comps in sorted(selected.items()):
        if REPROCESS_SENTINEL not in comps:
            continue
        if not any(i['run'] == run and i['subrun'] == subrun
                   and i['component'] == REPROCESS_SENTINEL for i in ok_items):
            continue          # refused anyway, nothing will be removed
        if REPROCESS_INPUT in comps:
            continue          # FDFs go too -> nothing left to reprocess from
        if _component_local_files(run, subrun, REPROCESS_INPUT):
            warnings.append({'run': run, 'subrun': subrun,
                             'message': f'{run}/{subrun}: combined hits removed while the '
                                        f'raw FDFs stay — the processor will re-decode, '
                                        f're-analyze and re-combine this subrun'})

    return {
        'items': ok_items, 'refused': refused,
        'n_ok': len(ok_items), 'n_refused': len(refused),
        'freed_bytes': freed, 'freed_h': human(freed),
        'reprocess_warnings': warnings,
        'unverifiable': rmap is None,
    }


def _delete_component(run: str, subrun: str, comp: str, remote: dict,
                      act: str, newest: str) -> dict:
    """Delete one component of one subrun after re-verifying it here.

    Guards, in order:
      1. component key known; run/subrun match their name regexes.
      2. target resolves to a real directory inside the component's own root
         (no symlinks, no traversal).
      3. run is not active and not the newest; the subrun has .subrun_complete.
      4. a fresh verification says SAFE for exactly the files about to go.
    """
    res = {'run': run, 'subrun': subrun, 'component': comp,
           'label': COMPONENTS.get(comp, {}).get('label', comp),
           'success': False, 'freed_bytes': 0, 'freed_h': '0 B', 'message': ''}

    path, root = _component_path(run, subrun, comp)
    if path is None:
        res['message'] = 'invalid run/subrun/component'
        return res
    try:
        rtarget = path.resolve()
        rroot = root.resolve()
    except OSError as e:
        res['message'] = f'cannot resolve path: {e}'
        return res
    if path.is_symlink():
        res['message'] = 'refusing to delete a symlink'
        return res
    if not rtarget.is_dir():
        res['message'] = 'not present'
        return res
    if rroot not in rtarget.parents:
        res['message'] = 'path escapes the managed root'
        return res

    guard = _run_guard(run, act, newest)
    if not guard and not subrun_complete(run, subrun):
        guard = 'missing .subrun_complete (possibly mid-write)'
    if guard:
        res['message'] = f'not safe: {guard}'
        _log_delete(f"REFUSED {run}/{subrun}/{comp}: {guard}")
        return res

    contents = _component_contents(run, subrun, comp)
    if not contents:
        res['message'] = 'nothing present'
        return res
    local = {rel: sz for rel, (sz, _) in contents.items()}
    v = _verify_component(comp, local, remote)
    if not v['safe']:
        res['message'] = f"not safe: {v['reason']}"
        _log_delete(f"REFUSED {run}/{subrun}/{comp}: {v['reason']}")
        return res

    spec = COMPONENTS[comp]
    size = sum(local.values())
    try:
        if spec['suffix']:
            # File-scoped component: remove exactly the files just verified,
            # leaving the directory and its metadata (run_time.txt, RunCtrl
            # logs, pedestal_run.txt, *.cfg, *.prg) untouched.
            for _, f in contents.values():
                f.unlink()
        else:
            shutil.rmtree(rtarget)
    except Exception as e:
        _log_delete(f"ERROR deleting {run}/{subrun}/{comp}: {e}")
        res['message'] = f'delete failed: {e}'
        return res

    res.update(success=True, freed_bytes=size, freed_h=human(size),
               message=f'freed {human(size)}')
    _log_delete(f"DELETED {run}/{subrun}/{comp}  freed={human(size)}  ({v['reason']})")
    return res


def delete_components(items, progress=None) -> dict:
    """Delete a set of (run, subrun, component) triples. Every one is
    independently re-verified here against a FRESH EOS listing — a caller
    verdict, or the cached listing a preflight used, is never trusted.

    One xrdfs call covers the whole batch; see the module docstring on why that
    matters. progress(phase, done, total, msg, item) reports the opaque
    'listing' phase and then a real byte-weighted 'delete' phase, handing back
    each per-item result as it lands so the GUI can log it live.
    """
    progress = progress or _noop_progress
    triples = _normalize_items(items)
    if not triples:
        return {'results': [], 'n_deleted': 0, 'n_failed': 0,
                'freed_bytes': 0, 'freed_h': '0 B',
                'message': 'nothing valid selected'}

    act, newest = active_run(), newest_run()
    rmap = _remote_runs_map(force=True, progress=progress)
    if rmap is None:
        return {'results': [], 'n_deleted': 0, 'n_failed': len(triples),
                'freed_bytes': 0, 'freed_h': '0 B',
                'message': 'could not list runs on EOS (Kerberos/network?) — '
                           'refusing to delete anything'}
    by_run = _partition_by_run(rmap)

    # Weight the bar by bytes, not item count: dropping one 12 GB raw_fdf and
    # one 200 MB combined_hits are not half the job each.
    ordered = sorted(triples, key=lambda t: (t[0], t[1], COMPONENTS[t[2]]['order']))
    weights = [sum(_component_local_files(*t).values()) for t in ordered]
    total = sum(weights) or 1

    results = []
    freed = done = 0
    for (run, subrun, comp), w in zip(ordered, weights):
        progress('delete', done, total, f'{run}/{subrun} — {COMPONENTS[comp]["label"]}')
        r = _delete_component(run, subrun, comp, by_run.get(run, {}), act, newest)
        results.append(r)
        if r['success']:
            freed += r['freed_bytes']
        done += w
        progress('delete', done, total,
                 f'{run}/{subrun} — {COMPONENTS[comp]["label"]}', item=r)

    _prune_empty_dream_run_dirs({run for run, _, c in triples if c == 'dream_run'})
    progress('delete', total, total, f'freed {human(freed)}')

    return {'results': results,
            'n_deleted': sum(1 for r in results if r['success']),
            'n_failed': sum(1 for r in results if not r['success']),
            'freed_bytes': freed, 'freed_h': human(freed)}


def _prune_empty_dream_run_dirs(runs):
    """Remove dream_run/<run> once its last subrun has gone, so the staging tree
    does not accumulate empty shells. Only ever removes directories that
    contain no files at all."""
    root = _dream_run_root()
    for run in runs:
        if not RUN_NAME_RE.match(run or ''):
            continue
        d = root / run
        try:
            if not d.is_dir() or d.is_symlink():
                continue
            if any(f.is_file() for f in d.rglob('*')):
                continue
            shutil.rmtree(d)
            _log_delete(f"PRUNED empty dream_run/{run}")
        except OSError:
            pass


# --- Restore (EOS -> local) -------------------------------------------------
# The inverse of delete: pull a run back from EOS onto the local data disk.
# EOS mirrors the local layout, so restore targets the same runs root. Only
# files missing or size-mismatched locally are fetched (xrdcp -f), so it is
# idempotent and cheap to re-run — exactly the reverse of the backup sync.

def _xrdcp_download(eos_file: str, local_path: Path, url: str = None):
    """Copy one file EOS -> local via native xrdcp. Returns (ok, stderr).
    `url` defaults to the primary endpoint; restore passes the endpoint the
    file was actually listed at, which may be an older destination."""
    if url is None:
        _, _, url, _ = _cfg()
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, str(e)
    src = f"{url}//{eos_file.lstrip('/')}"
    try:
        r = subprocess.run(['xrdcp', '-f', '--nopbar', src, str(local_path)],
                           capture_output=True, text=True)
    except OSError as e:
        return False, f'xrdcp not available: {e}'
    return (r.returncode == 0), (r.stderr or '').strip()


def list_eos_runs():
    """Sorted run names present on EOS, or None if the listing failed. Derived
    from the shared whole-tree listing, so it costs nothing extra."""
    rmap = _remote_runs_map()
    if rmap is None:
        return None
    return sorted({k.split('/')[0] for k in rmap if RUN_NAME_RE.match(k.split('/')[0])},
                  key=_run_key)


def scan_restore() -> dict:
    """List every run on EOS and, for each, how it compares to the local disk:
    complete (already local), partial, or missing. 'To fetch' is the bytes that
    would be pulled (files absent or size-mismatched locally)."""
    rmap = _remote_runs_map()
    if rmap is None:
        raise RuntimeError('could not list runs on EOS (Kerberos/network?)')
    by_run = _partition_by_run(rmap)
    runs = sorted((r for r in by_run if RUN_NAME_RE.match(r)), key=_run_key)
    act = active_run()
    runs_root, _, _, _ = _cfg()
    results = []
    fetch_total = 0
    for run in runs:
        remote = by_run.get(run, {})
        r = {'run': run, 'disk': 'data', 'active': run == act}
        eos_bytes = sum(remote.values())
        total = len(remote)
        local_root = runs_root / run
        local = _local_size_map(local_root) if local_root.is_dir() else {}
        have = fetch_bytes = 0
        for rel, sz in remote.items():
            if local.get(rel) == sz:
                have += 1
            else:
                fetch_bytes += sz
        fetch_files = total - have
        status = 'complete' if fetch_files == 0 else ('missing' if not local else 'partial')
        restorable = fetch_files > 0 and not r['active']
        r.update(status=status, restorable=restorable, eos_bytes=eos_bytes,
                 size_h=human(eos_bytes), total=total, have=have,
                 fetch_files=fetch_files, fetch_bytes=fetch_bytes, fetch_h=human(fetch_bytes))
        if restorable:
            fetch_total += fetch_bytes
        results.append(r)
    return {
        'runs': results, 'n_runs': len(results),
        'n_restorable': sum(1 for r in results if r['restorable']),
        'fetch_bytes_total': fetch_total, 'fetch_bytes_total_h': human(fetch_total),
        'active_run': act, 'usage': disk_usage().get('data', {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def restore_run(run: str) -> dict:
    """Pull one run from EOS onto the local disk. Copies only files missing or
    size-mismatched locally. Refuses the active run (would clobber live writes)
    and aborts if the disk lacks free space for the fetch."""
    res = {'run': run, 'disk': 'data', 'success': False, 'restored_files': 0,
           'fetched_bytes': 0, 'fetched_h': '0 B', 'message': ''}
    if not RUN_NAME_RE.match(run or ''):
        res['message'] = f'invalid run name {run!r}'
        return res
    if run == active_run():
        res['message'] = f'{run} is the active run — refusing'
        return res
    runs_root, fs_path, _, _ = _cfg()
    rmap = _remote_runs_map(force=True)
    if rmap is None:
        res['message'] = 'could not list runs on EOS (Kerberos/network?)'
        return res
    remote = _partition_by_run(rmap).get(run, {})
    if not remote:
        res['message'] = 'run not found on EOS'
        return res

    local_root = runs_root / run
    to_fetch = []
    for rel, sz in remote.items():
        lp = local_root / rel
        try:
            match = lp.is_file() and lp.stat().st_size == sz
        except OSError:
            match = False
        if not match:
            to_fetch.append((rel, sz))

    need = sum(sz for _, sz in to_fetch)
    if need == 0:
        res['success'] = True
        res['message'] = 'already complete locally (nothing to fetch)'
        return res

    try:
        free = shutil.disk_usage(fs_path).free
    except OSError:
        free = None
    MARGIN = 5 * 1024 ** 3   # keep 5 GB headroom on the disk
    if free is not None and need > free - MARGIN:
        res['message'] = f'not enough free space: need {human(need)}, have {human(free)}'
        return res

    fetched = nfiles = 0
    failed = []
    for rel, sz in to_fetch:
        # Per file, not per run: after a destination change a run can be split
        # across locations, so each file is pulled from where it was listed.
        src_url, src_runs = _remote_source(f'{run}/{rel}')
        ok, err = _xrdcp_download(f"{src_runs}/{run}/{rel}", local_root / rel,
                                  url=src_url)
        if ok:
            fetched += sz
            nfiles += 1
        else:
            failed.append(rel)
    res.update(restored_files=nfiles, fetched_bytes=fetched, fetched_h=human(fetched))
    if failed:
        res['success'] = False
        res['message'] = f'{len(failed)} file(s) failed to copy; {nfiles} restored'
        _log_delete(f"RESTORE partial data/{run}: {nfiles} ok, {len(failed)} failed")
    else:
        res['success'] = True
        res['message'] = f'restored {nfiles} files ({human(fetched)})'
        _log_delete(f"RESTORED data/{run}: {nfiles} files, {human(fetched)}")
    return res


def restore_runs(runs: list) -> dict:
    """Restore several runs; each independent. Reports per-run outcomes."""
    results = []
    fetched = 0
    for run in runs:
        r = restore_run(run)
        results.append(r)
        if r.get('success'):
            fetched += r.get('fetched_bytes', 0)
    return {'results': results, 'fetched_bytes': fetched, 'fetched_h': human(fetched),
            'n_restored': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Scan the DREAM data disk for reclaimable space')
    ap.add_argument('--components', action='store_true',
                    help='per-component breakdown instead of per-run verdicts')
    ap.add_argument('--no-verify', action='store_true',
                    help='skip EOS entirely (instant, nothing marked safe)')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if args.components:
        out = component_scan(verify=not args.no_verify)
        if args.json:
            print(json.dumps(out, indent=2))
            raise SystemExit
        u = out['usage']
        if u and not u.get('error'):
            print(f"Data disk: {human(u.get('free', 0))} free of {human(u.get('total', 0))} "
                  f"({u.get('pct', 0)}% used)\n")
        order = out['component_order']
        print(f"{'RUN/SUBRUN':34}" + ''.join(f'{COMPONENTS[c]["label"][:15]:>16}' for c in order))
        print('-' * (34 + 16 * len(order)))
        for r in out['runs']:
            print(f"{r['run']:34}" + ''.join(
                f"{(r['components'][c]['size_h'] if c in r['components'] else '—'):>16}"
                for c in order))
            for s in r['subruns']:
                cells = []
                for c in order:
                    v = s['components'].get(c)
                    cells.append(f"{(v['size_h'] + (' ✓' if v['safe'] else '')) if v else '—':>16}")
                print(f"  {s['subrun']:32}" + ''.join(cells))
        print('-' * (34 + 16 * len(order)))
        print(f"{'TOTAL':34}" + ''.join(f"{out['totals'][c]['size_h']:>16}" for c in order))
        print(f"{'  of which safe':34}" + ''.join(
            f"{out['totals'][c]['safe_size_h']:>16}" for c in order))
        print(f"\n{out['total_h']} on disk, {out['safe_bytes_h']} safe to reclaim"
              + ('' if out['verified'] else '  (NOT verified against EOS)'))
    else:
        out = scan('data')
        if args.json:
            print(json.dumps(out, indent=2))
            raise SystemExit
        u = out['usage']
        if u and not u.get('error'):
            print(f"{out['label']}: {human(u.get('free', 0))} free of {human(u.get('total', 0))} "
                  f"({u.get('pct', 0)}% used)")
        print(f"{'RUN':10} {'SIZE':>10}  {'SAFE':>5}  REASON")
        print('-' * 78)
        for r in out['runs']:
            print(f"{r['run']:10} {r['size_h']:>10}  {'YES' if r['safe'] else 'no':>5}  {r['reason']}")
        print('-' * 78)
        print(f"{out['n_safe']}/{out['n_runs']} runs safe to delete — "
              f"would free {out['safe_bytes_h']}")
