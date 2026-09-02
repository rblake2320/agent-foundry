# Run Agent Foundry under NVIDIA OpenShell

OpenShell is the runtime below this harness: it enforces what the agent CAN do (Landlock, seccomp, proxy egress,
routed inference, injected credentials) while agentkit governs what it TRIES (tools, approvals, budgets).
Host: Linux, macOS (Apple Silicon), or Windows with WSL 2. Install: `curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh`

```bash
# 1. credentials never enter the sandbox filesystem; they are injected as env vars at creation
openshell provider create --type generic --name agent-foundry-model --from-existing   # e.g. NVIDIA_API_KEY

# 2. route this agent's model calls through the gateway (caller-supplied keys are stripped, backend keys injected)
openshell inference set --provider agent-foundry-model --model nvidia/nemotron-3-super-120b-a12b

# 3. create the sandbox with the derived policy and start Mission Control inside it
openshell sandbox create agent-foundry --policy openshell/policy.yaml -- \
  env AGENTKIT_MODEL_BACKEND=openai_compat python3 -m agentkit --root /sandbox mc --port 8110

# 4. later policy changes (network / inference) hot-reload without restarting
openshell policy set agent-foundry --policy openshell/policy.yaml --wait
```

In `agent.toml`, point `[model].openai_base_url` at `https://inference.local/v1` when running under OpenShell.
Approval-gated actions (publish_agent, deploy_agent, launch_agent, apply_fix) get their egress rule only when the owner approves,
so the sandbox cannot send even if the harness were bypassed. A built agent's policy is a subset of its builder's
(authority ceiling): the Foundry's own policy has no tool egress at all.
