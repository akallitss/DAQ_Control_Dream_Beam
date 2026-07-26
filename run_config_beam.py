#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run configuration for the P2 SPS beam test DAQ.

Adapted from the nTof x17 beam configuration (Dylan Neff) for the P2 detectors.
The P2 detector definition (FEU/connector cabling, HV channels) is carried over
from Cosmic_Bench_DAQ_Control/run_config.py — same cabling as the cosmic bench.

Site switching: set SITE below.
  'local' — full simulation on this machine (fake CAEN HV + fake Dream DAQ that
            replays sample fdfs), for testing the whole chain without hardware.
  'sps'   — the banco machine (dedippcq196 = banco_daplxa, user banco), the
            DAQ computer for the SPS beam test. Its DAQ NIC (enp2s0,
            192.168.10.8/16, MTU 9000) is a private LAN with the DREAM FEUs
            (Ids 101/102/103 at 192.168.10.113-.115, per SelfTcm.cfg in
            ~/Feu/.../bin/EicP2Bt/) and the CAEN HV crate (192.168.10.199).
            Fields marked TODO-SPS must be filled in at the beam area.

@author: Alexandra Kallitsopoulou (based on Dylan Neff's nTof config)
"""

import os
import sys

from run_config_base import RunConfigBase

# ---------------------------------------------------------------------------
# Site configuration — the ONE place to switch local test <-> SPS machine
# ---------------------------------------------------------------------------
SITE = os.environ.get('DAQ_SITE', 'local')  # 'local' or 'sps'; export DAQ_SITE=sps on banco

# ---------------------------------------------------------------------------
# Trigger mode — switches the whole trigger configuration coherently. Flip it
# here (or per-run with the DAQ_TRIGGER env var) to move between the SPS beam
# and the Fe55 bench without editing anything else.
#   'external' : SPS beam. External scintillator trigger via the TCM. Uses the
#                P2B_Beam.cfg dream template (Sys DaqRun Trig Ext) and Dat FEU roles.
#   'self'     : Fe55 bench. Self-trigger via TCM multiplicity. Uses the
#                P2SelfTrigger.cfg template (Sys DaqRun Trig Slf) and Trg roles.
# The template file is picked up from <base_data_dir>/dream_config/ (both live
# there), so no per-site path edits are needed to switch.
# ---------------------------------------------------------------------------
TRIGGER_MODE = os.environ.get('DAQ_TRIGGER', 'external')  # 'external' (beam) or 'self' (Fe55)
assert TRIGGER_MODE in ('external', 'self'), \
    f"DAQ_TRIGGER must be 'external' or 'self', got {TRIGGER_MODE!r}"
_SELF_TRIGGER = (TRIGGER_MODE == 'self')
# P2B_Beam.cfg is a copy of the expert's optimized beam reference config
# EicP2Bt/P2B_TstBeam.cfg (2026-07-23 15:46) with the stale per-FEU PdFile/ZsFile
# references cleared — each run programs fresh pedestals/thresholds. Unlike the
# earlier self-trigger reference (P2B_SelfTcm.cfg), this one is itself an
# external-trigger config: Sys DaqRun Trig Ext, an all-'Dat' Topo, Mult 2/4.
# The beam-specific values it carries, which the run-config script never writes
# and so would otherwise silently drift: latency 32 (Feu * Dream * 12 0x0020),
# Feu_RunCtrl_RdDel 1, Feu_InterPacket_Delay 1, UdpChan_MultiPackThr 4888,
# DrmClk Rd/WrClk_Div 6.0, Main_Trig_Ovr* watermarks 36/40/48, the Dream
# channel-mask registers 8/9 = 0xffff, and no per-FEU Dream register-1
# overrides (all FEUs take the wildcard 0x081f/0xd023).
# Every trigger-mode field (Sys DaqRun Trig, the multiplicity window, the Topo
# Dream roles) is still written per run from TRIGGER_MODE, so it serves both modes.
# Cross-check any template change with scripts/check_cfg_vs_reference.py.
_DREAM_TEMPLATE_FILE = {'self': 'P2SelfTrigger.cfg', 'external': 'P2B_Beam.cfg'}[TRIGGER_MODE]

SITES = {
    'local': {
        # All data under a local test tree (runs/, pedestals/, dream_config/, ...)
        'base_data_dir': '/local/home/ak271430/Documents/PostDocSaclay/data/sps_p2_test/',
        'daq_host': '127.0.0.1',    # hv_control / dream_daq / processor servers
        'hv_ip': 'sim',             # 'sim' -> hv_control uses FakeCAENHVController
        'hv_n_cards': 4,
        'simulate': True,           # fake HV + fake Dream DAQ (replay sample fdfs)
        'reconstruction_build': '/local/home/ak271430/Documents/PostDocSaclay/'
                                'mm_dream_reconstruction/build/',
    },
    'sps': {
        # banco machine (dedippcq196.extra.cea.fr, ssh alias banco_daplxa).
        # Active runs write to the NVMe system disk (measured 1.4 GB/s direct
        # writes, >10x the 1 GbE FEU link) — back up to the Intenso USB drive
        # between runs, never record onto it directly (FAT32, ~106 MB/s, SMR).
        # SPS July-2026 beam test in the H4 line — separate campaign dir from
        # the Fe55 bench data (already backed up under P2_data/Fe55/).
        'base_data_dir': '/local/home/banco/P2_data/TB_July2026_H4/',
        'daq_host': '192.168.10.8',                  # banco's IP on its DAQ LAN (enp2s0)
        'hv_ip': '192.168.10.199',                   # CAEN mainframe on banco's DAQ LAN (web login on :80)
        # Crate probed 2026-07-18: 16-slot mainframe, 12-ch cards in slots 8 and
        # 12 only. n_cards bounds range() sweeps (e.g. power-off-all), so it must
        # reach slot 12; empty slots read power=off and are skipped harmlessly.
        'hv_n_cards': 13,
        'simulate': False,
        # Built 2026-07-18 against ROOT 6.32.02 in ~/opt/root_v6.32.02 (binaries
        # carry an rpath to it — no thisroot.sh needed to run them).
        'reconstruction_build': '/local/home/banco/mm_strip_reconstruction/cmake-build-release/',
        # Dream .cfg template: P2SelfTrigger.cfg is a copy of the FEU software's
        # EicP2Bt/SelfTcm.cfg (source of truth for FEU Ids/IPs and TCM input
        # numbering: input 3 = Id 101, 4 = 102, 5 = 103) with Sys Name = P2Fe55
        # and the stale per-FEU PdFile/ZsFile refs cleared (each run's own
        # PedThr phase programs fresh pedestals/thresholds instead).
        # dream_cfg_template is derived from TRIGGER_MODE below (P2B_Beam.cfg
        # for beam, P2SelfTrigger.cfg for Fe55) — both live in dream_config/.
    },
}

_SITE_CFG = SITES[SITE]
BASE_DATA_DIR = _SITE_CFG['base_data_dir']
RECONSTRUCTION_BUILD = _SITE_CFG['reconstruction_build']
SIMULATE = _SITE_CFG['simulate']
# Dream .cfg template for the current trigger mode (see TRIGGER_MODE above). An
# explicit SITES['<site>']['dream_cfg_template'] still wins if one is set.
DREAM_CFG_TEMPLATE = _SITE_CFG.get(
    'dream_cfg_template', f'{BASE_DATA_DIR}dream_config/{_DREAM_TEMPLATE_FILE}')

# ---------------------------------------------------------------------------
# RUN PLAN — the single knob that decides what the next run does.
#
# This is what the GUI "Start Run" button reads. That button re-runs this file
# to regenerate run_config_beam.json, but it cannot set environment variables,
# so a mode gated ONLY on a DAQ_* env var is unreachable from the GUI — which is
# the only reason ~/overnight_scans.sh existed. Setting RUN_PLAN here makes the
# scans startable with one click. The DAQ_* env vars still work and still win,
# so scripted and one-off runs are unaffected.
#
#   'commissioning'   : N_SUBRUNS identical sub-runs of SUBRUN_MIN minutes at
#                       the beam operating point (OPERATING_HV).
#   'drift_scan'      : mesh fixed, drift stepped UP (drift block below).
#   'mesh_scan'       : drift gap fixed, mesh+drift stepped DOWN together.
#   'drift_then_mesh' : the full overnight plan as ONE run — every drift point,
#                       then every mesh point. This is what overnight_scans.sh
#                       did as two separate daq_control invocations.
#   'drift_mesh_2d'   : 2D map — a full mesh scan AT EVERY drift point (the outer
#                       product, not the concatenation). See the BEAM_2D_SCAN
#                       block below; sub-run count is the product of the two
#                       point counts, so check the printed total before starting.
# ---------------------------------------------------------------------------
RUN_PLAN = os.environ.get('DAQ_RUN_PLAN', 'drift_then_mesh')
_RUN_PLANS = ('commissioning', 'drift_scan', 'mesh_scan', 'drift_then_mesh',
              'drift_mesh_2d')
assert RUN_PLAN in _RUN_PLANS, f'RUN_PLAN {RUN_PLAN!r} not one of {_RUN_PLANS}'

# Mesh-scan shape for the plan-driven (GUI) runs: the 5 V / 12 point / 10 min
# scan overnight_scans.sh asked for, rather than the coarser committed defaults
# further down. setdefault, so an explicitly exported DAQ_MESH_* still wins.
if RUN_PLAN in ('mesh_scan', 'drift_then_mesh'):
    os.environ.setdefault('DAQ_MESH_NOMINAL',    '1')
    os.environ.setdefault('DAQ_MESH_POINTS',     '12')
    os.environ.setdefault('DAQ_MESH_STEP_V',     '5')
    os.environ.setdefault('DAQ_MESH_SUBRUN_MIN', '10')

# ---------------------------------------------------------------------------
# Run schedule — the modes are checked in this order:
#
#   BEAM_DRIFT_SCAN and BEAM_HV_SCAN both True : drift points, then mesh points,
#                       in a single run. Takes precedence over every mode here.
#   BEAM_DRIFT_SCAN True : beam drift scan (mesh fixed, drift stepped up). See the
#                       block below.
#   BEAM_HV_SCAN True  : beam mesh scan (drift gap fixed, mesh+drift stepped down).
#   LATENCY_SCAN True : beam latency scan. One sub-run per value in
#                       LATENCY_SCAN_VALUES, all at the beam operating point,
#                       to find the latency that centres the pulse in the
#                       sample window. Takes precedence over HV_SCAN.
#   HV_SCAN True      : Fe55 self-trigger mesh HV scan. Per detector, start AT
#                       the operating (max) point and step mesh AND drift down
#                       together by SCAN_STEP_V per point — the potential across
#                       the drift gap (= drift − mesh) stays constant.
#   both False        : N_SUBRUNS identical sub-runs of SUBRUN_MIN minutes at
#                       the beam operating point (OPERATING_HV). This is the
#                       commissioning / physics case.
# ---------------------------------------------------------------------------
# Latency scan: confirm that the expert's 32 (0x0020) really centres the pulse
# on REAL beam signals. 'latency' is written as 'Feu * Dream * 12', the
# sample-window offset; too small and the pulse sits at the start of the 16
# samples (rise clipped), too large and it runs off the end.
#
# This works with no extra DAQ plumbing: dream_daq_control builds each sub-run's
# parameters as {**dream_daq_info, **sub_run}, so a 'latency' key in a sub-run
# dict overrides the run-level value for that sub-run only. hv_control reads
# only 'hvs' and 'sub_run_name' from the same dict and ignores the rest.
#
# Set LATENCY_SCAN=True (or DAQ_LATENCY_SCAN=1) for the scan, then put the
# winning value in dream_daq_info['latency'] and set it back to False.
# ---------------------------------------------------------------------------
# Beam mesh-HV scan — efficiency vs gain, the deliverable that needs beam.
# Enabled with DAQ_BEAM_HV_SCAN=1; takes precedence over LATENCY_SCAN/HV_SCAN.
#
# Structure: BEAM_SCAN_NOMINAL_SUBRUNS sub-runs at the operating point (the
# primary physics dataset), then BEAM_SCAN_POINTS points stepping every P2
# detector's mesh DOWN by BEAM_SCAN_MESH_STEP_V, drift following so each
# detector's own drift gap stays constant (P2_IN 210 V, P2_MID/P2_OUT 250 V).
#
# Downward only: drift is already at its 700 V maximum at the operating point,
# so holding the gap constant while raising mesh is not possible.
#
# The uRWELL references stay at their operating point throughout — they are the
# tracking telescope, and moving them would change the reference for every
# point of the scan.
#
# Sizing (2026-07-23 measured beam): 8 x 20 min ~ 2 h 49 m including the ~45 s
# per sub-run boundary and the initial ramp. At ~1150 Hz long-run average that
# is ~11 M events total, ~1.4 M per point.
BEAM_HV_SCAN = (os.environ.get('DAQ_BEAM_HV_SCAN', '0') == '1'
                or RUN_PLAN in ('mesh_scan', 'drift_then_mesh'))
# Detectors whose mesh AND drift step together (gap held constant → isolates gain).
# P2_IN is deliberately excluded — it is parked off (0 V), has no gain to scan, and
# stepping its 0 V setpoint down would go negative and fail the range assert.
BEAM_SCAN_DETS = ('P2_IN', 'P2_MID', 'P2_OUT')  # all three step DOWN (safe: P2_IN 400->lower stays < its 490 mesh / 700 drift max)
# Env-overridable so a finer or continuation gain scan needs no code edit, e.g.
# DAQ_MESH_STEP_V=5 DAQ_MESH_POINTS=12 DAQ_MESH_SUBRUN_MIN=10 DAQ_MESH_NOMINAL=1
# gives a 5 V scan 450..390 (13 mesh points incl. the nominal) at 10 min each.
BEAM_SCAN_NOMINAL_SUBRUNS = int(os.environ.get('DAQ_MESH_NOMINAL', '2'))    # sub-runs at the operating point, before the scan
BEAM_SCAN_POINTS = int(os.environ.get('DAQ_MESH_POINTS', '6'))              # scan points BELOW nominal
BEAM_SCAN_MESH_STEP_V = int(os.environ.get('DAQ_MESH_STEP_V', '10'))        # V per point; drift steps by the same amount
BEAM_SCAN_SUBRUN_MIN = int(os.environ.get('DAQ_MESH_SUBRUN_MIN', '20'))     # minutes per sub-run

# ---------------------------------------------------------------------------
# Beam DRIFT scan — efficiency vs drift field. The orthogonal partner to the
# mesh scan above: the mesh scan held the drift gap constant to isolate GAIN;
# this holds the mesh FIXED and moves ONLY the drift electrode, so the one thing
# changing is the drift field (electron transparency / primary collection).
# Enabled with DAQ_BEAM_DRIFT_SCAN=1; takes precedence over every scan below.
#
# Full curve from ZERO drift field to the plateau: the first point sets drift =
# mesh (gap 0, no drift field, ~no efficiency), then steps UP to 900 V so the
# efficiency-vs-drift curve rises out of zero and flattens onto its plateau. On
# 2026-07-24 (Alexandra) the P2 drift ceiling was opened to 900 V (MAX_HV) for
# this. Mesh stays at last run's operating value (P2_MID/P2_OUT 450). Only
# BEAM_DRIFT_SCAN_DETS move; the two uRWELL references stay fixed at 600/420 as
# the tracking telescope, and P2_IN stays parked off.
# Default 10 points 450..900 in 50 V steps, 10 min each (~100 min DAQ) — sized to
# the 2.5 h beam window of 2026-07-24.
BEAM_DRIFT_SCAN = (os.environ.get('DAQ_BEAM_DRIFT_SCAN', '0') == '1'
                   or RUN_PLAN in ('drift_scan', 'drift_then_mesh'))
BEAM_DRIFT_SCAN_DETS = ('P2_MID', 'P2_OUT')  # detectors whose drift is scanned. P2_IN is
                                             # excluded here only to keep this 1D scan
                                             # comparable with the 2026-07-24/25 runs that
                                             # defined it — the 700 V ceiling that originally
                                             # forced the exclusion was retired on 2026-07-25
                                             # (MAX_HV), and the 2D scan does move P2_IN.
# The scan window is env-overridable so a continuation run (e.g. the top points
# after a beam stop) needs no code edit — the committed default stays the full
# 450..900. e.g. DAQ_DRIFT_START_V=800 DAQ_DRIFT_POINTS=3 does 800/850/900.
BEAM_DRIFT_SCAN_START_V = int(os.environ.get('DAQ_DRIFT_START_V', '450'))  # first point: drift = mesh, ZERO drift field
BEAM_DRIFT_SCAN_STEP_V  = int(os.environ.get('DAQ_DRIFT_STEP_V',  '50'))   # V per point, stepping UP
BEAM_DRIFT_SCAN_POINTS  = int(os.environ.get('DAQ_DRIFT_POINTS',  '10'))   # default 450, 500, ... 900 inclusive
BEAM_DRIFT_SCAN_SUBRUN_MIN = int(os.environ.get('DAQ_DRIFT_SUBRUN_MIN', '10'))  # minutes per point

# ---------------------------------------------------------------------------
# Beam 2D DRIFT x MESH scan — the outer product of the two scans above: at every
# drift point, a full mesh scan. 'drift_then_mesh' runs the two 1D scans back to
# back and so only ever measures gain at ONE drift field and field at ONE gain;
# this maps the plane, which is what is needed if efficiency does not factorise
# into (gain) x (field).
#
# Enabled with DAQ_BEAM_2D_SCAN=1 or RUN_PLAN='drift_mesh_2d'. Takes precedence
# over every other scan (checked first in the sub-run builder below).
#
# Axes, both mirroring the 1D conventions above:
#   outer k: drift = BEAM_2D_DRIFT_START_V + k*BEAM_2D_DRIFT_STEP_V  (stepping UP)
#   inner j: mesh  = OPERATING_HV[det]['mesh'] - j*BEAM_2D_MESH_STEP_V (DOWN)
#
# BEAM_2D_DRIFT_MODE decides what drift does DURING an inner mesh scan, and it
# changes the physics of the resulting map:
#
#   'follow_mesh' (default) : drift steps down with mesh, so the drift GAP
#       (drift - mesh) is constant across the inner scan. Each inner scan is a
#       pure gain scan at one drift field, exactly like the 1D mesh scan, which
#       steps both together for this reason. The two axes come out orthogonal:
#       k indexes drift field, j indexes gain.
#   'absolute' : drift is held at the outer point's value while mesh steps down,
#       so the gap — and therefore the drift field — GROWS along the inner scan.
#       Literally 'a drift scan, and at each point a mesh scan', but the axes are
#       then skewed: no row of the map is at constant field.
#
# The default samples a 5 x 4 grid = 20 sub-runs x 10 min = 3 h 20 min. Sub-run
# count is the product, so raising both point counts gets expensive fast —
# run_config_beam.py prints the grid and the total before anything ramps.
# ---------------------------------------------------------------------------
BEAM_2D_SCAN = (os.environ.get('DAQ_BEAM_2D_SCAN', '0') == '1'
                or RUN_PLAN == 'drift_mesh_2d')
# All three P2 stations. P2_IN used to be excluded because its drift ceiling was
# 700 V, below the drift axis's top; that limit was retired on 2026-07-25 (see
# MAX_HV) and it now takes the same axis as MID/OUT. Deliberately NOT tied to
# BEAM_DRIFT_SCAN_DETS any more — the 1D drift scan keeps its own committed set.
# Both axes move only these detectors; the uRWELL references stay fixed at
# 600/420 as the tracking telescope.
BEAM_2D_SCAN_DETS = ('P2_IN', 'P2_MID', 'P2_OUT')
BEAM_2D_DRIFT_START_V = int(os.environ.get('DAQ_2D_DRIFT_START_V', '450'))  # first drift point (450 = drift-mesh, zero field)
BEAM_2D_DRIFT_STEP_V  = int(os.environ.get('DAQ_2D_DRIFT_STEP_V',  '50'))   # V per outer point, stepping UP
BEAM_2D_DRIFT_POINTS  = int(os.environ.get('DAQ_2D_DRIFT_POINTS',  '5'))    # default 450, 500, 550, 600, 650
BEAM_2D_MESH_STEP_V   = int(os.environ.get('DAQ_2D_MESH_STEP_V',   '10'))   # V per inner point, stepping DOWN
BEAM_2D_MESH_POINTS   = int(os.environ.get('DAQ_2D_MESH_POINTS',   '4'))    # default mesh 450, 440, 430, 420
BEAM_2D_SUBRUN_MIN    = int(os.environ.get('DAQ_2D_SUBRUN_MIN',    '10'))   # minutes per grid point
BEAM_2D_DRIFT_MODE    = os.environ.get('DAQ_2D_DRIFT_MODE', 'follow_mesh')
_2D_DRIFT_MODES = ('follow_mesh', 'absolute')
assert BEAM_2D_DRIFT_MODE in _2D_DRIFT_MODES, (
    f'BEAM_2D_DRIFT_MODE {BEAM_2D_DRIFT_MODE!r} not one of {_2D_DRIFT_MODES}')

LATENCY_SCAN = os.environ.get('DAQ_LATENCY_SCAN', '0') == '1'
# Centred on the reference's 32, +/- 8 in steps of 4. Widen the step first if
# none of these centres the pulse; narrow it to 2 once the region is bracketed.
LATENCY_SCAN_VALUES = [24, 28, 32, 36, 40]
LATENCY_SUBRUN_MIN = 2   # minutes per latency point — enough for a timing plot

# False for the first external-trigger beam run: a short commissioning pass at
# the nominal point (2 x 2 min) to confirm a non-zero trigger rate, that events
# actually land in the FDFs, and that decoding is clean — before committing beam
# time.
# NB: this flag is global, NOT per trigger mode. The Fe55 scan code below is
# intact but will not run while this is False — set it back to True (together
# with DAQ_TRIGGER=self) to get the Fe55 mesh scan.
HV_SCAN = False
# Operating (= maximum safe) voltages per detector — scan starts here, goes DOWN.
SCAN_START = {
    'P2_OUT': {'mesh': 420, 'drift': 700},   # max: 420 mesh / 700 drift
    'P2_MID': {'mesh': 510, 'drift': 700},   # max: 510 mesh / 700 drift
}
SCAN_STEP_V = 5         # V — mesh and drift both step down by this per point
SCAN_POINTS = 12        # 12 points x 5 min = 1 h of data
SCAN_SUBRUN_MIN = 5     # minutes per scan point

N_SUBRUNS = int(os.environ.get('DAQ_N_SUBRUNS', '2'))    # identical sub-runs (commissioning plan)
SUBRUN_MIN = int(os.environ.get('DAQ_SUBRUN_MIN', '2'))  # minutes per sub-run (env-overridable for long runs)
POST_SUBRUN_PAUSE_MIN = 0   # optional pause AFTER each sub-run (minutes); 0 = no pause

# ---------------------------------------------------------------------------
# Beam operating points, per detector (2026-07-23, Alexandra). Roles match the
# DET_HV channel map below:
#   P2 stations  -> 'drift' + 'mesh'
#   uRWELL refs  -> 'drift' + 'resist'  (front = uRWELL-inter, back = uRWELL-strip)
#
# This replaces the old common P2 point (mesh 440 / drift 600, inherited from
# the cosmic bench). That point was not just non-optimal for the beam, it was
# unsafe on P2_OUT: 440 V mesh exceeds its 420 V maximum.
# ---------------------------------------------------------------------------
OPERATING_HV = {
    'P2_IN':  {'drift': 700, 'mesh': 450},   # gap = 250 V. 2026-07-25: uniform P2
                                             # operating point (450/700) with MID/OUT
                                             # for the high-stat run. Above yesterday's
                                             # 430 mesh — watch its current.
    'P2_MID': {'drift': 700, 'mesh': 450},   # gap = 250 V; drift scanned up to 900
    'P2_OUT': {'drift': 700, 'mesh': 450},   # gap = 250 V; drift scanned up to 900
    'EIC_uRWELL_front': {'drift': 600, 'resist': 420},   # uRWELL-inter
    'EIC_uRWELL_back':  {'drift': 600, 'resist': 420},   # uRWELL-strip
}

# Maximum safe voltage per detector/role. Asserted against OPERATING_HV at
# import so a typo in a setpoint fails here rather than on the real crate.
#
# P2_OUT mesh was raised 420 -> 450 V on 2026-07-23 on Alexandra's instruction,
# to run MID and OUT at the same 250 V drift gap. This SUPERSEDES the earlier
# 420 V figure (which came from the Fe55 bench SCAN_START and is still what the
# Fe55 scan below starts from). Flagged because it is the one setpoint here that
# exceeds a previously documented maximum — watch P2_OUT's current draw on the
# first ramp and back off if it draws or trips.
# P2_MID/P2_OUT drift ceiling raised 700 -> 900 V on 2026-07-24 (Alexandra) for
# the beam drift scan (DAQ_BEAM_DRIFT_SCAN), which steps drift UP from the 700 V
# operating point to look for the transparency optimum above it. Mesh maxima are
# unchanged. Watch drift current on every up-step — 900 V drift over a 450 V gap
# is a higher drift field than these detectors have run at; back a channel off if
# it draws or trips (the monitor flags >2 uA / any trip).
# P2_IN drift ceiling raised 700 -> 900 V on 2026-07-25 (Alexandra): the 700 V
# figure was an old limit, not a property of the chamber, and it was the only
# reason P2_IN sat out the drift scans. It now matches MID/OUT, so all three P2
# stations take the same drift axis in the 2D scan. P2_IN has never run above
# 700 V drift — watch its current on the up-steps of the first few grid rows.
MAX_HV = {
    'P2_IN':  {'drift': 900, 'mesh': 450},   # mesh ceiling 450 V (Alexandra 2026-07-25);
                                             # drift 700 -> 900 (Alexandra 2026-07-25)
    'P2_MID': {'drift': 900, 'mesh': 450},   # mesh ceiling lowered 510 -> 450 (Alexandra 2026-07-24)
    'P2_OUT': {'drift': 900, 'mesh': 450},
    'EIC_uRWELL_front': {'drift': 600, 'resist': 420},
    'EIC_uRWELL_back':  {'drift': 600, 'resist': 420},
}
# ---------------------------------------------------------------------------
# P2_IN alive-check (DAQ_P2IN_CHECK=1): a short fixed-HV run to confirm the
# re-inserted / repaired P2_IN responds to beam. Reads out ONLY P2_IN + the two
# uRWELL references (P2_MID/P2_OUT excluded from readout AND powered off);
# P2_IN at DAQ_P2IN_MESH / DAQ_P2IN_DRIFT (default 400 mesh / 600 drift, gap 200).
# Takes precedence over every scan. One sub-run of DAQ_P2IN_MIN minutes.
# ---------------------------------------------------------------------------
P2IN_CHECK = os.environ.get('DAQ_P2IN_CHECK', '0') == '1'
P2IN_CHECK_MIN = int(os.environ.get('DAQ_P2IN_MIN', '12'))   # minutes of data
if P2IN_CHECK:
    OPERATING_HV['P2_IN']  = {'drift': int(os.environ.get('DAQ_P2IN_DRIFT', '600')),
                              'mesh':  int(os.environ.get('DAQ_P2IN_MESH',  '400'))}
    OPERATING_HV['P2_MID'] = {'drift': 0, 'mesh': 0}   # not read out in the P2_IN check
    OPERATING_HV['P2_OUT'] = {'drift': 0, 'mesh': 0}

# ---------------------------------------------------------------------------
# One-off setpoint override for a single run, e.g. a short aliveness check at a
# point that is known-good rather than at the full operating point:
#
#   DAQ_HV_OVERRIDE='P2_MID:drift=500,P2_OUT:drift=500'
#
# Deliberately applied ABOVE the MAX_HV assert below, so an override is range
# checked exactly like a committed setpoint and a typo fails here rather than on
# the real crate. Unset by default — the committed operating point is unchanged.
# ---------------------------------------------------------------------------
_HV_OVERRIDE = os.environ.get('DAQ_HV_OVERRIDE', '').strip()
if _HV_OVERRIDE:
    for _item in _HV_OVERRIDE.split(','):
        _item = _item.strip()
        if not _item:
            continue
        try:
            _det_role, _val = _item.split('=')
            _det, _role = _det_role.split(':')
        except ValueError:
            raise SystemExit(f"DAQ_HV_OVERRIDE item {_item!r} is not "
                             f"'<detector>:<role>=<volts>'")
        _det, _role = _det.strip(), _role.strip()
        if _det not in OPERATING_HV:
            raise SystemExit(f'DAQ_HV_OVERRIDE: unknown detector {_det!r} '
                             f'(known: {sorted(OPERATING_HV)})')
        if _role not in OPERATING_HV[_det]:
            raise SystemExit(f'DAQ_HV_OVERRIDE: {_det} has no role {_role!r} '
                             f'(known: {sorted(OPERATING_HV[_det])})')
        _old = OPERATING_HV[_det][_role]
        OPERATING_HV[_det][_role] = int(_val)
        print(f'HV OVERRIDE: {_det} {_role} {_old} -> {int(_val)} V')

for _det, _roles in OPERATING_HV.items():
    for _role, _v in _roles.items():
        assert _v <= MAX_HV[_det][_role], (
            f'{_det} {_role} setpoint {_v} V exceeds its maximum '
            f'{MAX_HV[_det][_role]} V')

# ---------------------------------------------------------------------------
# Telescope geometry — position along the beam (z, mm), confirmed at the beam
# 2026-07-22 (Alexandra). Beam order upstream -> downstream: uRWELL front
# reference, the three P2 stations, then uRWELL back reference. Gaps 32/31/31/43
# cm. TODO-SPS: survey the transverse x/y offsets.
# ---------------------------------------------------------------------------
DET_Z_MM = {
    'EIC_uRWELL_front':    0.0,   # front reference
    'P2_IN':             320.0,   # 32 cm
    'P2_MID':            630.0,   # +31 cm
    'P2_OUT':            940.0,   # +31 cm
    'EIC_uRWELL_back':  1370.0,   # +43 cm  back reference
}

# HV channels (card, channel) on the SPS CAEN crate (192.168.10.199), confirmed
# at the beam 2026-07-22. P2 detectors: mesh + drift on card 8. uRWELL
# references: drift on card 8 + a resistive layer on card 12 (the crate's second
# populated slot).
DET_HV = {
    'P2_IN':  {'drift': (8, 0), 'mesh': (8, 1)},
    'P2_MID': {'drift': (8, 2), 'mesh': (8, 3)},
    'P2_OUT': {'drift': (8, 4), 'mesh': (8, 5)},
    'EIC_uRWELL_front': {'drift': (8, 6), 'resist': (12, 0)},
    'EIC_uRWELL_back':  {'drift': (8, 7), 'resist': (12, 1)},
}


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        # Declared global up front (Python requires this before the names are
        # read) so the GUI override at the end of this method can retarget the
        # trigger mode. Untouched unless a GUI config is loaded there.
        global TRIGGER_MODE, _SELF_TRIGGER, _DREAM_TEMPLATE_FILE, DREAM_CFG_TEMPLATE
        # DAQ_RUN_NAME overrides the default (same knob run_config_pedestals.py
        # uses). Needed because 'run_1' is already taken on EOS by the Fe55 scan
        # under this campaign path — reusing it would merge beam sub-runs into
        # that directory. Every run_name-derived path below follows this.
        # Name follows the plan, so a GUI-started scan is not called 'run_N'.
        # The _1 suffix is what iterate_run_num.py bumps when the directory
        # already exists, so repeat scans land in _2, _3, ... automatically.
        _plan_run_name = {
            'drift_then_mesh': 'drift_mesh_scan_1',
            'drift_scan':      'drift_scan_1',
            'mesh_scan':       'mesh_scan_1',
            'drift_mesh_2d':   'drift_mesh_2d_1',
        }.get(RUN_PLAN, 'run_1')
        self.run_name = os.environ.get('DAQ_RUN_NAME') or _plan_run_name
        self.base_out_dir = BASE_DATA_DIR
        self.data_out_dir = f'{self.base_out_dir}runs/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.raw_daq_inner_dir = 'raw_daq_data'
        self.decoded_root_inner_dir = 'decoded_root'
        self.detector_info_dir = f'{self.base_out_dir}config/detectors/'
        self.save_fdfs = True  # True to save FDF files, False to delete after decoding
        self.start_time = None
        self.process_on_fly = False  # False: processor_watcher handles processing independently
        # False for a back-to-back beam series: powering off costs a full
        # ~2-3 min re-ramp from 0 V at the start of the next run, and with
        # spills every ~57 s that is real beam time. NB this leaves the
        # detectors biased when the series ends — power off by hand (or flip
        # this back to True for the last run of the day).
        # Power off all CAEN HV at the end of the run. Env-overridable (DAQ_POWER_OFF=0)
        # so chained runs keep HV up between them and only the last one powers off.
        self.power_off_hv_at_end = os.environ.get('DAQ_POWER_OFF', '1') == '1'
        # True to resume an existing run: sub-runs already marked .subrun_complete
        # are skipped, so only the missing ones are taken and they land in the
        # original run_out_dir. Env-overridable (DAQ_RESUME=1) so a series cut
        # short can be finished without editing this file — as on 2026-07-25,
        # when Stop Run ended highstat_eff_1 four seconds before sub-run 04's
        # natural end and beam_commissioning_05 was never taken.
        self.resume = os.environ.get('DAQ_RESUME', '0') == '1'
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        self.gas = 'Ar/Iso 95/5'  # Gas type for run
        # self.gas = 'Ar/CO2/Iso 93/5/2'
        # self.gas = 'Ar/CF4 90/10'
        self.beam_type = 'sps_beam'
        # self.beam_type = 'cosmics'
        self.target_type = 'none'
        # Trigger description follows TRIGGER_MODE.
        #   self:     Fe55 bench — each FEU's 'Trg' Dreams send hit primitives,
        #             the TCM forms the trigger from its multiplicity window.
        #   external: SPS beam — external scintillator coincidence into the TCM,
        #             distributed on the sync line; FEUs are pure 'Dat'.
        self.trigger = ('Fe55 self trigger via TCM multiplicity' if _SELF_TRIGGER
                        else 'SPS external scintillator coincidence via TCM')

        self.dream_daq_info = {
            'ip': _SITE_CFG['daq_host'],
            'port': 1101,
            # Site override (e.g. banco's SelfTcm.cfg) or the cosmic-bench P2
            # template copied into the data tree.
            'daq_config_template_path': DREAM_CFG_TEMPLATE,
            # Directory where RunCtrl writes fdfs (fast local disk on the DAQ CPU).
            'run_directory': f'{self.base_out_dir}dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 16,  # RackTcm.cfg (expert beam config)
            'sample_period': 60,  # ns, sampling period (same as cosmic bench)
            # Sample-window offset, written as 'Feu * Dream * 12'. 32 (0x0020)
            # is the expert value in the optimized beam reference
            # EicP2Bt/P2B_TstBeam.cfg (2026-07-23 15:46). Earlier values: 45
            # (0x002D) in RackTcm.cfg, 40 (0x0028) in the self-trigger
            # P2B_SelfTcm.cfg. Setting it here overrides the template.
            'latency': 32,
            # Event-count limit ('Sys DaqRun Events'). The reference caps runs at
            # 500 events; our sub-runs are bounded by time ('Sys DaqRun Time',
            # written per run), so leave the count unlimited or every sub-run
            # stops after 500 events regardless of the requested duration.
            'daq_run_events': 0,   # 0 = infinite
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode.
            'zero_suppress': True,   # ZS mode, matching RackTcm.cfg (beam)
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name
            'zs_check_sample': 1,  # Number of samples to read out beyond threshold crossing
            # On-FEU pedestal and common-mode subtraction ('Feu * Feu_RunCtrl_Pd'
            # / '_CM'). Both 1 in the expert's optimized beam reference
            # P2B_TstBeam.cfg, which runs them together with ZS: the FEU
            # subtracts the pedestal and the per-Dream common mode before
            # comparing against the ZS threshold, so zero suppression cuts on
            # real signal rather than on the pedestal level. Fed by the
            # PedThrRun phase at the start of each run.
            # NB: these are the ONLINE (FEU) flags. The offline decoder's own
            # common-noise subtraction is processor_config.py
            # 'common_noise_subtraction', which stays False — the data written
            # to disk is already subtracted, and subtracting twice would eat
            # signal.
            'pedestal_subtraction': True,
            'common_noise_subtraction': True,
            'zs_type': 'tpc',
            # Pedestals are taken ONCE, by a dedicated run_config_pedestals.py run
            # (all electrodes at 200 V), and reused for every beam run after it —
            # so beam runs do NOT re-run PedThrRun. dream_daq_control copies the
            # latest pedestals/<pedestals_*>/pedestals/*.prg into each sub-run dir
            # and points the cfg's per-FEU PdFile/ZsFile at them.
            # Re-take pedestals (python run_config_pedestals.py, then
            # daq_control.py with it) after ANY setup change: cabling, HV
            # operating point, n_samples, Pd/CM flags, or a template sync.
            'do_pedestal_threshold_run': False,  # Sys Action PedThrRun
            'do_trigger_threshold_run': False,   # Sys Action TrgThrRun
            'do_data_run': True,                 # Sys Action DataRun
            # Trigger mode (from TRIGGER_MODE): self-trigger gives used
            # connectors the 'Trg' Dream role (trigger-contributing AND read
            # out); external trigger gives them 'Dat'.
            'self_trigger': _SELF_TRIGGER,
            # TCM trigger-multiplicity window ('Sys Trg MultMoreThan/LessThan').
            # None = keep the template's values. The optimized beam reference
            # (P2B_TstBeam.cfg) is itself an external-trigger config and sets
            # 2/4, so we follow the expert rather than overriding. These knobs
            # stay here because the window is the first suspect if the first
            # beam run shows a trigger rate but no recorded events — setting
            # them to 0/8 opens it wide for a test.
            'trg_mult_more_than': None,
            'trg_mult_less_than': None,
            # Auto-select the active FEUs in the .cfg from the included detectors'
            # dream_feus maps (only P2 FEUs stay active; M3/trigger FEU lines are
            # commented out — the SPS trigger comes in externally on the TCM).
            'set_feus_from_detectors': True,
            # --- Simulation (SITE='local' only): instead of launching RunCtrl,
            # replay sample fdfs from sim_source_fdf_dir into the run directory.
            'simulate': SIMULATE,
            'sim_source_fdf_dir': f'{self.base_out_dir}sim_fdfs/',
            'sim_chunk_mb': 16,           # MB appended to each growing fdf per step
            'sim_chunk_interval': 10,     # seconds between append steps
            'sim_max_mb_per_file': 64,    # cap on replayed bytes per FEU file
        }

        self.processor_info = {
            'ip': _SITE_CFG['daq_host'],
            'port': 1200,
            'run_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'decoded_root_inner_dir': self.decoded_root_inner_dir,
            'decode_path': f'{RECONSTRUCTION_BUILD}decoder/decode',
            'detector_info_dir': self.detector_info_dir,
            'out_type': 'both',  # 'vec', 'array', or 'both'
            'on-the-fly_timeout': 2  # hours or None If running on-the-fly, time out and die after this time.
        }

        self.hv_control_info = {
            'ip': _SITE_CFG['daq_host'],
            'port': 1100,
        }

        self.hv_info = {
            'ip': _SITE_CFG['hv_ip'],
            'n_cards': _SITE_CFG['hv_n_cards'],
            'n_channels_per_card': 12,
            'run_out_dir': self.run_out_dir,
            'hv_monitoring': True,  # True to monitor HV during run, False to not monitor
            'monitor_interval': 1,  # Seconds between HV monitoring
            'simulate': SIMULATE,   # True -> hv_control uses FakeCAENHVController
        }

        # HV credentials: hv_creds.txt (username on line 1, password on line 2) next
        # to this file. Optional in simulation; required for the real CAEN crate.
        creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hv_creds.txt')
        if os.path.isfile(creds_path):
            with open(creds_path) as f:
                lines = f.readlines()
                self.hv_info['username'] = lines[0].strip()
                self.hv_info['password'] = lines[1].strip()
        else:
            self.hv_info['username'] = 'admin'
            self.hv_info['password'] = 'admin'
            if not SIMULATE:
                # stderr, NOT stdout: get_config_py.py runs this file and parses
                # its stdout as JSON for the GUI's Start Run confirmation, so a
                # warning on stdout breaks the button with "Expecting value:
                # line 1 column 1 (char 0)". Only fires when SIMULATE is False,
                # i.e. exactly on the real sps site where the GUI is used.
                print(f'WARNING: {creds_path} not found — using default admin/admin HV credentials.',
                      file=sys.stderr)

        # ----- Run schedule (built from module constants above) -----
        # NB: the Fe55 code schedule below only ramps the P2 detectors' mesh+drift
        # (SCAN_START). Beam runs (3 P2 + 2 uRWELL) are configured from the GUI
        # run builder, which sets every included detector's channels.
        def _operating_hvs():
            """{card: {channel: V}} at the beam operating point, ALL five detectors.

            Walks DET_HV role by role, so the uRWELL references (drift +
            resistive, no 'mesh') are included. The previous helper skipped any
            detector without a 'mesh' channel, which meant both uRWELLs would
            have sat unpowered through a beam run.
            """
            hvs = {}
            for det_name, det_hv in DET_HV.items():
                for role, (card, chan) in det_hv.items():
                    hvs.setdefault(str(card), {})[str(chan)] = OPERATING_HV[det_name][role]
            return hvs

        def _drift_scan_hvs(drift_v):
            """{card: {channel: V}} with the BEAM_DRIFT_SCAN_DETS drift set to
            drift_v and their mesh held at the operating point. Every other
            detector stays at OPERATING_HV — the uRWELL references (fixed
            tracking telescope) and the parked-off P2_IN (0 V).
            """
            hvs = {}
            for det_name, det_hv in DET_HV.items():
                roles = OPERATING_HV[det_name]
                for role, (card, chan) in det_hv.items():
                    scanned = det_name in BEAM_DRIFT_SCAN_DETS and role == 'drift'
                    volts = drift_v if scanned else roles[role]
                    assert 0 <= volts <= MAX_HV[det_name][role], (
                        f'{det_name} {role} drift-scan point {volts} V out of '
                        f'range (max {MAX_HV[det_name][role]} V)')
                    hvs.setdefault(str(card), {})[str(chan)] = volts
            return hvs

        def _scan_hvs(mesh_offset):
            """{card: {channel: V}} with every BEAM_SCAN_DETS detector's mesh AND
            drift stepped DOWN by mesh_offset, so each detector's drift gap is
            unchanged (isolates gain). Every other detector is held at its
            operating point: the uRWELL references (fixed tracking telescope) and
            the parked-off P2_IN (0 V — NOT stepped; it has no gain to scan and
            stepping its 0 V setpoint would go negative).
            """
            hvs = {}
            for det_name, det_hv in DET_HV.items():
                roles = OPERATING_HV[det_name]
                shift = mesh_offset if det_name in BEAM_SCAN_DETS else 0
                for role, (card, chan) in det_hv.items():
                    volts = roles[role] - shift
                    assert 0 <= volts <= MAX_HV[det_name][role], (
                        f'{det_name} {role} scan point {volts} V out of range '
                        f'(max {MAX_HV[det_name][role]} V)')
                    hvs.setdefault(str(card), {})[str(chan)] = volts
            return hvs

        def _2d_scan_hvs(mesh_v, drift_v):
            """{card: {channel: V}} with every BEAM_2D_SCAN_DETS detector's mesh
            set to mesh_v and drift to drift_v. Every other detector is held at
            its operating point: the uRWELL references (fixed tracking telescope)
            and P2_IN (drift-limited / parked off, so not scanned).

            Absolute values, not offsets: the 2D grid is defined on the (mesh,
            drift) plane, and the caller has already resolved the drift mode.
            """
            hvs = {}
            for det_name, det_hv in DET_HV.items():
                roles = OPERATING_HV[det_name]
                scanned = det_name in BEAM_2D_SCAN_DETS
                for role, (card, chan) in det_hv.items():
                    if scanned and role == 'mesh':
                        volts = mesh_v
                    elif scanned and role == 'drift':
                        volts = drift_v
                    else:
                        volts = roles[role]
                    assert 0 <= volts <= MAX_HV[det_name][role], (
                        f'{det_name} {role} 2D scan point {volts} V out of range '
                        f'(max {MAX_HV[det_name][role]} V)')
                    hvs.setdefault(str(card), {})[str(chan)] = volts
            return hvs

        def _2d_scan_sub_runs():
            """2D drift x mesh map: at each drift point, a full mesh scan.

            Sub-run names are dm_<k>_<j>_m<mesh>_d<drift>, with k the drift
            (outer) index and j the mesh (inner) index, so 'dm_03_*' globs one
            drift point's whole mesh scan — the analysis side selects sub-runs
            with --subruns-glob. mesh/drift in the name are the scanned
            detectors' values (identical across BEAM_2D_SCAN_DETS).
            """
            out = []
            # Reference mesh for the inner axis: the scanned detectors share an
            # operating mesh, so take the first one's.
            mesh_0 = OPERATING_HV[BEAM_2D_SCAN_DETS[0]]['mesh']
            for k in range(BEAM_2D_DRIFT_POINTS):
                drift_k = BEAM_2D_DRIFT_START_V + k * BEAM_2D_DRIFT_STEP_V
                for j in range(BEAM_2D_MESH_POINTS):
                    mesh_off = j * BEAM_2D_MESH_STEP_V
                    mesh_v = mesh_0 - mesh_off
                    # follow_mesh: drift tracks mesh down, holding this outer
                    # point's gap (drift - mesh) constant across the inner scan.
                    # absolute: drift stays put, so the gap grows as mesh drops.
                    drift_v = (drift_k - mesh_off
                               if BEAM_2D_DRIFT_MODE == 'follow_mesh' else drift_k)
                    out.append({
                        'sub_run_name': f'dm_{k:02d}_{j:02d}_m{mesh_v}_d{drift_v}',
                        'run_time': BEAM_2D_SUBRUN_MIN,
                        'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),
                        'hvs': _2d_scan_hvs(mesh_v, drift_v),
                    })
            return out

        def _drift_scan_sub_runs():
            """Drift scan: mesh fixed, drift stepped UP over BEAM_DRIFT_SCAN_DETS."""
            out = []
            for p in range(BEAM_DRIFT_SCAN_POINTS):
                drift_v = BEAM_DRIFT_SCAN_START_V + p * BEAM_DRIFT_SCAN_STEP_V
                out.append({
                    'sub_run_name': f'drift_{drift_v:03d}',
                    'run_time': BEAM_DRIFT_SCAN_SUBRUN_MIN,
                    'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),
                    'hvs': _drift_scan_hvs(drift_v),
                })
            return out

        def _mesh_scan_sub_runs():
            """Mesh scan: drift gap fixed, mesh+drift stepped DOWN together."""
            out = []
            for i in range(BEAM_SCAN_NOMINAL_SUBRUNS):
                out.append({
                    'sub_run_name': f'nominal_{i:02d}',
                    'run_time': BEAM_SCAN_SUBRUN_MIN,
                    'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),
                    'hvs': _scan_hvs(0),
                })
            for p in range(1, BEAM_SCAN_POINTS + 1):
                off = p * BEAM_SCAN_MESH_STEP_V
                out.append({
                    'sub_run_name': f'meshscan_{p:02d}_midout{OPERATING_HV["P2_MID"]["mesh"] - off}',
                    'run_time': BEAM_SCAN_SUBRUN_MIN,
                    'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),
                    'hvs': _scan_hvs(off),
                })
            return out

        self.sub_runs = []
        if P2IN_CHECK:
            # Single fixed-HV run: P2_IN at its check point, uRWELLs at operating,
            # P2_MID/P2_OUT off (see OPERATING_HV override above).
            self.sub_runs.append({
                'sub_run_name': f'p2in_check_m{OPERATING_HV["P2_IN"]["mesh"]}_d{OPERATING_HV["P2_IN"]["drift"]}',
                'run_time': P2IN_CHECK_MIN,
                'post_pause_s': 0,
                'hvs': _operating_hvs(),
            })
        elif BEAM_2D_SCAN:
            # Checked before the 1D scans: RUN_PLAN='drift_mesh_2d' leaves
            # BEAM_HV_SCAN/BEAM_DRIFT_SCAN False, but DAQ_BEAM_2D_SCAN=1 layered
            # on top of an exported DAQ_BEAM_*_SCAN=1 must still give the 2D map.
            self.sub_runs = _2d_scan_sub_runs()
        elif BEAM_DRIFT_SCAN and BEAM_HV_SCAN:
            # Full overnight plan in ONE run: every drift point, then every mesh
            # point. The two halves use disjoint sub-run names (drift_* vs
            # nominal_*/meshscan_*), so daq_control's .subrun_complete markers
            # and the resume logic work on the combined run unchanged.
            self.sub_runs = _drift_scan_sub_runs() + _mesh_scan_sub_runs()
        elif BEAM_DRIFT_SCAN:
            self.sub_runs = _drift_scan_sub_runs()
        elif BEAM_HV_SCAN:
            self.sub_runs = _mesh_scan_sub_runs()
        elif LATENCY_SCAN:
            # One sub-run per latency point, all at the beam operating point.
            # The per-sub-run 'latency' key overrides dream_daq_info['latency']
            # in dream_daq_control's {**dream_daq_info, **sub_run} merge.
            for lat in LATENCY_SCAN_VALUES:
                self.sub_runs.append({
                    'sub_run_name': f'latency_{lat:03d}',
                    'run_time': LATENCY_SUBRUN_MIN,  # Minutes
                    'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),
                    'hvs': _operating_hvs(),
                    'latency': lat,
                })
        elif HV_SCAN:
            # Fe55 mesh HV scan: per-detector setpoints, starting at the
            # operating point and stepping mesh+drift down together so the
            # drift-gap potential (drift − mesh) stays constant.
            for i in range(SCAN_POINTS):
                off = i * SCAN_STEP_V
                hvs, name_bits = {}, []
                for det_name, start in SCAN_START.items():
                    det_hv = DET_HV[det_name]
                    mesh_v, drift_v = start['mesh'] - off, start['drift'] - off
                    assert mesh_v <= start['mesh'] and drift_v <= start['drift'], \
                        f'{det_name} scan point above its maximum'
                    hvs.setdefault(str(det_hv['mesh'][0]), {})[str(det_hv['mesh'][1])] = mesh_v
                    hvs.setdefault(str(det_hv['drift'][0]), {})[str(det_hv['drift'][1])] = drift_v
                    name_bits.append(f'{det_name.rsplit("_", 1)[-1].lower()}{mesh_v}')
                self.sub_runs.append({
                    'sub_run_name': f'fe55_{i:02d}_mesh_' + '_'.join(name_bits),
                    'run_time': SCAN_SUBRUN_MIN,  # Minutes
                    'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),
                    'hvs': hvs,
                })
        else:
            for i in range(N_SUBRUNS):
                self.sub_runs.append({
                    'sub_run_name': f'beam_commissioning_{i:02d}',
                    'run_time': SUBRUN_MIN,  # Minutes
                    'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),  # pause after this sub-run (seconds)
                    'hvs': _operating_hvs(),
                })

        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        # P2_IN dropped from the readout 2026-07-24 (Alexandra) — under
        # investigation. It stays CABLED (its detector dict below is unchanged,
        # cfg Feu 3), so re-including it later needs only this list; its HV is
        # parked off in OPERATING_HV. Excluding it here drops FEU 3 from the .cfg
        # (get_active_feu_connectors → included_feus), cutting the readout to
        # FEUs 1/4/5. The external TCM trigger has no trigger_feu, so losing
        # FEU 3 does not touch the trigger/sync chain.
        if P2IN_CHECK:
            # Alive-check: read out P2_IN + the two uRWELL references only.
            self.included_detectors = ['EIC_uRWELL_front', 'P2_IN', 'EIC_uRWELL_back']
        else:
            # Full 5-plane telescope — P2_IN reinstated 2026-07-24.
            self.included_detectors = ['EIC_uRWELL_front', 'P2_IN', 'P2_MID',
                                       'P2_OUT', 'EIC_uRWELL_back']

        # Cabling confirmed at the beam 2026-07-22 (Alexandra). Cfg FEU numbers
        # are TCM input ports; Id/IP from RackTcm.cfg:
        #   cfg Feu 1 = Id 68  (.80)  -> both EIC uRWELL references
        #   cfg Feu 3 = Id 101 (.113) -> P2_IN
        #   cfg Feu 4 = Id 102 (.114) -> P2_MID
        #   cfg Feu 5 = Id 103 (.115) -> P2_OUT
        # P2: four connectors, each a bot/top Dream pair filling FEU Dream conn
        # 1-8 IN ORDER; all rotated_inverted. MID/OUT are cabled on connectors
        # 4-7; P2_IN is on 4,5,6,8 (connector 8 in place of 7, confirmed
        # 2026-07-24). The Dream-channel assignment is by index, so which physical
        # connector sits on which readout channel is correct for each.
        def _p2_dream_feus(feu, conns=(4, 5, 6, 7)):
            return {
                f'c_{conn}_{pos}': (feu, 2 * i + (1 if pos == 'bot' else 2))
                for i, conn in enumerate(conns) for pos in ('bot', 'top')
            }
        def _p2_orientation(conns=(4, 5, 6, 7)):
            return {
                f'c_{conn}_{pos}': 'rotated_inverted'
                for conn in conns for pos in ('bot', 'top')
            }
        # uRWELL x/y strips on cfg Feu 1 (Id 68): front on Dream conn 1-4, back
        # on 5-8. All eight uRWELL Dream connectors are cabled inverted (noticed
        # 2026-07-25); the earlier 'x normal / y inverted (front) / y rotated
        # (back)' mix was a bookkeeping error.
        #
        # This field records the PLUG orientation only. It is descriptive - no
        # analysis code reads it - and it does not say which of a view's two
        # connectors carries the low strips. Measured on drift_mesh_scan_1, three
        # of the four views (front x, back x, back y) have that pair order
        # interchanged with respect to the strip map and front y does not, so a
        # uniform 'inverted' must NOT be turned into a uniform mapping
        # correction. See ~/dylan/urw_analysis/ORDERING.md.
        def _urwell(feu, base):
            feus = {'x1': (feu, base), 'x2': (feu, base + 1),
                    'y1': (feu, base + 2), 'y2': (feu, base + 3)}
            orient = {key: 'inverted' for key in feus}
            return feus, orient
        _urwell_front_feus, _urwell_front_orient = _urwell(1, 1)
        _urwell_back_feus,  _urwell_back_orient  = _urwell(1, 5)

        self.detectors = [
            {
                'name': 'EIC_uRWELL_front',
                'description': 'EIC uRWELL front reference (z=0, first the beam '
                               'sees). FEU Id 68 (cfg Feu 1), Dream conn 1-4: '
                               'x1/x2=ch1/2, y1/y2=ch3/4.',
                'det_type': 'urw_inter',
                'resist_type': 'resistive',
                'bulked_from': '',
                'det_center_coords': {'x': 0, 'y': 0, 'z': DET_Z_MM['EIC_uRWELL_front']},
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},
                'hv_channels': DET_HV['EIC_uRWELL_front'],
                'dream_feus': _urwell_front_feus,
                'dream_feu_orientation': _urwell_front_orient,
            },
            {
                'name': 'P2_IN',
                'description': 'P2 telescope IN, upstream P2 (z=320 mm). '
                               'det2 in bulking order. FEU Id 101 (cfg Feu 3).',
                'det_type': 'P2',
                'resist_type': 'none',
                'bulked_from': 'Alex+Enzo',
                'det_center_coords': {'x': 0, 'y': 0, 'z': DET_Z_MM['P2_IN']},
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},
                'hv_channels': DET_HV['P2_IN'],
                'dream_feus': _p2_dream_feus(3, (4, 5, 6, 8)),
                'dream_feu_orientation': _p2_orientation((4, 5, 6, 8)),
            },
            {
                'name': 'P2_MID',
                'description': 'P2 telescope MID (z=630 mm). det1 in bulking '
                               'order. FEU Id 102 (cfg Feu 4).',
                'det_type': 'P2',
                'resist_type': 'none',
                'bulked_from': 'Alex+Enzo',
                'det_center_coords': {'x': 0, 'y': 0, 'z': DET_Z_MM['P2_MID']},
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},
                'hv_channels': DET_HV['P2_MID'],
                'dream_feus': _p2_dream_feus(4),
                'dream_feu_orientation': _p2_orientation(),
            },
            {
                'name': 'P2_OUT',
                'description': 'P2 telescope OUT, downstream P2 (z=940 mm). '
                               'det3 in bulking order. FEU Id 103 (cfg Feu 5).',
                'det_type': 'P2',
                'resist_type': 'none',
                'bulked_from': 'Alex+Enzo',
                'det_center_coords': {'x': 0, 'y': 0, 'z': DET_Z_MM['P2_OUT']},
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},
                'hv_channels': DET_HV['P2_OUT'],
                'dream_feus': _p2_dream_feus(5),
                'dream_feu_orientation': _p2_orientation(),
            },
            {
                'name': 'EIC_uRWELL_back',
                'description': 'EIC uRWELL back reference (z=1370 mm, last the '
                               'beam sees). FEU Id 68 (cfg Feu 1), Dream conn 5-8: '
                               'x1/x2=ch5/6, y1/y2=ch7/8.',
                'det_type': 'urw_strip',
                'resist_type': 'resistive',
                'bulked_from': '',
                'det_center_coords': {'x': 0, 'y': 0, 'z': DET_Z_MM['EIC_uRWELL_back']},
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},
                'hv_channels': DET_HV['EIC_uRWELL_back'],
                'dream_feus': _urwell_back_feus,
                'dream_feu_orientation': _urwell_back_orient,
            },
        ]

        if not self.write_all_detectors_to_json:
            self.detectors = [det for det in self.detectors if det['name'] in self.included_detectors]

        # Derive the active FEUs (and their used connectors) from the included detectors so
        # dream_daq_control can enable only those FEUs in the .cfg and set per-Dream roles.
        # Derived from the included subset explicitly so it works whether or not self.detectors
        # was already filtered above.
        if self.dream_daq_info.get('set_feus_from_detectors', False):
            feu_connectors = self.get_active_feu_connectors()
            if feu_connectors:
                self.dream_daq_info['included_feus'] = sorted(feu_connectors)
                self.dream_daq_info['feu_connectors'] = feu_connectors
                # External trigger on the TCM (like nTof) — no dedicated trigger FEU.
                self.dream_daq_info['trigger_feu'] = None
            else:
                print('set_feus_from_detectors is on but no included detector has dream_feus; '
                      'leaving the template FEU selection unchanged.')

        # --- GUI override (config/gui_run_config.json) ---
        # Pure additive override: when the GUI file is absent / disabled /
        # unparseable, gui_run_config.load() returns None and NOTHING below runs,
        # so this config is byte-identical to the code defaults. Imported lazily
        # to avoid an import cycle (gui_run_config imports this module).
        try:
            import gui_run_config as _gui_mod
            _gui = _gui_mod.load()
        except Exception as _gui_err:
            _gui, _gui_mod = None, None
            print(f'GUI run config load failed, using code defaults: {_gui_err}')
        if _gui:
            self.run_name = _gui.get('run_name', self.run_name)
            self.gas = _gui.get('gas', self.gas)
            self.operator = _gui.get('operator', '')
            self.notes = _gui.get('notes', '')
            self.detectors, self.included_detectors = _gui_mod.build_detectors(_gui)
            self.sub_runs = _gui_mod.build_sub_runs(_gui)

            # Trigger mode: follow the GUI so the dream template + self_trigger
            # role selection track it (external -> P2TB.cfg / Dat, self ->
            # P2SelfTrigger.cfg / Trg).
            _tm = _gui.get('trigger_mode', TRIGGER_MODE)
            if _tm in ('external', 'self'):
                TRIGGER_MODE = _tm
                _SELF_TRIGGER = (_tm == 'self')
                # Must stay in step with the module-level _DREAM_TEMPLATE_FILE
                # map above — this branch used to pin the retired RackTcm.cfg,
                # so any GUI-driven run silently took the drifted template.
                _DREAM_TEMPLATE_FILE = {'self': 'P2SelfTrigger.cfg',
                                        'external': 'P2B_Beam.cfg'}[_tm]
                DREAM_CFG_TEMPLATE = _SITE_CFG.get(
                    'dream_cfg_template',
                    f'{BASE_DATA_DIR}dream_config/{_DREAM_TEMPLATE_FILE}')
                self.dream_daq_info['self_trigger'] = _SELF_TRIGGER
                self.dream_daq_info['daq_config_template_path'] = DREAM_CFG_TEMPLATE
                self.trigger = ('Fe55 self trigger via TCM multiplicity' if _SELF_TRIGGER
                                else 'SPS external scintillator coincidence via TCM')

            # Re-derive the run-name-dependent paths so data lands in the GUI's
            # run directory (run_name was set from 'run_1' earlier).
            self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
            self.dream_daq_info['run_directory'] = f'{self.base_out_dir}dream_run/{self.run_name}/'
            self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
            self.processor_info['run_dir'] = f'{self.run_out_dir}'
            self.hv_info['run_out_dir'] = self.run_out_dir

            # Recompute the active FEUs from the GUI detectors (same logic as above).
            if self.dream_daq_info.get('set_feus_from_detectors', False):
                feu_connectors = self.get_active_feu_connectors()
                if feu_connectors:
                    self.dream_daq_info['included_feus'] = sorted(feu_connectors)
                    self.dream_daq_info['feu_connectors'] = feu_connectors
                    self.dream_daq_info['trigger_feu'] = None

    def get_active_feu_connectors(self):
        """Map each FEU used by the included detectors to the sorted list of its used connectors.

        Each dream_feus value is a (feu_number, connector) tuple. Connectors are 1-based (1..8) and
        correspond to FEU Dream indices 0..7 (Dream index = connector - 1). Detectors without a
        dict-valued dream_feus map carry no FEU/connector numbers and are skipped. Restricted to
        included_detectors so it is correct even when self.detectors still holds the full list.
        """
        included = [det for det in self.detectors if det['name'] in self.included_detectors]
        feu_connectors = {}
        for det in included:
            dream_feus = det.get('dream_feus')
            if not isinstance(dream_feus, dict):
                continue
            for mapping in dream_feus.values():
                if isinstance(mapping, (tuple, list)) and len(mapping) >= 2:
                    feu, connector = int(mapping[0]), int(mapping[1])
                    feu_connectors.setdefault(feu, set()).add(connector)
        return {feu: sorted(conns) for feu, conns in feu_connectors.items()}

    def get_active_feus(self):
        """Sorted FEU numbers used by the included detectors (keys of get_active_feu_connectors)."""
        return sorted(self.get_active_feu_connectors())


if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'
    os.makedirs(out_run_dir, exist_ok=True)

    config_name = 'run_config_beam.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    # Schedule summary — sanity-check timing and the HV setpoints.
    run_min = sum(sr['run_time'] for sr in config.sub_runs)
    n_sub = len(config.sub_runs)
    total_h = run_min / 60
    print(f'Site: {SITE}  (simulate={SIMULATE})')
    print(f'Base data dir: {BASE_DATA_DIR}')
    print(f'Trigger mode: {TRIGGER_MODE}  (self_trigger={_SELF_TRIGGER})')
    print(f'Dream template: {DREAM_CFG_TEMPLATE}')
    print(f'Gas: {config.gas}')
    print(f'Trigger: {config.trigger}')
    if BEAM_HV_SCAN or BEAM_DRIFT_SCAN or BEAM_2D_SCAN:
        inv = {}
        for d, m in DET_HV.items():
            for role, (cd, ch) in m.items():
                inv[(str(cd), str(ch))] = (d, role)
        print(f'RUN PLAN: {RUN_PLAN}')
        if BEAM_2D_SCAN:
            _last_drift = (BEAM_2D_DRIFT_START_V
                           + (BEAM_2D_DRIFT_POINTS - 1) * BEAM_2D_DRIFT_STEP_V)
            _mesh_0 = OPERATING_HV[BEAM_2D_SCAN_DETS[0]]['mesh']
            _last_mesh = _mesh_0 - (BEAM_2D_MESH_POINTS - 1) * BEAM_2D_MESH_STEP_V
            print(f'BEAM 2D DRIFT x MESH SCAN: '
                  f'{BEAM_2D_DRIFT_POINTS} drift x {BEAM_2D_MESH_POINTS} mesh = '
                  f'{BEAM_2D_DRIFT_POINTS * BEAM_2D_MESH_POINTS} points on '
                  f'{BEAM_2D_SCAN_DETS}, {BEAM_2D_SUBRUN_MIN} min each')
            print(f'  drift (outer): {BEAM_2D_DRIFT_START_V}->{_last_drift} V '
                  f'x +{BEAM_2D_DRIFT_STEP_V} V')
            print(f'  mesh  (inner): {_mesh_0}->{_last_mesh} V '
                  f'x -{BEAM_2D_MESH_STEP_V} V')
            print(f'  drift mode: {BEAM_2D_DRIFT_MODE} — '
                  + ('drift follows mesh down, so the drift gap is CONSTANT '
                     'along each inner scan (axes orthogonal)'
                     if BEAM_2D_DRIFT_MODE == 'follow_mesh' else
                     'drift held per outer point, so the gap GROWS along each '
                     'inner scan (axes skewed)'))
        if BEAM_DRIFT_SCAN:
            last_drift = (BEAM_DRIFT_SCAN_START_V
                          + (BEAM_DRIFT_SCAN_POINTS - 1) * BEAM_DRIFT_SCAN_STEP_V)
            print(f'BEAM DRIFT SCAN: {BEAM_DRIFT_SCAN_POINTS} points '
                  f'{BEAM_DRIFT_SCAN_START_V}->{last_drift} V '
                  f'x +{BEAM_DRIFT_SCAN_STEP_V} V drift on {BEAM_DRIFT_SCAN_DETS}, '
                  f'{BEAM_DRIFT_SCAN_SUBRUN_MIN} min each')
        if BEAM_HV_SCAN:
            print(f'BEAM MESH-HV SCAN: {BEAM_SCAN_NOMINAL_SUBRUNS} nominal + '
                  f'{BEAM_SCAN_POINTS} points x -{BEAM_SCAN_MESH_STEP_V} V mesh '
                  f'on {BEAM_SCAN_DETS}, {BEAM_SCAN_SUBRUN_MIN} min each')
        for sr in config.sub_runs:
            bits = []
            for det in ('P2_IN', 'P2_MID', 'P2_OUT'):
                cd, ch = DET_HV[det]['mesh']
                cd2, ch2 = DET_HV[det]['drift']
                bits.append(f'{det}={sr["hvs"][str(cd2)][str(ch2)]}/{sr["hvs"][str(cd)][str(ch)]}')
            print(f'  {sr["sub_run_name"]:<28} ' + '  '.join(bits))
    elif LATENCY_SCAN:
        print(f'LATENCY SCAN: {LATENCY_SCAN_VALUES} '
              f'(run-config default {config.dream_daq_info["latency"]}), '
              f'{LATENCY_SUBRUN_MIN} min each')
    elif HV_SCAN:
        last = (SCAN_POINTS - 1) * SCAN_STEP_V
        for det, start in SCAN_START.items():
            print(f'HV SCAN {det}: mesh {start["mesh"]}->{start["mesh"] - last} V, '
                  f'drift {start["drift"]}->{start["drift"] - last} V '
                  f'({SCAN_POINTS} points x -{SCAN_STEP_V} V, gap {start["drift"] - start["mesh"]} V const)')
    else:
        print('Beam operating points:')
        for det, roles in OPERATING_HV.items():
            bits = '  '.join(f'{r} {v} V' for r, v in roles.items())
            gap = (f'   gap = {roles["drift"] - roles["mesh"]} V'
                   if 'mesh' in roles else '')
            print(f'  {det:<18} {bits}{gap}')
    # A combined plan mixes sub-run lengths, so only claim "N x M min" when the
    # schedule really is uniform.
    _times = sorted({sr['run_time'] for sr in config.sub_runs})
    _shape = (f'{n_sub} x {_times[0]} min' if len(_times) == 1
              else f'{n_sub} sub-runs of ' + '/'.join(str(t) for t in _times) + ' min')
    print(f'Sub-runs: {_shape} = {run_min} min (~{total_h:.2f} h + overhead)')
    print(f'Active FEUs: {config.get_active_feus()}')

    print('donzo')
