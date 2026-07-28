#!/bin/bash
# ---------------------------------------------------------------------------
# lxplus side of the SPS beam monitor.
#
# NXCALS only works on the CERN network, so this runs the beam-intensity
# watcher on lxplus and publishes beam_state.json + per-day CSVs straight to
# the EOS project space (FUSE mount). The banco DAQ then pulls those via
# beam_bridge.py so the GUI /beam tab shows the beam.
#
# Idempotent + keepalive-safe: the SAME script is used to start the watcher by
# hand and as an acrontab keepalive:
#   */10 * * * * lxplus /eos/.../DAQ.../beam_monitor/lxplus_beam_watcher.sh
# (acrontab needs a valid Kerberos ticket, which acron refreshes.)
#
# ---------------------------------------------------------------------------
# LIVENESS IS JUDGED BY THE PUBLISHED DATA, NOT BY A PID.
#
# The previous version asked `kill -0 $(cat ~/.sps_beam_watcher.pid)`. lxplus is
# a CLUSTER and the acrontab host field is plain `lxplus`, so each keepalive tick
# lands on an arbitrary node, while the lockfile sits on shared AFS. The PID
# recorded by lxplus701 was then tested against whatever holds that PID on
# lxplus844 — on a busy node, very likely an unrelated process. The check
# silently succeeded and the keepalive exited "already running" forever.
#
# That is exactly how the 2026-07-23 outage went unnoticed for two days: the
# watcher died at 11:39, nothing restarted it, and beam_state.json on EOS sat
# frozen while the GUI showed a stale beam state.
#
# So the question this script asks is "is beam_state.json actually being
# updated?" — the thing that matters — and the PID is only consulted to decide
# whether a wedged process on THIS node needs killing before a restart:
#
#   published state fresh (< MAX_STALE_S)      -> healthy, no-op
#   started < GRACE_S ago                      -> still warming up, no-op
#   stale + live PID recorded on THIS host     -> wedged: kill it, restart
#   stale otherwise                            -> dead: restart
#
# GRACE_S matters: the watcher needs a JVM + Spark session (a minute or two)
# before it publishes anything, so without it a keepalive tick right after a
# start would see stale data, kill the starting watcher and loop forever. The
# grace timestamp lives in the lockfile on shared AFS, so it also stops two
# different nodes from racing each other into duplicate watchers — which would
# both append to the same per-day CSV on EOS and interleave its lines.
#
# Requirements on lxplus (one-time):
#   * NXCALS venv with pytimber (see beam_monitor/README.md); path below.
#   * kinit <user>@CERN.CH  (or acron ticket).
#     NB a nohup'd watcher only renews within its ticket's renewable life; past
#     that it needs a fresh kinit. This script restarts it when that stops the
#     data, but the restart still needs a valid ticket to write to EOS.
# ---------------------------------------------------------------------------
set -u

# --- config (override via env before calling) ---
NXCALS_VENV="${NXCALS_VENV:-/eos/user/a/akallits/nxcals_venv}"
REPO_DIR="${REPO_DIR:-$HOME/p2_beam_monitor}"          # where beam_watcher.py + beam_monitor/ live on lxplus
# User EOS, not the salsachip project space: the project quota filled up on
# 2026-07-25 and "Disk quota exceeded" froze beam_state.json mid-outage — the
# feed must not share a quota with the run backups. Must match EOS_URL/EOS_DIR
# in beam_bridge.py on banco (the consumer).
EOS_BEAM_DIR="${EOS_BEAM_DIR:-/eos/user/a/akallits/beam_monitor}"
LOCKFILE="${LOCKFILE:-$HOME/.sps_beam_watcher.pid}"
KEEPALIVE_LOG="${KEEPALIVE_LOG:-$HOME/sps_beam_keepalive.log}"

# Restart if the published state is older than this. Must stay well above the
# watcher's own poll interval (30 s) and NXCALS latency (~0.5-1 min).
MAX_STALE_S="${MAX_STALE_S:-600}"
# Do not touch a watcher started less than this ago: JVM + Spark startup, and a
# cross-node restart guard. Keep it >= the acrontab period.
GRACE_S="${GRACE_S:-900}"

# A watcher that cannot reach NXCALS still publishes on schedule, so freshness
# alone says nothing about health — it publishes "connected": false forever and
# looks perfectly alive. Restart it, but no more often than this: the usual cause
# is an expired ticket, and restarting cannot conjure credentials, so a short
# period here would just thrash. One attempt per half hour is enough to pick up a
# ticket that has since been reseeded.
UNHEALTHY_BACKOFF_S="${UNHEALTHY_BACKOFF_S:-1800}"

# --- Kerberos -----------------------------------------------------------------
# NXCALS is Hadoop/Spark: its Java client needs a real (INITIAL) TGT. A ticket
# forwarded over ssh — Flags FfRA, lowercase f — is not enough; it authenticates
# to AFS and EOS happily but fails the Hadoop handshake with KrbException 101,
# "Invalid option setting in ticket request". Setting forwardable=false in
# krb5.conf does not help; the ticket itself is the problem.
#
# So the watcher gets credentials from a KEYTAB when one exists, into its OWN
# cache. The private cache matters as much as the keytab: the default cache
# (/run/user/<uid>/krb5cc) is shared by every login session on the node, and any
# ssh with GSSAPIDelegateCredentials=yes silently overwrites it with a forwarded
# ticket — which is exactly how a good ticket got clobbered on 2026-07-25.
#
# In /tmp, deliberately, not in AFS $HOME: reading an AFS file needs a token,
# which needs the ticket that the file is meant to provide.
KEYTAB="${KEYTAB:-$HOME/private/.krb5.keytab}"
PRINCIPAL="${PRINCIPAL:-$(id -un)@CERN.CH}"
WATCHER_CCACHE="${WATCHER_CCACHE:-/tmp/krb5cc_sps_beam_$(id -u)}"

# Seam for testing the decision logic off-lxplus: override to run a stub instead
# of the real watcher (see the self-test in the commit that introduced this).
WATCHER_CMD="${WATCHER_CMD:-}"

STATE_FILE="$EOS_BEAM_DIR/beam_state.json"
HOST="$(hostname -s)"
NOW="$(date +%s)"
# Transient systemd unit the watcher runs in (see start_watcher).
UNIT="${UNIT:-sps-beam-watcher}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOST] $*" | tee -a "$KEEPALIVE_LOG"; }

# Refresh the watcher's private cache from the keytab. Called before every start
# and on every tick while the watcher is running, so the ticket is renewed long
# before its ~25 h life runs out and the feed never has to notice. Returns
# non-zero (and says so once) when there is no keytab, leaving the watcher to
# fall back on whatever is in the default cache.
kinit_from_keytab() {
    [ -r "$KEYTAB" ] || return 1
    if KRB5CCNAME="FILE:$WATCHER_CCACHE" kinit -kt "$KEYTAB" "$PRINCIPAL" 2>>"$KEEPALIVE_LOG"; then
        # A Kerberos ticket is not an AFS token. $HOME (script, logs, keytab) is
        # AFS, so without aklog a keytab-only refresh still loses access to them
        # once the login session's token expires.
        command -v aklog >/dev/null 2>&1 && \
            KRB5CCNAME="FILE:$WATCHER_CCACHE" aklog 2>>"$KEEPALIVE_LOG"
        return 0
    fi
    log "WARNING: kinit -kt from $KEYTAB failed for $PRINCIPAL"
    return 1
}

# Fallback when the keytab is absent or broken: a still-valid ticket already in
# the private cache is just as good as one freshly minted from the keytab. This
# is the recovery seam for a keytab with wrong keys ("Preauthentication failed",
# seen 2026-07-26: the keytab written on 07-25 carried kvno 1) — an INITIAL
# ticket pushed into $WATCHER_CCACHE from another machine keeps the feed alive
# until the keytab is regenerated (cern-get-keytab --user, needs the account
# password).
#
# Deliberately NO kinit -R here: a renewed ticket comes from a TGS exchange and
# loses the INITIAL flag, and the comment above blames exactly that flag for
# KrbException 101. Honest status of that theory (2026-07-26): a pushed
# never-renewed INITIAL ticket (flags FRIA, aes256 session key, readable by the
# unit) ALSO failed with the same 101 at SparkContext init — so initial-ness
# alone is NOT sufficient, and no credential flavor has yet been PROVEN to work
# under this systemd unit. The only historically working setup was an
# interactive password kinit on lxplus itself (07-22/23, default cache). Next
# discriminating test, needs the account password on lxplus945:
#     KRB5CCNAME=FILE:/tmp/krb5cc_sps_beam_$(id -u) kinit akallits@CERN.CH
#     systemctl --user restart sps-beam-keepalive.service
# If THAT also 101s, the breakage is environmental/service-side, not the
# ticket. Keeping no-renew regardless: it is the conservative choice (renewal
# can only ever strip a property, never add one).
private_cache_usable() {
    [ -f "$WATCHER_CCACHE" ] || return 1
    KRB5CCNAME="FILE:$WATCHER_CCACHE" klist -s 2>/dev/null || return 1
    command -v aklog >/dev/null 2>&1 && \
        KRB5CCNAME="FILE:$WATCHER_CCACHE" aklog 2>>"$KEEPALIVE_LOG"
    return 0
}

# NXCALS/Spark needs Java 11 and a bound local IP on lxplus.
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-11-openjdk-11.0.25.0.9-7.el9.x86_64}"
export PATH="$JAVA_HOME/bin:$PATH"
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"

# Publish straight to EOS (read by the banco bridge).
export SPS_BEAM_STATE="$STATE_FILE"
export SPS_BEAM_LOG_DIR="$EOS_BEAM_DIR"
# Same for the SPS spill / H4 monitor that rides along inside the watcher
# (sps_monitor/). Without these it would fall back to its repo-local defaults
# and write the spill state into AFS, where the banco bridge cannot see it —
# the Beam2 tab would then sit at "no data" with everything apparently healthy.
export SPS_SPILL_STATE="$EOS_BEAM_DIR/sps_state.json"
export SPS_SPILL_LOG_DIR="$EOS_BEAM_DIR"

# --- how old is the published state? ------------------------------------------
# Prefer the payload's own timestamp: that is when the watcher last had DATA. A
# fresh mtime only proves someone wrote the file. Falls back to mtime if the
# timestamp is missing or unparseable, so a format change degrades to the weaker
# check instead of forcing a restart loop. Echoes the age in seconds, or nothing
# if the file is absent.
state_age_s() {
    [ -f "$STATE_FILE" ] || return 0
    local ts epoch mtime age
    ts=$(sed -n 's/.*"timestamp"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
         "$STATE_FILE" 2>/dev/null | head -1)
    epoch=$(date -d "$ts" +%s 2>/dev/null)
    mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null)
    if [ -n "$epoch" ]; then
        age=$((NOW - epoch))
    elif [ -n "$mtime" ]; then
        age=$((NOW - mtime))
    else
        return 0
    fi
    # Clock skew between the watcher host and this node would otherwise read as
    # "aged in the future"; treat that as fresh rather than restart on it.
    [ "$age" -lt 0 ] && age=0
    echo "$age"
}

# Is the published state reporting a working NXCALS connection? Echoes "true",
# "false", or nothing when the field is absent/unreadable — absent is treated as
# "do not judge", so an older state format never triggers restarts.
state_connected() {
    [ -f "$STATE_FILE" ] || return 0
    sed -n 's/.*"connected"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' \
        "$STATE_FILE" 2>/dev/null | head -1
}

# --- lockfile: "host pid started_at" ------------------------------------------
# The old format was a bare PID. Read that too, so the first keepalive tick after
# deploying this does not mistake a legitimately running watcher for garbage: an
# unknown host means "cannot verify", which is handled the same as a foreign host.
read_lock() {
    LOCK_HOST=''; LOCK_PID=''; LOCK_STARTED=0
    [ -f "$LOCKFILE" ] || return 0
    local raw; raw=$(tr -d '\r' < "$LOCKFILE" 2>/dev/null)
    case "$raw" in
        *' '*) LOCK_HOST=$(echo "$raw" | awk '{print $1}')
               LOCK_PID=$(echo "$raw" | awk '{print $2}')
               LOCK_STARTED=$(echo "$raw" | awk '{print $3+0}') ;;
        *)     LOCK_PID="$raw" ;;   # legacy bare-PID file, host unknown
    esac
}

# Only meaningful on the node that recorded it — the whole point of this rewrite.
local_watcher_alive() {
    [ -n "$LOCK_PID" ] || return 1
    [ "$LOCK_HOST" = "$HOST" ] || return 1
    kill -0 "$LOCK_PID" 2>/dev/null
}

# lxplus runs systemd-logind with KillUserProcesses=yes, so EVERY process in a
# login session's cgroup dies the moment that session ends. A `nohup ... &`
# watcher therefore cannot survive the ssh/acron session that started it —
# measured 2026-07-25: nohup and even `setsid nohup` were both reaped within
# seconds of logout, while a systemd-run unit survived. This is the real reason
# the feed died on 2026-07-23, and it is why the watcher must be launched into
# its own transient unit, outside the session cgroup.
#
# The nohup path is kept only as a fallback for hosts without systemd-run; it
# logs loudly, because there it will not outlive the session.
start_watcher() {
    mkdir -p "$EOS_BEAM_DIR" 2>/dev/null
    cd "$REPO_DIR" || { log "ERROR: REPO_DIR $REPO_DIR not found"; exit 1; }

    local cmd
    if [ -n "$WATCHER_CMD" ]; then
        cmd="$WATCHER_CMD"
    else
        cmd="'$NXCALS_VENV/bin/python' '$REPO_DIR/beam_watcher.py'"
    fi
    # exec, so the unit's MainPID is the watcher itself and not a wrapping shell
    # — the kill path below and the lockfile PID both depend on that.
    # Point the watcher at its own credential cache when the keytab gave us one,
    # so a stray delegated ssh cannot pull the ticket out from under it.
    local ccname=''
    if kinit_from_keytab; then
        ccname="export KRB5CCNAME='FILE:$WATCHER_CCACHE'; "
        log "kinit -kt OK -> $WATCHER_CCACHE ($PRINCIPAL)"
    elif private_cache_usable; then
        ccname="export KRB5CCNAME='FILE:$WATCHER_CCACHE'; "
        log "keytab unusable, but $WATCHER_CCACHE holds a valid ticket — using it"
    else
        log "NOTE: no usable keytab at $KEYTAB — watcher will use the default cache, which expires and is shared with login sessions"
    fi

    # JAVA_TOOL_OPTIONS: the JVM must read krb5_jvm.conf, not /etc/krb5.conf —
    # the system config's proxiable=true makes the JDK throw the client-side
    # KrbException 101 on any TGT without the P flag (i.e. every ticket minted
    # on banco). See the header of krb5_jvm.conf.
    local inner="cd '$REPO_DIR' && ${ccname}export JAVA_HOME='$JAVA_HOME' PATH='$PATH' \
JAVA_TOOL_OPTIONS='-Djava.security.krb5.conf=$REPO_DIR/krb5_jvm.conf' \
SPARK_LOCAL_IP='$SPARK_LOCAL_IP' SPS_BEAM_STATE='$SPS_BEAM_STATE' \
SPS_BEAM_LOG_DIR='$SPS_BEAM_LOG_DIR' SPS_SPILL_STATE='$SPS_SPILL_STATE' \
SPS_SPILL_LOG_DIR='$SPS_SPILL_LOG_DIR'; exec $cmd >> '$HOME/sps_beam_watcher.log' 2>&1"

    local pid=''
    if [ "${USE_SYSTEMD_RUN:-1}" = "1" ] && command -v systemd-run >/dev/null 2>&1; then
        # Linger, or the unit dies ~10-20 s after the last session closes anyway:
        # with Linger=no systemd stops the whole user manager at final logout and
        # takes every transient unit with it. Measured 2026-07-25 — a probe unit
        # logged one line at sessions=0 and was gone by the next tick.
        #
        # This is per-NODE state (/var/lib/systemd/linger/<user> is local), and
        # acron lands on an arbitrary lxplus node, so it has to be (re)asserted
        # here on whichever node is starting the watcher. Idempotent.
        loginctl enable-linger "$(id -un)" 2>/dev/null \
            || log "WARNING: enable-linger failed — the watcher will not outlive this session on $HOST"
        systemctl --user stop "$UNIT" 2>/dev/null
        systemctl --user reset-failed "$UNIT" 2>/dev/null
        if systemd-run --user --unit="$UNIT" --quiet /bin/bash -c "$inner" 2>>"$KEEPALIVE_LOG"; then
            sleep 1
            pid=$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)
            [ "$pid" = "0" ] && pid=''
            [ -n "$pid" ] && log "STARTED unit $UNIT (MainPID $pid) -> $STATE_FILE"
        fi
    fi

    if [ -z "$pid" ]; then
        nohup /bin/bash -c "$inner" >/dev/null 2>&1 &
        pid=$!
        log "STARTED pid $pid via nohup fallback — WILL NOT survive this session's end if logind reaps it (systemd-run unavailable?)"
    fi

    echo "$HOST $pid $NOW" > "$LOCKFILE"
}

# --- decide -------------------------------------------------------------------
AGE=$(state_age_s)
CONNECTED=$(state_connected)
read_lock

if [ -n "$AGE" ] && [ "$AGE" -lt "$MAX_STALE_S" ] && [ "$CONNECTED" != "false" ]; then
    # Publishing normally. Silent on the happy path so acron does not mail every
    # 10 minutes; the log line is enough of a heartbeat. Top up the ticket while
    # we are here — cheap, and it keeps the watcher from ever reaching expiry.
    kinit_from_keytab >/dev/null 2>&1 || private_cache_usable >/dev/null 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOST] OK: state ${AGE}s old connected=${CONNECTED:-?}" >> "$KEEPALIVE_LOG"
    exit 0
fi

# Publishing on time, but every query is failing — the watcher is up and useless.
# Restart it (a fresh kinit -kt comes with the restart, which is the usual cure),
# but at most once per UNHEALTHY_BACKOFF_S so a real credential outage does not
# turn into a restart every tick.
if [ -n "$AGE" ] && [ "$AGE" -lt "$MAX_STALE_S" ] && [ "$CONNECTED" = "false" ]; then
    if [ "$LOCK_STARTED" -gt 0 ] && [ $((NOW - LOCK_STARTED)) -lt "$UNHEALTHY_BACKOFF_S" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOST] UNHEALTHY: publishing (${AGE}s) but connected=false; last start $((NOW - LOCK_STARTED))s ago, backing off ${UNHEALTHY_BACKOFF_S}s" >> "$KEEPALIVE_LOG"
        exit 0
    fi
    log "publishing (${AGE}s old) but connected=false — restarting with fresh credentials"
    if local_watcher_alive; then
        systemctl --user stop "$UNIT" 2>/dev/null
        kill "$LOCK_PID" 2>/dev/null
    fi
    start_watcher
    exit 0
fi

AGE_TXT="${AGE:-no state file}"
if [ "$LOCK_STARTED" -gt 0 ] && [ $((NOW - LOCK_STARTED)) -lt "$GRACE_S" ]; then
    log "state stale (${AGE_TXT}s) but watcher started $((NOW - LOCK_STARTED))s ago on ${LOCK_HOST:-?} — within ${GRACE_S}s grace, leaving it alone"
    exit 0
fi

if local_watcher_alive; then
    log "state stale (${AGE_TXT}s) and pid $LOCK_PID is alive HERE — wedged, killing it"
    # Stop the unit first when there is one: killing MainPID alone would leave
    # a transient unit behind in a failed state, and systemd-run refuses to
    # reuse that name until it is reset.
    systemctl --user stop "$UNIT" 2>/dev/null
    kill "$LOCK_PID" 2>/dev/null
    for _ in 1 2 3 4 5; do
        kill -0 "$LOCK_PID" 2>/dev/null || break
        sleep 1
    done
    kill -9 "$LOCK_PID" 2>/dev/null
elif [ -n "$LOCK_PID" ]; then
    log "state stale (${AGE_TXT}s); lockfile pid $LOCK_PID belongs to ${LOCK_HOST:-unknown host} — cannot verify or kill from here, starting a fresh watcher"
else
    log "state stale (${AGE_TXT}s) and no lockfile — starting watcher"
fi

start_watcher
