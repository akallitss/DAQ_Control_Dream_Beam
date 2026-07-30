#!/bin/bash
# Reliably stop the DAQ (RunCtrl) on the dream_daq tmux session.
#
# 'g' tells RunCtrl to END THE CURRENT PHASE, not to quit: a multi-phase run
# (PedThr + Data) needs one 'g' per remaining phase before RunCtrl exits. So we
# send 'g', verify it is gone, and repeat a few times.
#
# 2026-07-30 — WHY THIS IS NOT JUST `pgrep -x RunCtrl`:
#   RunCtrl is setuid root (-rwsrwxr-x root), so while it runs its process is
#   ROOT-owned. banco's /proc is mounted `hidepid=invisible,gid=998` and banco
#   is not in group 998 (proc: colord,polkitd) — so this user cannot see ANY
#   root process (`ps -eo user | grep -c root` == 0). `pgrep -x RunCtrl` is
#   therefore PERMANENTLY silent here.
#   The previous version opened its loop with `if ! pgrep -x RunCtrl; then
#   echo "RunCtrl stopped."; exit 0; fi`, so the first iteration always fired:
#   'g' was never sent, the signal fallbacks were unreachable, and the script
#   exited 0. Callers (stop_run.sh, and flask_app/vmm_trigger.py's
#   /vmm_trigger/stop, which reports success on rc==0) all believed the DAQ had
#   stopped while RunCtrl kept taking data to the end of its Sys DaqRun Time.
#   That is the "VMM stopped but Dream was still running" failure.
#
#   Fix: a POSITIVE pgrep sighting is still conclusive, but pgrep's SILENCE is
#   only trusted when we can see root processes at all. When we are blind we
#   fall back to RunCtrl's own console text in the pane, which hidepid cannot
#   hide from us. And we never exit 0 on "I could not tell".
#
#   The complementary machine-level fix is to put banco in the proc group
#   (`sudo usermod -aG proc banco`, then restart the tmux server) — after that
#   pgrep works again and the first branch below handles everything. This
#   script stays correct either way.
SESSION="dream_daq"

BASE_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
LOG_FILE="$BASE_DIR/logs/daq_events.log"
mkdir -p "$BASE_DIR/logs"
echo "$(date '+%Y-%m-%d %H:%M:%S') | STOP_DREAM     | bash_script  |" >> "$LOG_FILE"

# Can this user see root-owned processes? If not, pgrep can never observe a
# setuid-root RunCtrl and its silence carries no information.
pgrep_trustworthy() {
    ps -eo user= 2>/dev/null | grep -qx root
}

# RunCtrl draws its own console into the dream_daq pane ("*** RunCtrl:",
# "TestFun_TakeData: ... press 'g' to stop"). That text is visible regardless of
# hidepid. When RunCtrl exits, Server.py's output ("Listening on 0.0.0.0:...",
# "On-the-fly copy thread exiting.") is what sits at the bottom instead.
#
# DO NOT "simplify" this to the friendly-looking `press 'g' to stop`: RunCtrl
# hard-wraps that sentence at the pane width, so it arrives as two lines
# ("...; press 'g' to" / " stop <-") and a single-line grep for it matches
# NOTHING. Verified against a real run_22 capture. `TestFun_` is the marker
# that actually carries the detection.
pane_shows_runctrl() {
    tmux capture-pane -p -t "$SESSION" 2>/dev/null \
        | grep -v '^[[:space:]]*$' | tail -6 \
        | grep -qE "press 'g' to stop|TestFun_|\*\*\* RunCtrl:"
}

runctrl_running() {
    pgrep -x RunCtrl >/dev/null 2>&1 && return 0   # positive sighting: conclusive
    pgrep_trustworthy && return 1                  # visible, and it is not there
    pane_shows_runctrl                             # blind: ask RunCtrl's console
}

for i in 1 2 3 4 5 6; do
    if ! runctrl_running; then
        echo "RunCtrl stopped."
        exit 0
    fi
    echo "RunCtrl still running (attempt $i/6) — sending 'g'."
    tmux send-keys -t "$SESSION" 'g'
    sleep 3
done

# Signal escalation is only attempted when we can actually see the process.
# As an unprivileged user we cannot signal a root-owned RunCtrl anyway (pkill
# would just EPERM), so when blind we say so loudly instead of pretending.
if pgrep_trustworthy; then
    if pgrep -x RunCtrl >/dev/null; then
        echo "RunCtrl still running after repeated 'g'; sending SIGINT."
        pkill -INT -x RunCtrl
        sleep 3
    fi
    if pgrep -x RunCtrl >/dev/null; then
        echo "RunCtrl still running after SIGINT; sending SIGTERM."
        pkill -TERM -x RunCtrl
        sleep 2
    fi
    if pgrep -x RunCtrl >/dev/null; then
        echo "WARNING: RunCtrl still alive after SIGTERM."
        exit 1
    fi
    echo "RunCtrl stopped."
    exit 0
fi

if runctrl_running; then
    echo "ERROR: RunCtrl did not stop after 6 'g' keystrokes, and this user"
    echo "cannot signal it (setuid root + /proc hidepid). STOP IT BY HAND in"
    echo "the '$SESSION' tmux window, or add banco to the proc group."
    exit 1
fi
echo "RunCtrl stopped."
