#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous on-the-fly QA watcher for nTof DREAM DAQ data.

Watches all runs under a top-level runs directory and runs the QA analysis
script whenever new combined_hits files appear.  Runs independently of
daq_control.py and processor_watcher.py; start/stop from the flask UI.

Usage:
    python qa_watcher.py <qa_config_json_path>

Config keys (see qa_config.py to generate the JSON):
  runs_dir                : top-level directory containing run_N/ subdirs
  analysis_dir            : path to the repository holding the QA script
                            ('ntof_x17_dir' still accepted for backward compat)
  qa_script_rel_path      : QA entry script, relative to analysis_dir
                            (default: 'ntof_daq_analysis/detector_qa.py')
  qa_python_rel_path      : python interpreter, relative to analysis_dir
                            (default: '.venv/bin/python')
  combined_hits_inner_dir : subdir for combined hits files  (default: 'combined_hits_root')
  qa_file_mode            : 'all' | 'first' | 'per_file'   (default: 'all')
                              all      — rerun QA on all accumulated files whenever a new one appears
                              first    — run QA once per subrun using only file_num=0
                              per_file — independent QA output per file_num
  include_runs            : list of run directory names to process exclusively (null = all)
  exclude_runs            : list of run directory names to skip (null = none)
  poll_interval           : seconds between scans   (default: 10)
  stale_run_days          : runs with no new combined_hits for this many days are skipped (default: 4)
  memory_kill_pct         : kill the QA process if system RAM usage exceeds this % (default: 80)
                              The QA is always launched; memory is monitored during the run and
                              the process is terminated if the system crosses the threshold.
                              A killed subrun is NOT marked done and will be retried next poll.
  cpu_nice                : nice level for the QA subprocess (default: 19, lowest priority).
                              Also runs the process at ionice idle class so DAQ I/O wins.
                              null disables both niceing and ionice.
  cpu_affinity            : list of CPU core ids to pin the QA subprocess to via taskset
                              (default: null = all cores).  e.g. [2, 3, 4, 5] reserves cores
                              0-1 for the DAQ on a 6-core box.
  qa_threads              : cap numpy/BLAS/uproot thread pools to this many threads
                              (default: null = derived from len(cpu_affinity), else unlimited).
  qa_timeout_s            : kill the QA process if it runs longer than this (default: 1800).
                              Without a timeout a single wedged subrun blocks the whole
                              watcher indefinitely — the loop is serial, so nothing else
                              gets processed. Observed 2026-07-24: drift_scan_2/drift_850
                              sat in the waveform mean/RMS step for 15.5 h and only stopped
                              because the watcher was restarted the next morning.
  qa_max_attempts         : give up on a subrun after this many failed attempts (default: 2).
                              A killed subrun is retried, but without a cap a subrun that
                              always fails is retried forever and starves everything else.
                              Only timeouts and non-zero exits count towards this. Memory
                              kills do NOT: system RAM is usually pushed over the threshold
                              by some other process on the box, so giving up on those would
                              silently drop QA for a subrun that was never at fault.
  wf_mean_rms             : per-strip waveform mean/RMS maps (default: false).
                              By far the most expensive step in the QA — leave off unless
                              you specifically need it.
  wf_event_plots          : per-event waveform figures on beam runs (default: false).
  wf_step_size            : events per batch for the mean/RMS reduction (default: 50000).

State:
  Progress for every mode is persisted to config/qa_state.json so a restart does not
  reprocess subruns that already completed.
"""

import os
import re
import sys
import json
import time
import datetime
import subprocess
from pathlib import Path

_LOG_FILE = Path(__file__).parent / 'logs' / 'qa_watcher.log'


def _log(event: str, **details):
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts         = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={v}' for k, v in details.items())
        line       = f"{ts} | {event:<16} | qa_watcher   | {detail_str}\n"
        with open(_LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"[qa_watcher] Warning: could not write to log: {e}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python qa_watcher.py <qa_config_json_path>")
        sys.exit(1)

    config_path = Path(sys.argv[1])
    with open(config_path) as f:
        config = json.load(f)

    reset_signal_path = config_path.parent / 'qa_reset.json'
    run_watcher(config, reset_signal_path)


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher(config: dict, reset_signal_path: Path = None):
    runs_dir       = Path(config['runs_dir'])
    # 'analysis_dir' is the new generic key; 'ntof_x17_dir' kept for backward compat.
    analysis_dir   = Path(config.get('analysis_dir') or config['ntof_x17_dir'])
    combined_inner = config.get('combined_hits_inner_dir', 'combined_hits_root')
    mode           = config.get('qa_file_mode', 'all')

    include_runs = set(config['include_runs']) if config.get('include_runs') else None
    exclude_runs = set(config['exclude_runs']) if config.get('exclude_runs') else set()

    poll_interval   = config.get('poll_interval',   10)
    # A combined_hits file is only considered ready once it has been untouched for
    # this long — see _stable_combined_files(). Must stay above poll_interval.
    settle_s        = config.get('combined_settle_s', 20)
    stale_run_days  = config.get('stale_run_days',    4)
    memory_kill_pct = config.get('memory_kill_pct',  80)
    cpu_nice        = config.get('cpu_nice',         19)
    cpu_affinity    = config.get('cpu_affinity')  # list[int] or None
    qa_threads      = config.get('qa_threads')    # int or None
    if qa_threads is None and cpu_affinity:
        qa_threads = len(cpu_affinity)

    qa_timeout_s    = config.get('qa_timeout_s',    1800)
    qa_max_attempts = config.get('qa_max_attempts',    2)
    wf_mean_rms     = config.get('wf_mean_rms',    False)
    wf_event_plots  = config.get('wf_event_plots', False)
    wf_step_size    = config.get('wf_step_size',   50000)

    # QA entry point and interpreter, relative to analysis_dir (defaults = nTof layout).
    qa_script  = analysis_dir / config.get('qa_script_rel_path', 'ntof_daq_analysis/detector_qa.py')
    qa_python  = analysis_dir / config.get('qa_python_rel_path', '.venv/bin/python')

    print(f"[qa_watcher] runs_dir        : {runs_dir}")
    print(f"[qa_watcher] qa_script       : {qa_script}")
    print(f"[qa_watcher] python          : {qa_python}")
    print(f"[qa_watcher] mode            : {mode}")
    if include_runs:
        print(f"[qa_watcher] include_runs    : {sorted(include_runs)}")
    if exclude_runs:
        print(f"[qa_watcher] exclude_runs    : {sorted(exclude_runs)}")
    print(f"[qa_watcher] poll            : {poll_interval}s  stale_after={stale_run_days}d")
    print(f"[qa_watcher] memory_kill_pct : {memory_kill_pct}%")
    print(f"[qa_watcher] cpu_nice        : {cpu_nice}")
    print(f"[qa_watcher] cpu_affinity    : {cpu_affinity if cpu_affinity else 'all cores'}")
    print(f"[qa_watcher] qa_threads      : {qa_threads if qa_threads else 'unlimited'}")
    print(f"[qa_watcher] qa_timeout      : {f'{qa_timeout_s}s' if qa_timeout_s else 'none'}"
          f"  max_attempts={qa_max_attempts}")
    print(f"[qa_watcher] wf_mean_rms     : {wf_mean_rms}"
          f"{f'  (step_size={wf_step_size:,})' if wf_mean_rms else ''}")
    print(f"[qa_watcher] wf_event_plots  : {wf_event_plots}")
    _log('START', runs_dir=runs_dir, mode=mode, memory_kill_pct=f'{memory_kill_pct}%',
         cpu_nice=cpu_nice, cpu_affinity=cpu_affinity, qa_threads=qa_threads,
         qa_timeout_s=qa_timeout_s, wf_mean_rms=wf_mean_rms,
         wf_event_plots=wf_event_plots, wf_step_size=wf_step_size)

    state_path = reset_signal_path.parent / 'qa_state.json' if reset_signal_path else None

    checked_stale_runs: set = set()
    idle_ticks = 0
    idle_line = False
    _SPINNER = ['-', '\\', '|', '/']

    def _end_idle():
        nonlocal idle_line
        if idle_line:
            sys.stdout.write('\n')
            sys.stdout.flush()
            idle_line = False

    # Per-mode tracking state, keyed by (run_name, subrun_name).  All of it is
    # persisted: 'first'/'per_file' progress used to be in-memory only, so every
    # restart reprocessed subruns that had already completed.
    seen_files, done_first, done_fnums, attempts = _load_state(state_path)

    def _record(akey: str, status: str, mark_done, **logdetail):
        """Post-QA bookkeeping shared by all three modes.

        status is what _run_qa_monitored returned:
          'ok'      — mark the unit done and clear its failure count.
          'memory'  — killed because *system* RAM crossed the threshold, which is
                      usually another process's fault (concurrent analysis jobs on
                      this box routinely take several GB). Retried indefinitely and
                      deliberately NOT counted against qa_max_attempts: giving up
                      here would silently drop QA for a perfectly good subrun once
                      something unrelated spiked memory twice.
          'timeout' / 'error'
                    — the QA itself wedged or failed. Counted, and once
                      qa_max_attempts is reached the unit is marked done anyway so
                      it cannot block every other run forever.
        """
        if status == 'ok':
            attempts.pop(akey, None)
            mark_done()
            _log('QA_DONE', **logdetail)
        elif status != 'memory':
            n = attempts.get(akey, 0) + 1
            attempts[akey] = n
            if n >= qa_max_attempts:
                mark_done()
                _end_idle()
                print(f"[qa_watcher] Giving up on {akey} after {n} failed attempts"
                      f" ({status}) — skipping")
                _log('QA_GIVEUP', attempts=n, reason=status, **logdetail)
        _save_state(state_path, seen_files, done_first, done_fnums, attempts)

    while True:
        found_new = False

        if reset_signal_path:
            reset = _pop_reset_signal(reset_signal_path)
            if reset is not False:
                if reset is None:
                    seen_files.clear()
                    done_first.clear()
                    done_fnums.clear()
                    attempts.clear()
                    checked_stale_runs.clear()
                    _save_state(state_path, seen_files, done_first, done_fnums, attempts)
                    _end_idle()
                    print("[qa_watcher] Reset: all runs will be reprocessed")
                else:
                    for key in list(seen_files):
                        if key[0] in reset: del seen_files[key]
                    done_first -= {k for k in done_first if k[0] in reset}
                    for key in list(done_fnums):
                        if key[0] in reset: del done_fnums[key]
                    # attempts keys are 'run/subrun' or 'run/subrun#file_num';
                    # clear the counts too so a reset run gets its full retries back.
                    for akey in [k for k in attempts if k.split('/', 1)[0] in reset]:
                        del attempts[akey]
                    checked_stale_runs -= reset
                    _save_state(state_path, seen_files, done_first, done_fnums, attempts)
                    _end_idle()
                    print(f"[qa_watcher] Reset: {sorted(reset)} will be reprocessed")

        if not runs_dir.exists():
            pass
        else:
            for run_dir in sorted(runs_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                if include_runs is not None and run_dir.name not in include_runs:
                    continue
                if run_dir.name in exclude_runs:
                    continue
                if run_dir.name in checked_stale_runs:
                    continue

                run_config_path = run_dir / 'run_config.json'
                if not run_config_path.exists():
                    continue

                is_stale = _run_is_stale(run_dir, combined_inner, stale_run_days)

                for subrun_dir in sorted(run_dir.iterdir()):
                    if not subrun_dir.is_dir():
                        continue

                    combined_dir = subrun_dir / combined_inner
                    if not combined_dir.exists():
                        continue

                    stable = _stable_combined_files(combined_dir, settle_s)
                    if not stable:
                        continue

                    key = (run_dir.name, subrun_dir.name)

                    if mode == 'all':
                        current = frozenset(stable)
                        if current != seen_files.get(key):
                            _end_idle()
                            mem_pct, free_mb = _mem_usage_pct()
                            print(f"[qa_watcher] {run_dir.name}/{subrun_dir.name}"
                                  f"  n_files={len(stable)}  mem={mem_pct:.1f}%  free={free_mb:.0f}MB")
                            _log('QA_LAUNCH', run=run_dir.name, subrun=subrun_dir.name,
                                 n_files=len(stable), mem_pct=f'{mem_pct:.1f}%', free_mb=f'{free_mb:.0f}')
                            qa_status = _run_qa_monitored(
                                qa_python, qa_script, subrun_dir, run_config_path,
                                'all', memory_kill_pct=memory_kill_pct,
                                cpu_nice=cpu_nice, cpu_affinity=cpu_affinity,
                                qa_threads=qa_threads, timeout_s=qa_timeout_s,
                                wf_mean_rms=wf_mean_rms, wf_event_plots=wf_event_plots,
                                wf_step_size=wf_step_size)
                            _record(f'{key[0]}/{key[1]}', qa_status,
                                    lambda: seen_files.__setitem__(key, current),
                                    run=run_dir.name, subrun=subrun_dir.name)
                            found_new = True

                    elif mode == 'first':
                        if key not in done_first:
                            if any(_file_num(f) == 0 for f in stable):
                                _end_idle()
                                mem_pct, free_mb = _mem_usage_pct()
                                print(f"[qa_watcher] {run_dir.name}/{subrun_dir.name}"
                                      f"  file_num=0  mem={mem_pct:.1f}%  free={free_mb:.0f}MB")
                                _log('QA_LAUNCH', run=run_dir.name, subrun=subrun_dir.name,
                                     file_num=0, mem_pct=f'{mem_pct:.1f}%', free_mb=f'{free_mb:.0f}')
                                qa_status = _run_qa_monitored(
                                    qa_python, qa_script, subrun_dir, run_config_path,
                                    'first', memory_kill_pct=memory_kill_pct,
                                    cpu_nice=cpu_nice, cpu_affinity=cpu_affinity,
                                    qa_threads=qa_threads, timeout_s=qa_timeout_s,
                                    wf_mean_rms=wf_mean_rms, wf_event_plots=wf_event_plots,
                                    wf_step_size=wf_step_size)
                                _record(f'{key[0]}/{key[1]}', qa_status,
                                        lambda: done_first.add(key),
                                        run=run_dir.name, subrun=subrun_dir.name)
                                found_new = True

                    elif mode == 'per_file':
                        completed = done_fnums.get(key, set())
                        new_fnums = {_file_num(f) for f in stable} - {None} - completed
                        for fnum in sorted(new_fnums):
                            _end_idle()
                            mem_pct, free_mb = _mem_usage_pct()
                            print(f"[qa_watcher] {run_dir.name}/{subrun_dir.name}"
                                  f"  file_num={fnum:03d}  mem={mem_pct:.1f}%  free={free_mb:.0f}MB")
                            _log('QA_LAUNCH', run=run_dir.name, subrun=subrun_dir.name,
                                 file_num=fnum, mem_pct=f'{mem_pct:.1f}%', free_mb=f'{free_mb:.0f}')
                            qa_status = _run_qa_monitored(
                                qa_python, qa_script, subrun_dir, run_config_path,
                                'per_file', file_num=fnum, memory_kill_pct=memory_kill_pct,
                                cpu_nice=cpu_nice, cpu_affinity=cpu_affinity,
                                qa_threads=qa_threads, timeout_s=qa_timeout_s,
                                wf_mean_rms=wf_mean_rms, wf_event_plots=wf_event_plots,
                                wf_step_size=wf_step_size)
                            done_fnums[key] = completed  # so _record's _save_state sees it
                            _record(f'{key[0]}/{key[1]}#{fnum}', qa_status,
                                    lambda f=fnum: completed.add(f),
                                    run=run_dir.name, subrun=subrun_dir.name, file_num=fnum)
                            found_new = True
                        done_fnums[key] = completed

                if is_stale:
                    checked_stale_runs.add(run_dir.name)
                    _end_idle()
                    print(f"[qa_watcher] Marked stale (will skip): {run_dir.name}")

        if found_new:
            idle_ticks = 0
        else:
            idle_ticks += 1
            elapsed = idle_ticks * poll_interval
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            sp = _SPINNER[idle_ticks % 4]
            if not runs_dir.exists():
                msg = f'[qa_watcher] {sp} waiting for runs_dir  #{idle_ticks}  {ts}'
            else:
                msg = f'[qa_watcher] {sp} idle  #{idle_ticks}  {elapsed}s  {ts}'
            sys.stdout.write(f'\r{msg}          ')
            sys.stdout.flush()
            idle_line = True
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATE_VERSION = 2


def _state_key(s: str) -> tuple:
    """'run/subrun' -> ('run', 'subrun'); tolerates a missing subrun part."""
    parts = s.split('/', 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], '')


def _load_state(state_path: Path) -> tuple:
    """
    Load watcher progress as (seen_files, done_first, done_fnums, attempts).

      seen_files : {(run, subrun): frozenset(filenames)}  — 'all' mode
      done_first : {(run, subrun)}                        — 'first' mode
      done_fnums : {(run, subrun): set(file_nums)}        — 'per_file' mode
      attempts   : {'run/subrun[#file_num]': n_failures}

    A version-less file is the legacy layout, where the whole document was the
    'all'-mode seen_files map; it is upgraded in place on the next save rather
    than discarded.
    """
    empty = ({}, set(), {}, {})
    if state_path is None or not state_path.exists():
        return empty
    try:
        with open(state_path) as f:
            raw = json.load(f)
    except Exception as e:
        print(f"[qa_watcher] Could not load state from {state_path}: {e}")
        return empty

    try:
        if not isinstance(raw, dict):
            raise ValueError('state file is not a JSON object')
        if 'version' not in raw:
            return ({_state_key(k): frozenset(v) for k, v in raw.items()}, set(), {}, {})
        return (
            {_state_key(k): frozenset(v) for k, v in (raw.get('seen_files') or {}).items()},
            {_state_key(k) for k in (raw.get('done_first') or [])},
            {_state_key(k): set(v) for k, v in (raw.get('done_fnums') or {}).items()},
            dict(raw.get('attempts') or {}),
        )
    except Exception as e:
        print(f"[qa_watcher] Malformed state in {state_path} ({e}) — starting fresh")
        return empty


def _save_state(state_path: Path, seen_files: dict, done_first: set,
                done_fnums: dict, attempts: dict):
    """Persist progress for every mode so a restart resumes instead of redoing work."""
    if state_path is None:
        return
    try:
        payload = {
            'version':    _STATE_VERSION,
            'seen_files': {f'{k[0]}/{k[1]}': sorted(v) for k, v in seen_files.items()},
            'done_first': sorted(f'{k[0]}/{k[1]}' for k in done_first),
            'done_fnums': {f'{k[0]}/{k[1]}': sorted(v) for k, v in done_fnums.items()},
            'attempts':   dict(attempts),
        }
        # Write-then-rename: a crash mid-write leaves the previous state intact
        # instead of a truncated file that would be discarded as malformed.
        tmp = state_path.with_name(state_path.name + '.part')
        with open(tmp, 'w') as f:
            json.dump(payload, f, indent=2)
        tmp.replace(state_path)
    except Exception as e:
        print(f"[qa_watcher] Could not save state to {state_path}: {e}")


def _pop_reset_signal(signal_path: Path):
    """
    Check for a reset signal file.
    Returns False  — no file present (no reset needed).
    Returns None   — reset all runs.
    Returns set    — reset only the named runs.
    """
    if not signal_path.exists():
        return False
    try:
        with open(signal_path) as f:
            data = json.load(f)
        signal_path.unlink()
        runs = data.get('runs')
        return set(runs) if runs else None
    except Exception as e:
        print(f"[qa_watcher] Error reading reset signal: {e}")
        try:
            signal_path.unlink()
        except OSError:
            pass
        return False


def _read_meminfo() -> tuple:
    """
    Read /proc/meminfo and return (mem_total_kb, mem_available_kb).
    Returns (0, 0) on error.
    """
    total, avail = 0, 0
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    total = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    avail = int(line.split()[1])
                if total and avail:
                    break
    except Exception:
        pass
    return total, avail


def _mem_usage_pct() -> tuple:
    """
    Return (used_pct, free_mb).
    used_pct = percentage of total RAM that is in use (0-100).
    Returns (0.0, inf) if /proc/meminfo is unreadable.
    """
    total, avail = _read_meminfo()
    if total == 0:
        return 0.0, float('inf')
    used_pct = (total - avail) / total * 100
    free_mb  = avail / 1024
    return used_pct, free_mb


def _build_qa_command(cmd: list, cpu_nice, cpu_affinity) -> list:
    """
    Wrap the QA command with taskset (CPU affinity) + nice/ionice (priority) so
    the QA never starves the DAQ.  Each wrapper execs the next, so the final PID
    is still the python process (signals from _run_qa_monitored reach it).
    """
    wrapped = list(cmd)
    if cpu_affinity:
        cores   = ','.join(str(int(c)) for c in cpu_affinity)
        wrapped = ['taskset', '-c', cores] + wrapped
    if cpu_nice is not None:
        wrapped = ['nice', '-n', str(int(cpu_nice)), 'ionice', '-c', '3'] + wrapped
    return wrapped


def _thread_limited_env(qa_threads) -> dict:
    """Copy os.environ and cap numpy/BLAS/uproot thread pools if qa_threads is set."""
    env = os.environ.copy()
    if qa_threads:
        for var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                    'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
            env[var] = str(int(qa_threads))
    return env


def _kill_qa(proc, run_label: str):
    """SIGTERM the QA process, escalating to SIGKILL if it ignores it."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print(f"[qa_watcher] QA process killed ({run_label}) — will retry next poll")


def _run_qa_monitored(qa_python, qa_script: Path, subrun_dir: Path,
                       run_config_path: Path, mode: str, file_num: int = None,
                       memory_kill_pct: float = 80, monitor_interval: float = 1.0,
                       cpu_nice=19, cpu_affinity=None, qa_threads=None,
                       timeout_s: float = 1800, wf_mean_rms: bool = False,
                       wf_event_plots: bool = False, wf_step_size: int = None) -> str:
    """
    Launch detector_qa.py as a subprocess and monitor it while it runs.

    Polls every monitor_interval seconds (default 1 s) and kills the process
    (SIGTERM, then SIGKILL after 5 s) if either:
      - system RAM usage crosses memory_kill_pct (default 80%), or
      - the process has run longer than timeout_s (default 1800 s).

    Returns one of:
      'ok'      — exited 0 on its own
      'memory'  — killed on the system memory threshold
      'timeout' — killed for exceeding timeout_s
      'error'   — exited non-zero
    The caller distinguishes these because they deserve different retry policies
    (see _record in run_watcher): a memory kill is usually caused by an unrelated
    process and must stay retryable, while a timeout means the QA itself wedged.

    The timeout is what keeps a single pathological subrun from stalling the whole
    watcher: the run loop is serial, so before it existed a wedged QA process
    blocked every other run for as long as it hung.

    cpu_nice / cpu_affinity / qa_threads throttle CPU use so the QA yields to the
    DAQ (see _build_qa_command and _thread_limited_env).
    """
    cmd = [str(qa_python), str(qa_script),
           '--subrun_dir', str(subrun_dir),
           '--run_config', str(run_config_path),
           '--mode', mode]
    if file_num is not None:
        cmd += ['--file_num', str(file_num)]
    cmd.append('--wf-mean-rms'    if wf_mean_rms    else '--no-wf-mean-rms')
    cmd.append('--wf-event-plots' if wf_event_plots else '--no-wf-event-plots')
    if wf_step_size:
        cmd += ['--wf-step-size', str(int(wf_step_size))]

    cmd = _build_qa_command(cmd, cpu_nice, cpu_affinity)
    env = _thread_limited_env(qa_threads)

    run_label = f"{subrun_dir.parent.name}/{subrun_dir.name}"
    proc      = subprocess.Popen(cmd, env=env)
    started   = time.time()

    while proc.poll() is None:
        time.sleep(monitor_interval)

        elapsed = time.time() - started
        if timeout_s and elapsed >= timeout_s:
            print(f"\n[qa_watcher] QA exceeded {timeout_s}s ({elapsed:.0f}s elapsed)"
                  f" — killing QA process ({run_label})")
            _log('QA_TIMEOUT', run=subrun_dir.parent.name, subrun=subrun_dir.name,
                 elapsed_s=f'{elapsed:.0f}', timeout_s=timeout_s)
            _kill_qa(proc, run_label)
            return 'timeout'

        mem_pct, free_mb = _mem_usage_pct()
        if mem_pct >= memory_kill_pct:
            print(f"\n[qa_watcher] Memory {mem_pct:.1f}% >= {memory_kill_pct}%"
                  f" — killing QA process ({run_label})")
            _log('QA_KILLED', run=subrun_dir.parent.name, subrun=subrun_dir.name,
                 mem_pct=f'{mem_pct:.1f}%', free_mb=f'{free_mb:.0f}', threshold=f'{memory_kill_pct}%')
            _kill_qa(proc, run_label)
            return 'memory'

    return 'ok' if proc.returncode == 0 else 'error'


def _stable_combined_files(combined_dir: Path, settle_s: float = 20.0) -> list:
    """Return sorted filenames of feu-combined ROOT files that are finished being written.

    Size > 0 is NOT enough: the combine step writes the file incrementally and the
    'hits' TTree only lands in it at close. A file caught mid-write opens fine but
    has no keys at all, so QA reports 'no hits found, skipping' for every detector
    and produces empty plots — silently, since nothing errors out. Observed
    2026-07-23 on beam_nominal_meshscan_1/nominal_00: QA launched 2 s before the
    combine closed the file and skipped all five detectors.

    So require the file to have been untouched for settle_s (> poll_interval)
    before considering it ready.
    """
    result = []
    now = time.time()
    for f in combined_dir.iterdir():
        if f.suffix != '.root' or '_datrun_' not in f.name or 'feu-combined' not in f.name:
            continue
        try:
            st = f.stat()
            if st.st_size > 0 and (now - st.st_mtime) >= settle_s:
                result.append(f.name)
        except OSError:
            continue
    return sorted(result)


def _file_num(filename: str):
    m = re.match(r'.*_(\d{3})_feu-combined', filename)
    return int(m.group(1)) if m else None


def _run_is_stale(run_dir: Path, combined_inner: str, stale_days: float) -> bool:
    cutoff = time.time() - stale_days * 86400
    newest = 0.0
    found_any = False
    for subrun in run_dir.iterdir():
        if not subrun.is_dir():
            continue
        d = subrun / combined_inner
        if d.exists():
            found_any = True
            mtime = d.stat().st_mtime
            if mtime > newest:
                newest = mtime
    if not found_any:
        return False  # No combined_hits yet — run is new, not stale
    return newest < cutoff


if __name__ == '__main__':
    main()
