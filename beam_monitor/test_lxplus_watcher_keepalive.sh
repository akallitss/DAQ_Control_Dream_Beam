#!/bin/bash
# Exercises lxplus_beam_watcher.sh's decision logic WITHOUT lxplus, EOS or
# NXCALS: a fake EOS dir, a `sleep` stub in place of the watcher (via
# WATCHER_CMD) and a lockfile we control. Run it anywhere:
#
#   bash beam_monitor/test_lxplus_watcher_keepalive.sh
#
# Case 5 is the 2026-07-23 outage: a live PID belonging to a DIFFERENT lxplus
# node. The pre-2026-07-25 script exited "already running" there and never
# recovered, which is why the beam feed sat frozen for two days.
set -u
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lxplus_beam_watcher.sh"
T=$(mktemp -d)
export EOS_BEAM_DIR="$T/eos" REPO_DIR="$T" LOCKFILE="$T/lock" KEEPALIVE_LOG="$T/ka.log"
export HOME="$T"
mkdir -p "$EOS_BEAM_DIR"
STATE="$EOS_BEAM_DIR/beam_state.json"
HOST=$(hostname -s)
pass=0; fail=0
chk(){ if [ "$2" = "$3" ]; then echo "  PASS  $1"; pass=$((pass+1));
       else echo "  FAIL  $1 (got '$2', want '$3')"; fail=$((fail+1)); fi; }

mkstate(){ printf '{"connected": true, "timestamp": "%s", "beam_on": true}\n' \
           "$(date -d "@$1" '+%Y-%m-%dT%H:%M:%S')" > "$STATE"; }
# Stub watcher: sleeps, so it holds a real PID we can test kill-0 against.
export WATCHER_CMD="sleep 300"
# Force the nohup fallback: this harness tests the DECISION logic, and a
# transient systemd unit would not give us a plain PID to assert against.
export USE_SYSTEMD_RUN=0
# grep -c prints "0" AND exits 1 on no match, so `|| echo 0` would double it.
started(){ local n=0; [ -f "$KEEPALIVE_LOG" ] && n=$(grep -c STARTED "$KEEPALIVE_LOG" 2>/dev/null); echo "${n:-0}"; }

echo "== 1. fresh state -> no-op =="
mkstate $(( $(date +%s) - 60 )); rm -f "$LOCKFILE" "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "no watcher started" "$(started)" "0"

echo "== 2. stale state, no lockfile -> start =="
mkstate $(( $(date +%s) - 5000 )); rm -f "$LOCKFILE" "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "watcher started" "$(started)" "1"
chk "lockfile has host+pid+ts" "$(awk '{print NF}' "$LOCKFILE")" "3"
chk "lockfile host is us" "$(awk '{print $1}' "$LOCKFILE")" "$HOST"
LIVEPID=$(awk '{print $2}' "$LOCKFILE")
chk "recorded pid is alive" "$(kill -0 "$LIVEPID" 2>/dev/null && echo y || echo n)" "y"

echo "== 3. stale + just started -> grace, do NOT restart =="
rm -f "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "no second start" "$(started)" "0"
chk "grace message" "$(grep -c 'within .*grace' "$KEEPALIVE_LOG")" "1"
chk "original pid untouched" "$(kill -0 "$LIVEPID" 2>/dev/null && echo y || echo n)" "y"

echo "== 4. stale + past grace + live pid HERE -> kill and restart =="
echo "$HOST $LIVEPID $(( $(date +%s) - 99999 ))" > "$LOCKFILE"; rm -f "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
sleep 1
chk "restarted" "$(started)" "1"
chk "wedged pid was killed" "$(kill -0 "$LIVEPID" 2>/dev/null && echo y || echo n)" "n"
NEWPID=$(awk '{print $2}' "$LOCKFILE")
chk "new pid differs" "$([ "$NEWPID" != "$LIVEPID" ] && echo y || echo n)" "y"
kill "$NEWPID" 2>/dev/null

echo "== 5. THE OUTAGE CASE: stale + live pid on ANOTHER node -> restart =="
# Old code would exit 'already running' here and never recover.
OTHERPID=$(bash -c 'sleep 300 >/dev/null 2>&1 & echo $!')
echo "lxplus999 $OTHERPID $(( $(date +%s) - 99999 ))" > "$LOCKFILE"; rm -f "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "restarted despite foreign live pid" "$(started)" "1"
chk "foreign pid NOT killed" "$(kill -0 "$OTHERPID" 2>/dev/null && echo y || echo n)" "y"
chk "cannot-verify message" "$(grep -c 'cannot verify or kill' "$KEEPALIVE_LOG")" "1"
kill "$OTHERPID" "$(awk '{print $2}' "$LOCKFILE")" 2>/dev/null

echo "== 6. legacy bare-PID lockfile is handled =="
LEG=$(bash -c 'sleep 300 >/dev/null 2>&1 & echo $!')
echo "$LEG" > "$LOCKFILE"; mkstate $(( $(date +%s) - 5000 )); rm -f "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "restarted (host unknown => unverifiable)" "$(started)" "1"
chk "legacy pid NOT killed" "$(kill -0 "$LEG" 2>/dev/null && echo y || echo n)" "y"
kill "$LEG" "$(awk '{print $2}' "$LOCKFILE")" 2>/dev/null

echo "== 7. missing state file -> start =="
rm -f "$STATE" "$LOCKFILE" "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "started with no state file" "$(started)" "1"
kill "$(awk '{print $2}' "$LOCKFILE")" 2>/dev/null

echo "== 8. unparseable timestamp -> falls back to mtime (fresh => no-op) =="
echo '{"connected": true, "timestamp": "not-a-date"}' > "$STATE"
rm -f "$LOCKFILE" "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "no restart on fresh mtime" "$(started)" "0"

echo "== 9. future timestamp (clock skew) -> treated as fresh, no restart =="
mkstate $(( $(date +%s) + 7200 )); rm -f "$LOCKFILE" "$KEEPALIVE_LOG"
bash "$SCRIPT" >/dev/null 2>&1
chk "no restart on skew" "$(started)" "0"

echo; echo "passed=$pass failed=$fail"
rm -rf "$T"
[ "$fail" -eq 0 ]
