# SPS beam test (TB_July2026_H4) — finalization checklist

Things to settle **at the beam** before/while taking physics data. Everything
below is DAQ-side unless noted. Current state: paths on `TB_July2026_H4`,
`TRIGGER_MODE=external` (P2B_Beam.cfg template), `DAQ_TRIGGER=self` switches back
to Fe55. Last reviewed 2026-07-23.

## 1. Trigger (external)
- [x] **Scintillator trigger cabled into the TCM external-trigger input**
      (2026-07-23).
- [x] **`Sys Trg MultMoreThan/LessThan` available as run-config knobs**
      (`dream_daq_info['trg_mult_more_than'/'trg_mult_less_than']`), currently
      `None` = follow the template, i.e. the expert's **2 / 4**. Set them to
      0 / 8 to open the window wide if a run shows triggers but no events.
- [x] **`Sys DaqRun Trig` written per run** from `TRIGGER_MODE`
      (`Ext` for beam, `Slf` for Fe55) instead of trusting the template.
- [ ] **First real trigger check**: take a short run and confirm a non-zero
      trigger rate (during the Fe55 test the external input read 0 Hz) and that
      events actually land in the FDFs. If the rate is there but events are
      missing, the multiplicity window is the first thing to re-check.
- [ ] Confirm `Tg_Src_ExtSyn` behaves as expected with the real trigger
      (`Feu * Trig_Conf_Src Tg_Src_ExtSyn` in P2B_Beam.cfg) — i.e. the trigger is
      properly distributed on the sync line to all four FEUs.

## 2. Latency, samples & DAQ throughput
- [x] **Latency**: **32 (0x0020)** in `run_config_beam.py`
      (`dream_daq_info['latency']`, written as `Feu * Dream * 12`), from the
      optimized beam reference `EicP2Bt/P2B_TstBeam.cfg` (2026-07-23 15:46).
      History: 45 (0x002D) in RackTcm.cfg, 40 (0x0028) in the self-trigger
      P2B_SelfTcm.cfg.
- [ ] **Verify 32 on real beam signals** — scan is wired up and ready:
      ```
      DAQ_SITE=sps DAQ_TRIGGER=external DAQ_LATENCY_SCAN=1 python daq_control.py
      ```
      5 sub-runs `latency_024/028/032/036/040`, 2 min each (~10 min + overhead),
      all at the beam operating point. Needs no extra DAQ plumbing: each
      sub-run dict carries its own `latency`, and dream_daq_control merges
      `{**dream_daq_info, **sub_run}` per sub-run (hv_control reads only `hvs`
      and `sub_run_name` and ignores the rest). Verified end to end — the five
      generated cfgs come out with `Feu * Dream * 12` =
      0x0018/0x001C/0x0020/0x0024/0x0028 and `Sys DaqRun Trig Ext`.
      Pick the value that centres the pulse in the 16-sample window
      (`13_timing_waveforms` + online detector_qa), put it in
      `dream_daq_info['latency']`, then set `LATENCY_SCAN` back to False.
      If none of the five centres it, widen the step before narrowing it;
      once bracketed, rescan at step 2.
- [x] **Beam template = the expert's optimized beam config** (2026-07-23):
      `dream_config/P2B_Beam.cfg` is synced from `EicP2Bt/P2B_TstBeam.cfg`
      (an external-trigger config: `Trig Ext`, all-Dat topology, Mult 2/4) with
      the stale PdFile/ZsFile refs cleared. Values now inherited from it:
      latency 32, `Main_Conf_Samples` 16, `Feu_InterPacket_Delay` 1,
      `UdpChan_MultiPackThr` 4888, `DrmClk Rd/WrClk_Div` 6.0,
      `Main_Trig_Ovr*` 36/40/48, masks in Dream registers 8/9.
      Previous template kept as `P2B_Beam.cfg.bak` on banco.
- [ ] **Rate / data-integrity test run**: take a run at the expected beam rate
      and watch for corruption / dropped events (RunCtrl errors,
      `sample_cnt != nb_of_samples`, decode failures, event-ID gaps —
      cf. `24_event_sync_qa`). If it does not run clean, raise
      `Feu_InterPacket_Delay` (template setting; n_samples is per-run).
- [ ] Fix `sample_period` for beam (still 60 ns, inherited from the cosmic
      bench; per-run config value) — check it against the template's
      `DrmClk Rd/WrClk_Div 6.0`.
- [x] **`pedestal_subtraction` / `common_noise_subtraction` — decided ON**
      (2026-07-23), following the expert's beam reference (`Feu_RunCtrl_Pd 1`,
      `Feu_RunCtrl_CM 1`). The FEU now subtracts the pedestal and the per-Dream
      common mode before the ZS threshold comparison, so ZS cuts on signal
      rather than on the pedestal level. Consequence to remember: the FDFs on
      disk are **already** pedestal + CM subtracted, so the offline
      `processor_config.py['common_noise_subtraction']` must stay **False**
      (it is) — subtracting twice would eat signal. Worth confirming on the
      first beam run that the pedestal-subtracted baseline sits near zero.
- [ ] **When the expert updates the reference config, pull it in**:
      ```
      python scripts/sync_beam_template.py \
        --reference banco_cern:<...>/EicP2Bt/P2B_SelfTcm.cfg \
        --template  banco_cern:<...>/dream_config/P2B_Beam.cfg [--apply]
      ```
      Dry-run by default. It reports what the expert changed since our last sync
      (snapshot in `config/reference_snapshots/`, git-tracked — `git diff` there
      shows the expert's edits), what the sync would change in our template, and
      which values the **run config** overrides per run and so must be edited by
      hand in `run_config_beam.py` (currently `latency` and
      `n_samples_per_waveform` — a new expert latency shows up as STALE there,
      not as a template change).
- [ ] **Always cross-check after touching a template or the DAQ script**:
      `python scripts/check_cfg_vs_reference.py --from-run-config
      --template <dream_config/P2B_Beam.cfg> --reference <EicP2Bt/P2B_TstBeam.cfg>`
      must come back with **0 drift**. Anything in the drift bucket is a
      parameter nothing in our code writes, so the template value goes straight
      to the FEUs. Current state (2026-07-23, external mode): **0 drift**,
      0 trigger-mode differences, 0 one-sided, and just 2 deliberate overrides —
      `Sys Action PedThrRun` 1 (we take our own pedestals each run) and
      `Sys DaqRun Events` 0 (sub-runs are bounded by `Sys DaqRun Time`, so the
      reference's 500-event cap would cut them short).
- [x] **GUI trigger-mode override fixed** (2026-07-23): the
      `gui_run_config.json` branch in `run_config_beam.py` still mapped
      `external` to the retired `RackTcm.cfg`, so any GUI-built beam run would
      have silently used the drifted template while the code-default path used
      `P2B_Beam.cfg`. Both now point at `P2B_Beam.cfg`.

## 3. Detectors & HV
- [x] **5-detector telescope defined**: beam order uRWELL front 0 / P2_IN 320 /
      P2_MID 630 / P2_OUT 940 / uRWELL back 1370 mm (`DET_Z_MM`), all five in
      `included_detectors`, with FEU/connector cabling from 2026-07-22.
- [x] **HV channels confirmed and corrected** (2026-07-23): card 8 —
      P2_IN drift/mesh 0/1, P2_MID 2/3, P2_OUT 4/5, uRWELL front drift 6,
      uRWELL back drift 7; card 12 — uRWELL front resistive 0, back resistive 1.
      (IN and OUT were swapped before this fix.)
- [ ] Survey the real transverse x/y offsets of each plane on site
      (`det_center_coords` x/y are all 0 at the moment).
- [x] **HV operating points set per detector** (2026-07-23, `OPERATING_HV`):
      P2_IN 700 drift / 490 mesh (gap 210), P2_MID 700/510 (gap 190),
      P2_OUT 700/450 (gap 250), uRWELL front (uRWELL-inter) and back
      (uRWELL-strip) both 600 drift / 420 resistive. MID and OUT deliberately
      share the same 250 V drift gap. Asserted at import against `MAX_HV` so a
      typo fails before it reaches the crate.
      **Watch P2_OUT on the first ramp**: its mesh setpoint was raised
      420 -> 450 V on 2026-07-23, which is above the maximum documented for it
      up to that point (the Fe55 `SCAN_START` still starts from 420). Back off
      if it draws current or trips.
      This replaced the old common P2 point (mesh 440 / drift 600) inherited
      from the cosmic bench, which was **unsafe on P2_OUT** — 440 V mesh is
      above its 420 V maximum.
- [x] **uRWELL references now actually get powered**: the old `_both_det_hvs`
      helper skipped every detector without a `mesh` channel, so both uRWELLs
      would have sat at 0 V through a beam run. `_operating_hvs()` walks
      `DET_HV` role by role — all 10 channels (card 8 ch 0-7, card 12 ch 0-1)
      are set per sub-run. Verified.
- [ ] **Take the one pedestal run, before physics** (2026-07-23 decision:
      pedestals are taken ONCE and reused, not re-taken per run):
      ```
      DAQ_SITE=sps DAQ_TRIGGER=external python run_config_pedestals.py
      DAQ_SITE=sps DAQ_TRIGGER=external python daq_control.py   # with that config
      ```
      All 10 electrodes at **200 V** (`ped_voltage`), 30 s settle, full readout
      (ZS off, 32 samples), `PedThrRun` on / `DataRun` off, all four FEUs.
      Output lands in `pedestals/pedestals_<MM-DD-YY_HH-MM-SS>/pedestals/` —
      the exact layout and naming `get_pedestals()` scans for.
      **Take it with beam off**: `Sys PedRun Trig Cst` is an internal trigger so
      it does not need beam, but real hits during the pedestal phase would
      inflate the noise estimate and push the 5-sigma thresholds up.
- [x] **Pedestal reuse now actually works** (2026-07-23). It previously did not:
      `get_pedestals()` copied the `.prg` files into each sub-run directory as
      `dream_pedestals_NN_ped.prg` / `dream_thresholds_NN_thr.prg`, but nothing
      ever wrote those names into the cfg's per-FEU `PdFile`/`ZsFile` — which
      the template ships as `None`. A run with `do_pedestal_threshold_run=False`
      would therefore have zero-suppressed against whatever was left in FEU
      memory: a silent data-quality failure, not an error.
      Fixed by `set_feu_mem_file_refs()`, called from dream_daq_control after
      `get_pedestals` and before the cfg is archived. It warns loudly for any
      active FEU with no pedestal file, and dream_daq_control logs a warning if
      pedestals are missing entirely while `PedThrRun` is off.
      Also tightened the FEU-number regex in `get_pedestals` to match the FEU
      field specifically (`_NN_ped.prg`) rather than the first 2-digit group in
      the filename.
      `run_config_beam.py` now has `do_pedestal_threshold_run=False`, which
      incidentally brings `Sys Action PedThrRun` into agreement with the expert
      reference (they reuse pedestals the same way) — the cross-check is down
      to a single deliberate override, `Sys DaqRun Events`.
- [ ] **Re-take pedestals after ANY setup change**: cabling, HV operating
      point, `n_samples_per_waveform`, the Pd/CM flags, or a template sync.
      `pedestals: 'latest'` picks the newest directory automatically, so a new
      pedestal run is all that is needed — but nothing detects a stale one, so
      this is a discipline item. `pedestal_run.txt` in each sub-run records
      which pedestal set was used.

## 4. Run schedule
- [x] `HV_SCAN` set to **False** for the first beam run (2026-07-23):
      2 x 2 min commissioning sub-runs (`beam_commissioning_00/01`) at the
      operating point, to confirm trigger rate / event flow / clean decoding
      before committing beam time.
      Note `HV_SCAN` is **global, not per trigger mode**: the Fe55 scan code is
      intact but will not run while the flag is False, so going back to the
      Fe55 bench means setting `HV_SCAN=True` as well as `DAQ_TRIGGER=self`.
- [ ] After the commissioning run passes, pick the physics schedule
      (`N_SUBRUNS` / `SUBRUN_MIN`) using the **measured** event size and trigger
      rate from it — see the disk budget below.

## 4b. Disk budget
Calibrated from the Fe55 run of 2026-07-18 (RunCtrl log: 29 305 events/FEU in
300 s = 97.7 Hz; 39.6 kB/event/FEU at 32 samples, raw mode, 2 FEUs). That gives
a framing overhead of **1.21x** over the bare sample payload.

Beam config is 16 samples x 512 ch x 2 B = 16.4 kB/FEU/event of payload, so:
- **ZS off (upper bound)**: 19.8 kB/FEU/event x 4 FEUs = **79 kB/event**
- **ZS on** (what we actually run): only hit channels are stored, so this scales
  with occupancy — roughly **4 kB/event at 5 % occupancy**. This is the number
  the commissioning run must *measure*; treat it as a placeholder until then.

On-disk multiplier is **~2.5x the raw fdf volume**: `copy_on_fly=True` +
`save_fdfs=True` keep the fdf in BOTH `dream_run/` and `runs/<subrun>/raw_daq_data/`
(2x), and `decoded_root/` added ~0.5x on Fe55.

banco free space as of 2026-07-23: **473 GB of 938 GB** (47 % used; Fe55 65 GB,
nov25_test 11 GB). At 500 Hz / 5 % occupancy that is ~7 GB/h raw, ~18 GB/h on
disk — about a day of continuous running before the USB/EOS backup has to catch
up. If the rate or occupancy comes in much higher, drop `save_fdfs` or clear
`dream_run/` between runs rather than recording onto the USB drive directly.

## 5. Monitoring & backup
- [x] **SPS beam monitoring**: lxplus → EOS → banco bridge live; variable
      `SPSQC:MEAN_SPILL_INTENSITY` confirmed against Vistar 2026-07-23;
      renewable Kerberos ticket in place so the GUI card stops going Stale.
- [ ] Intra-spill profile: `MEAN_SPILL_INTENSITY` is one scalar per spill. For
      the Vistar-style spill shape an array variable (NA-SPILL profile) still
      needs to be identified and added.
- [x] **EOS backup confirmed live end-to-end** (2026-07-23). Verified on banco:
      `backup_watcher.py config/backup_config.json` running (PID 108222),
      source `TB_July2026_H4/`, dest
      `root://eosproject.cern.ch//eos/project/s/salsachip/Data/T2_tests/P2_SPS_Dream_Data/`.
      Kerberos `akallits@CERN.CH` valid to 07/24 16:59, renewable to 07/28, with
      an `xrootd/eosproject-i01.cern.ch` service ticket already issued.
      Proof of live sync: the `P2B_Beam.cfg` we deployed today is on EOS at
      8263 B — byte-identical to the local file — timestamped ~4 min after the
      local write (EOS lists UTC, banco is CEST; matches the 300 s
      `extra_sync_interval` for non-`runs` subdirs).
      Note `xrdcp`/`xrdfs` live in `~/bin` and are NOT on a non-interactive
      ssh PATH — prepend `PATH=$HOME/bin:$PATH` when checking by hand.
- [ ] Confirm the **first real beam run** appears under EOS `runs/<run>/` with
      all four FEUs' fdfs (config/dream_config/pedestals already mirror fine).
      `dream_run/` is in `exclude_dirs`, so EOS carries ~1.5x the raw volume
      (raw + decoded), not the ~2.5x that sits on the local disk.
- [ ] Confirm the GUI Analysis tab + Disk Space tab show the beam runs.

## 6. Analysis (ready — flip on when real beam data arrives)
- [ ] Register the beam runs (or use the `live` entry:
      `SPS_RUN=run_N python <stage>.py live` on banco).
- [ ] With 5 planes + real tracks, run `21_telescope_align` then
      `22_tag_probe_efficiency` (both were statistics-limited to nothing on the
      Fe55 dry-run; they need real through-going beam).
- [ ] Confirm the beam-monitor CSV overlay in `23_beam_profile` on a real run.
