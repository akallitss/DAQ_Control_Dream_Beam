# SPS beam test (TB_July2026_H4) — finalization checklist

Things to settle **at the beam** before/while taking physics data. Everything
below is DAQ-side unless noted. Current state: paths on `TB_July2026_H4`,
`TRIGGER_MODE=external` (P2TB.cfg), `DAQ_TRIGGER=self` switches back to Fe55.

## 1. Trigger (external) — the priority items you flagged
- [ ] **Scintillator trigger into the TCM**: cable the scintillator-coincidence
      logic signal into the TCM external-trigger input. Confirm the DAQ sees it
      (a short run should show a non-zero trigger rate; during the Fe55 test the
      external input read 0 Hz — that must become the real rate).
- [ ] **`Sys Trg MultMoreThan/LessThan` (currently 2/4, inherited from the rays
      cosmic config)**: decide what this should be for our external trigger.
      With a scintillator coincidence forming the trigger, the TCM multiplicity
      window may need to be wide-open / disabled (so the external signal alone
      triggers) rather than requiring FEU-hit multiplicity. Test: take short
      runs at a couple of settings, confirm events are recorded on a real
      external trigger and not gated away. Set the final value in P2TB.cfg.
- [ ] Confirm `Sys DaqRun Trig Ext` + `Tg_Src_ExtSyn` behave as expected with
      the real trigger (both already set in P2TB.cfg).

## 2. Latency, samples & DAQ throughput — the test runs you flagged
- [ ] **Find the correct latency for our signals**: the sample window offset
      (`Feu * Dream * 12` register / the config `latency` field) depends on the
      trigger-to-signal timing at the beam. Take a **latency-scan test run**
      (a few latency values, one short sub-run each) and pick the one that
      centres the pulse in the sample window (check the waveform QA /
      `13_timing_waveforms` + the online detector_qa plots). Then set it as the
      run-config latency (the code writes `Feu * Dream * 12` per run, so it's a
      config value, not a template edit).
- [ ] **Rate / data-integrity test run — `Feu_InterPacket_Delay` +
      `n_samples`**: the sustainable trigger rate without corrupted/dropped
      data depends on the per-event payload (number of samples) and the FEU
      packet pacing (`Feu * Feu_InterPacket_Delay`, currently 100 in P2TB.cfg
      from the rays config) vs the network/DAQ throughput. Take a test run at
      the expected beam rate and:
        - scan `Feu_InterPacket_Delay` (and `UdpChan_MultiPackThr`, now 4888)
          and `n_samples_per_waveform` (template 32),
        - watch for corruption / dropped events (RunCtrl errors,
          FeuDataFileReader `sample_cnt != nb_of_samples`, decode failures,
          gaps in event IDs — cf. `24_event_sync_qa`),
        - pick the largest samples + smallest delay that runs clean at rate.
      InterPacket_Delay + UdpChan_MultiPackThr are **template** settings
      (P2TB.cfg); n_samples is a per-run config value.
- [ ] Fix `sample_period` for beam (template 60 ns; per-run config value).

## 3. Detectors & HV
- [ ] **Third station P2_IN**: geometry is in — beam order P2_OUT 0 (first),
      P2_MID 300, P2_IN 600 mm (last), via `TELESCOPE_SPACING_MM`/
      `TELESCOPE_ORDER`. Still needed: its real `dream_feus` wiring
      (FEU/connectors) + `hv_channels`, then add 'P2_IN' to
      `included_detectors` and `P2_HV`. Survey the real x/y offsets on site.
- [ ] Confirm P2_OUT/P2_MID/P2_IN FEU + HV cabling at the beam matches the
      config (FEU Ids 101/102/103 @ .113/.114/.115 assumed from the bench).
- [ ] HV operating points per detector for beam (drift-gap convention:
      drift − mesh; maxima P2_OUT 700/420, P2_MID 700/510 — P2_IN TBD).
- [ ] Take **fresh pedestals in external-trigger mode** at the beam before
      physics runs (the pipeline references them; do_pedestal_threshold_run).

## 4. Run schedule
- [ ] Replace the Fe55 HV-scan schedule with the beam schedule (`HV_SCAN`,
      sub-run structure, run times) once the beam plan is set.

## 5. Monitoring & backup (mostly done — verify on site)
- [ ] **SPS beam monitoring (NXCALS)**: request NXCALS access for
      `akallits@CERN.CH` (currently ACL-denied). Watcher staged on lxplus
      (`~/p2_beam_monitor`); confirm the real beam-line variable (placeholder
      `SPS.BCTDC.51454:SFTPRO_INT` — swap for the H4/T2 line counters or BSI).
- [ ] EOS backup: already live (banco → `/eos/project/s/salsachip/…`). Confirm
      a first beam run reaches EOS; keep a CERN Kerberos ticket alive
      (`kinit akallits@CERN.CH`; optional `~/.cern_pass.gpg` for auto-renew).
- [ ] Confirm the GUI Analysis tab + Disk Space tab show the beam runs.

## 6. Analysis (ready — flip on when 3-plane data arrives)
- [ ] Register the beam runs (or use the `live` entry:
      `SPS_RUN=run_N python <stage>.py live` on banco).
- [ ] With 3 planes + real tracks, run `21_telescope_align` then
      `22_tag_probe_efficiency` (statistics-limited to nothing on the Fe55
      dry-run; they need real through-going beam).
- [ ] Confirm the beam-monitor CSV overlay in `23_beam_profile` once NXCALS
      is available.
