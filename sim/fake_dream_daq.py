#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fake Dream DAQ for local testing without the RunCtrl/FEU hardware.

Called by dream_daq_control.py instead of launching RunCtrl when
dream_daq_info['simulate'] is true. Replays real sample .fdf files from
sim_source_fdf_dir into the sub-run directory, growing them chunk by chunk so
the on-the-fly copier and the processor watcher see files that behave like a
live DAQ (appearing, growing, then going stable).

Output names follow the RunCtrl convention
    SpsSim_<sub_run_name>_datrun_<YYMMDD>_<HHhMM>_<NNN>_<FF>.fdf
so all downstream file-number/FEU parsing works unchanged. The decoder handles
files truncated mid-event ("End of file reached ... Exiting early!"), so each
replayed file is capped at sim_max_mb_per_file to keep disk usage reasonable.
"""

import os
import re
from datetime import datetime
from time import sleep, time

CHUNK_MB_DEFAULT = 16          # MB appended per step to each FEU file
CHUNK_INTERVAL_DEFAULT = 10    # seconds between append steps
MAX_MB_PER_FILE_DEFAULT = 96   # cap on replayed bytes per FEU file


def _find_source_group(sim_source_dir):
    """Return {feu_num: source_path} for the datrun fdfs of the lowest file_num found."""
    pat = re.compile(r'.*_datrun_.*_(\d{3})_(\d{2})\.fdf$')
    groups = {}
    for name in sorted(os.listdir(sim_source_dir)):
        if '_pedestals_' in name:
            continue  # copied-in pedestal reference files, not beam-like data
        m = pat.match(name)
        if not m:
            continue
        fnum, feu = int(m.group(1)), int(m.group(2))
        groups.setdefault(fnum, {})[feu] = os.path.join(sim_source_dir, name)
    if not groups:
        return {}
    return groups[min(groups)]


def run_simulated_daq(sub_run_dir, sub_run_name, run_time_min, dream_info):
    """Replay sample fdfs into sub_run_dir for run_time_min minutes.

    Mimics RunCtrl called synchronously: returns 0 on success, non-zero on
    failure (missing/empty source directory).
    """
    sim_source_dir = dream_info.get('sim_source_fdf_dir')
    chunk_bytes = int(dream_info.get('sim_chunk_mb', CHUNK_MB_DEFAULT)) * 1024 * 1024
    chunk_interval = float(dream_info.get('sim_chunk_interval', CHUNK_INTERVAL_DEFAULT))
    max_bytes = int(dream_info.get('sim_max_mb_per_file', MAX_MB_PER_FILE_DEFAULT)) * 1024 * 1024

    if not sim_source_dir or not os.path.isdir(sim_source_dir):
        print(f'[sim daq] Source fdf directory not found: {sim_source_dir}')
        return 1
    sources = _find_source_group(sim_source_dir)
    if not sources:
        print(f'[sim daq] No datrun fdfs found in {sim_source_dir}')
        return 1

    stamp = datetime.now().strftime('%y%m%d_%HH%M')
    deadline = time() + run_time_min * 60

    print(f'[sim daq] Simulating Dream DAQ for {run_time_min} min: '
          f'{len(sources)} FEUs {sorted(sources)}, chunk={chunk_bytes // 2**20}MB '
          f'every {chunk_interval}s, cap={max_bytes // 2**20}MB/file')

    file_num = 0
    while time() < deadline:
        dests = {
            feu: os.path.join(
                sub_run_dir,
                f'SpsSim_{sub_run_name}_datrun_{stamp}_{file_num:03d}_{feu:02d}.fdf')
            for feu in sources
        }
        handles = {feu: (open(src, 'rb'), open(dests[feu], 'wb'))
                   for feu, src in sources.items()}
        try:
            written = dict.fromkeys(sources, 0)
            active = set(sources)
            while active and time() < deadline:
                for feu in sorted(active):
                    src_f, dst_f = handles[feu]
                    data = src_f.read(chunk_bytes)
                    if data:
                        dst_f.write(data)
                        dst_f.flush()
                        written[feu] += len(data)
                    if not data or written[feu] >= max_bytes:
                        active.discard(feu)
                if active:
                    sleep(chunk_interval)
        finally:
            for src_f, dst_f in handles.values():
                src_f.close()
                dst_f.close()
        total_mb = sum(written.values()) / 2**20
        print(f'[sim daq] file_num {file_num:03d} complete ({total_mb:.0f} MB total)')
        file_num += 1
        # Brief gap between file numbers, like RunCtrl rolling over to a new file.
        if time() < deadline:
            sleep(min(10, max(0, deadline - time())))

    print(f'[sim daq] Run time reached — simulated DAQ stopping '
          f'({file_num} file number(s) written).')
    return 0
