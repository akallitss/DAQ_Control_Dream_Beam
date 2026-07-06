# P2 SPS Beam DAQ Control

DAQ control for the P2 detectors at the SPS beam test. Forked from the nTof x17
DAQ (Dylan Neff, https://github.com/Dyn0402); same architecture, reconfigured
for P2 with an external trigger arriving on the TCM. It:

- drives the **DREAM RunCtrl** DAQ through a schedule of sub-runs (HV points ×
  run time) defined in `run_config_beam.py`,
- sets and **monitors the CAEN HV** crate (ramp check, per-second logging to
  `hv_monitor.csv`, live plot in the GUI),
- **processes fdf files on the fly** into decoded ROOT trees, per-FEU hit
  trees, and FEU-combined hit trees (C++ executables from
  `mm_dream_reconstruction`),
- produces **online QA plots** per sub-run (occupancy, event rate, amplitudes,
  timing, waveform mean/RMS maps),
- serves a **Flask web GUI** (port 5001) to start/stop runs, toggle the
  watchers, follow run/sub-run progress, and browse HV plots and QA figures.

A full **simulation mode** (fake CAEN + fake Dream DAQ replaying real P2 fdfs)
lets you test the entire chain on any machine with no hardware attached.

---

## 1. What the code is doing

Cooperating processes, each in its own tmux session (created by
`start_servers.sh`):

| process | role |
|---|---|
| `daq_control.py <config.json>` | orchestrator: loops over sub-runs; per sub-run asks hv_control to ramp+monitor, then dream_daq_control to take data; writes `.subrun_complete` markers (resume support) |
| `hv_control.py` | TCP server, port 1100: sets CAEN channels, waits for ramp, monitor thread → `hv_monitor.csv` per sub-run |
| `dream_daq_control.py` | TCP server, port 1101: builds the run `.cfg` from the template (run time, samples, ZS, active FEUs from the detector map), fetches latest pedestals, launches `RunCtrl`, copies fdfs to the run output dir on the fly |
| `processor_watcher.py` | watches `runs/` for new stable fdf groups: `decode` → `analyze_waveforms` (hit trees) → `combine_feus_hits`; also decodes pedestal fdfs |
| `qa_watcher.py` | watches `combined_hits_root/` and runs `p2_daq_analysis/detector_qa.py` per sub-run → PNGs served by the GUI's Online QA tab |
| `pedestal_watcher.py` | QA for pedestal runs (`scripts/pedestal_strip_check.py`) |
| `backup_watcher.py` | optional rsync of the data tree to EOS |
| `flask_app/` | web GUI on port 5001 |

Everything is configured from **`run_config_beam.py`**: the `SITE` switch
(`'local'` = simulation, `'sps'` = real hardware), the P2 detector definition
(FEU/connector cabling, HV channels), and the sub-run schedule. Running it
(`python run_config_beam.py`) writes `config/json_run_configs/run_config_beam.json`,
which is what `daq_control.py` and the GUI actually consume.

Data flow per sub-run, under `<base_data_dir>/runs/<run>/<subrun>/`:

```
raw_daq_data/*.fdf  →  decoded_root/*.root  →  hits_root/*_hits.root
                    →  combined_hits_root/*_feu-combined_hits.root
                    →  <base_data_dir>/analysis/<run>/<subrun>/P2_1/*.png   (QA)
raw_daq_data/hv_monitor.csv, dream_daq.log, pedestal_run.txt, run .cfg copy
```

## 2. Requirements

**Python** ≥ 3.10 (tested with 3.12), in a venv at `.venv/` in this repo:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` covers everything: Flask + socketio (GUI), numpy/pandas,
psutil, `caen-hv-py` (real HV crate), and the QA stack (uproot, awkward,
matplotlib, scipy) — the QA runs with this same venv.

**System tools**: `tmux` (sessions), `rsync`/`ssh` (backup watcher only),
`cmake` + a C++17 compiler + **ROOT** (to build `mm_dream_reconstruction`).

**Only for real data taking** (not needed in simulation):
- the Dream DAQ software with `RunCtrl` in `PATH` on the DAQ machine,
  network access to the FEUs/TCM,
- network access to the CAEN HV mainframe + credentials,
- a pedestal run taken before the first data run (`run_config_pedestals.py`).

## 3. Setting up a new machine

Clone the two required repositories:

```bash
git clone https://github.com/akallitss/DAQ_Control_Dream_Beam.git
git clone git@github.com:akallitss/mm_dream_reconstruction.git
```

(`mm_dream_reconstruction` provides the `decode`, `analyze_waveforms`,
`combine_feus_hits` executables. The offline analysis repo
`P2_basket_analysis` is *not* needed for the DAQ — online QA is self-contained
in `p2_daq_analysis/`.)

Then:

```bash
# 1. Build the reconstruction (needs ROOT: source thisroot.sh first)
cd mm_dream_reconstruction && mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release && make -j4

# 2. Python environment
cd ../../DAQ_Control_Dream_Beam
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Data area (paths come from the SITE entry in run_config_beam.py)
mkdir -p <base_data_dir>/{runs,pedestals,dream_run,sim_fdfs}
mkdir -p <base_data_dir>/dream_config <base_data_dir>/config/detectors
# copy CosmicTb_P2.cfg (+ Grace_* files) into dream_config/
# copy p2.json / p2_map.txt into config/detectors/
# for simulation only: put a few real P2 datrun fdfs into sim_fdfs/
# copy at least one pedestal run dir (pedestals_MM-DD-YY_HH-MM-SS/) into pedestals/

# 4. HV credentials (real crate only; gitignored)
printf 'USERNAME\nPASSWORD\n' > hv_creds.txt

# 5. Point run_config_beam.py at this machine: edit the SITES dict
#    (base_data_dir, daq_host, hv_ip, reconstruction_build) and set SITE.

# 6. Regenerate the JSON configs
python run_config_beam.py
python processor_config.py
python qa_config.py
python pedestal_qa_config.py
```

Reference copies of the P2 dream config, detector jsons, pedestals and sample
fdfs live on the rays machine (`sedipcaa28`, ssh alias `rays_daplxa`) under
`/mnt/cosmic_data/P2/` and `/mnt/cosmic_data/config/detectors/`.

## 4. How to run

### Simulation (no hardware; `SITE = 'local'`)

The fake CAEN (`sim/fake_caen.py`) ramps voltages with realistic noise and the
fake Dream DAQ (`sim/fake_dream_daq.py`) replays sample fdfs as growing files,
so every downstream component behaves exactly as in a real run.

```bash
source .venv/bin/activate
python run_config_beam.py        # regenerate the run config JSON
./start_servers.sh               # tmux: hv_control, dream_daq, daq_control, flask
# open http://localhost:5001 → Start Processor, Start QA Watcher, Start Run
```

Headless alternative (no GUI): start `hv_control.py`, `dream_daq_control.py`,
`processor_watcher.py config/processor_config.json` and
`qa_watcher.py config/qa_config.json` in four terminals, then

```bash
python daq_control.py run_config_beam.json
```

Watch for: HV ramp lines → `[sim daq]` file lines → `[watcher] ... [combine]`
→ `[qa] P2_1 — saved to .../analysis/...`.

### Real run (at the beam; `SITE = 'sps'`)

Same commands. Define the sub-run schedule (HV points, minutes per sub-run) in
`run_config_beam.py`, run it to regenerate the JSON, then Start Run from the
GUI. Useful controls:

- `bash_scripts/stop_sub_run.sh` — end the current sub-run, continue with the next
- `bash_scripts/stop_run.sh` — end the whole run cleanly
- GUI "Pause after subrun" — hold HV and wait at the next sub-run boundary
- `resume = True` in the config — rerun only sub-runs without `.subrun_complete`
- `python iterate_run_num.py` — bump `run_name` to the next free `run_N`
  (the GUI's Start Run does this automatically)

Pedestals: take one with `python run_config_pedestals.py` + Start Run before
the first data run; `pedestals: 'latest'` in the config picks the newest one
automatically.

## 5. What must be adapted for the SPS beam

All marked `TODO-SPS` in the code. In `run_config_beam.py`:

1. `SITE = 'sps'`, and in `SITES['sps']`:
   - `base_data_dir` — the data disk on the SPS DAQ machine,
   - `daq_host` — IP of the machine running hv_control/dream_daq_control
     (127.0.0.1 if everything runs on one box),
   - `hv_ip`, `hv_n_cards` — the CAEN mainframe at SPS (unknown until arrival),
   - `reconstruction_build` — path of the `mm_dream_reconstruction/build/` there.
2. `P2_HV` — mesh/drift (card, channel) after cabling the SPS crate; scint/PMT
   bias channels can be added as extra detectors if HV-powered.
3. `det_center_coords` / `det_orientation` of P2_1 — beam-line survey values.
4. Sub-run schedule + `run_name`, gas, `trigger` description.

Outside `run_config_beam.py`:

5. `<base_data_dir>/dream_config/CosmicTb_P2.cfg` — update per-FEU
   `Feu_RunCtrl_Id` / `NetChan_Ip` for the beam-area network. The trigger is
   already `Sys DaqRun Trig Ext`; the external SPS trigger goes into the TCM,
   and the M3 trigger-FEU lines are dropped automatically
   (`set_feus_from_detectors` keeps only FEUs 3/4/6 used by P2_1). Rename to
   e.g. `Sps_P2.cfg` and update `daq_config_template_path` if you want.
6. `hv_creds.txt` — real CAEN credentials.
7. `backup_config.py` — EOS destination, CERN principal, gpg pass file
   (only if using the backup watcher).
8. Take fresh pedestals at the beam before the first run.

The step-by-step transition plan and implementation status are in
`SPS_P2_TRANSITION_PLAN.txt`.

## Docs

In `docs/`: [pedestal QA watcher & GUI tab](docs/pedestal_qa.md),
[HV plot zoom persistence](docs/hv_plot_zoom_persistence.md),
[N1081B trigger logger](docs/n1081b_trigger_logger.md),
[GUI refactor notes](docs/gui_refactor_notes.md).
