#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prune completed sub-runs of the RUNNING run, to keep a long scan on disk.

Why this exists
---------------
flask_app/space_manager.py refuses to delete anything belonging to the active
run, or to the newest run on disk (_run_guard). That is the right default:
between runs the state file may already point at the next run while this one
still has files in flight. But a 14 h scan produces ~845 GB against ~508 GB of
reachable space, so its own completed sub-runs have to be reclaimed WHILE it
runs or it fills the disk around hour 9.

This script lifts that ONE guard and keeps every other check space_manager
makes. In particular it still refuses unless:

  * a FRESH EOS listing is available (never the cache) — no listing, no delete;
  * every file about to be removed is present on EOS at a matching byte size,
    via space_manager's own _verify_component;
  * the sub-run has its .subrun_complete marker. This is what excludes the
    IN-PROGRESS sub-run: daq_control only writes the marker once the sub-run
    finishes cleanly, so the one currently being written is never a candidate.
    A sub-run stopped by hand deliberately has no marker either.
  * the target resolves to a real directory inside its own component root, and
    is not a symlink.

So the data is only ever dropped locally once EOS provably holds the same
bytes. backup_watcher is push-only, so a local delete cannot propagate there.

Usage
-----
    # dry run — shows what WOULD go, touches nothing (default)
    .venv/bin/python scripts/prune_active_run.py

    # actually delete
    .venv/bin/python scripts/prune_active_run.py --apply

    # choose components (default: dream_run,raw_fdf)
    .venv/bin/python scripts/prune_active_run.py --apply --components dream_run,raw_fdf,hits_root

    # a run other than the one in config/current_run_state.json
    .venv/bin/python scripts/prune_active_run.py --run drift_mesh_2d_1 --apply

Deletions land in logs/daq_events.log through space_manager's own _log_delete,
so the audit trail is the same as a GUI-driven prune.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, 'flask_app'))

import space_manager as sm  # noqa: E402

# Passed to _delete_component in place of the real active/newest run names.
# _run_guard only ever compares the run name against these two, so a sentinel
# that cannot be a real run directory disables exactly that guard and nothing
# else. Every other check in _delete_component still runs.
_NO_GUARD = '\x00not-a-run\x00'

DEFAULT_COMPONENTS = ('dream_run', 'raw_fdf')


def _candidates(run, components):
    """(subrun, component) pairs of `run` that are complete and have files."""
    run_root = sm._runs_root() / run
    if not run_root.is_dir():
        return [], f'run directory not found: {run_root}'
    out = []
    for sub in sorted(p.name for p in run_root.iterdir() if p.is_dir()):
        if not sm.subrun_complete(run, sub):
            continue          # in progress, or stopped by hand — leave it alone
        for comp in components:
            if sm._component_local_files(run, sub, comp):
                out.append((sub, comp))
    return out, ''


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', default=None,
                    help='run to prune (default: the one in current_run_state.json)')
    ap.add_argument('--components', default=','.join(DEFAULT_COMPONENTS),
                    help=f'comma-separated; known: {", ".join(sm.COMPONENTS)} '
                         f'(default: {",".join(DEFAULT_COMPONENTS)})')
    ap.add_argument('--apply', action='store_true',
                    help='actually delete (default is a dry run)')
    args = ap.parse_args()

    components = [c.strip() for c in args.components.split(',') if c.strip()]
    unknown = [c for c in components if c not in sm.COMPONENTS]
    if unknown:
        sys.exit(f'unknown component(s): {", ".join(unknown)}\n'
                 f'known: {", ".join(sm.COMPONENTS)}')

    run = args.run or sm.active_run()
    if not run:
        sys.exit('no active run in config/current_run_state.json — pass --run explicitly')

    before = sm.disk_usage()['data']
    print(f'run        : {run}')
    print(f'components : {", ".join(components)}')
    print(f'disk free  : {sm.human(before["free"])}  ({before["pct"]:.1f}% used)')
    print(f'mode       : {"APPLY — deleting" if args.apply else "DRY RUN — nothing will be deleted"}')
    print()

    pairs, err = _candidates(run, components)
    if err:
        sys.exit(err)
    if not pairs:
        print('no completed sub-run has any of these components on disk — nothing to do.')
        return

    # Fresh EOS listing, exactly as delete_components does. Without it nothing
    # can be verified, so nothing may be deleted.
    print('listing EOS (fresh, not cached)...')
    rmap = sm._remote_runs_map(force=True)
    if rmap is None:
        sys.exit('could not list EOS (Kerberos/network?) — refusing to delete anything')
    remote = sm._partition_by_run(rmap).get(run, {})

    freed = kept = 0
    n_ok = n_ref = 0
    for sub, comp in sorted(pairs, key=lambda t: (t[0], sm.COMPONENTS[t[1]]['order'])):
        size = sum(sm._component_local_files(run, sub, comp).values())
        label = f'{sub}/{comp}'
        if args.apply:
            r = sm._delete_component(run, sub, comp, remote, _NO_GUARD, _NO_GUARD)
            if r['success']:
                freed += r['freed_bytes']; n_ok += 1
                print(f'  DELETED  {label:52s} {r["freed_h"]:>10s}')
            else:
                kept += size; n_ref += 1
                print(f'  kept     {label:52s} {sm.human(size):>10s}  <- {r["message"]}')
        else:
            # Same verification the real delete would run, without touching anything.
            local = {rel: sz for rel, (sz, _) in sm._component_contents(run, sub, comp).items()}
            v = sm._verify_component(comp, local, remote)
            if v['safe']:
                freed += size; n_ok += 1
                print(f'  would go {label:52s} {sm.human(size):>10s}')
            else:
                kept += size; n_ref += 1
                print(f'  kept     {label:52s} {sm.human(size):>10s}  <- {v["reason"]}')

    print()
    verb = 'freed' if args.apply else 'would free'
    print(f'{verb} {sm.human(freed)} from {n_ok} component(s); {n_ref} kept ({sm.human(kept)})')
    if args.apply:
        after = sm.disk_usage()['data']
        print(f'disk free  : {sm.human(before["free"])} -> {sm.human(after["free"])}')
    else:
        print('re-run with --apply to delete.')


if __name__ == '__main__':
    main()
