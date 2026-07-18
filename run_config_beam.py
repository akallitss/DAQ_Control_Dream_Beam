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
            192.168.10.8/16, MTU 9000) is a private LAN where the DREAM FEUs
            get plugged in at SPS (it also carries the BANCO Xilinx readout at
            .113-.115). Fields marked TODO-SPS must be filled in once the
            beam-area cabling / HV crate details are known.

@author: Alexandra Kallitsopoulou (based on Dylan Neff's nTof config)
"""

import os

from run_config_base import RunConfigBase

# ---------------------------------------------------------------------------
# Site configuration — the ONE place to switch local test <-> SPS machine
# ---------------------------------------------------------------------------
SITE = os.environ.get('DAQ_SITE', 'local')  # 'local' or 'sps'; export DAQ_SITE=sps on banco

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
        'base_data_dir': '/local/home/banco/p2_sps_beam/',
        'daq_host': '192.168.10.8',                  # banco's IP on its DAQ LAN (enp2s0)
        'hv_ip': '192.168.10.81',                    # TODO-SPS: CAEN mainframe IP at SPS
        'hv_n_cards': 4,                             # TODO-SPS: number of cards in SPS crate
        'simulate': False,
        'reconstruction_build': '/local/home/banco/mm_dream_reconstruction/build/',  # TODO-SPS: clone + build on banco
    },
}

_SITE_CFG = SITES[SITE]
BASE_DATA_DIR = _SITE_CFG['base_data_dir']
RECONSTRUCTION_BUILD = _SITE_CFG['reconstruction_build']
SIMULATE = _SITE_CFG['simulate']

# ---------------------------------------------------------------------------
# Run schedule
#   N_SUBRUNS identical sub-runs of SUBRUN_MIN minutes at the nominal P2
#   operating point. Short values for local simulation; set beam values at SPS.
# ---------------------------------------------------------------------------
N_SUBRUNS = 2       # number of identical sub-runs
SUBRUN_MIN = 2      # run time per sub-run (minutes)
POST_SUBRUN_PAUSE_MIN = 0   # optional pause AFTER each sub-run (minutes); 0 = no pause

# Nominal P2 operating point (cosmic bench long-run values, Ar/Iso 95/5):
MESH_V = 440    # V, P2 mesh
DRIFT_V = 600   # V, P2 drift (drift gap = drift - mesh = 160 V)

# P2 HV channels: (card, channel). TODO-SPS: update once the SPS crate is cabled.
P2_HV = {
    'mesh': (1, 0),
    'drift': (1, 1),
}


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_1'
        self.base_out_dir = BASE_DATA_DIR
        self.data_out_dir = f'{self.base_out_dir}runs/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.raw_daq_inner_dir = 'raw_daq_data'
        self.decoded_root_inner_dir = 'decoded_root'
        self.detector_info_dir = f'{self.base_out_dir}config/detectors/'
        self.save_fdfs = True  # True to save FDF files, False to delete after decoding
        self.start_time = None
        self.process_on_fly = False  # False: processor_watcher handles processing independently
        self.power_off_hv_at_end = False  # True to power off all CAEN HV at the end of the run.
        self.resume = False  # True to resume an existing run: skip sub-runs already marked .subrun_complete.
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        self.gas = 'Ar/Iso 95/5'  # Gas type for run
        # self.gas = 'Ar/CO2/Iso 93/5/2'
        # self.gas = 'Ar/CF4 90/10'
        self.beam_type = 'sps_beam'
        # self.beam_type = 'cosmics'
        self.target_type = 'none'
        # External trigger arrives on the TCM (like nTof) — no dedicated trigger FEU.
        self.trigger = 'SPS external trigger (TCM)'  # TODO-SPS: describe actual trigger formation

        self.dream_daq_info = {
            'ip': _SITE_CFG['daq_host'],
            'port': 1101,
            # Cosmic bench P2 template (Sys DaqRun Trig Ext). TODO-SPS: copy to
            # Sps_P2.cfg and adjust trigger/FEU hardware lines for the beam area.
            'daq_config_template_path': f'{self.base_out_dir}dream_config/CosmicTb_P2.cfg',
            # Directory where RunCtrl writes fdfs (fast local disk on the DAQ CPU).
            'run_directory': f'{self.base_out_dir}dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 32,  # Same as cosmic bench P2
            'sample_period': 60,  # ns, sampling period (same as cosmic bench)
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode.
            'zero_suppress': False,  # True to run in zero suppression mode, False for full readout
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name
            'zs_check_sample': 1,  # Number of samples to read out beyond threshold crossing
            'pedestal_subtraction': False,
            'common_noise_subtraction': False,
            'zs_type': 'tpc',
            'do_pedestal_threshold_run': True,   # Sys Action PedThrRun
            'do_trigger_threshold_run': False,   # Sys Action TrgThrRun
            'do_data_run': True,                 # Sys Action DataRun
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
        self.sub_runs = []
        mesh_card, mesh_ch = P2_HV['mesh']
        drift_card, drift_ch = P2_HV['drift']
        for i in range(N_SUBRUNS):
            hvs = {}
            hvs.setdefault(str(mesh_card), {})[str(mesh_ch)] = MESH_V
            hvs.setdefault(str(drift_card), {})[str(drift_ch)] = DRIFT_V
            self.sub_runs.append({
                'sub_run_name': f'mesh_{MESH_V}V_drift_{DRIFT_V}V_{i:02d}',
                'run_time': SUBRUN_MIN,  # Minutes
                'post_pause_s': int(round(POST_SUBRUN_PAUSE_MIN * 60)),  # pause after this sub-run (seconds)
                'hvs': hvs,
            })

        # --- HV scan template (uncomment and adapt at the beam) ---
        # for mesh_v in range(430, 465, 5):
        #     self.sub_runs.append({
        #         'sub_run_name': f'mesh_{mesh_v}V_drift_{mesh_v + 160}V',
        #         'run_time': 20,
        #         'hvs': {str(mesh_card): {str(mesh_ch): mesh_v},
        #                 str(drift_card): {str(drift_ch): mesh_v + 160}},
        #     })

        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        self.included_detectors = ['P2_1']

        self.detectors = [
            {
                'name': 'P2_1',
                'description': 'Bulked at 11-6-26 with footprint on the mesh from the frame gluing',
                'det_type': 'P2',
                'resist_type': 'none',
                'bulked_from': 'Alex+Arnaud',
                'det_center_coords': {  # Center of detector. TODO-SPS: beam-line survey coordinates
                    'x': 0,  # mm
                    'y': 0,  # mm
                    'z': 0,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 0,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'mesh': P2_HV['mesh'],
                    'drift': P2_HV['drift'],
                },
                # Same connector cabling as the cosmic bench.
                'dream_feus': {
                    'c_1_bot': (3, 1),
                    'c_1_top': (3, 2),
                    'c_2_bot': (3, 3),
                    'c_2_top': (3, 4),
                    'c_3_bot': (3, 5),
                    'c_3_top': (3, 6),
                    'c_4_bot': (3, 7),
                    'c_4_top': (3, 8),
                    'c_5_bot': (4, 1),
                    'c_5_top': (4, 2),
                    'c_6_bot': (4, 3),
                    'c_6_top': (4, 4),
                    'c_7_bot': (4, 5),
                    'c_7_top': (4, 6),
                    'c_8_bot': (4, 7),
                    'c_8_top': (4, 8),
                    'c_9_bot': (6, 1),
                    'c_9_top': (6, 2),
                    'c_10_bot': (6, 3),
                    'c_10_top': (6, 4),
                },
                'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                    'c_1_bot': 'rotated_inverted',
                    'c_1_top': 'rotated_inverted',
                    'c_2_bot': 'rotated_inverted',
                    'c_2_top': 'rotated_inverted',
                    'c_3_bot': 'rotated_inverted',
                    'c_3_top': 'rotated_inverted',
                    'c_4_bot': 'rotated_inverted',
                    'c_4_top': 'rotated_inverted',
                    'c_5_bot': 'rotated_inverted',
                    'c_5_top': 'rotated_inverted',
                    'c_6_bot': 'rotated_inverted',
                    'c_6_top': 'rotated_inverted',
                    'c_7_bot': 'rotated_inverted',
                    'c_7_top': 'rotated_inverted',
                    'c_8_bot': 'rotated_inverted',
                    'c_8_top': 'rotated_inverted',
                    'c_9_bot': 'rotated_inverted',
                    'c_9_top': 'rotated_inverted',
                    'c_10_bot': 'rotated_inverted',
                    'c_10_top': 'rotated_inverted',
                },
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
    print(f'Gas: {config.gas}')
    print(f'P2 mesh: {MESH_V} V   drift: {DRIFT_V} V   (gap = {DRIFT_V - MESH_V} V)')
    print(f'Sub-runs: {n_sub} x {SUBRUN_MIN} min = {run_min} min (~{total_h:.2f} h + overhead)')
    print(f'Active FEUs: {config.get_active_feus()}')

    print('donzo')
