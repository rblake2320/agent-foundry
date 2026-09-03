#!/usr/bin/env bash
# Infrastructure fault injection for a box running an agent under OpenShell. Each scenario breaks something for real, then measures
# whether the system heals itself and how long it took. PASS = Mission Control answers again within the budget.
#
#   scripts/infra_faults.sh [--name agent-seller] [--port 8111] [--budget 30] [forward] [gateway] [sandbox]
#
# forward  — kill the port-forward process (the failure that actually happened on 2026-09-03); systemd must restart it.
# gateway  — restart the OpenShell gateway service (>=0.0.100 only); sandbox must survive and the forward must reconnect.
# sandbox  — stop + start the sandbox; Mission Control must come back with the ledger intact.
set -uo pipefail
NAME=agent-seller; PORT=8111; BUDGET=30; SCEN=()
while [ $# -gt 0 ]; do case "$1" in --name) NAME="$2"; shift 2;; --port) PORT="$2"; shift 2;; --budget) BUDGET="$2"; shift 2;; *) SCEN+=("$1"); shift;; esac; done
[ ${#SCEN[@]} -eq 0 ] && SCEN=(forward)
export PATH="$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="openshell-forward-$NAME"
up() { curl -sf -m 3 "http://127.0.0.1:$PORT/api/status" >/dev/null; }
wait_up() { local t0=$SECONDS; while [ $((SECONDS - t0)) -le "$BUDGET" ]; do up && { echo $((SECONDS - t0)); return 0; }; sleep 1; done; echo "$BUDGET+"; return 1; }
record() { python3 - "$@" <<'EOF'
import sys; sys.path.insert(0, sys.argv[1])
from agentkit.ledger import Ledger; from pathlib import Path
Ledger(Path(sys.argv[1]) / "foundry" / "data" / "ledger.jsonl").append("infra_fault", None, scenario=sys.argv[2], ok=sys.argv[3] == "PASS", recovery_s=sys.argv[4])
EOF
}
up || { echo "precondition failed: Mission Control not up on :$PORT"; exit 2; }
ledger_before="$(curl -s -m 5 "http://127.0.0.1:$PORT/api/status" | python3 -c 'import sys,json; print(json.load(sys.stdin)["ledger"]["count"])')"
rc=0
for s in "${SCEN[@]}"; do
  case "$s" in
    forward)
      pid="$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)"
      [ -n "$pid" ] && [ "$pid" != 0 ] || { echo "[SKIP] forward: $UNIT not running under systemd"; continue; }
      kill "$pid"; sleep 1; up && echo "(still up right after kill — kernel socket lingered)" ;;
    gateway)
      systemctl --user restart openshell-gateway 2>/dev/null || { echo "[SKIP] gateway: no systemd gateway service on this box (0.0.1x)"; continue; } ;;
    sandbox)
      openshell sandbox stop "$NAME" >/dev/null 2>&1 || { echo "[SKIP] sandbox: stop not supported here"; continue; }
      sleep 2; openshell sandbox start "$NAME" >/dev/null 2>&1 ;;
    *) echo "unknown scenario $s"; rc=2; continue ;;
  esac
  t="$(wait_up)"; ok=$?
  if [ $ok -eq 0 ]; then verdict=PASS; else verdict=FAIL; rc=1; fi
  echo "[$verdict] $s: Mission Control back in ${t}s (budget ${BUDGET}s)"
  record "$ROOT" "$s" "$verdict" "$t"
done
ledger_after="$(curl -s -m 5 "http://127.0.0.1:$PORT/api/status" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["ledger"]["count"])' 2>/dev/null)"
echo "sandbox ledger events before/after: $ledger_before / ${ledger_after:-?}"
exit $rc
