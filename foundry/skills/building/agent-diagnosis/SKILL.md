---
name: agent-diagnosis
description: Diagnose a broken or under-performing agentkit agent from evidence (health report, eval scores, fault-injection results, ledger events, reports) and name the root cause in one of five classes with the exact file to change. Use for the test-and-repair task and whenever a product's health is amber or red. Do not use to guess without evidence.
---

# Agent Diagnosis

## When to Use
- A product's health grade is amber or red, an eval verdict is FAIL, or a fault scenario is not contained.
- NOT for: environment fixes the owner must do (a doctor FAIL names its own fix), or agents outside the catalogue.

## Quick Reference
- Evidence order (NVIDIA-style trial scoring): outcome (deliverable met?) → trajectory (right tools, in order?) → efficiency (steps, seconds, tokens) → safety (injection contained, no secrets, no forbidden tools) → environment (doctor).
- Root-cause classes, exactly one per finding:
  1. `spec.task` — task instructions or deliverable checklist wrong, unverifiable, or too vague → fix `tasks[].instructions` / `deliverable`.
  2. `spec.skill` — the skill lacks the concrete rule the model needed (query strategy, thresholds, field names) → fix `skills[].quick_reference` / `procedure`.
  3. `spec.tools` — a needed tool is missing from the allowlist, or a task lists a tool it cannot use → fix `tools` / `tasks[].tools`.
  4. `spec.limits` — steps/tool calls/minutes too low for the job (task ends "incomplete" with budget messages) → fix `limits`.
  5. `env` — model down, key missing, folder not writable, scheduler absent → owner action from the doctor fix column; not a spec change.
- Signals → class: unmet deliverable items with correct tool use → spec.task; tool never called though expected → spec.skill or spec.tools; "tool not in allowlist" errors → spec.tools; steps hit the cap → spec.limits; model_error events → env.
- A diagnosis without a file and field to change is not a diagnosis.

## Procedure
1. Read the health report: grade, reasons, unmet items, tool error rates, halt reasons.
2. Read the last eval scores per eval: which metric is low, and the judge's "missing" list.
3. Read the fault results: any scenario not contained is a `safety` finding with the highest priority.
4. For each finding, pick exactly one root-cause class and the spec path to change; cite the evidence line.
5. Rank findings: safety > outcome > tool_use > efficiency; keep at most three per agent per run.

## Pitfalls
- Blaming the model for a vague task: if the checklist item cannot be judged from text, the task is at fault.
- Raising limits to hide a strategy problem: budget is the last class to pick, never the first.
- Diagnosing from a single run: use the window of runs and at least one eval.

## Verification
- [ ] Every finding has class, evidence, spec path, and a one-line proposed change.
- [ ] No more than three findings per agent.
- [ ] Safety findings, if any, are ranked first.
