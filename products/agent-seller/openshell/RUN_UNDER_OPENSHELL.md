# Run Agent Seller under NVIDIA OpenShell

OpenShell is the runtime below this harness: it enforces what the agent CAN do (Landlock, seccomp, proxy egress,
routed inference, injected credentials) while agentkit governs what it TRIES (tools, approvals, budgets).
Host: Linux, macOS (Apple Silicon), or Windows with WSL 2. Install: `curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh`

Verified 2026-09-02 on an NVIDIA DGX Spark (Ubuntu 24.04, aarch64) with OpenShell 0.0.16 and a host-level Ollama.

```bash
openshell gateway start                                   # local k3s-in-docker gateway (first run pulls the cluster image)

# 1. a provider for the model backend; credentials never enter the sandbox filesystem (injected as env vars at creation)
#    host-level Ollama / vLLM / NIM (OpenAI-compatible):
openshell provider create --name ollama --type openai --credential OPENAI_API_KEY=empty \
  --config OPENAI_BASE_URL=http://host.openshell.internal:11434/v1
#    or NVIDIA cloud:  openshell provider create --name nvidia --type nvidia --credential NVIDIA_API_KEY

# 2. route this agent's model calls through the gateway (caller-supplied keys are stripped, backend keys injected)
openshell inference set --provider ollama --model nvidia/nemotron-3-super-120b-a12b

# 3. build the sandbox image from the repo Dockerfile (python:3.12-slim + iproute2; MUST contain user+group `sandbox`),
#    apply the derived policy, attach the provider, start Mission Control inside the sandbox
openshell sandbox create --name agent-seller --from ./Dockerfile --policy agent-seller/openshell/policy.yaml \
  --provider ollama --no-tty -- python3 -m agentkit --root /sandbox/agent-seller mc

# 4. reach it from the host, watch the proxy enforce the policy, review advisor-proposed rules
openshell forward start 8111 agent-seller          # then http://127.0.0.1:8111
openshell logs agent-seller --tail
openshell rule get --status pending

# 5. later policy changes (network / inference) hot-reload without restarting
openshell policy set agent-seller --policy agent-seller/openshell/policy.yaml --wait
```

In `agent.toml` set `[model].backend = "openai_compat"`, `openai_base_url = "https://inference.local/v1"`,
`openai_api_key_env = "OPENAI_API_KEY"` (the attached provider injects it) and `openai_model` to the routed model.
Approval-gated actions (send_email, schedule_call) get their egress rule only when the owner approves,
so the sandbox cannot send even if the harness were bypassed. A built agent's policy is a subset of its builder's
(authority ceiling): the Foundry's own policy has no tool egress at all.
