#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous EOS backup watcher for nTof DREAM DAQ data.

Syncs the entire source_dir to eos_dir, excluding specified subdirectories.
The runs_subdir gets smart per-subrun sync (waits for each subrun to be stable
before transferring).  All other subdirs are synced wholesale on a slower
extra_sync_interval cadence.

Every file under each run is backed up: subrun subdirectories AND loose
run-level files (dream_daq.log, run_config.json, backups, etc.).  Loose files
are refreshed whenever any subrun of that run syncs.

A slow full-reconcile sweep (reconcile_interval, default once a day) runs while
the watcher is otherwise idle: it re-lists EVERY run on EOS and re-copies any
file that is missing or size-mismatched, INCLUDING runs long marked stale.  This
is what propagates after-the-fact edits (e.g. a run_config.json rewrite) to old
runs, which the fast per-subrun path alone would never revisit.

Transfers use the native xrootd protocol (xrdcp/xrdfs), NOT the FUSE mount:
the legacy xrootdfs mount cannot mkdir/rename/overwrite, so rsync-over-FUSE
fails for any new directory.  Files already on EOS at the same size are skipped
(data is write-once); size-mismatched files are re-copied (xrdcp -f overwrites).

Handles Kerberos via kinit -R (renewal) and falls back to a GPG-encrypted
password for a full re-kinit when renewal fails.

Usage:
    python backup_watcher.py <backup_config_json_path>

Config keys (see backup_config.py to generate the JSON):
  source_dir          : local top-level data directory (e.g. /mnt/data/x17/beam_may/)
  eos_dir             : EOS destination (locally FUSE-mounted, same structure)
  xrootd_url          : native xrootd endpoint (e.g. root://eospublic.cern.ch)
  runs_subdir         : name of the runs subdir that gets smart per-subrun sync
  exclude_dirs        : list of subdir names to never sync (e.g. ['dream_run'])
  gpg_pass_file       : path to GPG-encrypted CERN password (~/.cern_pass.gpg)
  cern_principal      : Kerberos principal (e.g. dneff@CERN.CH)
  kinit_interval      : seconds between kinit renewal attempts   (default: 3600)
  include_runs        : list of run dir names to sync exclusively (null = all)
  exclude_runs        : list of run dir names to skip             (null = none)
  poll_interval       : seconds between runs-dir scans           (default: 30)
  stale_run_days      : runs with no new data for N days skipped (default: 10)
  extra_sync_interval : seconds between full syncs of non-runs dirs (default: 300)
  reconcile_interval  : seconds between full-reconcile sweeps of all runs,
                        run only while idle (default: 86400 = once a day)
  rsync_extra_args    : extra arguments passed verbatim to rsync  (default: [])
"""

import os
import sys
import json
import time
import datetime
import subprocess
from pathlib import Path, PurePosixPath

# The DAQ machine (banco) has no CERN realm in its system krb5.conf — point
# kinit/xrdcp at the repo's minimal CERN config unless the caller already set
# one. Also make sure xrdcp/xrdfs from ~/bin are found under tmux/cron.
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('KRB5_CONFIG', os.path.join(_REPO_DIR, 'config', 'krb5_cern.conf'))
os.environ['PATH'] = os.path.expanduser('~/bin') + os.pathsep + os.environ.get('PATH', '')


def main():
    if len(sys.argv) != 2:
        print("Usage: python backup_watcher.py <backup_config_json_path>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        config = json.load(f)
    run_watcher(config, Path(sys.argv[1]))


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher(config: dict, config_path: Path):
    global _XROOTD_URL, _XRDCP_EXTRA

    source_dir    = Path(config['source_dir'])
    eos_dir       = Path(config['eos_dir'])
    runs_subdir   = config.get('runs_subdir', 'runs')
    exclude_dirs  = set(config.get('exclude_dirs', []))
    gpg_pass_file = Path(config['gpg_pass_file'])
    cern_principal = config['cern_principal']

    _XROOTD_URL   = config.get('xrootd_url', 'root://eospublic.cern.ch').rstrip('/')
    _XRDCP_EXTRA  = config.get('xrdcp_extra_args', [])

    kinit_interval      = config.get('kinit_interval',      3600)
    extra_sync_interval = config.get('extra_sync_interval',  300)
    poll_interval       = config.get('poll_interval',         30)
    stale_run_days      = config.get('stale_run_days',        10)
    reconcile_interval  = config.get('reconcile_interval',  86400)
    # Backlog sub-runs synced per poll before the live run is re-checked. 1
    # gives ~2-4 GB of granularity (one sub-run) without throttling the
    # backlog meaningfully — at ~30 MB/s that is still ~80 GB/h.
    backlog_per_poll    = max(1, int(config.get('backlog_subruns_per_poll', 1)))
    # Files the ancillary (non-runs) dirs may copy per poll. Sized in TIME, not
    # file count: with _XRDCP_BATCH multi-source copies a batch of 40 costs
    # ~11 s, not the ~5 min it cost when every file paid its own handshake, so
    # 200 is about a minute of work before the loop rechecks the runs. Anything
    # much smaller leaves 637 pedestal files trickling out 40 per idle poll,
    # which during acquisition means never.
    extra_files_per_poll = max(1, int(config.get('extra_sync_files_per_poll', 200)))

    # GUI's last-seen-run tracker, used only to decide sync ORDER.
    current_run_state = config_path.parent / 'current_run_state.json'

    include_runs = set(config['include_runs']) if config.get('include_runs') else None
    exclude_runs = set(config['exclude_runs']) if config.get('exclude_runs') else set()

    runs_dir     = source_dir / runs_subdir
    eos_runs_dir = eos_dir    / runs_subdir

    print(f"[backup] source_dir         : {source_dir}")
    print(f"[backup] eos_dir            : {eos_dir}")
    print(f"[backup] xrootd_url         : {_XROOTD_URL}")
    print(f"[backup] runs_subdir        : {runs_subdir}")
    print(f"[backup] exclude_dirs       : {sorted(exclude_dirs)}")
    print(f"[backup] principal          : {cern_principal}")
    print(f"[backup] kinit_interval     : {kinit_interval}s")
    print(f"[backup] extra_sync_interval: {extra_sync_interval}s")
    if include_runs:
        print(f"[backup] include_runs       : {sorted(include_runs)}")
    if exclude_runs:
        print(f"[backup] exclude_runs       : {sorted(exclude_runs)}")
    print(f"[backup] poll               : {poll_interval}s  stale_after={stale_run_days}d")

    state_path = config_path.parent / 'backup_state.json'
    loose_state_path = config_path.parent / 'backup_loose_state.json'
    # (run_name, subrun_name) -> total dir size at last successful rsync
    synced_sizes: dict = _load_state(state_path)
    loose_sigs:   dict = _load_loose_state(loose_state_path)
    # (run_name, subrun_name) -> total dir size from previous poll (stable check)
    prev_sizes: dict = {}

    checked_stale_runs: set = set()

    last_kinit_check  = -kinit_interval   # trigger immediately on first iteration
    last_extra_sync   = -extra_sync_interval
    last_reconcile    = -reconcile_interval  # reconcile on first idle after startup
    kerberos_ok       = False

    idle_ticks = 0
    idle_line  = False
    _SPINNER   = ['-', '\\', '|', '/']

    def _end_idle():
        nonlocal idle_line
        if idle_line:
            sys.stdout.write('\n')
            sys.stdout.flush()
            idle_line = False

    while True:
        now = time.time()

        # --- Kerberos refresh ---
        if now - last_kinit_check >= kinit_interval:
            ok, method = _refresh_kerberos(cern_principal, gpg_pass_file)
            last_kinit_check = now
            kerberos_ok = ok
            _end_idle()
            if ok:
                print(f"[backup] Kerberos OK ({method})")
            else:
                print(f"[backup] Kerberos FAILED: {method}")

        found_new = False

        if not source_dir.exists():
            pass
        elif not kerberos_ok:
            _end_idle()
            print("[backup] Skipping scan — Kerberos not authenticated")
        else:
            # --- Smart per-subrun sync for runs_subdir ---
            if runs_dir.exists():
                # The live run goes FIRST, always. Plain sorted() order put a
                # 137 GB backlog ahead of the run being acquired on 2026-07-27:
                # the live sub-runs then went un-backed-up for hours, which in
                # turn starved prune_active_run (it only deletes what it can
                # verify on EOS) and nearly filled the disk mid-run. Ordering
                # alone is not enough — see backlog_budget below.
                priority = _priority_run(runs_dir, current_run_state)
                run_dirs = [d for d in sorted(runs_dir.iterdir()) if d.is_dir()]
                if priority:
                    run_dirs.sort(key=lambda d: (d.name != priority, d.name))

                # ...because one iteration of this sweep can spend an hour
                # inside a single 82 GB backlog run before it ever loops back
                # to the live one. So only a bounded number of BACKLOG sub-runs
                # are synced per poll; then we break out, re-read the live run,
                # and come back. The live run itself is never budgeted.
                backlog_budget = backlog_per_poll
                stop_sweep = False

                for run_dir in run_dirs:
                    if stop_sweep:
                        break
                    if not run_dir.is_dir():
                        continue
                    if include_runs is not None and run_dir.name not in include_runs:
                        continue
                    if run_dir.name in exclude_runs:
                        continue

                    # --- Run-level loose files (before the stale skip) ---
                    # run_config.json & co. live directly in the run dir, so the
                    # per-subrun path below never looks at them: _xrd_loose_files
                    # only runs after a SUBRUN syncs, and a subrun only syncs when
                    # its size changes. An edit made after a run ends (e.g. the
                    # bulk uRWELL-orientation run_config.json rewrite) therefore
                    # stayed invisible until the once-a-day reconcile. Comparing a
                    # cheap local signature closes that 24 h window to one poll.
                    # Deliberately ahead of the stale/checked_stale_runs skips —
                    # a bulk config rewrite touches old runs too, and this costs
                    # nothing (local stat only) when nothing has changed.
                    sig = _loose_signature(run_dir)
                    if sig and loose_sigs.get(run_dir.name) != sig:
                        _end_idle()
                        print(f"[backup] loose files changed in {run_dir.name} — syncing")
                        if _xrd_loose_files(run_dir, eos_runs_dir / run_dir.name):
                            loose_sigs[run_dir.name] = sig
                            _save_loose_state(loose_state_path, loose_sigs)
                        else:
                            # Leave the signature stale so the next poll retries.
                            print(f"[backup] loose-file sync FAILED for {run_dir.name}")

                    if run_dir.name in checked_stale_runs:
                        continue

                    is_stale = _run_is_stale(run_dir, stale_run_days)

                    for subrun_dir in sorted(run_dir.iterdir()):
                        if not subrun_dir.is_dir():
                            continue

                        key = (run_dir.name, subrun_dir.name)
                        current_size = _dir_size(subrun_dir)

                        # Stable check: size must match the previous poll
                        if prev_sizes.get(key) != current_size:
                            prev_sizes[key] = current_size
                            continue

                        # Skip if already rsynced at this exact size
                        if synced_sizes.get(key) == current_size:
                            continue

                        _end_idle()
                        mb = current_size // (1024 * 1024)
                        print(f"[backup] {run_dir.name}/{subrun_dir.name}  size={mb}MB")

                        ok = _xrd_sync_tree(subrun_dir, eos_runs_dir / run_dir.name / subrun_dir.name)
                        if ok:
                            _xrd_loose_files(run_dir, eos_runs_dir / run_dir.name)
                            synced_sizes[key] = current_size
                            _save_state(state_path, synced_sizes)
                            found_new = True
                        else:
                            print(f"[backup] sync FAILED for {run_dir.name}/{subrun_dir.name}")

                        # Spend the backlog budget, then hand the loop back to
                        # the live run. Counted on the attempt, not on success,
                        # so a run that fails every time cannot monopolise the
                        # sweep either.
                        if priority and run_dir.name != priority:
                            backlog_budget -= 1
                            if backlog_budget <= 0:
                                _end_idle()
                                print(f"[backup] backlog budget spent — "
                                      f"rechecking {priority} first")
                                stop_sweep = True
                                break

                    if is_stale:
                        checked_stale_runs.add(run_dir.name)
                        _end_idle()
                        print(f"[backup] Marked stale (will skip): {run_dir.name}")

            # --- Periodic full sync for all other subdirs (LOWEST priority) ---
            # These are ancillary trees (config/, dream_config/, merged/,
            # pedestals/) — none of it is run data, and none of it is what the
            # prune loop waits on. Two rules keep them out of the way:
            #
            #   1. They only run when the runs sweep moved nothing this poll.
            #      Run data — live first, then backlog — always goes first.
            #   2. Even then they are capped at extra_sync_files_per_poll. One
            #      xrdcp per file costs 5-10 s of connect + Kerberos whatever
            #      the file size, so pedestals alone (637 files, median 36.6 KB)
            #      blocks a single poll iteration for ~an hour. The cap makes
            #      it resumable — already-copied files are skipped next pass —
            #      so a new sub-run never waits an hour behind a pile of 36 KB
            #      PNGs.
            #
            # Cost of the rules: ancillary dirs lag during heavy acquisition.
            # That is the intended trade — they are the least urgent thing here.
            if (not found_new
                    and now - last_extra_sync >= extra_sync_interval):
                last_extra_sync = now
                budget = extra_files_per_poll
                for subdir in sorted(source_dir.iterdir()):
                    if budget <= 0:
                        break
                    if not subdir.is_dir():
                        continue
                    if subdir.name == runs_subdir:
                        continue
                    if subdir.name in exclude_dirs:
                        continue
                    _end_idle()
                    print(f"[backup] extra sync: {subdir.name}/")
                    ok = _xrd_sync_tree(subdir, eos_dir / subdir.name,
                                        max_files=budget)
                    budget -= _SYNC_COPIED     # the cap is per POLL, not per dir
                    if not ok:
                        print(f"[backup] extra sync FAILED: {subdir.name}/")
                    elif _SYNC_TRUNCATED:
                        budget = 0
                        print(f"[backup] extra sync paused: {subdir.name}/ "
                              f"— yielding to the runs sweep, resumes next poll")
                    else:
                        print(f"[backup] extra sync done: {subdir.name}/")

            # --- Full-reconcile sweep (idle-only backstop) ---
            # Re-verifies EVERY run against EOS and re-copies any missing or
            # size-mismatched file, ignoring the stale-skip and per-subrun size
            # caches. This is what propagates after-the-fact edits (e.g. a bulk
            # run_config.json rewrite) and loose files to old/stale runs, which
            # the fast per-subrun path never revisits. Only runs while idle so it
            # never competes with live data transfer.
            #
            # "Idle" must mean NO DATA IS BEING TAKEN, not merely "this poll
            # synced nothing". This pass is one uninterruptible walk of every
            # run — with a 330 GB backlog that is hours — and it holds off the
            # fast sweep for the whole time, so sub-runs written meanwhile go
            # unbacked and prune_active_run stalls waiting for them. That is
            # the exact starvation the priority ordering exists to prevent, so
            # while the priority run is still receiving data the reconcile is
            # deferred; the fast sweep covers the backlog in the meantime,
            # budgeted, and the reconcile runs once acquisition stops.
            if (not found_new and runs_dir.exists()
                    and now - last_reconcile >= reconcile_interval
                    and not _run_is_receiving(runs_dir, _priority_run(runs_dir, current_run_state))):
                last_reconcile = now
                _end_idle()
                print(f"[backup] full reconcile: verifying all runs against EOS")
                n_runs = n_gap_runs = 0
                # Same priority rule as the fast sweep: with a backlog in play
                # this pass can run for hours, so the live run is verified and
                # closed out first rather than last.
                _prio = _priority_run(runs_dir, current_run_state)
                _rdirs = [d for d in sorted(runs_dir.iterdir()) if d.is_dir()]
                if _prio:
                    _rdirs.sort(key=lambda d: (d.name != _prio, d.name))
                for run_dir in _rdirs:
                    if not run_dir.is_dir():
                        continue
                    if include_runs is not None and run_dir.name not in include_runs:
                        continue
                    if run_dir.name in exclude_runs:
                        continue
                    n_runs += 1
                    # Recursive sync of the whole run: subruns + loose files.
                    if not _xrd_sync_tree(run_dir, eos_runs_dir / run_dir.name):
                        n_gap_runs += 1
                        print(f"[backup] reconcile: sync gaps remain in {run_dir.name}")
                print(f"[backup] full reconcile done: {n_runs} runs checked, "
                      f"{n_gap_runs} with unresolved gaps")

        if found_new:
            idle_ticks = 0
        else:
            idle_ticks += 1
            elapsed = idle_ticks * poll_interval
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            sp = _SPINNER[idle_ticks % 4]
            if not source_dir.exists():
                msg = f'[backup] {sp} waiting for source_dir  #{idle_ticks}  {ts}'
            elif not kerberos_ok:
                msg = f'[backup] {sp} AUTH ERROR — Kerberos not valid  {ts}'
            else:
                msg = f'[backup] {sp} idle  #{idle_ticks}  {elapsed}s  {ts}'
            sys.stdout.write(f'\r{msg}          ')
            sys.stdout.flush()
            idle_line = True

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Kerberos
# ---------------------------------------------------------------------------

def _refresh_kerberos(principal: str, gpg_pass_file: Path) -> tuple:
    """Try kinit -R first; fall back to GPG-decrypted password re-kinit."""
    result = subprocess.run(['kinit', '-R'], capture_output=True)
    if result.returncode == 0:
        return True, 'renewed'

    if not gpg_pass_file.exists():
        return False, f'GPG password file not found: {gpg_pass_file}'

    # Decrypt via gpg-agent — prompts for GPG passphrase via pinentry once per
    # boot; subsequent calls use the cached passphrase from the agent.
    gpg = subprocess.run(
        ['gpg', '--batch', '--yes', '--decrypt', str(gpg_pass_file)],
        capture_output=True,
    )
    if gpg.returncode != 0:
        stderr = gpg.stderr.decode(errors='replace').strip()
        return False, f'gpg decrypt failed (passphrase not cached?): {stderr}'

    kinit = subprocess.run(
        ['kinit', principal],
        input=gpg.stdout,
        capture_output=True,
    )
    if kinit.returncode == 0:
        return True, 'full kinit'
    stderr = kinit.stderr.decode(errors='replace').strip()
    return False, f'kinit failed: {stderr}'


# ---------------------------------------------------------------------------
# XRootD transfer helpers
#
# The EOS FUSE mount (legacy xrootdfs) cannot mkdir/rename/overwrite, so
# rsync-over-FUSE fails for every new directory.  The native xrootd protocol
# has no such limitation, so all transfers go through xrdcp/xrdfs instead.
# ---------------------------------------------------------------------------

_XROOTD_URL  = None   # e.g. 'root://eospublic.cern.ch' — set by run_watcher()
_XRDCP_EXTRA = []     # extra xrdcp args from config — set by run_watcher()

# Outcome of the last _xrd_sync_tree() call, for callers that budget their work:
# whether it stopped on max_files rather than finishing, and how many it copied.
_SYNC_TRUNCATED = False
_SYNC_COPIED    = 0

# Sources per xrdcp invocation. 40 measured 11.3 s against eospublic vs ~360 s
# for the same files one at a time. Kept well below the ~2 MB argv limit (40
# paths is a few kB) and small enough that a batch failure re-tries a modest
# number of files individually.
_XRDCP_BATCH = 40


def _xrd_url(eos_path: Path) -> str:
    """Native xrootd URL for an absolute EOS path: root://host//eos/..."""
    return f"{_XROOTD_URL}//{str(eos_path).lstrip('/')}"


def _remote_size_map(eos_dir: Path, recursive: bool = True) -> dict:
    """{relative_path: size} for files under eos_dir on EOS.

    recursive=True walks the whole tree (relpath keys); recursive=False lists only
    the immediate directory (bare-filename keys) — used for the cheap loose-file check.
    Empty dict if the directory does not exist yet (so all files get copied).
    Parses `xrdfs <url> ls -l [-R]` lines: '<flags> <owner> <group> <size> <date> <time> <path>'.
    """
    ls_args = ['ls', '-l', '-R', str(eos_dir)] if recursive else ['ls', '-l', str(eos_dir)]
    result = subprocess.run(
        ['xrdfs', _XROOTD_URL, *ls_args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    base = str(eos_dir).rstrip('/') + '/'
    sizes: dict = {}
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


def _xrdcp_file(local: Path, eos_path: Path) -> bool:
    """Copy one local file to EOS via native xrdcp (-f overwrite, -p make dirs)."""
    result = subprocess.run(
        ['xrdcp', '-f', '-p', '--nopbar', *_XRDCP_EXTRA, str(local), _xrd_url(eos_path)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True
    print(f"[backup] xrdcp FAILED (exit {result.returncode}) {local.name}: "
          f"{result.stderr.strip()[:200]}")
    return False


def _xrdcp_batch(locals_: list, eos_dir: Path) -> int:
    """Copy several local files into ONE EOS directory with a single xrdcp.
    Returns how many landed.

    xrdcp takes multiple sources when the destination is a directory URL, and
    the expensive part of a transfer here is per-INVOCATION, not per-byte: a
    fresh connect + Kerberos handshake against eospublic costs 5-10 s whatever
    the file size. Measured on this link:
        1 small file  -> 5-10 s        40 small files, 1 invocation -> 11.3 s
    i.e. ~32x on directories of small files (pedestals: 637 files, median
    36.6 KB, previously ~60 min). `--parallel 4` measured 10.35 s for the same
    40 — inside the noise, so it is not worth the extra moving part.

    A failed batch falls back to copying its files one by one, so a single bad
    file cannot silently take its 49 healthy neighbours down with it, and the
    per-file error reporting is preserved.
    """
    if not locals_:
        return 0
    dest = _xrd_url(eos_dir).rstrip('/') + '/'    # trailing / => "this is a directory"
    result = subprocess.run(
        ['xrdcp', '-f', '-p', '--nopbar', *_XRDCP_EXTRA,
         *[str(f) for f in locals_], dest],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return len(locals_)
    print(f"[backup] xrdcp batch FAILED (exit {result.returncode}) for {eos_dir} — "
          f"retrying individually: {result.stderr.strip()[:160]}")
    return sum(1 for f in locals_ if _xrdcp_file(f, eos_dir / f.name))


def _xrd_sync_tree(local_dir: Path, eos_dir: Path, max_files: int = None) -> bool:
    """Copy every file under local_dir into eos_dir on EOS, skipping files already
    there at the same size (data is write-once). Returns True if nothing failed.

    Incomplete trees self-heal: absent files copy, size-matched files skip, and a
    partial file (size mismatch) is re-copied — native xrdcp -f can overwrite it.

    max_files caps how many files one call will copy. Every xrdcp invocation
    pays a fresh connect + Kerberos handshake — measured 5-10 s against
    eospublic, regardless of file size — so a directory of small files costs
    minutes-to-hours of wall clock with almost no bytes moving (pedestals:
    637 files, median 36.6 KB, 2.2 GB total, ~60 min). Left uncapped that runs
    inside ONE poll iteration and the loop cannot return to the runs sweep
    meanwhile. Capping makes the pass resumable: the skip-existing check above
    means the next call simply carries on where this one stopped.

    Sets _SYNC_TRUNCATED when it stopped on the cap rather than finishing, so
    the caller can tell "done" from "more to do".
    """
    global _SYNC_TRUNCATED, _SYNC_COPIED
    _SYNC_TRUNCATED = False
    _SYNC_COPIED = 0
    remote_sizes = _remote_size_map(eos_dir)
    all_ok, copied, skipped = True, 0, 0

    # Decide what to send first, then send it grouped by destination directory:
    # one xrdcp invocation can take many sources but only ONE destination dir.
    pending = {}          # {relative parent dir: [local Path, ...]}
    n_pending = 0
    for f in sorted(local_dir.rglob('*')):
        if not f.is_file():
            continue
        rel = f.relative_to(local_dir).as_posix()
        try:
            local_size = f.stat().st_size
        except OSError:
            continue
        if remote_sizes.get(rel) == local_size:
            skipped += 1
            continue
        if max_files is not None and n_pending >= max_files:
            _SYNC_TRUNCATED = True
            break
        pending.setdefault(PurePosixPath(rel).parent.as_posix(), []).append(f)
        n_pending += 1

    for sub, files in sorted(pending.items()):
        dest_dir = eos_dir if sub == '.' else eos_dir / sub
        for i in range(0, len(files), _XRDCP_BATCH):
            chunk = files[i:i + _XRDCP_BATCH]
            n = _xrdcp_batch(chunk, dest_dir)
            copied += n
            if n < len(chunk):
                all_ok = False
    _SYNC_COPIED = copied
    if copied:
        tail = ' (capped, will resume)' if _SYNC_TRUNCATED else ''
        print(f"[backup] xrdcp -> {eos_dir}: {copied} new, {skipped} already there{tail}")
    return all_ok


def _xrd_loose_files(run_dir: Path, eos_run_dir: Path) -> bool:
    """Copy every loose file sitting directly in run_dir (not in a subrun subdir):
    dream_daq.log, run_config.json and its backups, notes, etc.

    Size-checked against EOS so unchanged files are skipped; changed files (e.g. an
    edited run_config.json) are re-copied since xrdcp -f overwrites. The per-subrun
    _xrd_sync_tree only walks subrun subdirectories, so these top-level files would
    otherwise never be backed up.

    Returns True if nothing failed, so the caller can avoid caching a signature
    for a sync that did not actually land.
    """
    remote_sizes = _remote_size_map(eos_run_dir, recursive=False)
    all_ok = True
    for f in sorted(run_dir.iterdir()):
        if not f.is_file():
            continue
        try:
            local_size = f.stat().st_size
        except OSError:
            continue
        if remote_sizes.get(f.name) == local_size:
            continue
        if not _xrdcp_file(f, eos_run_dir / f.name):
            all_ok = False
    return all_ok


def _loose_signature(run_dir: Path) -> str:
    """name:size for every loose file directly in run_dir, as one string.

    Pure local stat of a handful of files — cheap enough to evaluate for every
    run on every poll. Comparing it against the last synced signature is what
    lets the watcher notice run-level edits (a bulk run_config.json rewrite,
    added notes) without waiting for a subrun to change size or for the daily
    reconcile.
    """
    parts = []
    try:
        for f in sorted(run_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                parts.append(f'{f.name}:{f.stat().st_size}')
            except OSError:
                pass
    except OSError:
        return ''
    return '|'.join(parts)


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def _dir_size(path: Path) -> int:
    total = 0
    try:
        for f in path.rglob('*'):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _priority_run(runs_dir: Path, state_path: Path):
    """Name of the run that must be backed up before any backlog, or None.

    Read from the GUI's current_run_state.json, which is a LAST-SEEN tracker —
    it is not cleared when a run ends. That is fine here and deliberately not
    gated on the DAQ actually running: prioritising a run that just finished
    costs nothing (it has no new sub-runs, so the sweep falls straight through
    to the backlog), whereas failing to prioritise a live one is what caused
    the 2026-07-27 near-miss. Falls back to the most recently modified run dir
    so the priority rule still holds if the state file is missing or stale.
    """
    try:
        with open(state_path) as f:
            name = (json.load(f).get('run_name') or '').strip()
        if name and (runs_dir / name).is_dir():
            return name
    except Exception:
        pass
    try:
        dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        return max(dirs, key=lambda d: d.stat().st_mtime).name if dirs else None
    except OSError:
        return None


def _run_is_receiving(runs_dir: Path, run_name, window_s: float = 900) -> bool:
    """True if `run_name` has had a file written in the last window_s — i.e.
    data is still coming in. Used to keep the long full-reconcile pass out of
    the way of an active run. Deliberately mtime-based rather than asking the
    DAQ: this watcher is standalone by design, and a stale-by-15-minutes answer
    only costs one deferred reconcile cycle.
    """
    if not run_name:
        return False
    run_dir = runs_dir / run_name
    if not run_dir.is_dir():
        return False
    cutoff = time.time() - window_s
    try:
        for f in run_dir.rglob('*'):
            try:
                if f.is_file() and f.stat().st_mtime >= cutoff:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _run_is_stale(run_dir: Path, stale_days: float) -> bool:
    cutoff = time.time() - stale_days * 86400
    newest = 0.0
    found_any = False
    for subrun in run_dir.iterdir():
        if not subrun.is_dir():
            continue
        found_any = True
        mtime = subrun.stat().st_mtime
        if mtime > newest:
            newest = mtime
    if not found_any:
        return False
    return newest < cutoff


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            raw = json.load(f)
        return {tuple(k.split('/', 1)): v for k, v in raw.items()}
    except Exception as e:
        print(f"[backup] Could not load state from {state_path}: {e}")
        return {}


def _load_loose_state(path: Path) -> dict:
    """{run_name: loose-file signature} from the last successful loose sync.

    Kept in its own file rather than backup_state.json because that one is
    keyed 'run/subrun' and _load_state() splits every key on '/'.
    """
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_loose_state(path: Path, sigs: dict):
    try:
        with open(path, 'w') as f:
            json.dump(sigs, f, indent=2)
    except Exception as e:
        print(f"[backup] Could not save loose state to {path}: {e}")


def _save_state(state_path: Path, synced_sizes: dict):
    try:
        raw = {f"{k[0]}/{k[1]}": v for k, v in synced_sizes.items()}
        with open(state_path, 'w') as f:
            json.dump(raw, f, indent=2)
    except Exception as e:
        print(f"[backup] Could not save state to {state_path}: {e}")


if __name__ == '__main__':
    main()
