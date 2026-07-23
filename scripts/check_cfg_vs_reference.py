"""
Cross-check a Dream .cfg against the expert reference config.

Why: the DAQ never runs the template as-is — `make_config_from_template` rewrites
a set of parameters per run (samples, ZS, latency, trigger source, multiplicity,
FEU topology, ...). Two things can then silently go wrong:

  1. the expert updates the reference config (e.g. a new latency) and our
     template / run config keeps the stale value, or
  2. our template drifts from the reference in a parameter nobody is watching,
     because the script only ever touches its own list of keys.

This script makes both visible. Every parameter is put in one of three buckets:

  OVERRIDE   — a key the run config deliberately writes. Shown with reference vs
               ours so an intentional override is easy to eyeball, and a *stale*
               one (we still write the old expert value) is easy to catch.
  DRIFT      — differs from the reference and we do NOT write it. This is the
               dangerous bucket: whatever the template shipped with is what the
               FEUs get. Review each one.
  MISSING    — present in one file only.

Usage
-----
    python scripts/check_cfg_vs_reference.py GENERATED.cfg --reference REF.cfg

    # generate a run cfg from the current run config and check it in one go
    python scripts/check_cfg_vs_reference.py --from-run-config --reference REF.cfg

The reference lives on the DAQ machine, e.g.
    banco:/local/home/banco/Feu/Firmware/Implementation/Projects/Software/Linux/
          bin/EicP2Bt/P2B_SelfTcm.cfg
so from elsewhere fetch it first (`scp banco_cern:<path> .`).

Note the current reference is a *self-trigger* config: `Sys DaqRun Trig`, the
Topo Dream roles and the multiplicity window are expected to differ for a beam
(external-trigger) run. Those are listed as EXPECTED-BY-MODE rather than DRIFT.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Keys make_config_from_template() rewrites per run (see dream_daq_control.py).
SCRIPT_WRITTEN = {
    'Sys DaqRun Time', 'Sys DaqRun Mode', 'Sys DaqRun Trig', 'Sys DaqRun Events',
    'Sys Trg MultMoreThan', 'Sys Trg MultLessThan',
    'Sys NbOfSamples', 'Sys Action PedThrRun', 'Sys Action DataRun',
    'Feu * Feu_RunCtrl_ZS', 'Feu * Feu_RunCtrl_Pd', 'Feu * Feu_RunCtrl_CM',
    'Feu * Feu_RunCtrl_ZsTyp', 'Feu * Feu_RunCtrl_ZsChkSmp',
    'Feu * Dream * 12',
}

# Differences that are expected purely because the reference was taken in a
# different trigger mode than the run being checked.
MODE_DEPENDENT = re.compile(
    r'^(Sys DaqRun Trig|Sys Trg Mult(More|Less)Than|Sys Topo Feu \d+|'
    r'Feu (\d+|\*) SelfTrig_)')

# Per-run bookkeeping that is meaningless to compare.
IGNORE = re.compile(
    r'^(Sys Name|Sys DaqRun Time|Feu \d+ Feu_RunCtrl_(Pd|Zs)File|'
    r'Feu \* Feu_RunCtrl_(Pd|Zs)File|Feu \* Feu_RunCtrl_Id|Feu \d+ Feu_RunCtrl_Id|'
    r'Feu \d+ NetChan_Ip)')


def parse_cfg(path):
    """Parse a Dream .cfg into {key: value}, skipping comments and blank lines.

    The key is every token but the last, except for the multi-value forms
    (``Sys Topo Feu N Dream ...`` and ``Feu <s> Dream <s> <reg> v v v v``) which
    are keyed on their identifying prefix. The keying only has to be *consistent*
    between the two files for the comparison to be meaningful.
    """
    values = {}
    with open(path) as f:
        for raw in f:
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            tok = line.split()
            m = re.match(r'^(Sys\s+Topo\s+Feu\s+\d+)\s+Dream\s+(.*)$', line)
            if m:
                key, val = ' '.join(m.group(1).split()), ' '.join(m.group(2).split())
            elif re.match(r'^Feu\s+\S+\s+Dream\s+\S+\s+\d+\s', line):
                key, val = ' '.join(tok[:5]), ' '.join(tok[5:])
            elif len(tok) >= 2:
                key, val = ' '.join(tok[:-1]), tok[-1]
            else:
                continue
            values[key] = val
    return values


def norm(val):
    """Normalise a value for comparison: case-fold hex, drop trailing zeros."""
    out = []
    for tok in val.split():
        t = tok.lower()
        if re.fullmatch(r'0x[0-9a-f]+', t):
            t = f'0x{int(t, 16):04x}'
        elif re.fullmatch(r'-?\d+(\.\d*)?', t):
            t = str(float(t))  # 5 and 5.00 are the same setting
        out.append(t)
    return ' '.join(out)


def compare(generated_path, reference_path):
    gen, ref = parse_cfg(generated_path), parse_cfg(reference_path)

    overrides, drift, mode_diff, missing = [], [], [], []
    for key in sorted(set(gen) | set(ref)):
        if IGNORE.match(key):
            continue
        g, r = gen.get(key), ref.get(key)
        if g is None or r is None:
            missing.append((key, g, r))
        elif norm(g) == norm(r):
            continue
        elif key in SCRIPT_WRITTEN and not MODE_DEPENDENT.match(key):
            overrides.append((key, g, r))
        elif MODE_DEPENDENT.match(key):
            mode_diff.append((key, g, r))
        else:
            drift.append((key, g, r))

    return overrides, drift, mode_diff, missing


def _print(title, rows, note='', left='ours', right='reference'):
    print(f'\n=== {title} ({len(rows)}) ===')
    if note:
        print(f'    {note}')
    if not rows:
        print('    none')
        return
    width = max(len(k) for k, _, _ in rows)
    for key, g, r in rows:
        print(f'  {key:<{width}}   {left}: {g if g is not None else "-":<28} '
              f'{right}: {r if r is not None else "-"}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('generated', nargs='?', help='run .cfg produced by the DAQ script')
    ap.add_argument('--reference', required=True, help='expert reference .cfg')
    ap.add_argument('--from-run-config', action='store_true',
                    help='generate a run .cfg from run_config_beam first (needs --template)')
    ap.add_argument('--template', help='template .cfg to generate from')
    ap.add_argument('--out-dir', default='/tmp/cfg_crosscheck/',
                    help='where --from-run-config writes the generated cfg')
    args = ap.parse_args()

    generated = args.generated
    if args.from_run_config:
        if not args.template:
            ap.error('--from-run-config needs --template')
        from dream_daq_control import make_config_from_template
        from run_config_beam import Config
        d = Config().dream_daq_info
        os.makedirs(args.out_dir, exist_ok=True)
        generated = make_config_from_template(
            args.out_dir if args.out_dir.endswith('/') else args.out_dir + '/',
            args.template, 5,
            d['zero_suppress'], d['n_samples_per_waveform'], d['pedestal_subtraction'],
            d['common_noise_subtraction'], d['zs_type'], d['zs_check_sample'], d['latency'],
            # Same FEU selection / Dream roles a real run gets, so the check sees
            # the cfg the FEUs would actually be programmed with.
            d.get('included_feus'), d.get('feu_connectors'), d.get('trigger_feu'),
            do_pedestal_threshold_run=d['do_pedestal_threshold_run'],
            do_data_run=d['do_data_run'], self_trigger=d['self_trigger'],
            trg_mult_more_than=d['trg_mult_more_than'],
            trg_mult_less_than=d['trg_mult_less_than'],
            daq_run_events=d.get('daq_run_events'))
    if not generated:
        ap.error('give a generated .cfg or use --from-run-config')

    print(f'ours      : {generated}')
    print(f'reference : {args.reference}')
    overrides, drift, mode_diff, missing = compare(generated, args.reference)

    _print('DELIBERATE OVERRIDES', overrides,
           'keys the run config writes on purpose — check none is a stale expert value')
    _print('EXPECTED BY TRIGGER MODE', mode_diff,
           'trigger source / roles / multiplicity: differ if the reference is a different mode')
    _print('UNREVIEWED DRIFT', drift,
           'we do NOT write these — the template value is what the FEUs get. Review each.')
    _print('PRESENT IN ONE FILE ONLY', missing)

    print(f'\nsummary: {len(overrides)} override(s), {len(drift)} drift, '
          f'{len(mode_diff)} mode-dependent, {len(missing)} one-sided')
    return 1 if drift else 0


if __name__ == '__main__':
    sys.exit(main())
