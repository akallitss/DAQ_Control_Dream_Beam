---
name: rays-daq-deployment
description: "banco (dedippcq196) is the SPS DAQ computer — deployment state, disk/network facts, rays is Cosmic_Bench-only, machine roles"
metadata: 
  node_type: memory
  type: project
  originSessionId: 62da2cee-3ee5-42fc-8006-330e5520defe
---

Machine roles (settled 2026-07-18, after initially confusing the two):
- **rays** (sedipcaa28, ssh `rays_daplxa`, user usernsw, CentOS 7): cosmic
  bench machine, runs ONLY `Cosmic_Bench_DAQ_Control` (actively taking P2
  runs in tmux). It owns the bench FEU/HV subnet as 192.168.10.1. The
  2026-07-16 Dream_Beam deployment there was fully removed on 2026-07-18
  (repo, miniconda3_daq, /mnt/cosmic_data/P2/sps_daq_test) — verified the
  bench install was untouched.
- **banco** (dedippcq196, ssh `banco_daplxa`, user banco, Ubuntu 20.04): the
  SPS DAQ computer. DAQ_Control_Dream_Beam ([[p2-sps-beam-daq-setup]])
  deployed 2026-07-18 at `~/DAQ_Control_Dream_Beam`, venv from
  `~/miniconda3_daq/envs/py312` (conda-forge; base is 3.14 — use the py312
  env; `unset PYTHONPATH` first). Exact pinned requirements installed
  (numpy 2.4.6). Smoke test `DAQ_SITE=sps` passed. Commit 5cd43e5.

banco facts:
- NVMe OS disk: 1.4 GB/s direct writes — active runs go to
  `/local/home/banco/p2_sps_beam/` (runs/, pedestals/, dream_config/,
  config/detectors copied). Disk was 99% full; cleanup ongoing — see below.
- DAQ LAN: enp2s0, 192.168.10.8/16, MTU 9000 — a private LAN carrying the
  BANCO Xilinx readout (192.168.10.113-115). NOT connected to the bench
  FEU network despite same numbering; DREAM FEUs get plugged in at SPS.
  FEU/HV connectivity from banco is untestable until then.
- Bench FEU/HV facts (for reference, live on rays' subnet): FEU1/TCM .24,
  FEU3 .52, FEU4 .34, FEU6 .125, CAEN crate .81 (admin/admin, web on :80).
- SPS hardware on banco's LAN (verified 2026-07-18, commit 56e3348): DREAM
  FEUs at .113/.114/.115 = RunCtrl Ids 101/102/103 (source of truth:
  ~/Feu/.../Linux/bin/EicP2Bt/SelfTcm.cfg); CAEN crate at .199 —
  16 slots, 12-ch cards in slots 8 and 12 only, admin/admin works,
  hv_n_cards=13 (range must reach slot 12). Card 8 was live-biasing a P2
  detector (ch0 700 V drift, ch1 450 V mesh, ch2/ch3 300 V ?resists) —
  P2_HV now points at card 8; ch2/ch3 mapping still to confirm. The
  DREAM_Beam CosmicTb_P2.cfg (data tree, both local + banco) has FEU
  3/4/6 → Ids 101/102/103 @ .113/.114/.115.
- Intenso 4TB USB drive = WDC WD40NPJZ inside, FAT32 (4 GB file cap!),
  ~106 MB/s, SMR — backup only, never record onto it. Mount:
  `udisksctl mount -b /dev/sdd1` (needs interactive auth) →
  /media/banco/INTENSO. Holds beam_test_25_backup_2 (runs ≤160) and a
  p2_sps_beam skeleton. Also unmounted: internal 4TB Seagate ext4 (sda1),
  Transcend 1TB exFAT USB SSD (sdb1).
- Telescope (2026-07-18, commit 3f7a89d): two detectors. P2_OUT = HV card 8
  ch0 drift / ch1 mesh, det connectors 4-7 on FEU Id 103 (cfg Feu 6);
  P2_MID = card 8 ch2 drift / ch3 mesh, same pattern on FEU Id 102 (cfg
  Feu 4). c4_bot→feu ch1, c4_top→ch2 … c7_top→ch8. Per-detector HV
  setpoints still TBD (schedule uses shared MESH_V/DRIFT_V); orientations
  and survey coords TODO-SPS.
- mm_dream_reconstruction built on banco 2026-07-18: ROOT 6.32.02 binary
  distro at ~/opt/root_v6.32.02 (snap ROOT unusable — home outside /home;
  libtbb.so.2 from TBB 2020.3 dropped into its lib/), yaml-cpp 0.8.0 at
  ~/opt/yaml-cpp (headers only actually needed). Binaries have rpath —
  run without thisroot.sh. Remaining TODO-SPS: trigger lines in dream
  .cfg, per-detector HV values, orientations/survey.
- FIRST LIVE RUN 2026-07-18 (commit 2c03fb8): pedestal run
  test_daq_ped_P2_OUT_P2_MID passed end-to-end unattended — 200 V all 4
  channels, FEUs 4+5, 20.5 MB pedthr fdf + ped/thr .prg per FEU, HV left
  at 200 V. Ops facts: servers via start_servers.sh in tmux (hv_control/
  dream_daq/daq_control/flask_server); ~/.bashrc exports DAQ_SITE=sps and
  PATH with the Feu Linux/bin (RunCtrl). Launch: tmux send to daq_control
  "python daq_control.py run_config_pedestals.json"; regenerate json with
  DAQ_RUN_NAME=<name> python run_config_pedestals.py. Flask on :5001 is
  firewalled from outside — reach via ssh -L 5001:localhost:5001
  banco_daplxa. banco RunCtrl quirks fixed in dream_daq_control (commented
  FEU blocks, stale PdFile/ZsFile refs, interactive '***' prompt after
  PedThr — pedestal_run_watchdog SIGINTs it when .prg outputs complete).
  RunCtrl is setuid and invisible to ps/pgrep while running (check
  wchan/do_wait of the python parent instead).

Disk cleanup state (2026-07-18 evening): banco NVMe now 73%, 246G free.
- beam_test_2025 (runs 161-202): backed up to Intenso
  `beam_test_25_backup_3` (verified name+size, 6616 files) and DELETED
  from the NVMe. Runs ≤160 are in beam_test_25_backup_2.
- `/mnt/usbdrive` (304G, June 2023 P2-EIC beam test): backup to Intenso
  `beam_test_2023_usbdrive_backup/` launched (script
  /tmp/backup_usbdrive_2023.sh, done-marker /tmp/backup2023.done, 5 files
  >4GB stored as .partNN splits per README_SPLIT_FILES.txt there —
  FAT32). Delete /mnt/usbdrive only after verifying; that's the plan.
- Gotcha: never check "is rsync running" with pgrep -f over ssh with the
  pattern in the command line — it self-matches; use a done-marker file.
- Gotcha: banco interactive shells export ISEG SDK LD_LIBRARY_PATH/
  PYTHONPATH that shadow ROOT libs (decode dies with "symbol lookup
  error" in tmux, works in ssh shells — non-interactive bashrc exits
  early). start_servers.sh scrubs both since commit 281d623.
- banco: 62 GB RAM, only 1 GB swap, swappiness 60, no earlyoom/oomd.
  mem_guardian.py (commit 4e328c1, tmux session via start_servers.sh)
  kills the biggest allow-listed compute job (decode/analyze_waveforms/
  combine_feus_hits/QA) when MemAvailable <4 GB; protect-list vetoes the
  DAQ. Tested working. processor_watcher itself still has no in-app
  memory_kill_pct (qa/pedestal watchers do) — guardian is the backstop.
- run_19 (Nov-2025 SPS beam, TbSPS25) reconstruction 2026-07-19: staged
  2 HV points (drift_hv_-400/-450) to /local/home/banco/P2_data/nov25_test
  and ran via scoped config processor_config_nov25.json (tmux proc_nov25,
  one-shot — STOP it when done). External trigger, ZS, 16 samples, 5 FEUs
  = Ids 69/70/101/102/103 (101/102/103 are the SAME physical FEUs now on
  P2_OUT/MID). ~488k events/file. No run_config.json → detector wiring
  must be built from TbSPS25.cfg_cpy (saved in the run dir) for analysis.
- Fe55 campaign 2026-07-18 COMPLETE (data /local/home/banco/P2_data/Fe55/):
  200 V pedestal run pedestals_07-18-26_20-42-19 (verified clean hold),
  then 12-point mesh scan run_1 — OUT 420→365, MID 510→455, drift −5 V
  in step ([[p2-hv-conventions]]), 5 min/pt, self-trigger via TCM at
  Trg-role dreams, rate 142→38 Hz monotonic, 30 GB, all sub-runs carry
  pedestal_run.txt and analysis logs "Using pedestal" (no ZS bypass).
  HV powered off after (card 8 ch0-3). Source illuminated mostly P2_OUT
  (FEU3 ~143k primitives vs FEU4 ~500) — reposition if MID stats matter.
- Run policies (commits through d2ccc39): power_off_hv_at_end=True for
  data runs (pedestal runs keep their 200 V); processor_watcher
  auto-starts via start_servers.sh (on-the-fly decode→analyze→combine);
  pure-ped and data-run RunCtrl watchdogs in dream_daq_control; dream
  cfg knobs: self_trigger (Trg roles), per-site dream_cfg_template
  (P2SelfTrigger.cfg = SelfTcm copy, Sys Name P2Fe55, PdFile/ZsFile None).
- Beamtest infra (2026-07-19, commit b52eb87): EOS backup watcher ported
  from x17 — dest /eos/project/s/salsachip/Data/T2_tests/P2_SPS_Dream_Data/
  via root://eosproject.cern.ch (verified), xrdcp on banco ~/bin (conda
  env tools), KRB5_CONFIG=config/krb5_cern.conf baked into the watcher.
  WORKING since 2026-07-19 afternoon: user kinit'd on banco (ticket
  renewable to Jul 24; hourly kinit -R by the watcher; ~/.cern_pass.gpg
  still optional/not created — gpg key "banco_lxplus_key" exists on
  banco), backup_watcher tmux running, Fe55 run_1 fully on EOS
  (351/351 files verified incl. processing outputs) + pedestals +
  configs. Flask "Disk Space" tab (commit d7e86be) scans/deletes/
  restores per run vs EOS with active/newest/incomplete guards.
  Beam monitor ported (SPS variable
  SPS.BCTDC.51454:SFTPRO_INT placeholder, TODO-SPS: real line counters);
  NXCALS unreachable from CEA — watcher staged on lxplus
  ~/p2_beam_monitor with pytimber venv /eos/user/a/akallits/nxcals_venv
  (needs JAVA_HOME=java-11-openjdk + SPARK_LOCAL_IP=127.0.0.1); BLOCKED:
  akallits@CERN.CH lacks NXCALS ACL (user must request access); then a
  banco tmux job must ssh-pull beam_state.json+CSVs from lxplus (not yet
  written). Flask GUI got x17's beam tab, system-resources (auto-detected
  NICs/disks), OUT/MID HV colors, richer ped QA — all live on banco.
- Next-beamtest prep: Nov-2025 beam template found and staged 2026-07-19
  in dream_config (banco Fe55 tree + local sps_p2_test tree) as
  Beam_nov25_template.cfg (Ext trigger, ZS mode, TCM mult 3, FEU Ids
  69/.81, 70/.82, 101/.113, 102/.114, 103/.115) plus
  Beam_nov25_final_snapshot.cfg_cpy (resolved config of the last Nov 8
  run). Two firmware trees exist (~/Feu/Firmware and Firmware5) with
  identical cfg sets — ask FEU expert which is canonical.
- 2023 usbdrive→Intenso backup 85% done (262G verified): 47.6 GB in 208
  root-owned mode-600 files (incl. the five >4GB pcapng) blocked until
  user runs: ssh -t banco_daplxa "sudo chmod -R a+r /mnt/usbdrive/TestBeamData"
  then rerun /tmp/backup_usbdrive_2023.sh on banco (incremental; splits
  the big files). Only then may /mnt/usbdrive be deleted. run_1 + the
  pedestal run not yet backed up to the Intenso.
