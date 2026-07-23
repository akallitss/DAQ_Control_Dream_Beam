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
- [ ] **Verify 32 on real beam signals**: worth a short latency scan
      (e.g. 28/32/36) on the first beam — pick the value that centres the pulse
      in the sample window (`13_timing_waveforms` + online detector_qa).
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
      P2_OUT 700/420 (gap 280), uRWELL front (uRWELL-inter) and back
      (uRWELL-strip) both 600 drift / 420 resistive. Asserted at import against
      `MAX_HV` so a typo fails before it reaches the crate.
      This replaced the old common P2 point (mesh 440 / drift 600) inherited
      from the cosmic bench, which was **unsafe on P2_OUT** — 440 V mesh is
      above its 420 V maximum.
- [x] **uRWELL references now actually get powered**: the old `_both_det_hvs`
      helper skipped every detector without a `mesh` channel, so both uRWELLs
      would have sat at 0 V through a beam run. `_operating_hvs()` walks
      `DET_HV` role by role — all 10 channels (card 8 ch 0-7, card 12 ch 0-1)
      are set per sub-run. Verified.
- [ ] Take **fresh pedestals in external-trigger mode** at the beam before
      physics runs (`do_pedestal_threshold_run`).

## 4. Run schedule
- [x] `HV_SCAN` set to **False** for the first beam run (2026-07-23):
      2 x 2 min commissioning sub-runs (`beam_commissioning_00/01`) at the
      operating point, to confirm trigger rate / event flow / clean decoding
      before committing beam time. The Fe55 scan branch is untouched and still
      applies for `DAQ_TRIGGER=self`.
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
- [ ] EOS backup: live (banco → `/eos/project/s/salsachip/…`). Confirm a first
      real beam run reaches EOS.
- [ ] Confirm the GUI Analysis tab + Disk Space tab show the beam runs.

## 6. Analysis (ready — flip on when real beam data arrives)
- [ ] Register the beam runs (or use the `live` entry:
      `SPS_RUN=run_N python <stage>.py live` on banco).
- [ ] With 5 planes + real tracks, run `21_telescope_align` then
      `22_tag_probe_efficiency` (both were statistics-limited to nothing on the
      Fe55 dry-run; they need real through-going beam).
- [ ] Confirm the beam-monitor CSV overlay in `23_beam_profile` on a real run.
