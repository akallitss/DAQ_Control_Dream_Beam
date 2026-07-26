#!/bin/bash
# Readiness test for running the NXCALS beam watcher LOCALLY on banco
# (the n_TOF x17 pattern: pytimber/Spark on the DAQ machine, no lxplus hop).
#
# Why this exists: banco is on CERN GPN (128.141.21.144) but the NXCALS API
# (cs-ccr-nxcals*.cern.ch, 172.18.219.x) and acc-py-repo.cern.ch (172.18.203.37)
# are on the CERN Technical Network, which the TN firewall closes to
# non-TN-trusted hosts ("No route to host", measured 2026-07-26). A LanDB
# request to get banco TN-trusted is planned; run this script after it goes
# through. Each phase reports PASS/FAIL and the script stops at the first
# missing layer, so it doubles as a progress check:
#
#   phase 1  TN firewall open?        (pure TCP probes, no deps)
#   phase 2  pytimber installed?      (installs it from acc-py-repo if not)
#   phase 3  Kerberos ticket present?
#   phase 4  real NXCALS query        (SPSQC:MEAN_SPILL_INTENSITY, last 15 min)
#
# All-green means the local watcher will work:
#   tmux kill-session -t beam_watcher
#   tmux new-session -d -s beam_watcher "$HOME/venvs/nxcals/bin/python beam_watcher.py"
# (beam_watcher.py writes config/beam_state.json directly — the GUI needs no
# change; beam_bridge.py and the lxplus watcher then become the fallback.)

set -u
NXCALS_VENV="${NXCALS_VENV:-$HOME/venvs/nxcals}"
SPS_VAR="SPSQC:MEAN_SPILL_INTENSITY"
FAIL=0

probe() {  # probe <host> <port> <label>
    if timeout 6 bash -c "</dev/tcp/$1/$2" 2>/dev/null; then
        echo "  PASS  $1:$2  ($3)"
    else
        echo "  FAIL  $1:$2  ($3) — no route / blocked"
        FAIL=1
    fi
}

echo "== phase 1: TN firewall =="
probe acc-py-repo.cern.ch      443   "package repo, needed once for install"
probe cs-ccr-nxcals5.cern.ch   19093 "NXCALS API"
probe cs-ccr-nxcals6.cern.ch   19093 "NXCALS API"
probe cs-ccr-nxcals7.cern.ch   19093 "NXCALS API"
probe cs-ccr-nxcals8.cern.ch   19093 "NXCALS API"
probe ithdp1001.cern.ch        8020  "HDFS namenode (open even pre-LanDB)"
if [ "$FAIL" = 1 ]; then
    echo
    echo "TN firewall still closed — the LanDB registration has not taken effect yet."
    echo "(HDFS alone is not enough; the API ports must open too.) Re-run later."
    exit 1
fi

echo "== phase 2: pytimber in $NXCALS_VENV =="
if ! "$NXCALS_VENV/bin/python" -c 'import pytimber' 2>/dev/null; then
    echo "  pytimber missing — installing (x17 recipe: acc-py index, no pyarrow)"
    # --trusted-host: the acc-py CERN CA is not in banco's trust store; without
    # it pip silently falls back to PyPI, whose pytimber is an unusable stub.
    # Do NOT add pyarrow: PySpark's Arrow path is broken on this class of box.
    "$NXCALS_VENV/bin/pip" install setuptools pytimber \
        --index-url https://acc-py-repo.cern.ch/repository/vr-py-releases/simple \
        --extra-index-url https://pypi.org/simple \
        --trusted-host acc-py-repo.cern.ch || exit 1
fi
"$NXCALS_VENV/bin/python" -c 'import pytimber; print("  PASS  pytimber", pytimber.__version__)' || exit 1

echo "== phase 3: Kerberos =="
if klist -s 2>/dev/null; then
    echo "  PASS  $(klist 2>/dev/null | grep 'Default principal')"
else
    echo "  FAIL  no valid ticket in the default cache — run: kinit akallits@CERN.CH"
    exit 1
fi

echo "== phase 4: end-to-end NXCALS query ($SPS_VAR) =="
echo "  (first query spins up a local Spark JVM: 30-60 s is normal)"
"$NXCALS_VENV/bin/python" - <<EOF
import time, pytimber
ldb = pytimber.LoggingDB(source="nxcals")
t2 = time.time()
d = ldb.get("$SPS_VAR", t2 - 900, t2)
ts, vs = d.get("$SPS_VAR", ([], []))
print(f"  PASS  {len(ts)} points in the last 15 min", flush=True)
if len(ts):
    print(f"  last: {time.strftime('%H:%M:%S', time.localtime(ts[-1]))}  {vs[-1]:.1f}e10 p/spill")
EOF
[ $? -ne 0 ] && { echo "  FAIL  query died — see traceback above"; exit 1; }

echo
echo "ALL GREEN — banco can query NXCALS directly. To switch the watcher to"
echo "local mode, restart the beam_watcher tmux session with beam_watcher.py"
echo "(see the header of this script)."
