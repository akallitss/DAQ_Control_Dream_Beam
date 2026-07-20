#!/bin/bash
# ---------------------------------------------------------------------------
# lxplus side of the SPS beam monitor.
#
# NXCALS only works on the CERN network, so this runs the beam-intensity
# watcher on lxplus and publishes beam_state.json + per-day CSVs straight to
# the EOS project space (FUSE mount). The banco DAQ then pulls those via
# beam_bridge.py so the GUI /beam tab shows the beam.
#
# Idempotent + keepalive-safe: if a watcher is already running it exits, so the
# SAME script is used both to start it manually and as an acrontab keepalive:
#   */10 * * * * lxplus /eos/.../DAQ.../beam_monitor/lxplus_beam_watcher.sh
# (acrontab needs a valid Kerberos ticket, which acron refreshes.)
#
# Requirements on lxplus (one-time):
#   * NXCALS venv with pytimber (see beam_monitor/README.md); path below.
#   * kinit <user>@CERN.CH  (or acron ticket).
# ---------------------------------------------------------------------------
set -u

# --- config (override via env before calling) ---
NXCALS_VENV="${NXCALS_VENV:-/eos/user/a/akallits/nxcals_venv}"
REPO_DIR="${REPO_DIR:-$HOME/p2_beam_monitor}"          # where beam_watcher.py + beam_monitor/ live on lxplus
EOS_BEAM_DIR="${EOS_BEAM_DIR:-/eos/project/s/salsachip/Data/T2_tests/beam_monitor}"
LOCKFILE="${LOCKFILE:-$HOME/.sps_beam_watcher.pid}"

# NXCALS/Spark needs Java 11 and a bound local IP on lxplus.
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-11-openjdk-11.0.25.0.9-7.el9.x86_64}"
export PATH="$JAVA_HOME/bin:$PATH"
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"

# Publish straight to EOS (read by the banco bridge).
export SPS_BEAM_STATE="$EOS_BEAM_DIR/beam_state.json"
export SPS_BEAM_LOG_DIR="$EOS_BEAM_DIR"

# already running? (keepalive no-op)
if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE" 2>/dev/null)" 2>/dev/null; then
    echo "beam watcher already running (pid $(cat "$LOCKFILE"))"; exit 0
fi

mkdir -p "$EOS_BEAM_DIR" 2>/dev/null
cd "$REPO_DIR" || { echo "REPO_DIR $REPO_DIR not found"; exit 1; }
echo "starting SPS beam watcher -> $SPS_BEAM_STATE"
nohup "$NXCALS_VENV/bin/python" "$REPO_DIR/beam_watcher.py" >> "$HOME/sps_beam_watcher.log" 2>&1 &
echo $! > "$LOCKFILE"
echo "started pid $(cat "$LOCKFILE"); log: $HOME/sps_beam_watcher.log"
