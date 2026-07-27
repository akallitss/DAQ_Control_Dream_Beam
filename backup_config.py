#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone backup watcher configuration for the P2 SPS beam test
(ported from Dylan Neff's nTof_x17_DAQ implementation).
Edit the constants below, then run this script to regenerate config/backup_config.json.
The flask UI's Start Backup button reads that JSON to launch backup_watcher.py.

Runtime requirements on the DAQ machine (banco):
  * xrdcp/xrdfs in PATH  (~/bin symlinks -> ~/miniconda3_daq/envs/tools, conda-forge xrootd)
  * Kerberos for CERN.CH (KRB5_CONFIG=config/krb5_cern.conf, set by the launcher)
  * ~/.cern_pass.gpg     (GPG-encrypted CERN password for unattended re-kinit)
"""

import json
import os

from run_config_beam import BASE_DATA_DIR

SOURCE_DIR     = BASE_DATA_DIR
# 2026-07-27: moved to nTOF's EOS (Dylan Neff's x17 allocation) — the salsachip
# project quota is full ([3021] on every xrdcp since 07-25) and the akallits user
# EOS was only ever a stopgap. Measured ~44 MB/s to eospublic vs ~8.6 MB/s to
# eosuser, so this is also the faster path.
#
# We keep writing as akallits (no dneff credentials on this shared machine — a
# dneff keytab here would hand out his full CERN identity to anyone with the
# banco login). Two EOS attributes on the destination make that work; both are
# inherited by subdirectories at creation, so the watcher's own mkdirs get them:
#   sys.acl="u:akallits:rwx"        -> akallits may write/overwrite/delete
#   sys.owner.auth="krb5:akallits"  -> files are CREATED owned by dneff:za, so
#                                      the bytes bill to his nTOF quota (akallits
#                                      has no quota node there: [3021] "quota not
#                                      defined" until this was set)
# Verified end-to-end from banco 2026-07-27: mkdir, write, overwrite (-f, the
# reconcile path), read-back and delete all succeed and land as dneff:za.
# Previous destinations kept for reference / the eventual consolidation:
# EOS_DIR      = '/eos/project/s/salsachip/Data/T2_tests/P2_SPS_Dream_Data/'
# XROOTD_URL   = 'root://eosproject.cern.ch'
# EOS_DIR      = '/eos/user/a/akallits/P2_SPS_backup_temp/'
# XROOTD_URL   = 'root://eosuser.cern.ch'
EOS_DIR        = '/eos/experiment/ntof/data/x17/p2_sps_july/'
XROOTD_URL     = 'root://eospublic.cern.ch'
CERN_PRINCIPAL = 'akallits@CERN.CH'
GPG_PASS_FILE  = '/local/home/banco/.cern_pass.gpg'

CONFIG = {
    # Local top-level data directory
    'source_dir': SOURCE_DIR,

    # EOS destination path (mirrored structure). Transfers use the native xrootd
    # protocol (xrdcp/xrdfs), NOT the FUSE mount: the legacy xrootdfs mount cannot
    # mkdir/rename/overwrite, so rsync-over-FUSE fails for any new directory.
    'eos_dir': EOS_DIR,

    # Native xrootd endpoint for the EOS instance holding eos_dir. Full URLs are
    # built as f"{xrootd_url}//{absolute_eos_path}" (note the double slash).
    'xrootd_url': XROOTD_URL,

    # READ-ONLY locations that also count as a backup when deciding whether a
    # local file is safe to delete. The backup watcher ignores this key
    # entirely — it never writes or deletes here; only flask_app/space_manager
    # (the Disk Space page) and scripts/prune_active_run consult it.
    #
    # Why: a campaign outlives its destination. This run moved salsachip ->
    # user EOS -> nTOF in three days, and verifying against the current eos_dir
    # alone declared everything at the older paths "not backed up", which
    # permanently blocked ~66 GB (drift_mesh_scan_1, highstat_eff_1 on
    # salsachip) and the drift_mesh_2d_2 remnant on user EOS from ever being
    # reclaimed. Listing the old paths here makes that disk space visible
    # again without moving a single byte.
    #
    # Cost is one `xrdfs ls -l -R` per location per listing (~9 s each here),
    # so keep the list short and drop entries once a path is retired for good.
    'verify_eos_locations': [
        {'xrootd_url': 'root://eosproject.cern.ch',
         'eos_dir':    '/eos/project/s/salsachip/Data/T2_tests/P2_SPS_Dream_Data/'},
        {'xrootd_url': 'root://eosuser.cern.ch',
         'eos_dir':    '/eos/user/a/akallits/P2_SPS_backup_temp/'},
    ],

    # Subdir of source_dir that gets smart per-subrun sync
    'runs_subdir': 'runs',

    # Subdirs of source_dir to never sync
    # 2026-07-27 11:40 — 'pedestals' TEMPORARILY added for the duration of
    # eff_nominal_1. It is only 1.5 GB but 475 tiny files, and every xrdcp pays a
    # fresh connect + Kerberos handshake (~11 s/file under load), so the extra
    # sync was holding the poll loop for ~55 min. The per-subrun runs sweep runs
    # BEFORE the extra sync in each iteration, so parking pedestals lets the live
    # run reach EOS on the next poll and unblocks the prune loop, which is what
    # keeps the disk alive until ~13:36. Pedestals stay on local disk and the
    # INTENSO mirror in the meantime — nothing is at risk.
    # REMOVE after the run: post_run_push restarts the watcher with
    # config/backup_config_post_run.json, whose exclude_dirs has no 'pedestals',
    # so they sync automatically then. This list must go back to 3 entries.
    'exclude_dirs': ['dream_run', 'analysis', 'sim_fdfs'],

    # GPG-encrypted CERN password file (created with: gpg --encrypt -r KEY -o ~/.cern_pass.gpg)
    'gpg_pass_file': GPG_PASS_FILE,

    # Kerberos principal for kinit
    'cern_principal': CERN_PRINCIPAL,

    # Seconds between kinit renewal attempts (ticket lasts ~25h, renew well before expiry)
    'kinit_interval': 3600,

    # Run filtering for the runs_subdir (same semantics as processor_config.py)
    # 2026-07-27: opened up to ALL runs now that the destination is nTOF — the
    # narrow include list existed only to protect bandwidth on the slow user-EOS
    # stopgap. What still needs pushing (~330 GB): drift_mesh_2d_1 (only 47 of
    # its 82 GB reached salsachip before the quota wall), drift_mesh_2d_2,
    # low_mesh_scan_1 and mesh_drift_scan_up_1 (the last two never got a single
    # byte across — their salsachip dirs are 0-sized), plus the live eff_nominal_1.
    # Reopened 2026-07-27 12:55 — backlog AND live run. This is only safe
    # because backup_watcher now syncs the live run first every poll and caps
    # backlog work at backlog_subruns_per_poll before rechecking it. Earlier
    # today, with plain sorted() order, drift_mesh_2d_1 (82 GB) + 2d_2 (55 GB)
    # sat ahead of eff_nominal_1 and starved the prune loop (it only deletes
    # what it can verify on EOS), which nearly filled the disk mid-run.
    'include_runs': None,

    # Already complete on salsachip EOS, so re-uploading them to nTOF would just
    # burn Dylan's quota. Verified file-by-file 2026-07-27 (not by size): every
    # file in the external-disk mirror of these two exists under
    # /eos/project/s/salsachip/Data/T2_tests/P2_SPS_Dream_Data/runs/.
    # The other salsachip runs (beam_commissioning_1, beam_nominal_meshscan_1,
    # drift_scan_1/2/final, env_test_1, latency_scan_1, meshscan_fine_1,
    # p2in_check_1, run_1) are no longer on local disk, so they need no entry.
    # NOTE the cost of excluding: space_manager/prune_active_run verify against
    # eos_dir, so these two now look "not backed up" to them and their ~66 GB of
    # local disk cannot be auto-reclaimed. Delete by hand if space gets tight —
    # they are safe on salsachip.
    'exclude_runs': ['drift_mesh_scan_1', 'highstat_eff_1'],

    # Watcher behavior
    'poll_interval':       30,     # seconds between runs-dir scans
    'stale_run_days':      10,     # runs with no new data for this many days are skipped
    'extra_sync_interval': 300,    # seconds between full syncs of non-runs subdirs
    'reconcile_interval':  86400,  # seconds between idle-only full-reconcile sweeps of
                                   # ALL runs (verify vs EOS, re-copy missing/changed
                                   # files incl. stale runs); once a day

    # Extra arguments passed verbatim to xrdcp (e.g. ['-S', '4'] for 4 parallel data
    # streams per file, or ['--retry', '3'] on flaky WAN links). '-f' (overwrite) and
    # '-p' (create parent dirs) are always applied by the watcher.
    'xrdcp_extra_args': [],
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'backup_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
