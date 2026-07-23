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
        'reconstruction_build': '/local/home/banco/mm_dream_reconstruction/build/',
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
# Run schedule — the three modes are checked in this order:
#
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

N_SUBRUNS = 2       # number of identical sub-runs (HV_SCAN False) — commissioning
SUBRUN_MIN = 2      # run time per sub-run (minutes) — short, this is a check run
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
    'P2_IN':  {'drift': 700, 'mesh': 490},   # gap = 210 V
    'P2_MID': {'drift': 700, 'mesh': 450},   # gap = 250 V
    'P2_OUT': {'drift': 700, 'mesh': 450},   # gap = 250 V  (see MAX_HV note)
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
MAX_HV = {
    'P2_IN':  {'drift': 700, 'mesh': 490},
    'P2_MID': {'drift': 700, 'mesh': 510},
    'P2_OUT': {'drift': 700, 'mesh': 450},
    'EIC_uRWELL_front': {'drift': 600, 'resist': 420},
    'EIC_uRWELL_back':  {'drift': 600, 'resist': 420},
}
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
        self.run_name = os.environ.get('DAQ_RUN_NAME') or 'run_1'
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
        self.power_off_hv_at_end = False  # Power off all CAEN HV at the end of the run.
        self.resume = False  # True to resume an existing run: skip sub-runs already marked .subrun_complete.
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
                print(f'WARNING: {creds_path} not found — using default admin/admin HV credentials.')

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

        self.sub_runs = []
        if LATENCY_SCAN:
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

        self.included_detectors = ['EIC_uRWELL_front', 'P2_IN', 'P2_MID',
                                   'P2_OUT', 'EIC_uRWELL_back']

        # Cabling confirmed at the beam 2026-07-22 (Alexandra). Cfg FEU numbers
        # are TCM input ports; Id/IP from RackTcm.cfg:
        #   cfg Feu 1 = Id 68  (.80)  -> both EIC uRWELL references
        #   cfg Feu 3 = Id 101 (.113) -> P2_IN
        #   cfg Feu 4 = Id 102 (.114) -> P2_MID
        #   cfg Feu 5 = Id 103 (.115) -> P2_OUT
        # P2: connectors 4-7, each a bot/top Dream pair filling FEU Dream conn
        # 1-8; all rotated_inverted.
        _p2_dream_feus = lambda feu: {
            f'c_{conn}_{pos}': (feu, 2 * (conn - 4) + (1 if pos == 'bot' else 2))
            for conn in (4, 5, 6, 7) for pos in ('bot', 'top')
        }
        _p2_orientation = {
            f'c_{conn}_{pos}': 'rotated_inverted'
            for conn in (4, 5, 6, 7) for pos in ('bot', 'top')
        }
        # uRWELL x/y strips on cfg Feu 1 (Id 68): front on Dream conn 1-4, back
        # on 5-8. Orientation: x normal; y inverted (front) / rotated (back).
        def _urwell(feu, base, y_orient):
            feus = {'x1': (feu, base), 'x2': (feu, base + 1),
                    'y1': (feu, base + 2), 'y2': (feu, base + 3)}
            orient = {'x1': 'normal', 'x2': 'normal', 'y1': y_orient, 'y2': y_orient}
            return feus, orient
        _urwell_front_feus, _urwell_front_orient = _urwell(1, 1, 'inverted')
        _urwell_back_feus,  _urwell_back_orient  = _urwell(1, 5, 'rotated')

        self.detectors = [
            {
                'name': 'EIC_uRWELL_front',
                'description': 'EIC uRWELL front reference (z=0, first the beam '
                               'sees). FEU Id 68 (cfg Feu 1), Dream conn 1-4: '
                               'x1/x2=ch1/2, y1/y2=ch3/4.',
                'det_type': 'uRWELL',
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
                'dream_feus': _p2_dream_feus(3),
                'dream_feu_orientation': dict(_p2_orientation),
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
                'dream_feu_orientation': dict(_p2_orientation),
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
                'dream_feu_orientation': dict(_p2_orientation),
            },
            {
                'name': 'EIC_uRWELL_back',
                'description': 'EIC uRWELL back reference (z=1370 mm, last the '
                               'beam sees). FEU Id 68 (cfg Feu 1), Dream conn 5-8: '
                               'x1/x2=ch5/6, y1/y2=ch7/8.',
                'det_type': 'uRWELL',
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
    if LATENCY_SCAN:
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
    print(f'Sub-runs: {n_sub} x {config.sub_runs[0]["run_time"] if n_sub else 0} min '
          f'= {run_min} min (~{total_h:.2f} h + overhead)')
    print(f'Active FEUs: {config.get_active_feus()}')

    print('donzo')
