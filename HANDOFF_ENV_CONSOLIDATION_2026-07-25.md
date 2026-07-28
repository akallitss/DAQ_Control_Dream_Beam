# HANDOFF — consolidate banco onto ONE ROOT (6.32.02) and ONE Python (3.12)

**Written:** 2026-07-25, overnight session ending ~10:20 CEST by Claude (Opus 5), from Dylan's laptop over ssh.
**For:** a model working directly on banco (`/local/home/banco/DAQ_Control_Dream_Beam`).
**Task:** Dylan asked to streamline this machine to a single environment — ROOT **6.32.02** and
Python **3.12** — and to hand off the execution.

**Nothing in this document has been applied.** `.bashrc`, `start_flask.sh` and everything else are
untouched. Every fact tagged **[measured]** was observed on this machine tonight; **[unresolved]**
means I could not determine it and you should check rather than assume.

---

## 0. READ FIRST — safety preconditions

1. **Do not start any of this while a run is active.** Check:
   ```bash
   pgrep -af "daq_control.py" ; tmux capture-pane -p -t daq_control -S -5
   ```
   Part B requires restarting Flask and `processor_watcher`.
2. **This repo has 4 commits that exist ONLY on this disk.** `Dyn0402` has read-only access to
   `akallitss/DAQ_Control_Dream_Beam`; push is rejected for `main` *and* for new branches.
   The GUI's **"Git Reset" button runs `git reset --hard origin && git pull` and will destroy
   them.** Backups already exist in `~/precommit_backup_260725_0040/` (patches + bundle).
   Add your own commits on top; do not reset.
3. **`~/.bashrc` is shared by every person who uses this machine** (one Unix account `banco`,
   uid 1002, used by benjamin/camille/fabien/francesco/gregoire/maxence/yann/dylan/Alexandra).
   Editing it affects everyone. Keep changes minimal, comment them, and leave both ROOT
   installations *installed* — only the default environment changes.
4. Editing `.bashrc` affects **new shells only**. Running processes keep their environment until
   restarted.

---

## 1. Current state [measured]

### ROOT — two installations

```
/local/home/banco/opt/root_v6.32.02   -> 6.32.02   <-- what everything is BUILT against
/local/home/banco/P2/root             -> 6.26/10   <-- what .bashrc puts on the path
```

`.bashrc:126-127` exports `ROOTSYS=/local/home/banco/P2/root` and sources its `thisroot.sh`,
putting **6.26** on `LD_LIBRARY_PATH`. The reconstruction binaries carry a **`RUNPATH`** (not
`RPATH`), and `LD_LIBRARY_PATH` overrides `RUNPATH`:

```
$ readelf -d mm_strip_reconstruction/cmake-build-release/decoder/decode
 0x…1d (RUNPATH)  Library runpath: [/local/home/banco/opt/root_v6.32.02/lib]

# with .bashrc sourced:
libCore.so => /local/home/banco/P2/root/lib/libCore.so             <-- 6.26 into a 6.32 binary
# without LD_LIBRARY_PATH:
libCore.so => /local/home/banco/opt/root_v6.32.02/lib/libCore.so   <-- correct
```

**This is already known here** — `start_servers.sh:6-10` says so explicitly:

> *"Interactive shells on banco export LD_LIBRARY_PATH/PYTHONPATH for unrelated lab software
> (ISEG SDK etc.), which shadows the ROOT libraries the reconstruction binaries were built against
> (**"symbol lookup error" from decode under the watcher**). Scrub them for every session we start."*

So the mitigation `ENVCLEAN="env -u LD_LIBRARY_PATH -u PYTHONPATH"` already exists and works for
every session `start_servers.sh` launches. **The point of Part A is to remove the cause rather
than keep scrubbing the symptom** — anything launched *outside* `start_servers.sh` (a human in a
terminal, a new script, `overnight_scans.sh` before someone added its own `env -u`) is still exposed.

**Who actually needs 6.26:** only other people's beam-test binaries — and they do **not** need it
on the environment, because they have it baked in as RUNPATH:

```
camille/beam_test_2023/reco/combine       RUNPATH: [/local/home/banco/P2/root/lib]
camille/beam_test_2023/decode/decode_zs   RUNPATH: [/local/home/banco/P2/root/lib]
fabien/beam_test_2025/decode_zs           RUNPATH: [/local/home/banco/P2/root/lib]
```

**Zero** source references to `P2/root` exist in this repo, in `P2_basket_analysis`, or in any
`~/*.sh`. `RunCtrl` and `FeuDataFileReader` do not link ROOT at all (`ldd` shows no ROOT libs).
The analysis code uses `uproot`, not PyROOT. **Nothing breaks when 6.26 leaves the default path.**

### Python — two interpreters in production

| Process | Interpreter |
|---|---|
| `dream_daq_control`, `daq_control`, `hv_control`, `pedestal_watcher`, `backup_watcher`, `mem_guardian` | `.venv` → **3.12.13** |
| **Flask itself**, and therefore **`processor_watcher`** / `qa_watcher` / `backup_watcher` when (re)started *from the GUI* | `/usr/bin/python3` → **3.8** |

Chain: `~/.local/bin/flask` has shebang `#!/usr/bin/python3` → `flask_app/start_flask.sh` invokes
**bare `flask`** → `~/.local/bin` precedes `.venv/bin` on `PATH` → Flask runs on 3.8 → `app.py`
launches watchers with `sys.executable`, which is therefore 3.8.

The comment at `flask_app/app.py:530` says *"sys.executable (flask's venv python)"*. **That comment
is currently false** — `sys.executable` is `/usr/bin/python3.8`. Part B makes it true.

Why `PATH` ordering defeats `source .venv/bin/activate` in `start_servers.sh:4`: `start_tmux.sh`
creates a tmux pane, whose shell re-runs `.bashrc`/profile and re-prepends `~/.local/bin`, `~/bin`
and `P2/root/bin` ahead of `.venv/bin`. This is the same effect the `app.py` comments describe as
*"the tmux login shell resets PATH and drops the venv"*. **Therefore: fix with absolute paths, not
by reordering `PATH`.**

Other Python installs present: `~/anaconda3` (the conda block in `.bashrc:139-146` is
**commented out** — inactive), `~/miniconda3_daq/envs/py312` (**the venv's base — keep**),
`~/miniconda3_daq/envs/tools`.

Package versions across the split:

```
             venv 3.12      system 3.8
flask        3.1.3          3.0.3
numpy        2.4.6          1.24.2      <-- MAJOR jump, the main risk in Part B
pandas       2.3.3          1.5.3       <-- MAJOR jump
matplotlib   3.11.1         3.7.0
requests     2.32.5         2.32.4
```

**Third-party packages missing from the venv: none.** I parsed the imports of `app.py`,
`processor_watcher.py`, `qa_watcher.py`, `pedestal_watcher.py`, `backup_watcher.py`,
`dream_daq_control.py`, `daq_control.py`, `hv_control.py` and resolved every one under the venv.
(`sim`, `beam_monitor`, `space_manager`, `daq_status`, `monitor` appear "missing" to a naive
checker — they are **local repo modules**, not PyPI packages.)

---

## 2. PART A — single ROOT (6.32.02)

Low risk. Do this first, verify, then do Part B.

### A1. Edit `~/.bashrc`

Replace lines 125-127:

```bash
# define local root installation
export ROOTSYS=/local/home/banco/P2/root
source /local/home/banco/P2/root/bin/thisroot.sh
```

with:

```bash
# ROOT is NOT on the default environment any more (2026-07-25).
#
# Two ROOTs exist on this machine: P2/root = 6.26/10, opt/root_v6.32.02 = 6.32.02.
# Everything built here (mm_strip_reconstruction, mm_dream_reconstruction, the
# clusterizer) has RUNPATH -> opt/root_v6.32.02/lib. Putting 6.26 on
# LD_LIBRARY_PATH *overrode* that RUNPATH and loaded 6.26 libs into 6.32-built
# binaries -> "symbol lookup error" from decode (see start_servers.sh).
#
# Both installations remain on disk. Older beam-test binaries in camille/ and
# fabien/ carry RUNPATH -> P2/root/lib and keep working with no environment set.
# For an interactive ROOT session, pick one explicitly:
alias setup_root='source /local/home/banco/opt/root_v6.32.02/bin/thisroot.sh'
alias setup_root_p2='source /local/home/banco/P2/root/bin/thisroot.sh'
```

Also delete these four dead lines (**verified zero consumers** — nothing imports iseg/ics, and
`ISEG_SDK_PATH` is referenced nowhere; the Vivado `settings64.sh` line is already commented out):

```bash
export XILINXD_LICENSE_FILE=2100@irfupcg128                      # .bashrc:120
export ISEG_SDK_PATH="…/benjamin/Programs/icsPythonForLinux/…"   # .bashrc:158
export PYTHONPATH=$PYTHONPATH:$ISEG_SDK_PATH                     # .bashrc:159
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$ISEG_SDK_PATH           # .bashrc:160
```

Back it up first: `cp ~/.bashrc ~/.bashrc.bak_$(date +%y%m%d_%H%M)`.

**Leave `.bashrc:163` (`PATH += Feu/.../Linux/bin`) alone** — `dream_daq_control.py:142` invokes
`RunCtrl` by bare name and the DAQ cannot start a run without it. See §4.

### A2. Verify

```bash
# a fresh interactive login shell must no longer inject ROOT
bash -lic 'echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"; echo "PYTHONPATH=$PYTHONPATH"'
# expect: no P2/root, no icsPythonForLinux

# the decoder must resolve 6.32 with NO env scrubbing
bash -lic 'ldd ~/mm_strip_reconstruction/cmake-build-release/decoder/decode | grep libCore'
# expect: /local/home/banco/opt/root_v6.32.02/lib/libCore.so

# other people's binaries must still resolve 6.26 via their own RUNPATH
bash -lic 'ldd ~/camille/beam_test_2023/decode/decode_zs | grep -c "not found"'
# expect: 0
```

### A3. ⚠️ Test the decoder-hang hypothesis while you are here [unresolved — worth 10 minutes]

banco's `processor_watcher` upgrade was motivated by decoder **hangs**
(*"100% CPU, input position and output ROOT both frozen — seen 2026-07-23/24"*), and killed
decodes preserve the raw FDF as `<name>.hang` **specifically as reproducers**. The ABI mismatch is
confirmed to cause *"symbol lookup error"* (a hard crash). Whether it also causes the *hangs* is
**not established**. Find a `.hang` file and run the decoder on it both ways:

```bash
find /local/home/banco/P2_data -name "*.hang" | head
# with the OLD (broken) environment:
LD_LIBRARY_PATH=/local/home/banco/P2/root/lib timeout 300 <decode-cmd> <file>.hang
# with the corrected environment:
timeout 300 <decode-cmd> <file>.hang
```

If it only hangs with 6.26 on the path, Part A has retired a whole class of bug and the decode
watchdog becomes belt-and-braces rather than load-bearing. **Record the result either way** —
this is currently the biggest open question about this machine.

---

## 3. PART B — single Python (3.12, the repo venv)

Requires restarting Flask and `processor_watcher`. **Only with no run active.**

### B1. Make Flask run on the venv interpreter

`flask_app/start_flask.sh` — replace:

```bash
export FLASK_APP=flask_app/app.py
flask run --host=0.0.0.0 --port=5001
```

with:

```bash
# Absolute venv interpreter, NOT bare `flask`: ~/.local/bin/flask has shebang
# #!/usr/bin/python3 (3.8) and ~/.local/bin precedes .venv/bin on PATH, so the
# bare name silently ran the GUI on system python while every other DAQ process
# ran on the venv's 3.12. sys.executable is inherited by processor_watcher /
# qa_watcher / backup_watcher (app.py), so this one line decides the interpreter
# for all of them. Absolute path because tmux panes re-source .bashrc and
# reorder PATH, which defeats `source .venv/bin/activate`.
export FLASK_APP=flask_app/app.py
exec /local/home/banco/DAQ_Control_Dream_Beam/.venv/bin/python -m flask run --host=0.0.0.0 --port=5001
```

### B2. Make the remaining bare-`python` call sites explicit

Same reasoning — do not rely on `PATH`. Define once in `flask_app/app.py` near `BASE_DIR`:

```python
VENV_PYTHON = f"{BASE_DIR}/.venv/bin/python"
```

and replace the three bare-`"python"` subprocess calls:

| File | Line | Current | Change to |
|---|---|---|---|
| `flask_app/app.py` | 367 | `["python", f"{BASE_DIR}/iterate_run_num.py"]` | `[VENV_PYTHON, …]` |
| `flask_app/app.py` | 378 | `["python", f"{BASE_DIR}/run_config_beam.py"]` | `[VENV_PYTHON, …]` |
| `flask_app/app.py` | 482 | `["python", f"{BASE_DIR}/run_config_beam.py"]` | `[VENV_PYTHON, …]` |
| `bash_scripts/run_pedestals.sh` | 9 | `python run_config_pedestals.py` | `.venv/bin/python run_config_pedestals.py` |
| `bash_scripts/start_run.sh` | 13 | `COMMAND="python daq_control.py \"$CONFIG_PATH\""` | `COMMAND=".venv/bin/python daq_control.py \"$CONFIG_PATH\""` |

`start_servers.sh:15-22` uses bare `python` too, but there it is preceded by
`source .venv/bin/activate` in the *same* shell, so it resolves correctly. Making it absolute is
still tidier and removes the last PATH dependency.

**After this change the `sys.executable` sites (`app.py:522/534/554/564/584`) become correct as
written** — the comments there finally match reality. Do not change them.

### B3. Restart and verify

```bash
cd /local/home/banco/DAQ_Control_Dream_Beam
tmux kill-session -t flask_server
tmux kill-session -t processor_watcher
bash bash_scripts/start_tmux.sh flask_server "env -u LD_LIBRARY_PATH -u PYTHONPATH flask_app/start_flask.sh" 5000
# then restart the processor watcher from the GUI so it inherits the new sys.executable

# verify EVERY DAQ process is on 3.12:
for p in $(pgrep -f "processor_watcher.py|pedestal_watcher.py|dream_daq_control.py|backup_watcher.py|flask|daq_control.py"); do
  printf "%-46s -> %s\n" "$(tr '\0' ' ' < /proc/$p/cmdline | cut -c1-45)" "$(readlink -f /proc/$p/exe)"
done
# expect: every line -> …/miniconda3_daq/envs/py312/bin/python3.12
```

Then exercise the GUI: load the page, **Take Pedestals** (~2 min, verify `.prg` files appear),
check the Pedestal QA / Disk Space / Analysis tabs render (they are the numpy/pandas/matplotlib
consumers), and confirm the processor decodes at least one file.

### B4. ⚠️ The real risk in Part B: numpy 1.24 → 2.4 and pandas 1.5 → 2.3

Both are **major** version jumps with genuine breaking changes. `processor_watcher.py` imports
**zero** third-party packages, so the decode path is safe. The exposure is in `flask_app/`
(`space_manager.py`, `daq_status.py`, `monitor.py`) and any QA plotting. If a tab breaks, the
fastest fix is pinning in the venv (`.venv/bin/pip install "numpy<2"`), **not** reverting to
system Python.

### B5. Rollback

Part B is one line in `start_flask.sh` plus five call sites. `git diff` will show everything;
`git checkout -- flask_app/start_flask.sh flask_app/app.py bash_scripts/` reverts it. Then restart
`flask_server` and `processor_watcher` as in B3.

---

## 4. Do NOT change

- **`.bashrc:163`** — `PATH += /local/home/banco/Feu/.../Linux/bin`. `dream_daq_control.py:142`
  runs `['RunCtrl', '-c', …]` **by bare name**. Removing this stops the DAQ from starting runs.
  *Improvement for later, not now:* make it a `runctrl_bin` config key or an absolute path, so the
  DAQ stops depending on `.bashrc` at all.
- **`export DAQ_SITE=sps` (`.bashrc:164`)** — load-bearing for `run_config_beam.py`. It deserves to
  move into an explicit `bash_scripts/daq_env.sh` sourced by the launchers, but that is a separate
  change; doing it in the same pass as ROOT+Python makes a failure hard to attribute.
- **`~/miniconda3_daq/envs/py312`** — the venv's base interpreter. Deleting it destroys the venv.
- **Both ROOT installations on disk.** Part A only removes 6.26 from the *default environment*.

---

## 5. Unresolved — please determine rather than assume

1. **[unresolved]** tmux pane shells carry `DAQ_SITE=sps` but **not** `LD_LIBRARY_PATH`, even
   though both come from `.bashrc`. Measured:
   ```
   bash pid=81175 parent=tmux: server   LD_LIBRARY_PATH_set=0   DAQ_SITE_set=1
   ```
   I could not explain the asymmetry. It matters: `start_run.sh` does `tmux send-keys` into the
   `daq_control` pane, and that session is created with a plain `echo` (no `ENVCLEAN`), so if
   panes *were* poisoned, **every GUI-started run** would be. They appear not to be. Confirm
   before relying on it.
2. **[unresolved]** Whether the decoder *hangs* (as opposed to the confirmed symbol-lookup errors)
   are caused by the ABI mismatch. See §A3.
3. **[unresolved]** `~/miniconda3_daq/envs/tools` and `~/anaconda3` — no active references found
   (the conda block is commented out), but I did not confirm nobody uses them interactively.
   Leave them alone; just do not add them to any path.

---

## 6. Context you may need

- Full cross-machine comparison of this DAQ against the nTOF bench DAQ (`Dyn0402/nTof_x17_DAQ`,
  same codebase, forked at `82d44da3` on 2026-07-04) is on Dylan's laptop at
  `nTof_x17/DAQ_FORK_STUDY_2026-07-25.md`. It covers `dream_daq_control.py` / `daq_control.py` /
  `hv_control.py` differences and a ranked list of things worth porting in each direction.
- Recent local commits on this machine (unpushed): decode watchdog + Git Reset confirmation,
  `reconstruction_build` → `mm_strip_reconstruction`, the `RUN_PLAN` knob making the drift+mesh
  scans startable from the GUI, and the `hv_creds` stdout→stderr fix that had been breaking the
  Start Run button outright.
- `RUN_PLAN='drift_then_mesh'` is currently the default, so GUI Start Run produces
  `drift_mesh_scan_1`: 23 sub-runs, 230 min, HV off at end. Two open questions Dylan has not
  answered: whether P2_IN should step during the mesh half (it currently does, 630/430 → 570/370),
  and that the current pedestals cover only FEUs 1/4/5 — retake them before a physics run.
