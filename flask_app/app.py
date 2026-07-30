#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 29 3:45 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/app.py

@author: Dylan Neff, Dylan
"""

import os
import re
import sys
import subprocess
import pty
import select
import threading
import time
import uuid
import json
from datetime import datetime, timedelta
import pandas as pd
from urllib.parse import quote
from flask import Flask, render_template, jsonify, request, send_from_directory, abort
from flask_socketio import SocketIO, emit

import space_manager
from daq_status import (get_dream_daq_status, get_hv_control_status,
                        get_daq_control_status, get_processor_watcher_status,
                        get_qa_watcher_status, get_backup_watcher_status,
                        get_pedestal_watcher_status, get_beam_watcher_status)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Add parent dir to path
from run_config_beam import Config, BASE_DATA_DIR
from get_run_events import get_total_events_for_run
from monitor import DaqMonitor, fetch_chat_id, get_bot_username
from beam_monitor.beam_intensity_controller import (BEAM_LOG_DIR, BEAM_STATE_PATH,
                                                    NXCALS_PYTHON, BEAM_UNIT,
                                                    PULSE_THRESHOLD_E10)
from sps_monitor.sps_spill_controller import (SPS_LOG_DIR, SPS_STATE_PATH, SPS_UNIT,
                                              EXTRACTED_DEST, SPILL_THRESHOLD_E10,
                                              H4_BEAM_COUNTERS, H4_COUNT_VARS)

# Repo root (parent of flask_app/) — no per-machine edit needed.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Absolute venv interpreter for every python we spawn. NOT bare "python":
# subprocess.Popen has no shell, so the interactive `alias python='python3'`
# does not apply and bare "python" resolves to /usr/bin/python (3.8) while the
# rest of the DAQ runs on the venv's 3.12. Absolute rather than PATH-relative
# because tmux panes re-source .bashrc and reorder PATH (2026-07-25).
VENV_PYTHON = f"{BASE_DIR}/.venv/bin/python"
CONFIG_TEMPLATE_DIR = f"{BASE_DIR}/config/json_templates"
CONFIG_RUN_DIR = f"{BASE_DIR}/config/json_run_configs"
CONFIG_PY_PATH = f"{BASE_DIR}/run_config_beam.py"
BASH_DIR = f"{BASE_DIR}/bash_scripts"
PROCESSOR_CONFIG_PATH = f"{BASE_DIR}/config/processor_config.json"
PROCESSOR_TMUX = "processor_watcher"
QA_CONFIG_PATH = f"{BASE_DIR}/config/qa_config.json"
QA_RESET_PATH  = f"{BASE_DIR}/config/qa_reset.json"
QA_TMUX = "qa_watcher"
BACKUP_CONFIG_PATH = f"{BASE_DIR}/config/backup_config.json"
BACKUP_TMUX = "backup_watcher"
PED_QA_CONFIG_PATH = f"{BASE_DIR}/config/pedestal_qa_config.json"
PED_QA_TMUX = "pedestal_watcher"
# Last run name seen in the daq_control log; persisted so "Current run" survives
# the status line scrolling out of the tmux pane / between runs / server restarts.
CURRENT_RUN_STATE_PATH = f"{BASE_DIR}/config/current_run_state.json"
# Post-sub-run pause flag; presence tells daq_control to wait at the next sub-run
# boundary. Path must match PAUSE_FLAG in daq_control.py (repo root).
PAUSE_FLAG_PATH = f"{BASE_DIR}/.pause_run"
# ANALYSIS_DIR = "/media/dylan/data/x17"
# RUN_DIR = "/media/dylan/data/x17/dream_run_test"
ANALYSIS_DIR = f'{BASE_DATA_DIR}analysis'
RUN_DIR = f'{BASE_DATA_DIR}runs'
GENERAL_ANALYSIS_DIR = f'{BASE_DATA_DIR}analysis'
HV_TAIL = 1000  # number of most recent rows to show

LOG_DIR = f"{BASE_DIR}/logs"
LOG_FILE = f"{LOG_DIR}/daq_events.log"

MONITOR_CONFIG_PATH = f"{BASE_DIR}/config/monitor_config.json"
monitor = DaqMonitor(MONITOR_CONFIG_PATH)


def log_event(event, source, **details):
    """Append one line to the DAQ event log."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={v}' for k, v in details.items())
        line = f"{ts} | {event:<14} | {source:<12} | {detail_str}\n"
        with open(LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"Warning: could not write to event log: {e}")


app = Flask(__name__)
socketio = SocketIO(app)

TMUX_SESSIONS = ["daq_control", "dream_daq", "hv_control", "processor_watcher", "qa_watcher", "backup_watcher",
                 "pedestal_watcher", "beam_watcher"]
sessions = {}

@app.route("/")
def index():
    configs = [f for f in os.listdir(CONFIG_RUN_DIR) if f.endswith(".json")]
    return render_template("index.html", screens=TMUX_SESSIONS, run_configs=configs)


# --- Current run tracking (from daq_control log, with persistence) ---
def _load_current_run():
    """Load the last-seen run name from disk (survives server restarts)."""
    try:
        with open(CURRENT_RUN_STATE_PATH) as f:
            return json.load(f).get("run_name")
    except Exception:
        return None


_current_run_cache = _load_current_run()


def _extract_daq_run(daq_info):
    """Pull the Run value out of a get_daq_control_status() result, or None."""
    for field in daq_info.get("fields", []):
        if field.get("label") == "Run":
            value = field.get("value")
            if value and value not in ("?", "None"):
                return value
    return None


def _save_current_run(run_name):
    """Persist run_name as the current run if it changed from what we have."""
    global _current_run_cache
    if not run_name or run_name == _current_run_cache:
        return
    _current_run_cache = run_name
    try:
        with open(CURRENT_RUN_STATE_PATH, "w") as f:
            json.dump({"run_name": run_name, "updated": datetime.now().isoformat()}, f)
    except Exception as e:
        print(f"[current_run] Failed to persist run name: {e}")


@app.route("/get_current_run")
def get_current_run():
    """Current run as last seen in the daq_control log, falling back to the
    persisted value so it doesn't blank out between runs."""
    return jsonify({"success": True, "run_name": _current_run_cache or "None"})


def _status_field(info, label):
    """Value of a named field in a get_*_status() result, or None."""
    for f in (info or {}).get("fields", []):
        if f.get("label") == label:
            return f.get("value")
    return None


def _hms_to_min(s):
    """'0h 1m 47s' -> minutes (float). Missing/garbage -> 0.0."""
    if not s:
        return 0.0
    m = re.search(r'(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?', s)
    if not m:
        return 0.0
    h, mm, ss = (int(g) if g else 0 for g in m.groups())
    return h * 60 + mm + ss / 60.0


def _fmt_min(minutes):
    """Minutes -> '50m' or '3h45m'."""
    t = int(round(minutes))
    h, m = divmod(t, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# On-disk event total only changes when a subrun completes, so cache it briefly:
# /status is polled every 1s and get_total_events_for_run walks every subrun's logs.
_events_cache = {"run": None, "t": 0.0, "total": 0}


def _ondisk_run_events(run_name):
    now = time.time()
    c = _events_cache
    if c["run"] == run_name and now - c["t"] < 4.0:
        return c["total"]
    try:
        total, _ = get_total_events_for_run(run_dir=RUN_DIR, run_name=run_name)
    except Exception:
        total = 0
    c.update(run=run_name, t=now, total=total)
    return total


def _live_events_from(dream_info):
    """Live nb_of_events from an already-fetched dream_daq status (no re-capture)."""
    if (dream_info or {}).get("status") != "RUNNING":
        return 0
    try:
        return int(str(_status_field(dream_info, "Subrun Events")).strip())
    except (TypeError, ValueError):
        return 0


def _run_progress(daq_info, dream_info):
    """{subrun_idx, subrun_total, elapsed_min, total_min} for the current run, from
    its run_config.json sub_runs + the live subrun name/elapsed. {} if unavailable.
    Elapsed = completed subruns' planned time + the current subrun's elapsed (capped
    at its planned length), so it pairs with the subrun index and never exceeds total."""
    run_name = _current_run_cache
    if not run_name:
        return {}
    try:
        with open(os.path.join(RUN_DIR, run_name, "run_config.json")) as f:
            subs = json.load(f).get("sub_runs", [])
    except Exception:
        return {}
    if not subs:
        return {}
    names = [s.get("sub_run_name") for s in subs]
    durs  = [float(s.get("run_time", 0) or 0) for s in subs]  # minutes
    prog  = {"subrun_total": len(subs), "total_min": sum(durs)}
    subrun = _status_field(daq_info, "Subrun")
    if subrun in names:
        i = names.index(subrun)
        cur = min(_hms_to_min(_status_field(dream_info, "Run Time")), durs[i])
        prog["subrun_idx"]  = i + 1
        prog["elapsed_min"] = sum(durs[:i]) + cur
    return prog


@app.route("/status")
def status_all():
    statuses = []
    by_name = {}

    for s in TMUX_SESSIONS:
        if s == "dream_daq":
            info = get_dream_daq_status()
        elif s == "hv_control":
            info = get_hv_control_status()
        elif s == "daq_control":
            info = get_daq_control_status()
            _save_current_run(_extract_daq_run(info))  # keep Current run in sync
        elif s == "processor_watcher":
            info = get_processor_watcher_status()
        elif s == "qa_watcher":
            info = get_qa_watcher_status()
        elif s == "backup_watcher":
            info = get_backup_watcher_status()
        elif s == "pedestal_watcher":
            info = get_pedestal_watcher_status()
        elif s == "beam_watcher":
            info = get_beam_watcher_status()
        else:
            info = {"status": "READY", "color": "secondary", "fields": []}

        entry = {"name": s, **info}
        statuses.append(entry)
        by_name[s] = entry

    # Enrich the dream_daq card with run progress (subrun x/N, elapsed/total time)
    # and the live "Events this run" total, so both refresh with the 1s /status poll
    # (instead of a separate slower timer).
    dream = by_name.get("dream_daq")
    if dream is not None:
        prog = _run_progress(by_name.get("daq_control"), dream)
        if prog.get("subrun_idx"):
            dream.setdefault("fields", []).append(
                {"label": "Subrun", "value": f'{prog["subrun_idx"]}/{prog["subrun_total"]}'})
            dream["fields"].append(
                {"label": "Progress",
                 "value": f'{_fmt_min(prog["elapsed_min"])} / {_fmt_min(prog["total_min"])}'})
        elif prog.get("subrun_total"):
            dream.setdefault("fields", []).append(
                {"label": "Subrun", "value": f'–/{prog["subrun_total"]}'})
        if _current_run_cache:
            dream["run_events"] = _ondisk_run_events(_current_run_cache) + _live_events_from(dream)

    # Surface whether a post-sub-run pause is armed so the button reflects it.
    daq = by_name.get("daq_control")
    if daq is not None:
        daq["pause_armed"] = os.path.exists(PAUSE_FLAG_PATH)

    return jsonify(statuses)


@app.route("/start_run", methods=["POST"])
def start_run():
    data = request.get_json()
    config_file = data.get("config")

    if not config_file:
        return jsonify({"message": "No config selected"}), 400

    config_path = os.path.join(CONFIG_RUN_DIR, config_file)
    if not os.path.exists(config_path):
        return jsonify({"message": f"Config not found: {config_path}"}), 404

    script_path = f"{BASH_DIR}/start_run.sh"
    result = subprocess.run(
        [script_path, config_path],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        return jsonify({"message": f"Run started with {config_file}"})
    else:
        return jsonify({"message": f"Error: {result.stderr}"}), 500

@app.route("/stop_sub_run", methods=["POST"])
def stop_sub_run():
    try:
        if is_dream_daq_running():
            log_event('STOP_SUB_RUN', 'flask_button', remote_addr=request.remote_addr)
            subprocess.Popen([f"{BASH_DIR}/stop_sub_run.sh"])
            return jsonify({"success": True, "message": "Stopping Sub-Run"})
        else:
            return jsonify({"success": False, "message": "Dream DAQ is not running"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/stop_run", methods=["POST"])
def stop_run():
    try:
        # Always stop the WHOLE run. stop_run.sh drops the .stop_run flag that
        # daq_control honors at its next checkpoint (before the next sub-run, or
        # before (re)starting the DAQ), so the run ends and HV powers off even when
        # we're mid HV-ramp / file-copy / between sub-runs — states where the DAQ
        # isn't "running". stop_dream.sh safely no-ops if RunCtrl isn't running.
        # (Previously this fell back to stop_sub_run.sh when the DAQ wasn't actively
        # taking data, which only stopped the current sub-run and let the run go on.)
        dream_running = is_dream_daq_running()
        log_event('STOP_RUN', 'flask_button', remote_addr=request.remote_addr,
                  dream_running=dream_running)
        subprocess.Popen([f"{BASH_DIR}/stop_run.sh"])
        return jsonify({"success": True, "message": "Stopping Run"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/toggle_pause_run", methods=["POST"])
def toggle_pause_run():
    """Arm/clear the post-sub-run pause. Presence of the flag file tells daq_control
    to wait at the next sub-run boundary; removing it resumes (one-shot)."""
    try:
        if os.path.exists(PAUSE_FLAG_PATH):
            os.remove(PAUSE_FLAG_PATH)
            log_event('RESUME_RUN', 'flask_button', remote_addr=request.remote_addr)
            return jsonify({"success": True, "paused": False,
                            "message": "Pause cleared — run continues"})
        else:
            open(PAUSE_FLAG_PATH, "w").close()
            log_event('PAUSE_RUN', 'flask_button', remote_addr=request.remote_addr)
            return jsonify({"success": True, "paused": True,
                            "message": "Will pause after the current sub-run"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/restart_all", methods=["POST"])
def restart_all():
    try:
        subprocess.Popen([f"{BASH_DIR}/restart_daq_tmux_processes.sh"])
        return jsonify({"success": True, "message": "All processes restarted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/restart_flask", methods=["POST"])
def restart_flask():
    """Restart ONLY the Flask GUI server (tmux `flask_server`), leaving the DAQ,
    HV, and watcher sessions running — the "Restart GUI" button. Picks up edited
    Python/template code without disturbing a live run.

    The restart runs detached (screen) because it kills the process serving this
    request, so this response is sent ~2 s before the server goes down; the GUI
    drops for ~3 s and the page reconnects itself."""
    try:
        subprocess.Popen([f"{BASH_DIR}/restart_flask.sh"])
        return jsonify({"success": True,
                        "message": "GUI restarting — reconnecting in a few seconds…"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/update_run_config_py", methods=['POST'])
def update_run_config_py():
    try:
        subprocess.Popen([VENV_PYTHON, f"{BASE_DIR}/iterate_run_num.py"])
        time.sleep(0.2)  # Give it a moment to complete

        return jsonify({"success": True, "message": f"Run number iterated"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/run_config_py", methods=['POST'])
def run_config_py():
    try:
        subprocess.Popen([VENV_PYTHON, f"{BASE_DIR}/run_config_beam.py"])
        time.sleep(1)
        config_path = os.path.join(CONFIG_RUN_DIR, 'run_config_beam.json')
        if not os.path.exists(config_path):
            return jsonify({"message": f"Config not found: {config_path}"}), 404

        script_path = f"{BASH_DIR}/start_run.sh"
        result = subprocess.run(
            [script_path, config_path],
            capture_output=True,
            text=True
        )

        # Load config path json to get run name
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            run_name = cfg.get("run_name", "Unknown")
        except Exception as e:
            run_name = "Error loading run name"

        if result.returncode == 0:
            _save_current_run(run_name)  # seed Current run immediately
            return jsonify({"success": True, "message": f"Run started with loaded run_config_beam.py", "run_name": run_name})
        else:
            return jsonify({"message": f"Error: {result.stderr}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Run Setup builder — operators configure a run (detectors, HV schedule, trigger)
# from the GUI, written to config/gui_run_config.json which run_config_beam.py
# picks up as an additive override. No effect until saved with "enabled": true.
# ---------------------------------------------------------------------------
@app.route("/run_builder/config", methods=["GET"])
def run_builder_config():
    try:
        import gui_run_config as grc
        if request.args.get("defaults"):
            gui = grc.defaults_from_code()
            gui["_from_defaults"] = True
            return jsonify({"success": True, "config": gui})
        gui = grc.load()
        if gui is None:
            # No enabled file yet: hand the UI a seed built from the code defaults
            # (in-memory; the file is only created when the operator saves).
            gui = grc.defaults_from_code()
            gui["_from_defaults"] = True
        return jsonify({"success": True, "config": gui})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/run_builder/options", methods=["GET"])
def run_builder_options():
    try:
        import gui_run_config as grc
        # DET_HV, not the old P2_HV: renamed when the 2 uRWELL references joined
        # the 3 P2 stations, so this now also offers card 12 (uRWELL 'resist')
        # and lists all five detectors as templates.
        from run_config_beam import DET_HV
        hv_cards = sorted({card for det in DET_HV.values()
                           for (card, _chan) in det.values()})
        return jsonify({
            "success": True,
            "run_types": grc.RUN_TYPES,
            "trigger_modes": grc.TRIGGER_MODES,
            "gas_presets": grc.GAS_PRESETS,
            "hv_cards": hv_cards,
            "n_channels_per_card": 12,
            "detector_templates": list(DET_HV.keys()),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/run_builder/preview", methods=["POST"])
def run_builder_preview():
    try:
        import gui_run_config as grc
        gui = request.get_json(force=True) or {}
        ok, errors = grc.validate(gui)
        preview = grc.preview(gui)
        return jsonify({"success": True, "valid": ok, "errors": errors, "preview": preview})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/run_builder/save", methods=["POST"])
def run_builder_save():
    try:
        import gui_run_config as grc
        gui = request.get_json(force=True) or {}
        gui.pop("_from_defaults", None)
        ok, errors = grc.validate(gui)
        if not ok:
            return jsonify({"success": False, "valid": False, "errors": errors,
                            "message": "Validation failed — not saved."}), 400

        os.makedirs(os.path.dirname(grc.GUI_CONFIG_PATH), exist_ok=True)
        with open(grc.GUI_CONFIG_PATH, "w") as f:
            json.dump(gui, f, indent=4)

        # Regenerate config/json_run_configs/run_config_beam.json so Start Run
        # picks up the new schedule (same as /run_config_py does before starting).
        result = subprocess.run(
            [VENV_PYTHON, f"{BASE_DIR}/run_config_beam.py"],
            cwd=BASE_DIR, capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "valid": True, "errors": [],
                            "message": f"Saved, but config generation failed: {result.stderr}"}), 500

        log_event("run_setup", "gui", run_name=gui.get("run_name", "?"),
                  run_type=gui.get("run_type", "?"),
                  enabled=gui.get("enabled", False))
        return jsonify({"success": True, "valid": True, "errors": [],
                        "message": f"Saved run setup for {gui.get('run_name', '?')} "
                                   f"and regenerated run_config_beam.json."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# Pedestal launches are serialised and debounced (2026-07-27). run_pedestals.sh
# regenerates the pedestal config and then start_run.sh does `tmux send-keys` into
# the daq_control pane. send-keys TYPES the command, it does not execute it, so a
# second launch while the first is still running does not fail loudly — it sits in
# that shell's input buffer and runs the instant the first one exits.
#
# Two guards, because neither alone is sufficient:
#   - busy: refuse while daq_control is doing anything. This is the one that stops
#     "take pedestals in the middle of a beam run".
#   - cooldown: on 2026-07-27 seven clicks landed inside a single second and queued
#     seven pedestal runs (14:14 -> 14:27, five pedestal sets into one run dir).
#     daq_control still read "Run Complete" from the previous run for all seven, so
#     the busy check alone would have passed every one of them. The cooldown covers
#     the gap between send-keys and the pane reflecting the new process.
#
# Neither guard can be replaced by a "stop" afterwards: daq_control clears
# .stop_run on startup, so Stop Run cannot drain a queue that has already formed.
#
# UNKNOWN STATE is a third case, neither idle nor busy: get_daq_control_status()
# reports it when the last 50 lines of the daq_control pane match none of its
# status rules — a scrolled or stale pane (someone ran `ls` in it), which usually
# means nothing is running at all. Refusing outright would leave no way to take
# pedestals from the GUI; allowing it silently would hide a live run the GUI can't
# see. So it is overridable, but only behind a second confirmation: the client
# re-POSTs with confirm_unknown=1. Genuinely busy states are never overridable.
PEDESTAL_IDLE_STATES = ("WAITING", "Run Complete", "ERROR")
PEDESTAL_UNKNOWN_STATE = "UNKNOWN STATE"
PEDESTAL_COOLDOWN_S = 60
_pedestal_launch_lock = threading.Lock()
_last_pedestal_launch = float("-inf")


@app.route("/take_pedestals", methods=["POST"])
def take_pedestals():
    global _last_pedestal_launch
    try:
        with _pedestal_launch_lock:
            waited = time.monotonic() - _last_pedestal_launch
            if waited < PEDESTAL_COOLDOWN_S:
                return jsonify({
                    "success": False,
                    "message": f"Pedestals already launching — ignoring duplicate "
                               f"click ({int(PEDESTAL_COOLDOWN_S - waited)}s to go)."
                }), 409

            daq_state = get_daq_control_status().get("status", PEDESTAL_UNKNOWN_STATE)
            if daq_state == PEDESTAL_UNKNOWN_STATE:
                # Not a refusal — an "are you sure". needs_confirm tells the client
                # to ask a second time and re-POST with confirm_unknown=1.
                if request.args.get("confirm_unknown") != "1":
                    return jsonify({
                        "success": False,
                        "needs_confirm": True,
                        "daq_state": daq_state,
                        "message": f"daq_control state could not be read "
                                   f"({daq_state}) — confirm before launching."
                    }), 409
            elif daq_state not in PEDESTAL_IDLE_STATES:
                return jsonify({
                    "success": False,
                    "message": f"daq_control is busy ({daq_state}) — stop the run "
                               f"before taking pedestals."
                }), 409

            subprocess.Popen([f"{BASH_DIR}/run_pedestals.sh"])
            _last_pedestal_launch = time.monotonic()
        return jsonify({"success": True, "message": "Taking pedestals"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/git_reset", methods=["POST"])
def git_reset():
    try:
        subprocess.Popen([f"{BASH_DIR}/git_reset.sh"])
        return jsonify({"success": True, "message": "Git now up to date"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_processor", methods=["POST"])
def start_processor():
    try:
        # Regenerate processor_config.json from processor_config.py
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/processor_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500

        # Kill any existing session first (ignore errors if not running)
        subprocess.run(["tmux", "kill-session", "-t", PROCESSOR_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # login shell resets PATH and drops the venv, so "python" may not resolve.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", PROCESSOR_TMUX,
            sys.executable, f"{BASE_DIR}/processor_watcher.py", PROCESSOR_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "Processor watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_processor", methods=["POST"])
def stop_processor():
    try:
        subprocess.run(["tmux", "kill-session", "-t", PROCESSOR_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "Processor watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_qa", methods=["POST"])
def start_qa():
    try:
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/qa_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500
        subprocess.run(["tmux", "kill-session", "-t", QA_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # login shell resets PATH and drops the venv, so "python" may not resolve.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", QA_TMUX,
            sys.executable, f"{BASE_DIR}/qa_watcher.py", QA_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "QA watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_qa", methods=["POST"])
def stop_qa():
    try:
        subprocess.run(["tmux", "kill-session", "-t", QA_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "QA watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_backup", methods=["POST"])
def start_backup():
    try:
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/backup_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500
        subprocess.run(["tmux", "kill-session", "-t", BACKUP_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # login shell resets PATH and drops the venv, so "python" may not resolve.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", BACKUP_TMUX,
            sys.executable, f"{BASE_DIR}/backup_watcher.py", BACKUP_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "Backup watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_backup", methods=["POST"])
def stop_backup():
    try:
        subprocess.run(["tmux", "kill-session", "-t", BACKUP_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "Backup watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_beam_watcher", methods=["POST"])
def start_beam_watcher():
    """Start the beam-intensity feed in the 'beam_watcher' tmux session.

    Two modes (SPS_BEAM_MODE env, default 'bridge'):
      * bridge — the DAQ machine (banco) is off the CERN network and cannot reach
        NXCALS, so it pulls the beam_state.json/CSVs the lxplus watcher publishes
        to EOS (beam_bridge.py, runs under the Flask venv + xrdcp + Kerberos).
      * direct — a CERN-network DAQ machine queries NXCALS itself
        (beam_watcher.py under the NXCALS venv)."""
    try:
        mode = os.environ.get("SPS_BEAM_MODE", "bridge")
        subprocess.run(["tmux", "kill-session", "-t", "beam_watcher"], capture_output=True)
        if mode == "direct":
            if not os.path.exists(NXCALS_PYTHON):
                return jsonify({"success": False,
                                "message": f"NXCALS venv missing: {NXCALS_PYTHON} "
                                           f"(see beam_monitor/README.md)"}), 500
            cmd = [NXCALS_PYTHON, f"{BASE_DIR}/beam_watcher.py"]
            msg = "Beam watcher started (direct NXCALS; first query ~1 min)"
        else:
            cmd = [sys.executable, f"{BASE_DIR}/beam_bridge.py"]
            msg = "Beam bridge started (pulling beam state from EOS)"
        subprocess.Popen(["tmux", "new-session", "-d", "-s", "beam_watcher", *cmd])
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_beam_watcher", methods=["POST"])
def stop_beam_watcher():
    """Stop the beam watcher. Beam-intensity logging pauses until it restarts."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "beam_watcher"], capture_output=True)
        return jsonify({"success": True, "message": "Beam watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_ped_qa", methods=["POST"])
def start_ped_qa():
    try:
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/pedestal_qa_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500
        subprocess.run(["tmux", "kill-session", "-t", PED_QA_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # server env doesn't always carry the venv PATH, so name resolution
        # inside new sessions is unreliable.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", PED_QA_TMUX,
            sys.executable, f"{BASE_DIR}/pedestal_watcher.py", PED_QA_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "Pedestal QA watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_ped_qa", methods=["POST"])
def stop_ped_qa():
    try:
        subprocess.run(["tmux", "kill-session", "-t", PED_QA_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "Pedestal QA watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _ped_qa_cfg():
    """(pedestals_dir, output_inner_dir) from the ped QA config, with the same
    defaults pedestal_qa_config.py writes (config may not exist yet)."""
    try:
        with open(PED_QA_CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    return (cfg.get("pedestals_dir", f"{BASE_DATA_DIR}pedestals/"),
            cfg.get("output_inner_dir", "ped_qa"))


@app.route("/list_ped_runs")
def list_ped_runs():
    """Pedestal run dirs (newest first) with whether QA output exists yet."""
    ped_dir, inner_dir = _ped_qa_cfg()

    if not os.path.isdir(ped_dir):
        return jsonify(success=False, message=f"Pedestals dir not found: {ped_dir}")

    def run_sort_key(name, full):
        # Prefer the datetime in the dir name (pedestals_MM-DD-YY_HH-MM-SS);
        # dir mtime is unreliable since QA output writes touch the dir.
        # Both key kinds are epoch floats so they compare consistently.
        m = re.search(r'(\d{2})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})', name)
        if m:
            try:
                mo, d, y, h, mi, s = (int(g) for g in m.groups())
                return datetime(2000 + y, mo, d, h, mi, s).timestamp()
            except ValueError:
                pass
        return os.path.getmtime(full)

    runs = []
    for d in os.listdir(ped_dir):
        full = os.path.join(ped_dir, d)
        if not os.path.isdir(full):
            continue
        runs.append({
            "name": d,
            "sort_key": run_sort_key(d, full),
            "has_qa": os.path.isfile(os.path.join(full, inner_dir, "summary.json")),
        })
    runs.sort(key=lambda r: r["sort_key"], reverse=True)
    return jsonify(success=True, runs=runs, inner_dir=inner_dir, ped_dir=ped_dir)


@app.route("/ped_qa_data")
def ped_qa_data():
    """Summary JSON + image/PDF URLs for one pedestal run's QA output."""
    run_name = request.args.get("run", "")
    ped_dir, inner_dir = _ped_qa_cfg()

    # Plain directory names only — no separators, no '.'/'..' path tricks
    if not re.fullmatch(r'(?!\.+$)[\w.\-]+', run_name):
        return jsonify(success=False, message="Invalid run name"), 400
    qa_dir = os.path.join(ped_dir, run_name, inner_dir)
    if not os.path.isdir(qa_dir):
        return jsonify(success=True, has_qa=False, summary=None, images=[], pdf=None)

    summary = None
    summary_path = os.path.join(qa_dir, "summary.json")
    if os.path.isfile(summary_path):
        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except Exception:
            pass

    dir_q  = quote(qa_dir, safe='')
    images = [f"/serve_png?dir={dir_q}&file={quote(f, safe='')}"
              for f in sorted(os.listdir(qa_dir)) if f.lower().endswith(".png")]
    pdf = None
    if os.path.isfile(os.path.join(qa_dir, "pedestal_strip_check.pdf")):
        pdf = f"/serve_png?dir={dir_q}&file=pedestal_strip_check.pdf"

    return jsonify(success=True, has_qa=summary is not None,
                   summary=summary, images=images, pdf=pdf)


@app.route("/rerun_qa", methods=["POST"])
def rerun_qa():
    try:
        data = request.get_json(silent=True) or {}
        runs = data.get('runs') or None  # null/missing/empty → all runs
        with open(QA_RESET_PATH, 'w') as f:
            json.dump({"runs": runs}, f)
        if runs:
            msg = f"QA rerun queued for: {', '.join(runs)}"
        else:
            msg = "QA rerun queued for all runs"
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/get_runs")
def get_runs():
    runs = []
    for f in os.listdir(CONFIG_RUN_DIR):
        if f.endswith(".json"):
            runs.append(f)
    return jsonify(runs)

def _run_has_hv_data(run_dir, hv_file="hv_monitor.csv"):
    """True if any subrun directory under run_dir has an HV monitor CSV."""
    if not run_dir or not os.path.isdir(run_dir):
        return False
    for sub in os.listdir(run_dir):
        if os.path.isfile(os.path.join(run_dir, sub, hv_file)):
            return True
    return False


def _load_hv_cfg(run_name):
    """Run config dict for the HV panel. run_name is a json filename under
    CONFIG_RUN_DIR, or 'auto': pick the config whose run_out_dir holds the most
    recently modified hv_monitor.csv — i.e. follow whatever run (beam,
    pedestals, ...) is currently writing HV data. None if nothing matches."""
    if run_name and run_name != "auto":
        config_path = os.path.join(CONFIG_RUN_DIR, run_name)
        if not os.path.isfile(config_path):
            return None
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            return None
    best_cfg, best_mtime = None, -1.0
    try:
        json_names = [f for f in os.listdir(CONFIG_RUN_DIR) if f.endswith(".json")]
    except OSError:
        json_names = []
    for name in json_names:
        try:
            with open(os.path.join(CONFIG_RUN_DIR, name)) as f:
                cfg = json.load(f)
        except Exception:
            continue
        run_dir = _hv_run_dir(cfg)
        mtime = _newest_hv_mtime(run_dir)
        if mtime > best_mtime:
            best_cfg, best_mtime = cfg, mtime
    return best_cfg


def _newest_hv_mtime(run_dir, hv_file="hv_monitor.csv"):
    """Most recent mtime of any subrun's HV monitor CSV under run_dir, or -1."""
    newest = -1.0
    if not run_dir or not os.path.isdir(run_dir):
        return newest
    for sub in os.listdir(run_dir):
        csv_path = os.path.join(run_dir, sub, hv_file)
        if os.path.isfile(csv_path):
            newest = max(newest, os.path.getmtime(csv_path))
    return newest


def _hv_run_dir(cfg, hv_file="hv_monitor.csv"):
    """Run directory the HV plot should read: normally the config's run_out_dir, but
    when that run has no HV data yet (a run just started, or between runs while the
    config already points at the next one) fall back to the most recent run under
    RUN_DIR that does — so the plot shows the previous run instead of going blank at
    run boundaries. None if nothing has HV data."""
    primary = cfg.get("run_out_dir")
    if _run_has_hv_data(primary, hv_file):
        return primary
    # Fall back to sibling runs of the same kind first (e.g. other runs under
    # pedestals/ for a pedestal config), then the beam runs dir — so a freshly
    # regenerated config (whose own run dir doesn't exist yet) still shows the
    # latest run that has HV data.
    candidates = []
    for coll_dir in ([os.path.dirname(primary.rstrip('/'))] if primary else []) + [RUN_DIR]:
        try:
            candidates.extend(os.path.join(coll_dir, d) for d in os.listdir(coll_dir))
        except OSError:
            continue
    try:
        candidates = sorted(dict.fromkeys(candidates), key=os.path.getmtime, reverse=True)
    except OSError:
        candidates = []
    for d in candidates:
        if _run_has_hv_data(d, hv_file):
            return d
    return primary if (primary and os.path.isdir(primary)) else None


@app.route("/get_subruns")
def get_subruns():
    run_name = request.args.get("run")
    if not run_name:
        return jsonify([])

    try:
        cfg = _load_hv_cfg(run_name)
        if cfg is None:
            return jsonify([])
        run_dir = _hv_run_dir(cfg)
        if not run_dir:
            return jsonify([])

        # Only offer subruns that actually have an HV monitor CSV, so the selector
        # never lands on an empty subrun (what blanks the plot at run boundaries).
        # This replaces the old cfg['sub_runs'] name match, which returned nothing
        # when run_out_dir and sub_runs briefly disagreed during a run transition.
        subruns = [d for d in os.listdir(run_dir)
                   if os.path.isfile(os.path.join(run_dir, d, "hv_monitor.csv"))]
        subruns.sort(key=lambda f: os.path.getmtime(os.path.join(run_dir, f)), reverse=True)
        return jsonify(subruns)
    except Exception as e:
        print("Error reading subruns:", e)
        return jsonify([])

@app.route("/get_run_name")
def get_run_name():
    run_name = request.args.get("run")
    if not run_name:
        return jsonify({"success": False, "message": "No run specified"}), 400

    config_path = os.path.join(CONFIG_RUN_DIR, run_name)
    if not os.path.isfile(config_path):
        return jsonify({"success": False, "message": "Run config not found"}), 404

    try:
        with open(config_path) as f:
            cfg = json.load(f)
        actual_run_name = cfg.get("run_name", "Unknown")
        return jsonify({"success": True, "run_name": actual_run_name})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _hv_channel_labels(cfg):
    """{'slot:channel' -> 'A_Drift'} from a run config's detectors[].hv_channels.
    Detector label = the name suffix after the last '_' (mx17_A -> A); electrode
    capitalized (drift -> Drift, resist -> Resist, bias -> Bias)."""
    labels = {}
    for det in cfg.get("detectors", []):
        name  = str(det.get("name", ""))
        short = name.rsplit("_", 1)[-1] or name
        for electrode, ch in (det.get("hv_channels") or {}).items():
            try:
                slot, channel = ch
            except (TypeError, ValueError):
                continue
            labels[f"{slot}:{channel}"] = f"{short}_{str(electrode).title()}"
    return labels


@app.route("/hv_data")
def hv_data():
    try:
        run_name = request.args.get("run")
        subrun_name = request.args.get("subrun")
        hv_file_name = request.args.get("hv_file", "hv_monitor.csv")

        cfg = _load_hv_cfg(run_name)
        if cfg is None:
            return jsonify([])
        # Resolve the same run dir as /get_subruns (with the previous-run fallback),
        # so the subrun the selector offers is found here too.
        output_dir = _hv_run_dir(cfg, hv_file_name)
        if not output_dir:
            return jsonify([])
        hv_csv_path = os.path.join(output_dir, subrun_name, hv_file_name)

        df = pd.read_csv(hv_csv_path)
        df = df.tail(HV_TAIL)

        # Extract timestamps
        time = df["timestamp"].astype(str).tolist()

        # Map "slot:channel" -> detector label (e.g. "A_Drift") from the run config's
        # detectors[].hv_channels. Label = detector name suffix (mx17_A -> A) + the
        # capitalized electrode (drift -> Drift). Channels absent from the config keep
        # their raw "slot:channel" name.
        chan_label = _hv_channel_labels(cfg)

        voltage_data = {}
        current_data = {}

        # Loop through columns to find slot:channel prefixes
        for col in df.columns:
            if "vmon" in col:
                key = col.replace(" vmon", "")
                voltage_data[chan_label.get(key, key)] = df[col].tolist()
            elif "imon" in col:
                key = col.replace(" imon", "")
                current_data[chan_label.get(key, key)] = df[col].tolist()

        # Sort by label so each detector's traces group together (A_Drift, A_Resist, …)
        voltage_data = dict(sorted(voltage_data.items()))
        current_data = dict(sorted(current_data.items()))

        return jsonify({
            "success": True,
            "time": time,
            "voltage": voltage_data,
            "current": current_data
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/list_analysis_dirs")
def list_analysis_dirs():
    subdir = request.args.get("subdir", "")
    target_dir = os.path.join(ANALYSIS_DIR, subdir)

    if not os.path.isdir(target_dir):
        return jsonify(success=False, message=f"Invalid directory: {target_dir}")

    dirs = [d for d in os.listdir(target_dir)
            if os.path.isdir(os.path.join(target_dir, d))]
    dirs.sort()

    return jsonify(success=True, subdirs=dirs)

@app.route("/list_pngs")
def list_pngs():
    directory = request.args.get("dir")
    directory = os.path.join(ANALYSIS_DIR, directory)
    if not directory:
        return jsonify(success=False, message="No directory specified")
    if not os.path.isdir(directory):
        return jsonify(success=False, message=f"Invalid directory: {directory}")

    pngs = sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))
    if not pngs:
        return jsonify(success=True, images=[])

    # Create static-serving routes for these files
    image_urls = [f"/serve_png?dir={directory}&file={f}" for f in pngs]
    return jsonify(success=True, images=image_urls)


@app.route("/serve_png")
def serve_png():
    directory = request.args.get("dir")
    filename = request.args.get("file")
    if not directory or not filename:
        abort(400, "Missing parameters")
    if not os.path.isfile(os.path.join(directory, filename)):
        abort(404, "File not found")
    return send_from_directory(directory, filename)


@app.route("/browse_analysis")
def browse_analysis():
    rel_path = request.args.get("path", "").strip("/")
    target = os.path.normpath(os.path.join(GENERAL_ANALYSIS_DIR, rel_path)) if rel_path \
             else os.path.normpath(GENERAL_ANALYSIS_DIR)

    # Prevent path traversal outside the analysis directory
    if not target.startswith(os.path.abspath(GENERAL_ANALYSIS_DIR)):
        return jsonify(success=False, message="Invalid path"), 403
    if not os.path.isdir(target):
        return jsonify(success=False, message=f"Directory not found: {target}")

    subdirs = sorted(d for d in os.listdir(target)
                     if os.path.isdir(os.path.join(target, d)))
    images  = [f"/serve_png?dir={quote(target, safe='')}&file={quote(f, safe='')}"
               for f in sorted(os.listdir(target))
               if f.lower().endswith(".png")]

    return jsonify(success=True, subdirs=subdirs, images=images, path=rel_path)


@app.route("/get_config_py", methods=['GET'])
def get_config_py():
    try:
        # Call get_config function from run_config_beam.py
        result = subprocess.run(
            [VENV_PYTHON, f"{BASE_DIR}/get_config_py.py"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Error: {result.stderr}"}), 500
        output = result.stdout.strip()
        config_data = json.loads(output)
        run_name = config_data.get("run_name", "Unknown")

        return jsonify({
            "success": True,
            "run_name": run_name,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _live_dream_events():
    """Live per-FEU event count of the in-progress subrun (nb_of_events ≈ the per-FEU
    physics count), captured fresh from the dream_daq pane. Only while RUNNING; 0
    otherwise. The in-progress subrun has no RunCtrl log yet, so get_total_events_for_run()
    excludes it; adding this keeps 'Events this run' live without double-counting —
    once the subrun finishes, status leaves RUNNING and the count appears on disk."""
    return _live_events_from(get_dream_daq_status())


@app.route("/get_run_events", methods=['GET'])
def get_run_events():
    try:
        # Count events for the run daq_control is actually running (not the
        # possibly-edited run_config_beam.py). Falls back to the persisted value.
        run_name = _current_run_cache
        if not run_name:
            return jsonify({"success": True, "total_events": 0,
                            "live_events": 0, "subrun_details": {}})
        total_events, subrun_details = get_total_events_for_run(
            run_dir=RUN_DIR,
            run_name=run_name
        )
        # Add the in-progress subrun's live events (not yet on disk) so the total
        # reflects the live count shown in the dream_daq card.
        live_events = _live_dream_events()
        return jsonify({
            "success": True,
            "total_events": total_events + live_events,
            "live_events": live_events,
            "subrun_details": subrun_details
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error getting run events: {str(e)}"}), 500


@app.route("/monitor/toggle", methods=["POST"])
def monitor_toggle():
    monitor.toggle()
    return jsonify({"running": monitor.is_running})


@app.route("/monitor/status")
def monitor_status():
    return jsonify(monitor.status_dict())


@app.route("/monitor/fetch_chat_id", methods=["POST"])
def monitor_fetch_chat_id():
    if not monitor.token:
        return jsonify({"success": False, "message": "No Telegram token configured."})
    chat_id, err = fetch_chat_id(monitor.token)
    if err:
        return jsonify({"success": False, "message": err})
    monitor.set_chat_id(chat_id)
    return jsonify({"success": True, "chat_id": chat_id})


@app.route("/monitor/set_chat_id", methods=["POST"])
def monitor_set_chat_id():
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    if chat_id is None:
        return jsonify({"success": False, "message": "No chat_id provided."})
    monitor.set_chat_id(int(chat_id))
    return jsonify({"success": True, "chat_id": monitor.chat_id})


@app.route("/monitor/test", methods=["POST"])
def monitor_test():
    ok, err = monitor.send_test_alert()
    if ok:
        return jsonify({"success": True, "message": "Test alert sent."})
    return jsonify({"success": False, "message": err or "Unknown error"})


@app.route("/monitor/bot_info")
def monitor_bot_info():
    if not monitor.token:
        return jsonify({"success": False})
    username, err = get_bot_username(monitor.token)
    if err:
        return jsonify({"success": False, "message": err})
    return jsonify({"success": True, "username": username})


# Network interfaces and physical disks to report I/O rates for. x17 hardcodes
# these per-machine; here they are auto-detected so the same code runs on banco
# and on the local dev/sim setup: every non-virtual NIC, plus the block devices
# backing / and BASE_DATA_DIR (a single entry when they share a filesystem —
# the banco single_disk case).
def _detect_net_ifaces():
    try:
        import psutil
        virtual = ("lo", "veth", "docker", "br-", "virbr", "tun", "tap", "vnet")
        return sorted(n for n in psutil.net_io_counters(pernic=True)
                      if not n.startswith(virtual))
    except Exception:
        return []


def _block_dev_for_path(path):
    """Base block device name (e.g. 'sda', 'nvme0n1') backing path, or None.
    Resolves the st_dev major:minor through /sys/dev/block; a partition symlink
    ends .../block/<disk>/<part>, a whole disk (or dm device) .../block/<dev>."""
    try:
        st = os.stat(path)
        link = os.readlink(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}")
        parent = os.path.basename(os.path.dirname(link))
        return parent if parent != "block" else os.path.basename(link)
    except OSError:
        return None


_NET_IFACES = _detect_net_ifaces()
_DISK_DEVS = {}
for _key, _path in (("ssd", "/"), ("hdd", BASE_DATA_DIR)):
    _dev = _block_dev_for_path(_path)
    if _dev and _dev not in _DISK_DEVS.values():
        _DISK_DEVS[_key] = _dev

# Previous I/O counter sample, kept between /system_stats calls to derive rates.
_io_prev = {"t": None, "net": None, "disk": None}


@app.route("/system_stats")
def system_stats():
    try:
        import psutil
        cpu_pcts = psutil.cpu_percent(percpu=True)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        load = os.getloadavg()

        def disk_stats(path):
            try:
                d = psutil.disk_usage(path)
                return {"total": d.total, "used": d.used, "percent": d.percent}
            except Exception:
                return None

        ssd = disk_stats('/')            # OS/system SSD
        hdd = disk_stats(BASE_DATA_DIR)  # data disk (from run_config_beam SITE)
        # On banco the data dir lives on the OS NVMe — same filesystem. Flag it
        # so the page shows a single "Disk" row instead of identical SSD/HDD.
        try:
            single_disk = os.stat('/').st_dev == os.stat(BASE_DATA_DIR).st_dev
        except OSError:
            single_disk = False

        # ---- I/O rates (bytes/sec) derived from the previous sample ----
        now = time.monotonic()
        net_ctr = psutil.net_io_counters(pernic=True)
        # psutil can fail to parse /proc/diskstats lines on some kernel/psutil
        # combinations (seen on banco with loop devices). Disk I/O rates then
        # just read 0 — never fail the whole system_stats response over it.
        try:
            disk_ctr = psutil.disk_io_counters(perdisk=True)
        except Exception:
            disk_ctr = {}
        prev = _io_prev
        dt = (now - prev["t"]) if prev["t"] else None

        def rate(cur, prev_val):
            if dt and dt > 0 and prev_val is not None:
                return max(0.0, (cur - prev_val) / dt)
            return 0.0

        net_rates = {}
        for name in _NET_IFACES:
            cur = net_ctr.get(name)
            p = (prev["net"] or {}).get(name)
            if cur:
                net_rates[name] = {
                    "rx": rate(cur.bytes_recv, p.bytes_recv if p else None),
                    "tx": rate(cur.bytes_sent, p.bytes_sent if p else None),
                }
            else:
                net_rates[name] = None

        disk_rates = {}
        for key, dev in _DISK_DEVS.items():
            cur = disk_ctr.get(dev)
            p = (prev["disk"] or {}).get(dev)
            if cur:
                disk_rates[key] = {
                    "read":  rate(cur.read_bytes,  p.read_bytes if p else None),
                    "write": rate(cur.write_bytes, p.write_bytes if p else None),
                }
            else:
                disk_rates[key] = None

        _io_prev["t"] = now
        _io_prev["net"] = net_ctr
        _io_prev["disk"] = disk_ctr

        return jsonify({
            "success": True,
            "cpu_cores": cpu_pcts,
            "memory": {"total": mem.total, "used": mem.used, "percent": mem.percent},
            "swap":   {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "ssd":    ssd,
            "hdd":    hdd,
            "single_disk": single_disk,
            "net":    net_rates,
            "disk_io": disk_rates,
            "load_avg": list(load),
        })
    except ImportError:
        return jsonify({"success": False, "message": "psutil not installed"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# Logged system-stats CSVs (per-day) for /system_stats/history. x17 has a separate
# system_stats_watcher (system_monitor/) writing here; this repo doesn't run one
# yet, so until it exists the route just returns empty history and the Overview
# plots fill in live. Kept in the same slow_control tree as the beam logs.
SYSTEM_STATS_LOG_DIR = os.path.join(os.path.dirname(BEAM_LOG_DIR.rstrip('/')),
                                    "system_stats")


@app.route("/system_stats/history")
def system_stats_history():
    """Logged system-resource history from the per-day CSV(s) a system_stats_watcher
    writes, so the Overview plots come up already populated instead of filling in live.
    `minutes` trims to a recent window; the result is downsampled to keep the payload
    light. Net/disk rates are summed across interfaces/devices to match the live plots."""
    import glob
    minutes = request.args.get("minutes", default=30.0, type=float)
    max_points = request.args.get("max_points", default=600, type=int)
    empty = {"success": True, "time": [], "cpu": [], "cpu_avg": [], "mem": [],
             "swap": [], "net_rx": [], "net_tx": [], "disk_r": [], "disk_w": []}
    try:
        files = sorted(glob.glob(os.path.join(SYSTEM_STATS_LOG_DIR, "system_stats_*.csv")))
        if not files:
            return jsonify(empty)
        # Read the last couple of day-files so a window spanning midnight still works.
        df = pd.concat([pd.read_csv(f) for f in files[-2:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        if minutes and minutes > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(minutes=minutes)]
        if df.empty:
            return jsonify(empty)
        # Downsample by striding so the trace stays light but keeps its shape.
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]

        def sum_pattern(pattern):
            """Sum every column matching pattern into one series (interface/device
            names are auto-detected here, so match by shape rather than by name)."""
            s = None
            for n in df.columns:
                if re.fullmatch(pattern, n):
                    col = pd.to_numeric(df[n], errors="coerce").fillna(0)
                    s = col if s is None else s + col
            return (s if s is not None else pd.Series(0.0, index=df.index)).tolist()

        core_cols = sorted(
            [c for c in df.columns if re.fullmatch(r"cpu\d+", c)],
            key=lambda c: int(c[3:]))
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "cpu": df[core_cols].round(1).values.tolist() if core_cols else [],
            "cpu_avg": df["cpu_avg"].round(1).tolist() if "cpu_avg" in df else [],
            "mem": df["mem_percent"].round(1).tolist() if "mem_percent" in df else [],
            "swap": df["swap_percent"].round(1).tolist() if "swap_percent" in df else [],
            "net_rx": sum_pattern(r"net_.+_rx_bps"),
            "net_tx": sum_pattern(r"net_.+_tx_bps"),
            "disk_r": sum_pattern(r"disk_.+_read_bps"),
            "disk_w": sum_pattern(r"disk_.+_write_bps"),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# --- SPS beam intensity ---
# The NXCALS session is owned by the separate beam_watcher process (see
# beam_monitor/beam_intensity_controller.py). Flask only reads the watcher's
# published state and CSV history. Intensity is in 1e10 protons per spill.

# A published state older than this is not "the beam", it's a memory of it: the
# watcher republishes every ~30 s (bridge every 20 s), so anything past a few
# minutes means the chain is broken somewhere (lxplus watcher dead, Kerberos
# expired, EOS unreachable) and the on/off answer is UNKNOWN, not the last one
# seen. Same cutoff as get_beam_watcher_status() in daq_status.py.
# 300 s, not the original 180: the watcher publishes every 30 s, but the Beam2
# spill poll shares its thread and takes ~80 s when it runs, so a normal cycle can
# legitimately be ~110 s old before the bridge's own 20 s poll is added. 180 left
# under a minute of margin and flapped to "unknown" on 2026-07-27. This matches
# BEAM_OFF_GAP_S: we declare beam off after 5 min without a spill, and the state
# unknown after 5 min without an update.
BEAM_STALE_S = float(os.environ.get("SPS_BEAM_STALE_S", 300))


def _beam_read_state():
    """The beam watcher's latest published state, or a disconnected stub if it isn't
    running yet / hasn't written the file.

    Adds `stale`/`age_s`: freshness is judged from the payload timestamp, NOT the
    file mtime — beam_bridge.py rewrites the local copy on every poll, so a frozen
    state pulled from EOS still looks freshly written on disk. When stale, beam_on
    is forced to None so nothing downstream can report a two-day-old BEAM ON as
    current."""
    try:
        with open(BEAM_STATE_PATH) as f:
            state = json.load(f)
    except Exception:
        return {"connected": False, "stale": True, "age_s": None,
                "last_error": "beam watcher not running",
                "unit": BEAM_UNIT, "beam_on": None}

    try:
        stamp = state.get("timestamp") or state.get("updated")
        age = (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except Exception:
        age = None          # unparseable/absent stamp -> treat as unknown age
    state["age_s"] = age
    state["stale"] = age is None or age > BEAM_STALE_S
    if state["stale"]:
        state["beam_on"] = None
        if not state.get("last_error"):
            state["last_error"] = (
                f"beam state has not been updated for {_fmt_age(age)} "
                "— is the lxplus watcher / beam bridge running?"
                if age is not None else
                "beam state has no usable timestamp — cannot tell if it is current")
    return state


def _fmt_age(seconds):
    """Coarse human age ('4 min', '2 d') for stale-data messages."""
    if seconds is None:
        return "unknown"
    for div, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= div:
            return f"{seconds / div:.0f} {unit}"
    return f"{seconds:.0f} s"


@app.route("/beam/status")
def beam_status():
    """Latest SPS beam-intensity summary published by the beam_watcher process."""
    return jsonify(_beam_read_state())


@app.route("/beam/history")
def beam_history():
    """Logged beam-spill history from the per-day CSV(s) for a plot. `hours` trims
    the window, striding keeps the payload light."""
    import glob
    hours = request.args.get("hours", default=6.0, type=float)
    max_points = request.args.get("max_points", default=1500, type=int)
    try:
        files = sorted(glob.glob(os.path.join(BEAM_LOG_DIR, "beam_intensity_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "intensity": [], "unit": BEAM_UNIT})
        # Load enough per-day files to cover `hours`. This was a flat files[-2:],
        # which silently capped EVERY request at ~2 days however large `hours`
        # was: a 336 h request returned 07-27..07-28 and looked exactly like an
        # archive that simply stopped there. The +2 covers the partial day at
        # each end and gives the rolling window its left-edge context.
        keep = max(2, int(hours // 24) + 2)
        df = pd.concat([pd.read_csv(f) for f in files[-keep:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        # Early watcher versions could re-log the lookback window on restart:
        # sort + dedup so old files still plot cleanly.
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        avg_window_min = 10
        # Compute the rolling series on the FULL loaded frame and trim to the
        # display window afterwards: a trailing sum/mean can't see cycles before
        # the left edge, so trimming first would undercount the first
        # avg_window_min of the window.
        #
        # Two complementary measures, same window:
        #  * avg — rolling mean of REAL spills only (empty cycles excluded). Beam
        #    QUALITY: how hot each spill is when beam is on. Blind to duty cycle.
        #  * delivery — rolling SUM over ALL cycles (empty ones included, so they
        #    count as zero). Protons delivered in the trailing window; this DROPS
        #    to zero during beam-off, so it reflects duty cycle, not just quality.
        pulses = df[df["intensity_e10"] >= PULSE_THRESHOLD_E10]
        avg = (pulses.set_index("timestamp")["intensity_e10"]
               .rolling(f"{avg_window_min}min").mean().reset_index())
        delivery = (df.set_index("timestamp")["intensity_e10"]
                    .rolling(f"{avg_window_min}min").sum().reset_index())
        if hours and hours > 0:
            cutoff = datetime.now() - timedelta(hours=hours)
            df = df[df["timestamp"] >= cutoff]
            avg = avg[avg["timestamp"] >= cutoff]
            delivery = delivery[delivery["timestamp"] >= cutoff]
        if len(avg) > max_points:
            avg = avg.iloc[:: (len(avg) // max_points) + 1]
        if len(delivery) > max_points:
            delivery = delivery.iloc[:: (len(delivery) // max_points) + 1]
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "intensity": df["intensity_e10"].round(3).tolist(),
            "avg_time": avg["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "avg_intensity": avg["intensity_e10"].round(3).tolist(),
            "avg_window_min": avg_window_min,
            "delivery_time": delivery["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "delivery_intensity": delivery["intensity_e10"].round(1).tolist(),
            "delivery_window_min": avg_window_min,
            "unit": BEAM_UNIT,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Beam2 tab — SPS slow-extraction spill structure + H4 line state
# ---------------------------------------------------------------------------
# Where the Beam tab answers "how much beam per spill", this answers "what does
# the spill look like, and is it reaching H4". The NXCALS polling happens inside
# the lxplus beam_watcher process (it borrows that Spark session); Flask only
# reads what beam_bridge.py pulled down from EOS. See sps_monitor/.

def _sps_read_state():
    """The SPS monitor's latest published state, or a disconnected stub.

    Applies the same payload-timestamp staleness rule as _beam_read_state(), and
    for the same reason: the bridge rewrites the local copy on every poll, so a
    frozen state from a dead lxplus watcher still looks freshly written on disk.
    When stale, spill_on and h4_open are forced to None — an old "SPILLING" or
    "line open" is worse than an honest "unknown".
    """
    try:
        with open(SPS_STATE_PATH) as f:
            state = json.load(f)
    except Exception:
        return {"connected": False, "stale": True, "age_s": None,
                "last_error": "no SPS spill data yet — is the beam watcher "
                              "(and the bridge) running?",
                "unit": SPS_UNIT, "spill_on": None, "h4_open": None}

    try:
        stamp = state.get("timestamp") or state.get("updated")
        age = (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except Exception:
        age = None
    state["age_s"] = age
    state["stale"] = age is None or age > BEAM_STALE_S
    if state["stale"]:
        state["spill_on"] = None
        state["h4_open"] = None
        if not state.get("last_error"):
            state["last_error"] = (
                f"SPS spill state has not been updated for {_fmt_age(age)} "
                "— is the lxplus watcher / beam bridge running?"
                if age is not None else
                "SPS spill state has no usable timestamp — cannot tell if it is current")
    return state


@app.route("/sps/status")
def sps_status():
    """Latest SPS spill summary, including the stitched extraction-rate timeline,
    the newest single-cycle spill profile and the H4 line state."""
    return jsonify(_sps_read_state())


@app.route("/sps/history")
def sps_history():
    """Per-cycle spill history from the CSVs: extracted intensity, effective
    spill length and duty factor, one row per SPS cycle."""
    import glob
    hours = request.args.get("hours", default=6.0, type=float)
    max_points = request.args.get("max_points", default=3000, type=int)
    empty = {"success": True, "time": [], "extracted": [], "spill_time": [],
             "spill_len_ms": [], "duty": [], "unit": SPS_UNIT}
    try:
        files = sorted(glob.glob(os.path.join(SPS_LOG_DIR, "sps_spill_*.csv")))
        if not files:
            return jsonify(empty)
        # See the note in beam_history(): a flat files[-2:] capped every request
        # at ~2 days regardless of `hours`.
        keep = max(2, int(hours // 24) + 2)
        df = pd.concat([pd.read_csv(f) for f in files[-keep:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        if hours and hours > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
        if df.empty:
            return jsonify(empty)
        # Only extracting cycles carry a spill; dump/other cycles are plotted as
        # zero-intensity markers so the supercycle gaps stay visible.
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]
        ext = df[df["destination"] == EXTRACTED_DEST]
        out = {
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "extracted": df["extracted_e10"].fillna(0).round(1).tolist(),
            "spill_time": ext["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "spill_len_ms": ext["spill_len_ms"].round(0).where(
                ext["spill_len_ms"].notna(), None).tolist(),
            "duty": ext["duty_factor"].round(4).where(
                ext["duty_factor"].notna(), None).tolist(),
            "unit": SPS_UNIT,
        }

        # Beam ACTUALLY reaching our zone, spill by spill. Plotted alongside what
        # the SPS extracted, this is what makes an access visible: the SPS trace
        # carries on while this one drops out. Nothing else in the tab shows that,
        # because every SPS-side variable is blind to our branch being closed.
        avail = [c for c in H4_BEAM_COUNTERS if c in ext.columns]
        if avail:
            delivered = ext[ext["extracted_e10"] >= SPILL_THRESHOLD_E10]
            seen = delivered[avail].fillna(0).sum(axis=1)
            out["beam_here_time"] = delivered["timestamp"].dt.strftime(
                "%Y-%m-%d %H:%M:%S").tolist()
            out["beam_here_counts"] = seen.round(0).tolist()
            out["beam_here_vars"] = [H4_COUNT_VARS[c] for c in avail]
            # Contiguous runs of "SPS delivered but we saw nothing", which is the
            # access signature. Short single-cycle dropouts are statistical at
            # these rates, so a run has to last a few minutes to be reported.
            gaps, start, last = [], None, None
            for t, v in zip(delivered["timestamp"], seen):
                if v <= 0:
                    start = t if start is None else start
                    last = t
                else:
                    if start is not None and (last - start).total_seconds() >= 300:
                        gaps.append((start, last))
                    start = None
            if start is not None and (last - start).total_seconds() >= 300:
                gaps.append((start, last))
            out["no_beam_windows"] = [
                {"from": a.strftime("%Y-%m-%d %H:%M:%S"),
                 "to": b.strftime("%Y-%m-%d %H:%M:%S"),
                 "minutes": round((b - a).total_seconds() / 60, 1)}
                for a, b in gaps]
        return jsonify(out)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# H4 barrier (T2 TAX) — PROXIED from the n_TOF x17 DAQ, not computed here.
# ---------------------------------------------------------------------------
# The barrier variable is XTAX_022_023:POSITION_MEAS, which lives in NXCALS.
# NXCALS is on the CERN Technical Network and banco is not TN-trusted (it IS on
# the GPN — that is a different grant), so `cs-ccr-nxcals*:19093` answers "No
# route to host" here. mx17 IS TN-trusted, already polls the TAX, writes the
# per-day CSVs and computes the blocked spans, so we forward its finished
# answer instead of standing up a second Spark session behind a tunnel.
#
# Use the DNS NAME, never the address: mx17 has no static reservation, its
# lease is sticky by MAC only, and it has already moved .103 -> .17 once.
X17_BASE = os.environ.get("X17_FLASK_URL", "http://ntof-x17-daq.dyndns.cern.ch:5001")
X17_TIMEOUT_S = float(os.environ.get("X17_FLASK_TIMEOUT_S", "8"))


def _x17_get(path, params=None):
    """GET a JSON endpoint on the x17 DAQ. Returns (obj, error_string)."""
    from urllib.parse import urlencode
    from urllib.request import urlopen
    url = f"{X17_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        with urlopen(url, timeout=X17_TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8")), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


@app.route("/sps/tax_history")
def sps_tax_history():
    """H4 barrier trace + blocked spans, forwarded from mx17.

    The spans are ACCESS CANDIDATES, not confirmed accesses: the position says
    the line is blocked, it does not say why. Confirmation needs the H4 flux
    counters — see docs/H4_ACCESS_INFERENCE.md on the x17 repo."""
    params = {"hours": request.args.get("hours", "24")}
    if "max_points" in request.args:
        params["max_points"] = request.args["max_points"]
    obj, err = _x17_get("/sps/tax_history", params)
    if obj is None:
        # Degrade to the same empty shape the panel already handles, so an mx17
        # restart shows "unreachable" instead of breaking the rest of the tab.
        return jsonify({"success": True, "time": [], "position_mm": [],
                        "intervals": [],
                        "note": f"x17 DAQ unreachable — {err}"})
    return jsonify(obj)


@app.route("/sps/tax_state")
def sps_tax_state():
    """Live barrier state (the h4_tax block of mx17's SPS status).

    Separate from /sps/tax_history on purpose: this is small and polled on the
    tile cadence, the history is ~100 kB and polled on the plot cadence."""
    obj, err = _x17_get("/sps/status")
    if obj is None:
        return jsonify({"error": f"x17 DAQ unreachable — {err}"})
    return jsonify(obj.get("h4_tax") or {"error": "x17 published no h4_tax block"})


# ===========================================================================
# Disk Space tab — free space by clearing DREAM runs that are provably backed up
# ---------------------------------------------------------------------------
# All the safety logic lives in flask_app/space_manager.py: a run is "safe to
# delete" only when every file of its tree is verified on EOS (native xrdfs,
# relpath + size). /space/scan is read-only; /space/delete re-verifies every
# run server-side before removing it (never trusts the client) and requires
# the typed "DELETE" confirmation.
# ===========================================================================

@app.route("/space/usage")
def space_usage():
    return jsonify(space_manager.disk_usage())


@app.route("/space/local")
def space_local():
    """Local-only listing of the runs on the data disk (sizes + local guard
    flags). No EOS access, so it is instant and works with Kerberos/network
    down — it shows WHAT is on the disk; only /space/scan can say what is
    provably backed up and therefore safe to delete."""
    disk = request.args.get("disk", "data")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    try:
        return jsonify(space_manager.local_scan(disk))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/scan")
def space_scan():
    disk = request.args.get("disk", "data")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    try:
        return jsonify(space_manager.scan(disk))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# --- Background jobs for the long space operations -------------------------
# The EOS-backed operations take ~9 s (listing) to minutes (deleting), which is
# too long to hold a request open and gives the GUI nothing to draw. Each one
# runs on a worker thread that publishes progress into _space_jobs; the browser
# starts a job, then polls /space/job/<id>.
#
# Honesty note on the bar: the `xrdfs ls -R` listing CANNOT be tracked. It
# emits nothing for ~8.8 s and then dumps every line in ~0.06 s, because the
# cost is connect + Kerberos + the server-side walk. So the listing phase
# reports indeterminate and the GUI animates it against the previous run's
# duration (space_manager.listing_estimate_s), clearly labelled as an estimate.
# The phases after it — per-run verification, per-item deletion — are counted
# for real.
_space_jobs = {}
_space_jobs_lock = threading.Lock()
_SPACE_JOB_TTL = 900        # forget finished jobs after 15 min


def _space_job_prune():
    now = time.time()
    for jid in [j for j, v in _space_jobs.items()
                if v.get("finished_at") and now - v["finished_at"] > _SPACE_JOB_TTL]:
        _space_jobs.pop(jid, None)


def _space_job_start(kind, fn):
    """Run fn(progress) on a worker thread; return the new job id."""
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "phase": "starting", "done": 0, "total": None,
           "msg": "", "items": [], "running": True, "result": None, "error": None,
           "started_at": time.time(), "finished_at": None,
           "listing_estimate_s": space_manager.listing_estimate_s()}
    with _space_jobs_lock:
        _space_job_prune()
        _space_jobs[jid] = job

    def progress(phase, done, total, msg, item=None):
        with _space_jobs_lock:
            job.update(phase=phase, done=done, total=total, msg=msg)
            if item is not None:
                job["items"].append(item)

    def run():
        try:
            out = fn(progress)
            with _space_jobs_lock:
                job["result"] = out
        except Exception as e:
            with _space_jobs_lock:
                job["error"] = str(e)
        finally:
            with _space_jobs_lock:
                job["running"] = False
                job["finished_at"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"space-{kind}-{jid}").start()
    return jid


@app.route("/space/estimate")
def space_estimate():
    """How long the last EOS listing took. The GUI animates the untrackable
    listing phase against this, so the bar is calibrated to the real link."""
    return jsonify({"listing_s": space_manager.listing_estimate_s()})


@app.route("/space/job/<job_id>")
def space_job_status(job_id):
    """Poll a running space job. `since` trims the per-item log to what the
    caller has not seen yet, so polling stays cheap on long deletes."""
    with _space_jobs_lock:
        job = _space_jobs.get(job_id)
        if job is None:
            return jsonify({"success": False, "message": "unknown or expired job"}), 404
        try:
            since = int(request.args.get("since", 0))
        except ValueError:
            since = 0
        out = {k: v for k, v in job.items() if k != "items"}
        out["items"] = job["items"][since:]
        out["n_items"] = len(job["items"])
        out["elapsed"] = round(time.time() - job["started_at"], 2)
    return jsonify(out)


@app.route("/space/job/check", methods=["POST"])
def space_job_check():
    """Start an EOS safety check. mode=prune runs the per-component scan, any
    other value runs the whole-run scan."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "prune")
    if mode == "prune":
        fn = lambda p: space_manager.component_scan(verify=True, force=True, progress=p)
    else:
        disk = data.get("disk", "data")
        if disk not in space_manager.DISKS:
            return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
        fn = lambda p: space_manager.scan(disk, force=True, progress=p)
    return jsonify({"job": _space_job_start("check", fn)})


@app.route("/space/job/delete_components", methods=["POST"])
def space_job_delete_components():
    """Start a component delete. Same guards as the synchronous route —
    space_manager re-verifies every piece against a fresh EOS listing."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    confirm = data.get("confirm")
    if not isinstance(items, list) or not items:
        return jsonify({"success": False, "message": "nothing selected"}), 400
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    comps = sorted({i.get("component") for i in items if isinstance(i, dict)} - {None})
    runs = sorted({i.get("run") for i in items if isinstance(i, dict)} - {None})

    def fn(p):
        out = space_manager.delete_components(items, progress=p)
        log_event("SPACE_DELETE_COMPONENTS", "disk_space",
                  runs=",".join(runs), components=",".join(comps),
                  items=len(items), freed=out["freed_h"],
                  ok=out["n_deleted"], failed=out["n_failed"])
        out["success"] = out["n_failed"] == 0
        out["usage"] = space_manager.disk_usage().get("data", {})
        return out

    return jsonify({"job": _space_job_start("delete", fn)})


@app.route("/space/components")
def space_components():
    """The run -> subrun -> component tree with a delete verdict per component.

    verify=0 skips EOS entirely (instant, works offline) so the tab can paint
    the breakdown immediately; verify=1 issues ONE recursive EOS listing for
    the whole tree and marks each component safe/unsafe from it.

    verify=cached is what a page reload uses: it replays the LAST listing at
    whatever age it has, without touching EOS, and returns checked_age_h so the
    tab can say "good as of 2 minutes ago". A fresh listing now costs ~32 s
    (one xrdfs per verify location) and the verdicts only move when the backup
    watcher pushes, so paying that on every reload buys nothing. Deletion is
    unaffected — it always re-lists and re-verifies.
    """
    v = request.args.get("verify", "1")
    allow_stale = v in ("cached", "stale")
    verify = allow_stale or v not in ("0", "false", "no")
    force = request.args.get("force", "0") in ("1", "true", "yes")
    try:
        return jsonify(space_manager.component_scan(
            verify=verify, force=force, allow_stale=allow_stale))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/preflight", methods=["POST"])
def space_preflight():
    """Dry-run a component selection: bytes freed, what is refused and why, and
    which subruns the processor would reprocess. Read-only — deletes nothing."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        return jsonify({"success": False, "message": "items must be a list"}), 400
    try:
        return jsonify(space_manager.preflight_components(items))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/delete_components", methods=["POST"])
def space_delete_components():
    """Delete selected (run, subrun, component) triples. space_manager
    re-verifies every one against a FRESH EOS listing before removing it, so
    the client verdict is never trusted."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    confirm = data.get("confirm")
    if not isinstance(items, list) or not items:
        return jsonify({"success": False, "message": "nothing selected"}), 400
    # Typed confirmation must match exactly, so a stray click can't delete.
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    out = space_manager.delete_components(items)
    comps = sorted({i.get("component") for i in items if isinstance(i, dict)} - {None})
    runs = sorted({i.get("run") for i in items if isinstance(i, dict)} - {None})
    log_event("SPACE_DELETE_COMPONENTS", "disk_space",
              runs=",".join(runs), components=",".join(comps),
              items=len(items), freed=out["freed_h"],
              ok=out["n_deleted"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get("data", {})
    return jsonify(out)


@app.route("/space/delete", methods=["POST"])
def space_delete():
    data = request.get_json(silent=True) or {}
    disk = data.get("disk")
    runs = data.get("runs") or []
    confirm = data.get("confirm")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    if not isinstance(runs, list) or not runs:
        return jsonify({"success": False, "message": "no runs selected"}), 400
    # Typed confirmation must match exactly, so a stray click can't delete.
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    out = space_manager.delete_runs(disk, runs)
    log_event("SPACE_DELETE", "disk_space", disk=disk,
              runs=",".join(runs), freed=out["freed_h"],
              ok=out["n_deleted"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get(disk, {})
    return jsonify(out)


@app.route("/space/scan_subruns")
def space_scan_subruns():
    """Per-subrun verdicts for one run (read-only) — backs the run-row expander
    that lets an operator prune individual subruns of a long run. Reuses the
    cached EOS listing rather than re-listing, so opening a run is instant."""
    disk = request.args.get("disk", "data")
    run = request.args.get("run", "")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    try:
        return jsonify(space_manager.scan_subruns(disk, run))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/delete_subruns", methods=["POST"])
def space_delete_subruns():
    """Delete selected subruns of one run. space_manager re-verifies every one
    against a fresh EOS listing before removing it, so the client verdict is
    never trusted."""
    data = request.get_json(silent=True) or {}
    disk = data.get("disk")
    run = data.get("run")
    subruns = data.get("subruns") or []
    confirm = data.get("confirm")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    if not space_manager.RUN_NAME_RE.match(run or ""):
        return jsonify({"success": False, "message": f"invalid run {run!r}"}), 400
    if not isinstance(subruns, list) or not subruns:
        return jsonify({"success": False, "message": "no subruns selected"}), 400
    # Typed confirmation must match exactly, so a stray click can't delete.
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    out = space_manager.delete_subruns(disk, run, subruns)
    log_event("SPACE_DELETE_SUBRUNS", "disk_space", disk=disk, run=run,
              subruns=",".join(subruns), freed=out["freed_h"],
              ok=out["n_deleted"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get(disk, {})
    return jsonify(out)


@app.route("/space/restore_scan")
def space_restore_scan():
    """List runs on EOS and how each compares to the local disk (read-only)."""
    try:
        return jsonify(space_manager.scan_restore())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/restore", methods=["POST"])
def space_restore():
    """Pull runs back from EOS onto the local data disk. Non-destructive: only
    files missing or size-mismatched locally are fetched. Sent one run per
    request by the UI so it can show per-run progress."""
    data = request.get_json(silent=True) or {}
    runs = data.get("runs") or []
    if not isinstance(runs, list) or not runs:
        return jsonify({"success": False, "message": "no runs selected"}), 400
    out = space_manager.restore_runs(runs)
    log_event("SPACE_RESTORE", "disk_space", runs=",".join(runs),
              fetched=out["fetched_h"], ok=out["n_restored"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get("data", {})
    return jsonify(out)


def is_dream_daq_running():
    """
    Checks tmux session 'daq_control' and returns True if Dream DAQ is running.

    Running = "Received: Dream DAQ starting" appears in recent output
              AND
              "Dream Subrun complete." has NOT appeared.
    """
    try:
        # Increase the buffer slightly to ensure we don't miss the transition
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-20", "-t", "daq_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return False

    lines = output.splitlines()

    # We iterate backwards (from most recent to oldest)
    for line in reversed(lines):
        if "Received: Dream DAQ starting" in line:
            return True
        if "Dream Subrun complete." in line:
            return False

    return False  # Neither found in recent history
    # try:
    #     # Grab last ~10 lines of the pane
    #     output = subprocess.check_output(
    #         ["tmux", "capture-pane", "-pS", "-10", "-t", "daq_control:0.0"],
    #         text=True
    #     )
    # except subprocess.CalledProcessError:
    #     # If tmux session doesn't exist or some error occurs
    #     return False
    #
    # # Normalize
    # lines = output.splitlines()
    #
    # # State checks
    # saw_start = any("Received: Dream DAQ starting" in line for line in lines)
    # saw_complete = any("Dream Subrun complete." in line for line in lines)
    #
    # # Running only if started AND not complete
    # return saw_start and not saw_complete


# ---------------------------------------------------------------------------
# Combined VMM + Dream runs: /vmm_trigger/start and /vmm_trigger/stop, so the
# VMM DAQ (dedippce185) can fire a paired Dream run under the same run name and
# hand over the per-subrun P2-basket HV schedule. Purely additive and passive —
# both routes are token- and IP-guarded by gitignored config/vmm_trigger.json,
# and nothing here runs unless that route is called. Dream's own GUI, DAQ and
# standalone runs are untouched. Source of truth for the module lives in the VMM
# repo (docs/dream_side/vmm_trigger.py); re-copy it after any edit there.
# MUST stay above the __main__ block below — code after socketio.run() would not
# execute until the server shuts down, so the routes would never register.
import vmm_trigger
vmm_trigger.register(app, BASE_DIR, CONFIG_RUN_DIR, BASH_DIR,
                     VENV_PYTHON, _save_current_run)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001)
