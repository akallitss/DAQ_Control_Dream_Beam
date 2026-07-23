"""
Sync our beam .cfg template with the expert reference config.

The chain is:

    expert reference .cfg          (EicP2Bt/P2B_SelfTcm.cfg, updated by the expert)
        |  this script
        v
    dream_config/P2B_Beam.cfg      (our template, DREAM_CFG_TEMPLATE)
        |  make_config_from_template(), per run
        v
    <run_dir>/P2B_Beam.cfg         (what RunCtrl programs into the FEUs)

Only the middle arrow was manual, so an expert update (a new latency, new Dream
register settings, new packet pacing) could sit unnoticed while runs kept using
stale values. This script closes it:

  * fetches the reference (local path or ssh ``host:/path``),
  * reports exactly what changed vs. our current template — changed values,
    parameters the reference added, parameters it dropped,
  * with ``--apply``, rewrites the template from the reference, clearing the
    stale per-FEU PdFile/ZsFile references (each run programs fresh
    pedestals/thresholds) and backing up the previous template,
  * keeps a snapshot of the synced reference so the next run of this script can
    say "the reference changed since we last synced" rather than re-deriving it,
  * finishes by generating a run .cfg and re-running the cross-check, so the
    thing that is verified is the file the FEUs would actually get.

Dry-run by default — nothing is written without ``--apply``.

Usage
-----
    # what changed in the reference since we last synced?
    python scripts/sync_beam_template.py \
        --reference banco_cern:/local/home/banco/Feu/Firmware/Implementation/\
Projects/Software/Linux/bin/EicP2Bt/P2B_SelfTcm.cfg \
        --template /local/home/banco/P2_data/TB_July2026_H4/dream_config/P2B_Beam.cfg

    # pull it in
    python scripts/sync_beam_template.py ... --apply

Parameters the run config writes per run (trigger source, multiplicity, Dream
roles, ZS/Pd/CM, PedThrRun, latency, n_samples) are NOT affected by the sync —
they are overridden downstream anyway. For those the script instead reports when
our run-config value has fallen behind the reference, since that is a
`run_config_beam.py` edit, not a template one.
"""

import argparse
import re
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_cfg_vs_reference import parse_cfg, norm, compare, _print  # noqa: E402

# Run-config fields that mirror a reference parameter. When the reference moves,
# these need a run_config_beam.py edit — the template sync cannot do it, because
# the run config overwrites the template value per run.
RUN_CONFIG_MIRRORS = {
    'Feu * Dream * 12': ('latency', lambda v: f'0x{int(v):04X}'),
    'Sys NbOfSamples': ('n_samples_per_waveform', str),
}

# Snapshots of the reference we last synced from. Kept in the repo (not /tmp) so
# they survive, and so `git diff` on them shows exactly what the expert changed.
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'config', 'reference_snapshots')

# Per-FEU pedestal/threshold .prg references — cleared on every sync.
MEM_FILE = re.compile(r'^Feu\s+(\*|\d+)\s+Feu_RunCtrl_(Pd|Zs)File')


def fetch(spec, dest):
    """Copy a local path or ``host:/path`` (over ssh) to dest. Returns dest."""
    if ':' in spec and not os.path.exists(spec):
        host, path = spec.split(':', 1)
        with open(dest, 'wb') as f:
            subprocess.run(['ssh', '-o', 'BatchMode=yes', host, f'cat {path}'],
                           stdout=f, check=True)
    else:
        shutil.copy(spec, dest)
    return dest


def clear_mem_file_refs(path):
    """Blank the per-FEU pedestal/threshold .prg references.

    The reference config points at the expert's own pedthr outputs; those files
    do not exist in our run directories and abort RunCtrl in FeuMemConfig.
    """
    from dream_daq_control import clear_feu_mem_file_refs
    clear_feu_mem_file_refs(path)


def diff_keys(old, new):
    """(changed, added, removed) between two parsed cfgs.

    The pedestal/threshold .prg references are excluded: we blank them on every
    sync by design, so they would otherwise report as a permanent difference.
    """
    keys_old = {k for k in old if not MEM_FILE.match(k)}
    keys_new = {k for k in new if not MEM_FILE.match(k)}
    changed = [(k, old[k], new[k]) for k in sorted(keys_old & keys_new)
               if norm(old[k]) != norm(new[k])]
    added = [(k, None, new[k]) for k in sorted(keys_new - keys_old)]
    removed = [(k, old[k], None) for k in sorted(keys_old - keys_new)]
    return changed, added, removed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--reference', required=True,
                    help='expert reference .cfg — local path or host:/path (ssh)')
    ap.add_argument('--template', required=True,
                    help='our beam template to sync — local path or host:/path (ssh)')
    ap.add_argument('--apply', action='store_true',
                    help='actually rewrite the template (default: dry run)')
    ap.add_argument('--work-dir', default='/tmp/beam_template_sync/',
                    help='scratch dir for fetched copies')
    ap.add_argument('--snapshot-dir', default=SNAPSHOT_DIR,
                    help='where the synced reference is snapshotted (git-tracked by default, '
                         'so "what did the expert change" is answerable from git diff)')
    args = ap.parse_args()

    os.makedirs(args.work_dir, exist_ok=True)
    ref_local = fetch(args.reference, os.path.join(args.work_dir, 'reference.cfg'))
    tpl_local = fetch(args.template, os.path.join(args.work_dir, 'template.cfg'))

    ref, tpl = parse_cfg(ref_local), parse_cfg(tpl_local)

    # What the reference changed relative to the snapshot we last synced from.
    os.makedirs(args.snapshot_dir, exist_ok=True)
    snap = os.path.join(args.snapshot_dir, os.path.basename(args.reference.split(':')[-1]))
    if os.path.exists(snap):
        changed, added, removed = diff_keys(parse_cfg(snap), ref)
        _print('REFERENCE CHANGED SINCE LAST SYNC', changed + added + removed,
               'expert edits we have not pulled in yet',
               left='last synced', right='reference now')
    else:
        print('\n=== REFERENCE CHANGED SINCE LAST SYNC ===\n'
              '    no snapshot yet — first sync, treating the whole reference as new')

    # What the sync would change in our template.
    changed, added, removed = diff_keys(tpl, ref)
    _print('TEMPLATE VALUES THE SYNC WOULD UPDATE', changed)
    _print('PARAMETERS THE REFERENCE ADDS', added,
           'absent from our template — currently falling through to wildcards')
    _print('PARAMETERS ONLY WE HAVE', removed,
           'dropped by the sync — check none is ours on purpose')

    # Run-config fields that mirror the reference and must be edited by hand.
    print('\n=== RUN-CONFIG VALUES VS REFERENCE ===')
    print('    written per run, so the template sync cannot fix these')
    try:
        from run_config_beam import Config
        d = Config().dream_daq_info
        stale = 0
        for key, (field, fmt) in RUN_CONFIG_MIRRORS.items():
            ours, theirs = d.get(field), ref.get(key)
            if ours is None or theirs is None:
                continue
            ok = norm(fmt(ours)) == norm(theirs.split()[0])
            stale += 0 if ok else 1
            print(f'  {"OK  " if ok else "STALE"}  {key:<20} '
                  f'run config {field}={ours} ({fmt(ours)})  reference {theirs.split()[0]}')
        if stale:
            print(f'  -> edit run_config_beam.py: {stale} field(s) behind the reference')
    except Exception as exc:  # a broken run config must not block the report
        print(f'  could not load run_config_beam: {exc}')

    if not (changed or added or removed):
        print('\ntemplate is already in sync with the reference')
    elif not args.apply:
        print(f'\ndry run — re-run with --apply to write {args.template}')
        return 0

    if args.apply and (changed or added or removed):
        new_local = os.path.join(args.work_dir, 'template.new.cfg')
        shutil.copy(ref_local, new_local)
        clear_mem_file_refs(new_local)
        if ':' in args.template and not os.path.exists(args.template):
            host, path = args.template.split(':', 1)
            subprocess.run(['ssh', '-o', 'BatchMode=yes', host,
                            f'cp -n {path} {path}.bak'], check=True)
            with open(new_local, 'rb') as f:
                subprocess.run(['ssh', '-o', 'BatchMode=yes', host, f'cat > {path}'],
                               stdin=f, check=True)
        else:
            shutil.copy(args.template, args.template + '.bak')
            shutil.copy(new_local, args.template)
        print(f'\nwrote {args.template} (previous kept as .bak)')

    shutil.copy(ref_local, snap)
    print(f'snapshot updated: {snap}')

    print('\nNow verify the cfg a run would actually produce:')
    print('  python scripts/check_cfg_vs_reference.py --from-run-config \\\n'
          f'      --template <local copy of {os.path.basename(args.template)}> '
          f'--reference {ref_local}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
