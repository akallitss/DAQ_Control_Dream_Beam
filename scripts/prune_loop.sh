#!/usr/bin/env bash
# Keep a long scan on disk by pruning its completed sub-runs while it runs.
#
# scripts/prune_active_run.py is a one-shot: it prunes once and exits. A grid
# that generates more than the free space (p2_mesh_drift_2d_1: ~462 GB against
# ~200 GB free) needs it called repeatedly for the whole night, which is all
# this wrapper does.
#
# Every safety check in prune_active_run.py still applies on every pass — only
# sub-runs with a .subrun_complete marker are candidates, and each file must be
# verified present on EOS at a matching byte size before anything is removed.
# The in-progress sub-run has no marker, so it is never touched. If EOS cannot
# be listed the pass refuses to delete and the loop simply tries again later.
#
# The run is named explicitly rather than read from current_run_state.json, so
# a stale or half-written state file cannot point this at the wrong run.
#
# Usage:
#   scripts/prune_loop.sh                          # p2_mesh_drift_2d_1, every 5 min
#   scripts/prune_loop.sh <run_name> [interval_s] [components]
#
# Stop it with Ctrl-C, or `pkill -f prune_loop.sh`.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

RUN="${1:-p2_mesh_drift_2d_1}"
INTERVAL="${2:-300}"
# dream_run + raw_fdf are ~93% of the bytes a sub-run leaves on disk. Add
# hits_root as a third component if the night still runs tight — it is on EOS
# too, so it is recoverable, but it is what the QA plots read locally.
COMPONENTS="${3:-dream_run,raw_fdf}"

PY="$REPO/.venv/bin/python"
LOG="$REPO/logs/prune_loop.log"
mkdir -p "$REPO/logs"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" | tee -a "$LOG"; }

# Ask space_manager where runs live rather than hardcoding the campaign path —
# it is the same resolution prune_active_run.py itself uses, so the existence
# check below cannot disagree with what the pruner will look at.
RUNS_ROOT="$("$PY" -c "
import sys; sys.path.insert(0, '$REPO/flask_app')
import space_manager as sm; print(sm._runs_root())
")" || { log "could not resolve runs root — is the venv intact?"; exit 1; }

log "prune_loop starting: run=$RUN interval=${INTERVAL}s components=$COMPONENTS"
log "  runs root: $RUNS_ROOT"
log "  (dry-run first pass to show what the guards make available)"
"$PY" scripts/prune_active_run.py --run "$RUN" --components "$COMPONENTS" >>"$LOG" 2>&1

trap 'log "prune_loop stopped"; exit 0' INT TERM

while true; do
    # The run directory does not exist until the first sub-run is written, and
    # prune_active_run exits non-zero on that. Not an error here — the loop is
    # started alongside the run and is expected to idle until data appears.
    if [[ -d "$RUNS_ROOT/$RUN" ]]; then
        "$PY" scripts/prune_active_run.py --run "$RUN" \
              --components "$COMPONENTS" --apply >>"$LOG" 2>&1
        rc=$?
        if [[ $rc -ne 0 ]]; then
            log "pass exited $rc (see $LOG) — retrying in ${INTERVAL}s"
        else
            # Surface the one-line summary so the terminal is readable at a glance.
            tail -n 3 "$LOG" | grep -E '^(freed|disk free)' | while read -r l; do
                log "  $l"
            done
        fi
    else
        log "run directory for $RUN not there yet — waiting"
    fi
    sleep "$INTERVAL"
done
