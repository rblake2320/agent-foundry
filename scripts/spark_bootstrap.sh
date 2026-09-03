#!/usr/bin/env bash
# Rebuild the NVIDIA OpenShell runtime for an agentkit agent on a fresh Linux box (DGX Spark or any docker/podman host),
# from zero, idempotently. Run it on the standby box and the agent comes up there with the same policy, same evidence
# bundle format and (after `scripts/sync_state.py peer push --with-keys`) the same identity — no single point of failure.
#
#   scripts/spark_bootstrap.sh [--agent products/agent-seller] [--model nemotron-3-nano:30b] [--ollama http://HOST:11434] [--port 8111] [--check]
#
# Steps: 1 CLI  2 host prerequisites (docker|podman, ollama, model)  3 gateway  4 provider (host ollama behind the gateway,
# key injected, never in the sandbox)  5 inference route  6 policy export  7 sandbox create/hot-reload  8 forward + health.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="${AGENT:-products/agent-seller}"; MODEL="${MODEL:-}"; PORT="${PORT:-8111}"; CHECK=0; RECREATE=0
OLLAMA_URL="${OLLAMA_URL:-http://$(hostname -I | awk '{print $1}'):11434}"
while [ $# -gt 0 ]; do case "$1" in
  --agent) AGENT="$2"; shift 2;; --model) MODEL="$2"; shift 2;; --ollama) OLLAMA_URL="$2"; shift 2;; --port) PORT="$2"; shift 2;;
  --check) CHECK=1; shift;; --recreate) RECREATE=1; shift;; *) echo "unknown option $1"; exit 2;; esac; done
export PATH="$HOME/.local/bin:$PATH"
NAME="$(basename "$AGENT")"
step() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*"; exit 1; }

step "1/8 openshell CLI"
if ! command -v openshell >/dev/null 2>&1; then
  [ "$CHECK" = 1 ] && fail "openshell not installed"
  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
fi
echo "openshell $(openshell --version | awk '{print $2}')"

step "2/8 host prerequisites"
(docker info >/dev/null 2>&1 && echo "docker: ok") || (podman info >/dev/null 2>&1 && echo "podman: ok") || fail "neither docker nor podman is usable"
curl -sf -m 5 "$OLLAMA_URL/api/tags" >/dev/null || fail "ollama not reachable at $OLLAMA_URL (pass --ollama http://HOST:11434)"
TAGS="$(curl -s "$OLLAMA_URL/api/tags")"
if [ -z "$MODEL" ]; then
  for m in nemotron-3-nano:30b nemotron-3-nano:latest qwen3.8:27b qwen3.6:27b qwen3:30b-a3b qwen3:32b qwen3:latest; do
    echo "$TAGS" | grep -q "\"name\":\"$m\"" && MODEL="$m" && break; done
fi
[ -n "$MODEL" ] || fail "no known model in ollama; pass --model"
echo "model: $MODEL via $OLLAMA_URL"

step "3/8 gateway"
if ! openshell gateway info >/dev/null 2>&1; then
  [ "$CHECK" = 1 ] && fail "gateway not reachable"
  if openshell gateway --help 2>&1 | grep -qE '^\s+start\b'; then openshell gateway start          # ≤0.0.16: k3s-in-docker gateway
  else
    # ≥0.0.100: the installer registers a systemd user service that auto-detects Kubernetes → Podman → Docker. Rootless podman
    # without pasta cannot take gateway callbacks (podman 4.x + CNI), so pin the docker driver when docker is usable.
    mkdir -p ~/.config/openshell
    if docker info >/dev/null 2>&1 && ! grep -q OPENSHELL_DRIVERS ~/.config/openshell/gateway.env 2>/dev/null; then
      echo "OPENSHELL_DRIVERS=docker" >> ~/.config/openshell/gateway.env
    fi
    systemctl --user restart openshell-gateway
  fi
  for _ in $(seq 1 30); do openshell gateway info >/dev/null 2>&1 && break; sleep 2; done
fi
openshell gateway info 2>&1 | head -8

step "4/8 provider: host ollama as an OpenAI-compatible endpoint"
if openshell provider list 2>/dev/null | grep -qE '^ollama\b'; then
  echo "provider ollama exists"
else
  [ "$CHECK" = 1 ] && fail "provider ollama missing"
  openshell provider create --type openai --name ollama --credential OPENAI_API_KEY=ollama --config OPENAI_BASE_URL="$OLLAMA_URL/v1"
fi

step "5/8 inference route"
if openshell inference get 2>/dev/null | grep -q "Model: *$MODEL"; then echo "route already $MODEL"; else
  [ "$CHECK" = 1 ] && fail "inference route not set to $MODEL"
  openshell inference set --provider ollama --model "$MODEL"; fi
openshell inference get 2>&1 | sed -n 1,5p

step "6/8 policy export (deny-by-default, derived from the tool allowlist)"
cd "$ROOT"
VER="$(openshell --version | awk '{print $2}')"
L7=rest; case "$VER" in 0.0.[0-9]|0.0.[12][0-9]|0.0.3[0-6]) L7=https;; esac    # policies before 0.0.37 spelled the L7 protocol "https"
echo "openshell $VER → L7 protocol '$L7'"
python3 -m agentkit --root "$AGENT" openshell --l7 "$L7" >/dev/null
sha256sum "$AGENT/openshell/policy.yaml"

step "7/8 sandbox $NAME"
if [ "$RECREATE" = 1 ] && openshell sandbox list 2>/dev/null | grep -qE "^$NAME\b"; then
  echo "recreating sandbox from the current code"; openshell sandbox delete "$NAME" || true; sleep 3
fi
if openshell sandbox list 2>/dev/null | grep -qE "^$NAME\b"; then
  echo "sandbox exists → hot-reload policy"
  [ "$CHECK" = 1 ] || openshell policy set "$NAME" --policy "$AGENT/openshell/policy.yaml" --wait
else
  [ "$CHECK" = 1 ] && fail "sandbox $NAME missing"
  mkdir -p "$ROOT/logs"
  # the create process stays attached to the sandbox's main command; detach it from this shell so it survives logout
  UPLOAD=(); [ -f products/catalog.json ] && UPLOAD=(--upload products/catalog.json:/sandbox/products/catalog.json)
  setsid nohup openshell sandbox create --name "$NAME" --from ./Dockerfile --policy "$AGENT/openshell/policy.yaml" --provider ollama --no-tty \
      "${UPLOAD[@]}" -- \
      env AGENTKIT_MODEL_BACKEND=openai_compat AGENTKIT_OPENAI_BASE_URL=https://inference.local/v1 AGENTKIT_OPENAI_MODEL="$MODEL" \
      python3 -m agentkit --root "/sandbox/$AGENT" mc --port "$PORT" > "$ROOT/logs/sandbox-$NAME.log" 2>&1 < /dev/null &
  for _ in $(seq 1 90); do openshell sandbox list 2>/dev/null | grep -E "^$NAME\b" | grep -q Ready && break; sleep 5; done
  openshell sandbox list 2>/dev/null | grep -E "^$NAME\b" || fail "sandbox did not become Ready (see logs/sandbox-$NAME.log)"
fi

step "8/8 forward :$PORT → sandbox Mission Control + health"
if ! curl -sf -m 3 "http://127.0.0.1:$PORT/api/status" >/dev/null; then
  [ "$CHECK" = 1 ] && fail "Mission Control not answering on :$PORT"
  # forward start maps local PORT to the same PORT inside the sandbox (Mission Control was started on it above)
  setsid nohup openshell forward start -d "$PORT" "$NAME" > "$ROOT/logs/forward-$NAME.log" 2>&1 < /dev/null &
  for _ in $(seq 1 30); do curl -sf -m 3 "http://127.0.0.1:$PORT/api/status" >/dev/null && break; sleep 2; done
fi
curl -sf -m 5 "http://127.0.0.1:$PORT/api/status" | python3 -c "import sys,json; s=json.load(sys.stdin); print('Mission Control:', s['agent']['name'], 'model', s['model'], 'ledger', s['ledger'])" \
  || fail "Mission Control not reachable on :$PORT (see logs/forward-$NAME.log)"
curl -sf -m 5 "http://127.0.0.1:$PORT/api/evidence" | python3 -c "import sys,json; e=json.load(sys.stdin); print('identity:', e['identity']['did'])"
echo; echo "READY: $NAME under OpenShell on $(hostname) — http://127.0.0.1:$PORT/  (policy sha above; evidence at /api/evidence)"
