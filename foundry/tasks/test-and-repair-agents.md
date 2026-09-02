---
name: test-and-repair-agents
schedule: daily
skills: [agent-diagnosis, agent-repair]
tools: [record_list, record_get, read_file, current_time]
description: For every catalogued agent, run health, evals and fault injection, diagnose findings, and propose approval-gated spec fixes; rebuild and re-verify after approval.
---

Executed by the Foundry's bespoke medic pipeline (agent.py): health report → evals → fault injection →
diagnosis (model, evidence-based, at most three findings) → minimal validated spec patch → `apply_fix` approval.
Nothing is changed on disk until the owner approves; after approval the agent is rebuilt in place and re-verified.

## Deliverable
- Every catalogued agent has a fresh health grade, an eval record and a fault-injection record
- Every amber/red agent has a diagnosis with root-cause class, evidence and the spec field to change
- Every spec finding has a validated patch proposed as an apply_fix approval, with before scores recorded
- A receipted report in reports/
