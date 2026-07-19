---
name: p2-sps-beam-daq-setup
description: "State of the P2 SPS beam DAQ transition — SITE switch, simulation mode, data locations, remaining TODO-SPS items"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7c0af0b2-c6b0-4673-b9d0-8efb706c624c
  modified: 2026-07-19T15:55:10.861Z
---

DAQ_Control_Dream_Beam (fork of Dylan Neff's nTof x17 DAQ) was transitioned on
2026-07-06 for Alexandra's P2 detector SPS beam test and verified end-to-end
locally in full simulation (fake CAEN HV + fake Dream DAQ replaying real P2
fdfs → decode → hit trees → combine → QA plots → Flask GUI).

Key facts:
- `run_config_beam.py` has a `SITE = 'local' | 'sps'` switch at the top; all
  IPs/paths/simulate flags come from the `SITES` dict. Keep the filename —
  5+ files import it.
- Local test data tree: `~/Documents/PostDocSaclay/data/sps_p2_test/`
  (runs/, pedestals/, dream_config/CosmicTb_P2.cfg, sim_fdfs/, config/detectors/).
  The 2026-07-06 test run left ~16 GB there (dream_run/ 6.3G is a redundant
  copy, safe to delete).
- Simulators: `sim/fake_caen.py` (used when hv_info['simulate'] or ip=='sim'),
  `sim/fake_dream_daq.py` (used when dream_daq_info['simulate']).
- QA runs from this repo: `p2_daq_analysis/detector_qa.py` with the repo's own
  .venv (uproot/matplotlib/scipy added); qa_watcher got generic
  `analysis_dir`/`qa_script_rel_path`/`qa_python_rel_path` config keys with
  nTof defaults so Dylan's setup still works.
- One P2 detector (P2_1), cosmic-bench FEU cabling (FEUs 3/4/6), external
  trigger into the TCM (trigger_feu=None), 32 samples @ 60 ns, nominal
  mesh 440 V / drift 600 V.
- Remaining `TODO-SPS` items (fill at CERN): HV crate IP/cards + P2_HV
  card/channels, SPS data disk + DAQ host in SITES['sps'], reconstruction
  build path on the DAQ machine, dream .cfg FEU Id/IP + trigger lines,
  backup_config EOS destination.
- Sample P2 data/pedestals came from the rays machine (ssh alias
  `rays_daplxa` = sedipcaa28.extra.cea.fr, works with keys; alias
  `cosmic_bench` does NOT authenticate), path /mnt/cosmic_data/P2/.
- Plan + status: `SPS_P2_TRANSITION_PLAN.txt` in the repo root. Changes were
  left uncommitted (user didn't ask for a commit).

SPS beam-test OFFLINE ANALYSIS (2026-07-19, separate repo
~/Documents/PostDocSaclay/P2_basket_analysis/sps_beam_analysis/, sibling of
cosmic_bench_analysis, imports its shared core p2_io/p2_mapping/p2_sparks):
7 stages built + validated. PLAN.md there. No M3 at SPS (dropped M3 stages);
external scintillator trigger via TCM; beam 150/80 GeV mu/pi.
- 24_event_sync_qa, 20_beam_spectra (Landau MPV via scipy moyal, NOT Fe55
  Gaussian), 21_telescope_align (translation+rotation, no track fit),
  22_tag_probe_efficiency (Clopper-Pearson, majority tag min_tag, no BANCO
  tracker), 23_beam_profile (spill/pileup, --beam-csv overlay), build_beam_pdf.
- sps_config.py run registry: fe55_telescope (LaCie Fe55 data) and run19_sps25.
  Pad map = Detector_Mapping/P2_BASKET/P2_BASKET_mapping.csv (1280 pads/10
  connectors, channel_id-keyed, detector-intrinsic, reused per detector);
  FEU->connector wiring per-run from run_config.json dream_feus.
- run_19 (Nov TbSPS25, /local/home/ak271430/.../data/nov25_run19_test):
  large P2 'P2_SPS25' = physical connectors 1-4 on FEU 5 (512 ch = channel_id
  0-511, sectors 0-3, mapping covers it; run_config.json hand-built here,
  has_geometry=True). FEUs 1-4 = other small detectors. Stage 24: 5-FEU TCM
  sync PASS (all aligned, ~97-98% common). Stage 20: real MIP Landau,
  channel-space MPV 1035 ADC robust; geometry MPV 223 ADC broad because FEU5
  only caught beam edge (5.8% hits) + orientation 'rotated_inverted'
  UNCONFIRMED for Nov. Stages 21/22 N/A (single plane). For the real beamtest
  (3 planes P2_IN/MID/OUT) everything runs from the live run_config.json with
  no code change.
- GIT: GitHub (akallitss) is the source of truth for BOTH repos; CEA GitLab
  (remotes 'cea'/'extended' on the analysis repo) are MIRRORS — push to
  origin (GitHub SSH, works), NOT cea (GitLab HTTPS, no creds). Remotes:
  DAQ origin=git@github.com:akallitss/DAQ_Control_Dream_Beam.git;
  analysis origin=git@github.com:akallitss/P2_basket_analysis.git.
  Both banco AND laptop authenticate to GitHub as akallitss over SSH.
  2026-07-19 analysis commit 51958f3 pushed to GitHub origin.
- BANCO NOW TRACKS GITHUB (2026-07-19, was rsync-drifted before): DAQ repo
  git reset --hard origin/main (HEAD 4e328c1; gitignored config JSONs
  survived); analysis repo replaced rsync copy with a real git clone
  (HEAD 51958f3). Workflow going forward: edit on laptop -> push origin ->
  git pull on banco (NOT rsync, which leaves banco git stale).
- CAMPAIGN DIR: SPS July-2026 H4 beam test writes to
  /local/home/banco/P2_data/TB_July2026_H4/ (switched from Fe55 2026-07-19,
  commit f8db264). Fe55 bench data stays under P2_data/Fe55/ (EOS-backed-up,
  untouched). Two path knobs, both now = TB_July2026_H4: (1) base_data_dir in
  run_config_beam.py SITES['sps'] (DAQ), (2) P2_BASE in banco ~/.bashrc
  (analysis). All config JSONs regenerated + watchers (backup/processor/qa/
  pedestal) + flask restarted to TB; dream_config templates + config copied
  over. Verified: DAQ base dir, backup source_dir, analysis DATA_ROOT/
  ANALYSIS_ROOT all = TB_July2026_H4.
- Analysis RUNS ON BANCO (git clone ~/P2_basket_analysis). Paths ALIGNED
  with the DAQ via banco ~/.bashrc: P2_BASE=.../TB_July2026_H4,
  SPS_DATA_ROOT=$P2_BASE/runs, SPS_ANALYSIS_ROOT=$P2_BASE/analysis (= where
  GUI Analysis tab reads + backup watcher syncs to EOS),
  SPS_COSMIC_BENCH_ROOT=$P2_BASE/runs. Interactive banco:
  `SPS_RUN=run_N python <stage>.py live`. DAQ side: DAQ_SITE=sps in .bashrc.
  NOTE: banco non-interactive bashrc exits early, so these env vars apply to
  LOGIN/tmux shells; automated ssh 'cmd' runs still need explicit env (or
  bash -lic).
- P2TB.cfg (2026-07-19) = external-trigger beam dream template, in
  TB_July2026_H4/dream_config/ (banco) + laptop data tree. Built from
  P2SelfTrigger.cfg (KEEPS banco FEU Ids/IPs 101/102/103 @ .113/.114/.115)
  merged with the rays external DAQ config /mnt/cosmic_data/P2/dream_config/
  CosmicTb_P2.cfg: SysName P2SPS, DaqRun Trig Ext (was Slf), Mode Raw,
  topology Dat (was Trg), Trg Mult 2/4, Pd/CM/ZS 0/0/0, Main_Trig_Ovr
  16/20/28, InterPacket 100, UdpChan 4888, Dream regs 1/2/8/9/12 from rays,
  NbOfSamples 32.
- TRIGGER_MODE switch (commit 5182b13) in run_config_beam.py: 'external' (beam,
  P2TB.cfg, self_trigger=False, Dat roles) DEFAULT, or 'self' (Fe55,
  P2SelfTrigger.cfg, self_trigger=True, Trg roles) via env DAQ_TRIGGER=self.
  Template auto-resolved from <base>/dream_config/. Verified both modes on
  banco. So Fe55 self-trigger is still one env var away.
- BEAM_TODO.md (repo root, commit) = beam-time finalization checklist. Key
  open items: scintillator trigger into TCM + test Sys Trg Mult (2/4 inherited
  from rays cosmic — may need wide-open for external); LATENCY scan test run
  (Dream*12 / config latency, centre pulse in sample window); add 3rd station
  P2_IN wiring+HV+survey z; beam run schedule (replace Fe55 HV_SCAN); NXCALS
  access; fresh external-mode pedestals. Detectors still just P2_OUT/MID
  (FEUs 3/4) — P2_IN not yet added. sps_config is site-aware: env SPS_DATA_ROOT/SPS_ANALYSIS_ROOT/
  SPS_COSMIC_BENCH_ROOT override; banco auto-detect (/local/home/banco) →
  DATA_ROOT /local/home/banco/P2_data, ANALYSIS_ROOT /local/home/banco/
  P2_data/Analysis. 'live' registry entry: SPS_RUN=<run> selects any DAQ run
  under DATA_ROOT, wiring from its run_config.json. Verified: stage 24 on live
  Fe55 run_1 → ALIGNED. Beamtime usage on banco: SPS_DATA_ROOT=<base>/runs
  SPS_RUN=run_N .venv/bin/python <stage>.py live. Set SPS_ANALYSIS_ROOT=
  <base_data_dir>/analysis so outputs show in the GUI Analysis tab + get EOS
  backed up. Uses banco's DAQ .venv (uproot/awkward/scipy already there).
