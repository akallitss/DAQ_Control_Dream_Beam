# Seven queued pedestal runs from one button (2026-07-27)

**Status:** root cause understood; GUI + endpoint guards implemented and deployed.
The underlying `.stop_run` weakness is documented below but deliberately NOT fixed yet.

## What was seen

Colleagues went to stop the run before an access at ~14:00. Instead of stopping,
the DAQ started taking pedestals, repeatedly, for about thirteen minutes.

## What actually happened

`eff_nominal_1` had already **finished normally at 13:48:23** — there was no run to
stop. At 14:14:39 the **Take Pedestals** button was clicked roughly **seven times
inside a single second**, and all seven launches went through.

The `daq_control` pane records it plainly: the first invocation starts, then six
identical command lines sit queued behind it, echoed but not yet executed.

```
banco@dedippcq196:~$ .../python daq_control.py "run_config_pedestals.json"
Starting DAQ Control
...
.../python daq_control.py "run_config_pedestals.json"     <- queued x6
.../python daq_control.py "run_config_pedestals.json"
...
```

The chain, end to end:

1. `/take_pedestals` did a bare `subprocess.Popen(run_pedestals.sh)` — no
   confirmation, no button disable, no "is a run active" check. (`Stop Run` and
   `Git Reset` both confirm; this one did not.)
2. `run_pedestals.sh` regenerates `run_config_pedestals.json`, then `start_run.sh`
   does `tmux send-keys -t daq_control "<cmd>" C-m`.
3. **`send-keys` types the command, it does not execute it.** A second launch while
   the first is still running therefore does not fail loudly — it lands in that
   shell's input buffer and runs the instant the previous one exits. Hence the
   0.3 s gaps between `Run finished normally` and the next `Run started` in
   `dream_daq.log`.
4. All seven clicks landed inside the same second, so all seven regenerated configs
   got the same timestamped run name. That is why **five pedestal sets (20 `_pedthr_`
   FDFs) ended up inside one run directory**, `pedestals_07-27-26_14-14-39`, instead
   of five directories.

### Stop Run could not stop it

The Stop Run press at 14:17:46 (`logs/daq_events.log`, `remote_addr=127.0.0.1`,
`dream_running=False`) worked — on the *one* invocation that was live at that moment:

```
Prepping DAQs for pedestals
[stop] Stop requested — not (re)starting DAQ controller.
[stop] Sub run pedestals stopped manually — not marking complete.
```

Thirteen seconds later the next queued command started and took pedestals anyway.
This is the structural part: **`daq_control.py` clears `.stop_run` on startup**
("clear any stale stop requests from a previous run"), so every queued invocation
wipes the stop request and proceeds. Stop Run cannot drain a queue that has already
formed. From the operator's seat this reads exactly as "we pressed stop and
pedestals started".

### Sequence

| # | window | outcome |
|---|--------|---------|
| 1 | 14:14:39 → 14:17:17 | pedestals taken (14H16) |
| 2 | 14:17:17 → 14:17:59 | **stopped by the 14:17:46 Stop Run** |
| 3 | 14:18:00 → 14:18:52 | pedestals taken (14H18) |
| 4 | 14:18:52 → 14:23:55 | HV ramp timed out after 300 s — every channel stuck at 0.0 V, sub-run skipped |
| 5 | 14:23:56 → 14:25:38 | pedestals taken (14H25) |
| 6 | 14:25:39 → 14:26:31 | pedestals taken (14H26) |
| 7 | 14:26:31 → 14:27:23 | pedestals taken (14H27) |

Invocation 4 is the one that lines up with the access: for five minutes the CAEN
reported `8:0`–`8:7`, `12:0`, `12:1` all at 0.0 V and refused to ramp. It recovered
on its own by 14:23:56. Cause not established.

## Consequences

- **Data is fine.** QA on the 14:27 set is clean — 0/2048 bad channels across FEUs
  1/3/4/5. Both the QA watcher and the `pedestals: 'latest'` lookup take the newest
  set, so nothing downstream was confused by the five sets sharing a directory.
- **~13 minutes of beam time lost.**
- **HV was left biased.** Pedestal runs have `power_off_hv_at_end: false`, so
  invocation 7 left the crate at 200 V, including P2_IN's `8:0`/`8:1`, which were
  still live when P2_IN was pulled from the beam line. Separately fixed the same
  afternoon: `run_config_pedestals.py` now lists excluded detectors at 0 V instead
  of skipping them, so a pedestal run actively de-energises a removed station.

## Fix

`flask_app/app.py` → `/take_pedestals`, three-way on `daq_control`'s state, all under
one lock:

- **cooldown (`PEDESTAL_COOLDOWN_S = 60`)** — the guard that would actually have
  stopped this. All seven clicks landed while `daq_control` still read
  `Run Complete` from `eff_nominal_1`, so a state check *alone* would have passed
  every one of them. The cooldown covers the window between `send-keys` and the
  pane reflecting the new process. By the time it expires a real pedestal run
  (~2.5 min) is into `Ramping HV`, so the state check takes over with no gap.
- **busy** — reject anything outside `PEDESTAL_IDLE_STATES`. This is what stops
  "take pedestals in the middle of a beam run".
- **`UNKNOWN STATE`** — `get_daq_control_status()` returns this when the last 50
  lines of the pane match none of its rules (scrolled or stale pane; usually means
  nothing is running). Neither idle nor busy, so it is overridable behind a second
  confirmation: the client re-POSTs with `confirm_unknown=1`. The override is
  scoped to that state only — it cannot bypass a genuinely busy `daq_control`, and
  it does not touch the cooldown, so it cannot be used to re-arm a click storm.

`flask_app/templates/index.html` → the handler now confirms before POSTing, disables
the button while the request is in flight (re-enabling after 5 s so it can never
wedge), colours failures red, and handles the `needs_confirm` round-trip.

## Not fixed

`daq_control.py:107` still clears `.stop_run` at startup. Making it a persistent
"stop everything" latch would let one Stop Run drain a queue, but it changes
behaviour for every run type and was deliberately deferred — do not change it
mid-beam-time without agreement.

## Deploy note

`flask_app/start_flask.sh` runs `flask run` with no `--debug` and no reloader, so
**neither the endpoint nor the template is live until `flask_server` is restarted**
(`bash_scripts/restart_flask.sh`, or the "Restart GUI" button). Restarting the GUI
does not touch `daq_control` / `dream_daq` / `hv_control`.

## Aside

`flask_app/templates/index.html` contains 4 stray NUL bytes, which is why `grep`
treats it as binary and silently reports no matches without `-a`. Pre-existing and
unrelated; preserved by the edits above, but worth cleaning up.
